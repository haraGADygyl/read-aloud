#!/usr/bin/env python3
"""Tests for `read-aloud again` — reading the last reply back.

    python3 -m unittest discover tests

The reply is kept by the Stop hook rather than dug out of Claude Code's
transcript afterwards: what gets stored is the text that was actually spoken,
already cleaned and already cut to maxChars, so a repeat is the same words
rather than nearly the same words. One file per project, keyed by the label
play and clear use.

Nothing here plays audio: notify is off, which stops the hook before the ding,
and the meeting guard is off so it never shells out to pactl.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cspaths  # noqa: E402

HOOK = os.path.join(ROOT, "scripts", "speak-hook.py")
CLI = os.path.join(ROOT, "scripts", "csconfig.py")


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.json")
        self.env = dict(os.environ,
                        READ_ALOUD_HOME=self.dir,
                        READ_ALOUD_CONFIG=self.cfg)
        self.write_cfg({})

    def write_cfg(self, extra):
        base = {"notify": False, "meetingGuard": False, "holdSound": "off"}
        base.update(extra)
        with open(self.cfg, "w") as fh:
            json.dump(base, fh)

    def reply(self, text, cwd="/tmp/some-project", session="s"):
        payload = {"cwd": cwd, "last_assistant_message": text}
        if session:
            payload["session_id"] = session
        payload = json.dumps(payload)
        return subprocess.run([sys.executable, HOOK], input=payload,
                              capture_output=True, text=True, env=self.env)

    def last(self, label):
        return subprocess.run([sys.executable, CLI, "last", "get", label],
                              capture_output=True, text=True, env=self.env)


class Remembering(Base):

    def test_the_reply_is_kept_under_the_project_name(self):
        self.reply("All nineteen tests pass.")
        self.assertEqual(self.last("some-project").stdout, "All nineteen tests pass.")

    def test_what_is_kept_is_what_was_spoken(self):
        """Cleaned, not raw: a repeat must not read the markdown aloud."""
        self.reply("# Heading\n\n- first\n- second\n\n```py\nx = 1\n```")
        said = self.last("some-project").stdout
        self.assertNotIn("#", said)
        self.assertNotIn("```", said)
        self.assertIn("Code block", said)

    def test_the_maxChars_cut_is_kept_too(self):
        self.write_cfg({"maxChars": 40})
        self.reply("This sentence is quite long. " * 6)
        said = self.last("some-project").stdout
        self.assertTrue(said.endswith("Rest is on screen."), said)

    def test_the_newest_reply_wins(self):
        self.reply("first")
        self.reply("second")
        self.assertEqual(self.last("some-project").stdout, "second")

    def test_projects_do_not_share(self):
        self.reply("from api", cwd="/tmp/api-server")
        self.reply("from web", cwd="/tmp/web-client")
        self.assertEqual(self.last("api-server").stdout, "from api")
        self.assertEqual(self.last("web-client").stdout, "from web")

    def test_held_replies_are_kept_as_well(self):
        """Hold mode means you have not heard it yet — again still applies."""
        self.write_cfg({"holdReplies": True})
        self.reply("stashed, not spoken")
        self.assertEqual(self.last("some-project").stdout, "stashed, not spoken")

    def test_nothing_is_kept_while_switched_off(self):
        self.write_cfg({"enabled": False})
        self.reply("never spoken")
        self.assertEqual(self.last("some-project").returncode, 1)

    def test_an_empty_reply_leaves_no_trace(self):
        self.reply("")
        self.assertEqual(self.last("some-project").returncode, 1)


class Asking(Base):

    def test_a_project_never_heard_from_fails_quietly(self):
        out = self.last("never-seen")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")

    def test_drop_forgets_it(self):
        self.reply("something")
        subprocess.run([sys.executable, CLI, "last", "drop", "some-project"],
                       capture_output=True, env=self.env)
        self.assertEqual(self.last("some-project").returncode, 1)

    def test_dropping_nothing_is_not_an_error(self):
        out = subprocess.run([sys.executable, CLI, "last", "drop", "never-seen"],
                             capture_output=True, env=self.env)
        self.assertEqual(out.returncode, 0)

    def test_an_unknown_action_says_so(self):
        out = subprocess.run([sys.executable, CLI, "last", "sing"],
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 2)
        self.assertIn("unknown last action", out.stderr)


class PerSession(Base):
    """Several sessions open in one directory share the project copy, so the
    project copy is whichever finished last — /read-aloud:speak again once
    read a neighbouring terminal's reply instead of its own."""

    def session(self, sid):
        return subprocess.run([sys.executable, CLI, "last", "session", sid],
                              capture_output=True, text=True, env=self.env)

    def test_sessions_in_one_directory_keep_their_own(self):
        self.reply("review summary", session="341")
        self.reply("share-link draft", session="other")
        self.assertEqual(self.session("341").stdout, "review summary")
        self.assertEqual(self.session("other").stdout, "share-link draft")

    def test_the_project_copy_is_still_the_newest(self):
        """A plain shell has no session and gets this one."""
        self.reply("review summary", session="341")
        self.reply("share-link draft", session="other")
        self.assertEqual(self.last("some-project").stdout, "share-link draft")

    def test_a_session_never_heard_from_fails_quietly(self):
        self.assertEqual(self.session("never").returncode, 1)

    def test_no_session_id_writes_no_session_file(self):
        self.reply("anonymous", session="")
        self.assertEqual(self.last("some-project").stdout, "anonymous")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "last", "sessions")))

    def test_month_old_sessions_are_forgotten(self):
        self.reply("old", session="ancient")
        path = os.path.join(self.dir, "last", "sessions", "ancient.txt")
        month = time.time() - 31 * 86400
        os.utime(path, (month, month))
        self.reply("new", session="current")
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.session("current").stdout, "new")


class CommandReplies(Base):
    """Claude's one-line report of a /read-aloud:speak run — "Stopped
    speaking." — is not a reply worth hearing, and must not become the one
    `again` repeats."""

    def mute(self, sid="s"):
        subprocess.run([sys.executable, CLI, "last", "mute", sid],
                       capture_output=True, env=self.env)

    def test_the_report_is_not_kept(self):
        self.reply("review summary")
        self.mute()
        self.reply("Stopped speaking.")
        self.assertEqual(self.last("some-project").stdout, "review summary")

    def test_the_report_is_not_held_either(self):
        self.write_cfg({"holdReplies": True})
        self.mute()
        self.reply("Repeating the last reply.")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "held.jsonl")))

    def test_only_the_next_reply_is_skipped(self):
        self.mute()
        self.reply("Stopped speaking.")
        self.reply("real work")
        self.assertEqual(self.last("some-project").stdout, "real work")

    def test_the_marker_belongs_to_one_session(self):
        self.mute("341")
        self.reply("from next door", session="other")
        self.assertEqual(self.last("some-project").stdout, "from next door")

    def test_a_stale_marker_swallows_nothing(self):
        """A command whose report never came — Esc, so no Stop — must not eat
        a real reply minutes later."""
        self.mute()
        path = os.path.join(self.dir, "last", "sessions", "s.mute")
        old = time.time() - 600
        os.utime(path, (old, old))
        self.reply("real work")
        self.assertEqual(self.last("some-project").stdout, "real work")
        self.assertFalse(os.path.exists(path))

    def test_turning_it_off_still_uses_up_the_marker(self):
        self.mute()
        self.write_cfg({"enabled": False})
        self.reply("Voice off.")
        self.write_cfg({})
        self.reply("real work")
        self.assertEqual(self.last("some-project").stdout, "real work")


class FromTheSlashCommand(Base):
    """bin/read-aloud --session, as /read-aloud:speak runs it. Only the
    paths that play nothing are driven here."""

    BIN = os.path.join(ROOT, "bin", "read-aloud")

    def cli(self, *args):
        env = dict(self.env, HOME=self.dir, XDG_RUNTIME_DIR=self.dir)
        return subprocess.run([self.BIN] + list(args), capture_output=True,
                              text=True, env=env, cwd=self.dir)

    def test_again_asks_for_this_session_not_the_project(self):
        self.reply("someone else's", cwd=self.dir, session="other")
        out = self.cli("--session", "341", "again")
        self.assertEqual(out.stdout.strip(), "nothing spoken in this session yet")

    def test_the_command_marks_its_report(self):
        self.cli("--session", "341", "pending")
        self.assertTrue(os.path.exists(
            os.path.join(self.dir, "last", "sessions", "341.mute")))

    def test_a_plain_shell_marks_nothing(self):
        self.cli("pending")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "last", "sessions")))

    def test_an_empty_session_is_a_plain_shell(self):
        """An older Claude Code leaves ${CLAUDE_SESSION_ID} unexpanded and
        the shell turns it into an empty argument."""
        out = self.cli("--session", "", "pending")
        self.assertNotIn("no such command", out.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "last", "sessions")))


class LabelsAreFilenames(unittest.TestCase):
    """A project name is a directory basename and can hold anything."""

    def test_a_traversal_cannot_escape(self):
        for label in ("..", "../..", "."):
            with self.subTest(label):
                self.assertEqual(os.path.dirname(cspaths.last_file(label)),
                                 cspaths.LAST)

    def test_awkward_names_still_get_a_file(self):
        self.assertTrue(cspaths.last_file("my project").endswith("my_project.txt"))
        self.assertTrue(cspaths.last_file("").endswith("claude.txt"))


if __name__ == "__main__":
    unittest.main()
