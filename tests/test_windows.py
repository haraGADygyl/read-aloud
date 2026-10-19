#!/usr/bin/env python3
"""Tests for running on Windows, under Git Bash and from Command Prompt.

    python3 -m unittest discover tests

The shell helpers decide they are on Windows from `uname`, so a stub `uname`
(and a stub `cygpath`) on PATH runs the Windows branches of csheal.sh from
Linux and macOS: a change that breaks Windows fails everywhere. Nothing here
plays a sound, starts the daemon, or writes outside a temporary directory.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAL = os.path.join(ROOT, "scripts", "csheal.sh")
HOOK = os.path.join(ROOT, "scripts", "speak-hook.py")
CMD = os.path.join(ROOT, "bin", "read-aloud.cmd")
LAUNCHER = os.path.join(ROOT, "scripts", "speak-hook.sh")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

QUIET_CFG = '{"notify": false, "meetingGuard": false, "holdSound": "off"}'

# What cygpath -w does to the paths these tests use: /x/y -> W:\x\y. Close
# enough to tell "converted" from "not", which is all the helpers need.
CYGPATH = """#!/bin/sh
[ "$1" = "-w" ] && shift
printf 'W:%s\\n' "$1" | tr / '\\\\'
"""


class Stubbed(unittest.TestCase):
    """Stand-ins on PATH for what the shell side asks: uname decides the
    platform, so these run the Windows branches on Linux and the Linux ones
    on Windows alike."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.win = os.path.join(self.dir, "win")
        self.posix = os.path.join(self.dir, "posix")
        self.stub(self.win, "uname", "echo MINGW64_NT-10.0-26200\n")
        self.stub(self.win, "cygpath", CYGPATH)
        self.stub(self.posix, "uname", "echo Linux\n")

    def stub(self, where, name, body):
        os.makedirs(where, exist_ok=True)
        path = os.path.join(where, name)
        # "\n" whatever the platform: #!/bin/sh\r is not a shell.
        with open(path, "w", newline="\n") as fh:
            fh.write(body if body.startswith("#!") else "#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

    # Put from inside bash: Git Bash puts its own /usr/bin ahead of whatever
    # PATH it is handed, which would hide a stub uname behind the real one.
    FIRST = 'PATH="$(cygpath -u "$STUBS" 2>/dev/null || printf %s "$STUBS"):$PATH"\n'

    def env(self, windows):
        return dict(os.environ, STUBS=self.win if windows else self.posix)

    def sh(self, snippet, windows=True):
        return subprocess.run(
            [support.BASH, "-c",
             self.FIRST + ". %s\n%s" % (support.sh_path(HEAL), snippet)],
            capture_output=True, text=True, cwd=self.dir, env=self.env(windows))


class GitBash(Stubbed):

    def test_it_knows_where_it_is(self):
        self.assertEqual(self.sh("cs_windows && echo yes").stdout, "yes\n")
        self.assertEqual(self.sh("cs_windows || echo no", windows=False).stdout,
                         "no\n")

    def test_the_venv_python_moves(self):
        self.assertEqual(self.sh("cs_venv_py /d/venv").stdout,
                         "/d/venv/Scripts/python.exe")
        self.assertEqual(self.sh("cs_venv_py /d/venv", windows=False).stdout,
                         "/d/venv/bin/python")

    def test_the_pointer_is_written_the_way_the_hook_writes_it(self):
        # Python wrote C:\...\scripts; bash writing /c/.../scripts made the
        # hook rewrite it on every reply, and Python could not read it back.
        pointer = os.path.join(self.dir, "plugin-root")
        self.sh('cs_record_root "%s" /p/read-aloud/scripts' % pointer)
        with open(pointer) as fh:
            self.assertEqual(fh.read(), "W:\\p\\read-aloud\\scripts\n")

    def shim(self):
        return os.path.join(self.dir, "bin", "read-aloud.cmd")

    def sync(self, pointer="/d/plugin-root"):
        out = self.sh('cs_shim_sync "%s/bin" %s; echo "rc=$?"' % (self.dir, pointer))
        return out.stdout.strip()

    def test_the_shim_is_written_once(self):
        self.assertEqual(self.sync(), "rc=10")
        self.assertEqual(self.sync(), "rc=0")

    def test_the_shim_reads_the_pointer_and_names_no_release(self):
        self.sync()
        with open(self.shim(), "rb") as fh:
            text = fh.read()
        self.assertIn(b'<"W:\\d\\plugin-root"', text)
        self.assertIn(b'"%ROOT%\\..\\bin\\read-aloud.cmd" %*', text)
        self.assertNotRegex(text, rb"\d+\.\d+\.\d+")   # no version directory

    def test_the_shim_has_crlf_line_endings(self):
        self.sync()
        with open(self.shim(), "rb") as fh:
            lines = fh.read().split(b"\n")[:-1]
        self.assertTrue(lines and all(line.endswith(b"\r") for line in lines))

    def test_a_moved_data_directory_rewrites_the_shim(self):
        self.sync()
        self.assertEqual(self.sync("/e/plugin-root"), "rc=11")

    def test_somebody_elses_read_aloud_cmd_is_left_alone(self):
        os.makedirs(os.path.dirname(self.shim()))
        with open(self.shim(), "wb") as fh:
            fh.write(b"@echo mine\r\n")
        self.assertEqual(self.sync(), "rc=12")
        with open(self.shim(), "rb") as fh:
            self.assertEqual(fh.read(), b"@echo mine\r\n")


class PythonByAnyName(Stubbed):
    """The python.org installer gives Windows python and py, not python3, and
    python3 there can be the Microsoft Store's stand-in, which runs nothing.
    A hook that only knew python3 dropped every reply without a word."""

    def setUp(self):
        Stubbed.setUp(self)
        self.log = os.path.join(self.dir, "ran.log")
        self.probes = os.path.join(self.dir, "probes.log")
        self.data = os.path.join(self.dir, "data")
        os.makedirs(self.data)
        self.cache = os.path.join(self.data, "python")
        # The Store's stand-in: an advert and a failure, whatever it is asked.
        self.store = 'echo "Python was not found" >&2\nexit 9009\n'

    def works(self, name):
        """A python that answers the probe with its own path, as sys.executable
        does, and records the real run."""
        log, probes = support.sh_path(self.log), support.sh_path(self.probes)
        return ('[ "$1" = -c ] && { echo %s >> "%s"; echo "$0"; exit 0; }\n'
                'echo "%s $*" >> "%s"\ncat >> "%s"\n'
                % (name, probes, name, log, log))

    def give(self, windows, **pythons):
        where = self.win if windows else self.posix
        for name in ("python3", "python", "py"):
            self.stub(where, name, pythons.get(name, self.store))

    def launch(self, windows, data=None):
        env = dict(self.env(windows), READ_ALOUD_HOME=data or self.data)
        return subprocess.run(
            [support.BASH, "-c", self.FIRST + 'exec sh "$0"', support.sh_path(LAUNCHER)],
            input=b'{"payload": 1}', capture_output=True, env=env)

    def probed(self):
        try:
            with open(self.probes) as fh:
                return fh.read().split()
        except OSError:
            return []

    def ran(self):
        try:
            with open(self.log) as fh:
                return fh.read()
        except OSError:
            return ""

    def test_python_is_used_when_python3_is_the_store_stub(self):
        self.give(True, python=self.works("python"))
        self.assertEqual(self.launch(True).returncode, 0)
        self.assertRegex(self.ran(), r"^python -X utf8 \S*speak-hook\.py\n")

    def test_the_payload_reaches_the_hook_not_the_probe(self):
        self.give(True, python=self.works("python"))
        self.launch(True)
        self.assertTrue(self.ran().endswith('{"payload": 1}'), self.ran())

    def test_py_is_the_last_resort(self):
        self.give(True, py=self.works("py"))
        self.launch(True)
        self.assertTrue(self.ran().startswith("py -X utf8 "), self.ran())

    def test_python3_first_when_it_works(self):
        self.give(True, python3=self.works("python3"), python=self.works("python"))
        self.launch(True)
        self.assertTrue(self.ran().startswith("python3 "), self.ran())

    def test_no_python_is_as_quiet_as_a_failing_hook(self):
        self.give(True)
        out = self.launch(True)
        self.assertEqual((out.returncode, self.ran()), (0, ""))

    def test_linux_and_macos_just_run_python3(self):
        # No probe there: python3 is the name, and a probe would cost every
        # reply a second interpreter start.
        self.give(False, python3=self.works("python3"))
        self.launch(False)
        self.assertEqual(self.ran().splitlines()[0].split()[:3],
                         ["python3", "-X", "utf8"])

    def cs_python(self):
        return self.sh('cs_python "%s"; echo " rc=$?"' % self.data).stdout

    def test_the_cli_picks_the_same_one(self):
        self.give(True, python=self.works("python"))
        self.assertRegex(self.cs_python(), r"/python rc=0\n$")
        os.remove(self.cache)
        self.give(True)
        self.assertEqual(self.cs_python(), " rc=1\n")

    # python3 on Windows is often an alias that takes ~0.3 s to hand over to
    # the real python.exe; the hook and every CLI command paid it repeatedly.

    def test_the_interpreter_is_remembered(self):
        self.give(True, python=self.works("python"))
        self.launch(True)
        self.launch(True)
        self.assertEqual(self.probed(), ["python"])          # the first run only
        with open(self.cache) as fh:
            self.assertRegex(fh.read(), r"/python\n$")
        self.assertEqual(self.ran().count("python -X utf8 "), 2)

    def test_a_vanished_interpreter_is_looked_up_again(self):
        with open(self.cache, "w", newline="\n") as fh:
            fh.write(os.path.join(self.dir, "uninstalled", "python.exe") + "\n")
        self.give(True, python=self.works("python"))
        self.launch(True)
        self.assertEqual(self.probed(), ["python"])
        with open(self.cache) as fh:
            self.assertNotIn("uninstalled", fh.read())

    def test_the_hook_and_the_cli_share_it(self):
        self.give(True, python=self.works("python"))
        self.launch(True)
        self.assertRegex(self.cs_python(), r"/python rc=0\n$")
        self.assertEqual(self.probed(), ["python"])

    def test_no_data_directory_is_created_for_it(self):
        # csmigrate.py moves the claude-speak directory into place only if
        # nothing is there yet, so the cache waits for the hook to make it.
        fresh = os.path.join(self.dir, "fresh")
        self.give(True, python=self.works("python"))
        self.launch(True, data=fresh)
        self.assertFalse(os.path.exists(fresh))
        self.assertTrue(self.ran().startswith("python -X utf8 "), self.ran())

    def test_the_two_lists_agree(self):
        lists = []
        for path in (LAUNCHER, HEAL):
            with open(path, encoding="utf-8") as fh:
                lists += re.findall(r"for name in ([^;]+); do", fh.read())
        self.assertEqual(len(lists), 2)
        self.assertEqual(lists[0], lists[1])


class CommandPrompt(unittest.TestCase):

    def test_the_cmd_entry_point_is_ascii(self):
        # It switches the console to UTF-8 partway through, and cmd rereads
        # the rest of the file in the new code page.
        with open(CMD, "rb") as fh:
            data = fh.read()
        self.assertEqual(data, data.decode("ascii", "replace").encode("ascii"))

    def test_it_never_runs_plain_bash(self):
        # In Command Prompt "bash" is usually WSL's, which cannot reach
        # winsound or the Windows Python the hook uses.
        with open(CMD) as fh:
            code = [l.strip() for l in fh if not l.strip().lower().startswith("rem")]
        self.assertFalse([l for l in code if l.lower().startswith("bash ")])
        self.assertIn('"%GITBASH%" "%SELF%" %*', code)


@unittest.skipUnless(shutil.which("git") and os.path.isdir(os.path.join(ROOT, ".git")),
                     "needs git and a checkout")
class LineEndings(unittest.TestCase):
    """Claude Code installs the plugin with git, and Git for Windows defaults
    to core.autocrlf=true: without these, bash meets a \\r on every line."""

    def eol(self, path):
        out = subprocess.run(["git", "check-attr", "eol", "--", path], cwd=ROOT,
                             capture_output=True, text=True).stdout
        return out.rsplit(":", 1)[-1].strip()

    def test_shell_scripts_check_out_with_lf(self):
        for path in ("bin/read-aloud", "scripts/install.sh", "scripts/csheal.sh",
                     ".githooks/commit-msg"):
            with self.subTest(path):
                self.assertEqual(self.eol(path), "lf")

    def test_cmd_files_check_out_with_crlf(self):
        self.assertEqual(self.eol("bin/read-aloud.cmd"), "crlf")


class Utf8(unittest.TestCase):
    """Python on Windows reads pipes and writes files in the ANSI code page,
    so "Café — ok" reached the queue as "CafÃ© â€” ok"."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        cfg = os.path.join(self.dir, "config.json")
        with open(cfg, "w") as fh:
            fh.write(QUIET_CFG)
        self.env = dict(os.environ, READ_ALOUD_HOME=self.dir, READ_ALOUD_CONFIG=cfg)
        self.env.pop("PYTHONUTF8", None)

    def test_the_hook_is_run_in_utf8_mode(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            cmd = json.load(fh)["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, 'sh "${CLAUDE_PLUGIN_ROOT}/scripts/speak-hook.sh"')
        with open(LAUNCHER, encoding="utf-8") as fh:
            runs = [l.strip() for l in fh if l.strip().startswith("exec ")]
        self.assertTrue(runs and all("-X utf8" in l for l in runs), runs)

    def test_a_reply_keeps_its_accents_and_dashes(self):
        payload = {"session_id": "s", "cwd": os.path.join(self.dir, "proj"),
                   "last_assistant_message": "Café — ok."}
        # As hooks.json runs it, fed bytes the way Claude Code sends them.
        subprocess.run([sys.executable, "-X", "utf8", HOOK],
                       input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       capture_output=True, env=self.env)
        with open(os.path.join(self.dir, "held.jsonl"), encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.readline())["text"], "Café — ok.")

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_home_follows_home_like_the_shell_does(self):
        # Python on Windows reads USERPROFILE; the shell scripts read $HOME.
        # Disagreeing split the queue in two — and let a test that redirects
        # HOME write into the real profile.
        home = os.path.join(self.dir, "elsewhere")
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import cspaths; "
             "print(cspaths.DATA)", os.path.join(ROOT, "scripts")],
            capture_output=True, text=True,
            env=dict(os.environ, HOME=home, READ_ALOUD_HOME="",
                     CLAUDE_SPEAK_HOME="", XDG_DATA_HOME=""))
        self.assertEqual(out.stdout.strip(),
                         os.path.join(home, ".local", "share", "read-aloud"))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_a_posix_home_is_not_trusted(self):
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import cspaths; "
             "print(cspaths.HOME)", os.path.join(ROOT, "scripts")],
            capture_output=True, text=True, env=dict(os.environ, HOME="/c/Users/x"))
        self.assertEqual(out.stdout.strip(), os.path.expanduser("~"))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_everything_started_from_the_scripts_inherits_it(self):
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import cspaths, os; "
             "print(os.environ.get('PYTHONUTF8'))", os.path.join(ROOT, "scripts")],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(out.stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
