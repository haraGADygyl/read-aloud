#!/usr/bin/env python3
"""Tests for scripts/csheal.sh and the pointer the Stop hook writes.

    python3 -m unittest discover tests

Claude Code stamps the plugin directory with the version, so it moves on every
update and leaves the old one on disk, still resolvable. The PATH link and the
systemd unit are absolute paths into it. Before this, an update left the
daemon launching the previous release's kokorod.py while the hook ran the new
code — a shipped fix that never arrived, with nothing to see.

Nothing here touches the real ~/.local/bin, the real unit, or systemctl:
every path is a temporary directory, and cs_unit_sync is exercised through
cs_unit_write and cs_unit_stale, which do not shell out.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAL = os.path.join(ROOT, "scripts", "csheal.sh")
HOOK = os.path.join(ROOT, "scripts", "speak-hook.py")

PAYLOAD = '{"session_id":"s","cwd":"/tmp/proj","last_assistant_message":"hi"}'

# Quiet: notify off keeps the hook from reaching the ding, and the meeting
# guard off keeps it from shelling out to pactl. Tests play no audio.
QUIET_CFG = '{"notify": false, "meetingGuard": false, "holdSound": "off"}'


class Shell(unittest.TestCase):
    """Drive the helpers the way bin/read-aloud and install.sh do."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def sh(self, snippet):
        return subprocess.run(["bash", "-c", ". %s\n%s" % (HEAL, snippet)],
                              capture_output=True, text=True, cwd=self.dir)

    def plugin_tree(self, version):
        """A directory shaped like an installed plugin of that version."""
        root = os.path.join(self.dir, "cache", "read-aloud", version)
        os.makedirs(os.path.join(root, "scripts"))
        os.makedirs(os.path.join(root, "bin"))
        for rel in ("scripts/cspaths.py", "scripts/kokorod.py"):
            open(os.path.join(root, rel), "w").close()
        exe = os.path.join(root, "bin", "read-aloud")
        open(exe, "w").close()
        os.chmod(exe, 0o755)
        return root


class LinkSync(Shell):

    def link(self):
        return os.path.join(self.dir, "bin", "read-aloud")

    def sync(self, scripts):
        return self.sh('cs_link_sync "%s" "%s"; echo "rc=$?"'
                       % (os.path.join(self.dir, "bin"), scripts))

    def test_created_when_absent(self):
        new = self.plugin_tree("0.7.0")
        self.assertIn("rc=10", self.sync(os.path.join(new, "scripts")).stdout)
        self.assertEqual(os.path.realpath(self.link()),
                         os.path.join(new, "bin", "read-aloud"))

    def test_correct_link_is_left_alone(self):
        new = self.plugin_tree("0.7.0")
        self.sync(os.path.join(new, "scripts"))
        self.assertIn("rc=0", self.sync(os.path.join(new, "scripts")).stdout)

    def test_an_older_copy_is_repaired(self):
        old, new = self.plugin_tree("0.6.2"), self.plugin_tree("0.7.0")
        os.makedirs(os.path.join(self.dir, "bin"))
        os.symlink(os.path.join(old, "bin", "read-aloud"), self.link())

        self.assertIn("rc=11", self.sync(os.path.join(new, "scripts")).stdout)
        self.assertEqual(os.path.realpath(self.link()),
                         os.path.join(new, "bin", "read-aloud"))

    def test_a_deleted_copy_is_repaired(self):
        """Old plugin directories are usually kept, but need not be."""
        old, new = self.plugin_tree("0.6.2"), self.plugin_tree("0.7.0")
        os.makedirs(os.path.join(self.dir, "bin"))
        os.symlink(os.path.join(old, "bin", "read-aloud"), self.link())
        subprocess.run(["rm", "-rf", old], check=True)

        self.assertIn("rc=11", self.sync(os.path.join(new, "scripts")).stdout)
        self.assertEqual(os.path.realpath(self.link()),
                         os.path.join(new, "bin", "read-aloud"))

    def test_somebody_elses_binary_is_never_touched(self):
        new = self.plugin_tree("0.7.0")
        os.makedirs(os.path.join(self.dir, "bin"))
        theirs = os.path.join(self.dir, "their-read-aloud")
        open(theirs, "w").close()
        os.symlink(theirs, self.link())

        self.assertIn("rc=12", self.sync(os.path.join(new, "scripts")).stdout)
        self.assertEqual(os.readlink(self.link()), theirs)

    def test_a_real_file_is_never_replaced(self):
        new = self.plugin_tree("0.7.0")
        os.makedirs(os.path.join(self.dir, "bin"))
        with open(self.link(), "w") as fh:
            fh.write("#!/bin/sh\necho mine\n")

        self.assertIn("rc=12", self.sync(os.path.join(new, "scripts")).stdout)
        self.assertFalse(os.path.islink(self.link()))


class UnitSync(Shell):

    def unit(self):
        return os.path.join(self.dir, "read-aloud.service")

    def exec_start(self):
        with open(self.unit()) as fh:
            return [ln.strip() for ln in fh if ln.startswith("ExecStart=")][0]

    def test_written_with_the_scripts_path(self):
        self.sh('cs_unit_write "%s" /venv/bin/python /plug/scripts' % self.unit())
        self.assertEqual(self.exec_start(),
                         "ExecStart=/venv/bin/python /plug/scripts/kokorod.py")

    def test_stale_when_the_plugin_moved(self):
        self.sh('cs_unit_write "%s" /venv/bin/python /plug/0.6.2/scripts' % self.unit())
        out = self.sh('cs_unit_stale "%s" /venv/bin/python /plug/0.7.0/scripts; echo "rc=$?"'
                      % self.unit())
        self.assertIn("rc=0", out.stdout)          # 0 means "yes, stale"

    def test_not_stale_when_unchanged(self):
        self.sh('cs_unit_write "%s" /venv/bin/python /plug/scripts' % self.unit())
        out = self.sh('cs_unit_stale "%s" /venv/bin/python /plug/scripts; echo "rc=$?"'
                      % self.unit())
        self.assertIn("rc=1", out.stdout)

    def test_a_missing_unit_is_not_stale(self):
        """No unit means the daemon starts on demand; nothing to repair."""
        out = self.sh('cs_unit_stale "%s" /venv/bin/python /plug/scripts; echo "rc=$?"'
                      % self.unit())
        self.assertIn("rc=1", out.stdout)


class UnitRestart(Shell):
    """Whether the daemon was restarted decides whether the CLI waits for it.

    systemctl returns as soon as the process is exec'd, so a status check
    straight afterwards reported a starting daemon as stopped. The distinct
    code is what tells bin/read-aloud to wait for the socket — and only
    then, so an unenabled unit costs nothing.
    """

    def fake_systemctl(self, is_enabled):
        """A systemctl that records its calls instead of touching the real one."""
        binned = os.path.join(self.dir, "fakebin")
        os.makedirs(binned, exist_ok=True)
        path = os.path.join(binned, "systemctl")
        with open(path, "w") as fh:
            fh.write("#!/usr/bin/env bash\n"
                     'echo "$*" >> "%s"\n' % os.path.join(self.dir, "calls")
                     + ("exit 0\n" if is_enabled else
                        '[[ "$*" == *is-enabled* ]] && exit 1\nexit 0\n'))
        os.chmod(path, 0o755)
        return binned

    def calls(self):
        try:
            with open(os.path.join(self.dir, "calls")) as fh:
                return fh.read()
        except OSError:
            return ""

    def sync(self, is_enabled):
        unit = os.path.join(self.dir, "read-aloud.service")
        subprocess.run(["bash", "-c", '. %s\ncs_unit_write "%s" /venv/py /old/scripts'
                        % (HEAL, unit)], check=True)
        env = dict(os.environ, PATH=self.fake_systemctl(is_enabled) + os.pathsep
                   + os.environ["PATH"])
        return subprocess.run(
            ["bash", "-c", '. %s\ncs_unit_sync "%s" /venv/py /new/scripts; echo "rc=$?"'
             % (HEAL, unit)], capture_output=True, text=True, env=env)

    def test_restarted_when_the_service_is_enabled(self):
        self.assertIn("rc=13", self.sync(True).stdout)
        self.assertIn("restart read-aloud.service", self.calls())

    def test_not_restarted_when_it_is_not_enabled(self):
        self.assertIn("rc=10", self.sync(False).stdout)
        self.assertNotIn("restart", self.calls())


class RedirectedStateIsLeftAlone(unittest.TestCase):
    """READ_ALOUD_HOME redirects the state, never $HOME.

    The pointer then describes a scratch world while the link and the unit
    being repaired from it are the real ones. A test run that forgot to
    redirect HOME as well re-pointed a live daemon at a git working tree,
    where it failed 203/EXEC until the unit was rewritten by hand.
    """

    def test_the_cli_repairs_nothing_when_state_is_redirected(self):
        sandbox = tempfile.mkdtemp()
        home = os.path.join(sandbox, "home")
        data = os.path.join(sandbox, "data")
        os.makedirs(os.path.join(home, ".local", "bin"))
        os.makedirs(data)

        stale = os.path.join(sandbox, "stale-read-aloud")
        open(stale, "w").close()
        link = os.path.join(home, ".local", "bin", "read-aloud")
        os.symlink(stale, link)

        # A pointer that would otherwise be acted on.
        with open(os.path.join(data, "plugin-root"), "w") as fh:
            fh.write(os.path.join(ROOT, "scripts") + "\n")

        subprocess.run(["bash", os.path.join(ROOT, "bin", "read-aloud"), "hold"],
                       capture_output=True, text=True,
                       env=dict(os.environ, HOME=home,
                                READ_ALOUD_HOME=data,
                                READ_ALOUD_CONFIG=os.path.join(sandbox, "c.json"),
                                XDG_RUNTIME_DIR=sandbox))

        self.assertEqual(os.readlink(link), stale)


class Pointer(unittest.TestCase):
    """The Stop hook is the only part that always runs from the current copy."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.json")
        with open(self.cfg, "w") as fh:
            fh.write(QUIET_CFG)
        self.env = dict(os.environ,
                        READ_ALOUD_HOME=self.dir,
                        READ_ALOUD_CONFIG=self.cfg)
        self.pointer = os.path.join(self.dir, "plugin-root")

    def run_hook(self):
        return subprocess.run([sys.executable, HOOK], input=PAYLOAD,
                              capture_output=True, text=True, env=self.env)

    def test_the_hook_records_where_it_ran_from(self):
        self.run_hook()
        with open(self.pointer) as fh:
            self.assertEqual(fh.read().strip(),
                             os.path.join(ROOT, "scripts"))

    def test_a_stale_pointer_is_corrected(self):
        with open(self.pointer, "w") as fh:
            fh.write("/gone/0.6.2/scripts\n")
        self.run_hook()
        with open(self.pointer) as fh:
            self.assertEqual(fh.read().strip(), os.path.join(ROOT, "scripts"))

    def fake_plugin(self, version):
        """A plugin directory at some version, for a pointer to name."""
        root = os.path.join(self.dir, "plug-" + version)
        os.makedirs(os.path.join(root, ".claude-plugin"))
        os.makedirs(os.path.join(root, "scripts"))
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w") as fh:
            json.dump({"name": "read-aloud", "version": version}, fh)
        return os.path.join(root, "scripts")

    def point_at(self, scripts):
        with open(self.pointer, "w") as fh:
            fh.write(scripts + "\n")

    def read_pointer(self):
        with open(self.pointer) as fh:
            return fh.read().strip()

    def test_an_older_session_does_not_drag_the_pointer_back(self):
        """Several terminals, each holding the release it started with.

        Last writer winning is what sent the PATH link and the daemon back to
        an earlier version — and restarted the daemon — every time an old
        session answered.
        """
        newer = self.fake_plugin("99.0.0")
        self.point_at(newer)
        self.run_hook()
        self.assertEqual(self.read_pointer(), newer)

    def test_a_newer_session_takes_it(self):
        older = self.fake_plugin("0.0.1")
        self.point_at(older)
        self.run_hook()
        self.assertEqual(self.read_pointer(), os.path.join(ROOT, "scripts"))

    def test_a_pointer_with_no_manifest_is_replaced(self):
        """Deleted, or never a plugin: nothing there to defer to."""
        orphan = os.path.join(self.dir, "orphan", "scripts")
        os.makedirs(orphan)
        self.point_at(orphan)
        self.run_hook()
        self.assertEqual(self.read_pointer(), os.path.join(ROOT, "scripts"))

    def test_the_shell_agrees_the_pointer_is_usable(self):
        self.run_hook()
        out = subprocess.run(
            ["bash", "-c", '. %s\ncs_scripts_from_pointer "%s"' % (HEAL, self.pointer)],
            capture_output=True, text=True)
        self.assertEqual(out.stdout, os.path.join(ROOT, "scripts"))

    def test_a_pointer_at_nothing_is_refused(self):
        with open(self.pointer, "w") as fh:
            fh.write("/gone/0.6.2/scripts\n")
        out = subprocess.run(
            ["bash", "-c", '. %s\ncs_scripts_from_pointer "%s"; echo "rc=$?"'
             % (HEAL, self.pointer)], capture_output=True, text=True)
        self.assertIn("rc=1", out.stdout)


class OnePlaceForTheUnit(unittest.TestCase):
    """The unit text used to live in install.sh alone; the CLI needs it too."""

    def test_no_unit_heredoc_outside_csheal(self):
        for rel in ("bin/read-aloud", "scripts/install.sh"):
            with self.subTest(rel):
                with open(os.path.join(ROOT, rel)) as fh:
                    self.assertIsNone(re.search(r"^\[Unit\]", fh.read(), re.M),
                                      "%s writes its own unit again" % rel)


if __name__ == "__main__":
    unittest.main()
