#!/usr/bin/env python3
"""Tests for carrying an install over from claude-speak, the name until 0.10.0.

    python3 -m unittest discover tests

Claude Code began refusing plugin names that start with "claude-". The data
directory holds a 338 MB model, so the rename moves it rather than starting
again — along with the config, the held replies, the daemon unit and the link.

Every run gets its own HOME, XDG directories and runtime dir, and a stub
systemctl that only writes down what it was asked: the real unit is named
claude-speak.service too, and a test that reached it would stop the daemon.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATE = os.path.join(ROOT, "scripts", "csmigrate.py")
HOOK = os.path.join(ROOT, "scripts", "speak-hook.py")
HEAL = os.path.join(ROOT, "scripts", "csheal.sh")
BIN = os.path.join(ROOT, "bin", "read-aloud")

# Held, not spoken, and no notification — so no ding either. Tests play nothing.
QUIET = {"notify": False, "meetingGuard": False, "holdSound": "off",
         "holdReplies": True}


class Home(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.share = os.path.join(self.home, ".local", "share")
        self.conf = os.path.join(self.home, ".config")
        self.stubs = os.path.join(self.home, "stubs")
        self.calls = os.path.join(self.home, "systemctl.log")
        os.makedirs(self.stubs)
        stub = os.path.join(self.stubs, "systemctl")
        with open(stub, "w") as fh:
            # is-enabled says yes, so the migration has a choice to carry over.
            fh.write('#!/bin/sh\necho "$*" >> "%s"\nexit 0\n' % self.calls)
        os.chmod(stub, 0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("READ_ALOUD_", "CLAUDE_SPEAK_", "XDG_"))}
        self.env.update(HOME=self.home, XDG_RUNTIME_DIR=self.home,
                        PATH=self.stubs + os.pathsep + os.environ["PATH"])

    def old_install(self):
        """What 0.9.2 left behind."""
        data = os.path.join(self.share, "claude-speak")
        os.makedirs(os.path.join(data, "models"))
        with open(os.path.join(data, "models", "kokoro-v1.0.onnx"), "w") as fh:
            fh.write("model")
        with open(os.path.join(data, "held.jsonl"), "w") as fh:
            fh.write('{"label": "proj", "text": "waiting"}\n')
        cfg = os.path.join(self.conf, "claude-speak")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "config.json"), "w") as fh:
            json.dump(dict(QUIET, voice="bm_george"), fh)
        return data, cfg

    def new(self, *parts):
        return os.path.join(*parts).replace("claude-speak", "read-aloud")

    def systemctl(self):
        try:
            with open(self.calls) as fh:
                return fh.read()
        except OSError:
            return ""


class Directories(Home):

    def run_migrate(self, **extra):
        return subprocess.run([sys.executable, MIGRATE], capture_output=True,
                              text=True, env=dict(self.env, **extra))

    def test_the_model_and_config_move_over(self):
        data, cfg = self.old_install()
        out = self.run_migrate()
        with open(self.new(data, "models", "kokoro-v1.0.onnx")) as fh:
            self.assertEqual(fh.read(), "model")
        with open(self.new(cfg, "config.json")) as fh:
            self.assertEqual(json.load(fh)["voice"], "bm_george")
        self.assertIn("moved ~/.local/share/claude-speak to ~/.local/share/read-aloud",
                      out.stdout)

    def test_a_link_is_left_where_they_were(self):
        """A terminal not yet restarted still runs the old hook, which writes
        its held replies to the old path."""
        data, cfg = self.old_install()
        self.run_migrate()
        self.assertEqual(os.path.realpath(data), self.new(data))
        self.assertEqual(os.path.realpath(cfg), self.new(cfg))

    def test_a_second_run_moves_nothing(self):
        self.old_install()
        self.run_migrate()
        self.assertEqual(self.run_migrate().stdout, "")

    def test_an_existing_read_aloud_directory_is_not_overwritten(self):
        data, _ = self.old_install()
        os.makedirs(self.new(data))
        self.run_migrate()
        self.assertFalse(os.path.islink(data))
        self.assertTrue(os.path.isdir(os.path.join(data, "models")))

    def test_a_redirected_run_moves_nothing(self):
        data, _ = self.old_install()
        for var in ("READ_ALOUD_HOME", "READ_ALOUD_CONFIG",
                    "CLAUDE_SPEAK_HOME", "CLAUDE_SPEAK_CONFIG"):
            with self.subTest(var):
                self.run_migrate(**{var: os.path.join(self.home, "elsewhere")})
                self.assertFalse(os.path.islink(data))

    def test_a_fresh_install_has_nothing_to_move(self):
        out = self.run_migrate()
        self.assertEqual(out.stdout, "")
        self.assertFalse(os.path.exists(os.path.join(self.share, "read-aloud")))


class OldPluginStillInstalled(Home):

    def registry(self, *keys):
        path = os.path.join(self.home, ".claude", "plugins")
        os.makedirs(path)
        with open(os.path.join(path, "installed_plugins.json"), "w") as fh:
            json.dump({"version": 2, "plugins": {k: [] for k in keys}}, fh)

    def test_it_is_named_with_the_command_to_remove_it(self):
        self.registry("claude-speak@claude-speak", "read-aloud@read-aloud")
        out = subprocess.run([sys.executable, MIGRATE], capture_output=True,
                             text=True, env=self.env).stdout
        self.assertIn("every reply is read twice", out)
        self.assertIn("claude plugin uninstall claude-speak@claude-speak", out)

    def test_quiet_once_it_is_gone(self):
        self.registry("read-aloud@read-aloud")
        out = subprocess.run([sys.executable, MIGRATE], capture_output=True,
                             text=True, env=self.env).stdout
        self.assertEqual(out, "")

    def test_a_missing_registry_is_not_an_error(self):
        out = subprocess.run([sys.executable, MIGRATE], capture_output=True,
                             text=True, env=self.env)
        self.assertEqual((out.returncode, out.stdout), (0, ""))


class TheHook(Home):
    """The first reply after the switch carries the state over — before the
    hook writes its pointer, which would otherwise start a fresh directory."""

    def test_the_first_reply_moves_it_and_lands_in_the_moved_queue(self):
        data, _ = self.old_install()
        payload = json.dumps({"session_id": "s", "cwd": "/tmp/proj",
                              "last_assistant_message": "hello"})
        subprocess.run([sys.executable, HOOK], input=payload, text=True,
                       capture_output=True, env=self.env)
        self.assertTrue(os.path.islink(data))
        with open(self.new(data, "held.jsonl")) as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 2)          # the old one, then this one
        self.assertIn("hello", lines[1])
        with open(self.new(data, "plugin-root")) as fh:
            self.assertEqual(fh.read().strip(), os.path.join(ROOT, "scripts"))


class TheCLI(Home):

    def old_unit_and_link(self):
        """A 0.9.2 plugin directory, its unit and its PATH link."""
        plugin = os.path.join(self.home, ".claude", "plugins", "cache",
                              "claude-speak", "claude-speak", "0.9.2")
        for d in ("scripts", "bin"):
            os.makedirs(os.path.join(plugin, d))
        for rel in ("scripts/cspaths.py", "scripts/kokorod.py", "bin/claude-speak"):
            open(os.path.join(plugin, rel), "w").close()
        os.chmod(os.path.join(plugin, "bin", "claude-speak"), 0o755)
        with open(os.path.join(self.share, "claude-speak", "plugin-root"), "w") as fh:
            fh.write(os.path.join(plugin, "scripts") + "\n")

        units = os.path.join(self.conf, "systemd", "user")
        os.makedirs(units)
        with open(os.path.join(units, "claude-speak.service"), "w") as fh:
            fh.write("ExecStart=%s/scripts/kokorod.py\n" % plugin)
        bindir = os.path.join(self.home, ".local", "bin")
        os.makedirs(bindir)
        os.symlink(os.path.join(plugin, "bin", "claude-speak"),
                   os.path.join(bindir, "claude-speak"))
        return units, bindir

    def cli(self, *args):
        return subprocess.run([BIN] + list(args), capture_output=True, text=True,
                              env=self.env, cwd=self.home)

    def test_everything_moves_in_one_run(self):
        data, cfg = self.old_install()
        units, bindir = self.old_unit_and_link()
        out = self.cli("pending").stdout

        self.assertTrue(os.path.islink(data) and os.path.islink(cfg))
        self.assertFalse(os.path.exists(os.path.join(units, "claude-speak.service")))
        with open(os.path.join(units, "read-aloud.service")) as fh:
            unit = fh.read()
        # The pointer still names the claude-speak copy, so this release
        # stands in for it until the hook runs.
        self.assertIn("ExecStart=%s %s/kokorod.py"
                      % (os.path.join(self.share, "read-aloud", "venv", "bin", "python"),
                         os.path.join(ROOT, "scripts")), unit)
        calls = self.systemctl()
        self.assertIn("disable --now claude-speak.service", calls)
        self.assertIn("enable --now read-aloud.service", calls)
        self.assertFalse(os.path.lexists(os.path.join(bindir, "claude-speak")))
        self.assertIn("removed", out)
        self.assertIn("replaced the claude-speak daemon unit", out)

    def test_the_settings_survive(self):
        self.old_install()
        self.assertEqual(self.cli("pending").returncode, 0)
        with open(os.path.join(self.conf, "read-aloud", "config.json")) as fh:
            self.assertEqual(json.load(fh)["voice"], "bm_george")

    def test_a_second_run_says_nothing_about_it(self):
        self.old_install()
        self.old_unit_and_link()
        self.cli("pending")
        out = self.cli("pending").stdout
        self.assertNotIn("claude-speak", out)

    def test_a_declined_service_stays_declined(self):
        """No old unit, no new one: the rename does not enable anything."""
        self.old_install()
        self.cli("pending")
        self.assertFalse(os.path.exists(
            os.path.join(self.conf, "systemd", "user", "read-aloud.service")))
        self.assertNotIn("enable", self.systemctl())


class SomebodyElsesLink(unittest.TestCase):

    def test_a_claude_speak_link_that_is_not_ours_stays(self):
        d = tempfile.mkdtemp()
        os.symlink("/usr/bin/true", os.path.join(d, "claude-speak"))
        out = subprocess.run(["bash", "-c", '. %s; cs_link_retire "%s"; echo "rc=$?"'
                              % (HEAL, d)], capture_output=True, text=True)
        self.assertIn("rc=12", out.stdout)
        self.assertTrue(os.path.islink(os.path.join(d, "claude-speak")))


if __name__ == "__main__":
    unittest.main()
