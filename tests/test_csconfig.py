#!/usr/bin/env python3
"""Tests for scripts/csconfig.py — the config and held-reply plumbing.

    python3 -m unittest discover tests

This is what replaced jq in bin/read-aloud, so it has to behave the way the
jq expressions did: defaults underneath the user's choices, held replies read
back oldest first, and a scope of "" meaning every project.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cstext  # noqa: E402

CLI = os.path.join(ROOT, "scripts", "csconfig.py")


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.json")
        self.held = os.path.join(self.dir, "held.jsonl")
        self.env = dict(os.environ,
                        READ_ALOUD_HOME=self.dir,
                        READ_ALOUD_CONFIG=self.cfg)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI] + list(args),
                              capture_output=True, text=True, env=self.env)

    def read_cfg(self):
        with open(self.cfg) as fh:
            return json.load(fh)

    def write_cfg(self, obj):
        with open(self.cfg, "w") as fh:
            json.dump(obj, fh)

    def write_held(self, rows):
        with open(self.held, "w") as fh:
            for ts, label, text in rows:
                fh.write(json.dumps({"ts": ts, "session": "s",
                                     "label": label, "text": text}) + "\n")


class Config(Base):

    def test_ensure_writes_every_default(self):
        self.run_cli("config", "ensure")
        self.assertEqual(self.read_cfg(), cstext.DEFAULTS)

    def test_ensure_keeps_user_choices(self):
        self.write_cfg({"voice": "bm_george", "speed": 1.4})
        self.run_cli("config", "ensure")
        got = self.read_cfg()
        self.assertEqual(got["voice"], "bm_george")
        self.assertEqual(got["speed"], 1.4)
        self.assertEqual(set(got), set(cstext.DEFAULTS))

    def test_ensure_is_idempotent(self):
        self.run_cli("config", "ensure")
        first = self.read_cfg()
        self.run_cli("config", "ensure")
        self.assertEqual(self.read_cfg(), first)

    def test_ensure_survives_a_corrupt_config(self):
        with open(self.cfg, "w") as fh:
            fh.write("{not json")
        self.run_cli("config", "ensure")
        self.assertEqual(self.read_cfg(), cstext.DEFAULTS)

    def test_get_is_shell_friendly(self):
        self.run_cli("config", "ensure")
        for key, want in [("voice", "af_heart"), ("speed", "1.0"),
                          ("holdReplies", "true"), ("meetingGuard", "true"),
                          ("holdSound", "")]:
            with self.subTest(key):
                self.assertEqual(self.run_cli("config", "get", key).stdout.strip(),
                                 want)

    def test_get_falls_back_to_defaults(self):
        self.write_cfg({})            # a config missing everything
        self.assertEqual(self.run_cli("config", "get", "voice").stdout.strip(),
                         cstext.DEFAULTS["voice"])

    def test_set_parses_json_but_accepts_bare_words(self):
        self.run_cli("config", "ensure")
        for key, given, want in [("enabled", "false", False),
                                 ("speed", "1.3", 1.3),
                                 ("maxChars", "4000", 4000),
                                 ("voice", '"bm_george"', "bm_george"),
                                 ("holdSound", "off", "off"),
                                 ("holdSound", "", "")]:
            with self.subTest(key=key, given=given):
                self.run_cli("config", "set", key, given)
                self.assertEqual(self.read_cfg()[key], want)

    def test_set_leaves_the_file_valid_json(self):
        self.run_cli("config", "ensure")
        self.run_cli("config", "set", "voice", '"af_bella"')
        self.assertEqual(self.read_cfg()["voice"], "af_bella")   # parses at all

    def test_summary_is_the_status_block(self):
        self.run_cli("config", "ensure")
        got = json.loads(self.run_cli("config", "summary").stdout)
        self.assertEqual(list(got), list(("enabled", "engine", "voice", "speed",
                                          "maxChars", "multiSession",
                                          "sameSession", "holdReplies", "holdSound",
                                          "meetingGuard", "notify")))


class Held(Base):

    ROWS = [(300, "api-server", "The migration finished."),
            (100, "web-ui", "Build is green."),
            (200, "api-server", "Tests pass.")]

    def setUp(self):
        super().setUp()
        self.write_held(self.ROWS)

    def test_count(self):
        self.assertEqual(self.run_cli("held", "count").stdout.strip(), "3")
        self.assertEqual(self.run_cli("held", "count", "api-server").stdout.strip(), "2")
        self.assertEqual(self.run_cli("held", "count", "nope").stdout.strip(), "0")

    def test_labels(self):
        out = self.run_cli("held", "labels").stdout
        self.assertIn("api-server (2 waiting)", out)
        self.assertIn("web-ui (1 waiting)", out)

    def test_pending_marks_the_current_project(self):
        out = self.run_cli("held", "pending", "web-ui").stdout.splitlines()
        starred = [ln for ln in out if ln.startswith(" *")]
        self.assertEqual(len(starred), 1)
        self.assertIn("web-ui", starred[0])

    def test_text_is_oldest_first(self):
        out = self.run_cli("held", "text", "api-server").stdout.strip()
        self.assertEqual(out, "Tests pass. The migration finished.")

    def test_text_names_the_project_only_when_mixed(self):
        mixed = self.run_cli("held", "text", "").stdout
        self.assertIn("From web ui.", mixed)          # dashes spoken as spaces
        single = self.run_cli("held", "text", "api-server").stdout
        self.assertNotIn("From", single)

    def test_drop_one_project(self):
        self.run_cli("held", "drop", "web-ui")
        self.assertEqual(self.run_cli("held", "count").stdout.strip(), "2")
        self.assertEqual(self.run_cli("held", "count", "web-ui").stdout.strip(), "0")

    def test_drop_all(self):
        self.run_cli("held", "drop", "")
        self.assertEqual(self.run_cli("held", "count").stdout.strip(), "0")

    def test_a_corrupt_line_is_skipped_not_fatal(self):
        with open(self.held, "a") as fh:
            fh.write("{ this is not json\n\n")
        self.assertEqual(self.run_cli("held", "count").stdout.strip(), "3")

    def test_missing_file(self):
        os.unlink(self.held)
        self.assertEqual(self.run_cli("held", "count").stdout.strip(), "0")


class NoDuplicateDefaults(unittest.TestCase):
    """The shell used to carry its own copy of the defaults. It must not again.

    Two heredocs and the Python dict drifted apart once already, which is how
    fallbackRate and fallbackVoice went missing for four releases.
    """

    def test_no_json_heredoc_in_the_shell(self):
        for rel in ("bin/read-aloud", "scripts/install.sh"):
            with self.subTest(rel):
                with open(os.path.join(ROOT, rel)) as fh:
                    self.assertIsNone(re.search(r"<<'JSON'", fh.read()),
                                      "%s carries its own defaults again" % rel)

    def test_the_shell_does_not_need_jq(self):
        for rel in ("bin/read-aloud", "scripts/install.sh"):
            with self.subTest(rel):
                with open(os.path.join(ROOT, rel)) as fh:
                    calls = [ln for ln in fh
                             if re.search(r"(^|[|(;&\s])jq\s", ln)
                             and not ln.lstrip().startswith("#")]
                self.assertEqual(calls, [], "%s still calls jq" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
