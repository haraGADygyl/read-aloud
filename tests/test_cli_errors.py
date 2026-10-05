#!/usr/bin/env python3
"""Tests for what bin/read-aloud says when you get the command wrong.

    python3 -m unittest discover tests

A mistyped command used to reach bash's own ${2:?} error —

    ./bin/read-aloud: line 183: 2: usage: read-aloud same queue|interrupt

— which names a file and a line number the reader does not have open, buries
the usage behind them, and exits 1 where every other bad argument exits 2.

Every run here redirects HOME, XDG_RUNTIME_DIR and both READ_ALOUD_ vars at a
temporary directory: the CLI repairs the PATH link and the systemd unit on
every invocation, and the socket it reaches would be the real daemon.
"""

import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin", "read-aloud")


class CLI(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def run_cli(self, *args):
        env = dict(os.environ,
                   HOME=self.dir, XDG_RUNTIME_DIR=self.dir,
                   READ_ALOUD_HOME=self.dir,
                   READ_ALOUD_CONFIG=os.path.join(self.dir, "config.json"))
        return subprocess.run([BIN] + list(args), capture_output=True,
                              text=True, env=env, cwd=self.dir)


class MissingArgument(CLI):
    """A command that needs a value and did not get one."""

    CASES = [("voice", "voice af_bella"),
             ("speed", "speed 1.2"),
             ("max", "max 4000"),
             ("mode", "mode queue|interrupt|drop"),
             ("same", "same queue|interrupt"),
             ("read", "read <file>")]

    def test_the_usage_line_is_the_whole_message(self):
        for cmd, expect in self.CASES:
            with self.subTest(cmd=cmd):
                r = self.run_cli(cmd)
                self.assertTrue(r.stdout.startswith("usage: read-aloud " + expect),
                                "got: %r" % (r.stdout or r.stderr,))

    def test_no_script_path_or_line_number_leaks(self):
        for cmd, _ in self.CASES:
            with self.subTest(cmd=cmd):
                out = self.run_cli(cmd).stdout + self.run_cli(cmd).stderr
                self.assertNotIn("read-aloud: line", out)
                self.assertNotRegex(out, r"^\S*/bin/read-aloud")

    def test_it_exits_two_like_every_other_bad_argument(self):
        for cmd, _ in self.CASES:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.run_cli(cmd).returncode, 2)

    def test_a_value_that_is_not_one_of_the_choices_is_refused(self):
        r = self.run_cli("same", "sometimes")
        self.assertEqual(r.returncode, 2)
        self.assertIn("queue", r.stdout)
        # ...and the setting is left as it was, not written as "sometimes".
        with open(os.path.join(self.dir, "config.json")) as fh:
            self.assertNotIn("sometimes", fh.read())


class UnknownCommand(CLI):
    def test_it_names_the_command_and_exits_two(self):
        r = self.run_cli("nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn('no such command "nope"', r.stdout)

    def test_a_typo_gets_a_suggestion(self):
        for typo, want in [("spedd", "speed"), ("statsu", "status"),
                           ("instal", "install"), ("audtion", "audition")]:
            with self.subTest(typo=typo):
                self.assertIn("did you mean: read-aloud " + want,
                              self.run_cli(typo).stdout)

    def test_nonsense_gets_no_suggestion(self):
        # A confident wrong guess reads worse than no guess at all.
        self.assertNotIn("did you mean", self.run_cli("xyzzy").stdout)

    def test_the_message_goes_to_stdout(self):
        # /read-aloud:speak reports what the binary printed; on stderr only,
        # the user would see an empty reply.
        r = self.run_cli("nope")
        self.assertIn("no such command", r.stdout)
        self.assertEqual(r.stderr.strip(), "")

    def test_every_command_is_offered(self):
        listed = self.listed_commands()
        for cmd in ("install", "on", "off", "stop", "again", "status", "speed",
                    "voice", "voices", "max", "mode", "same", "queue", "hold",
                    "guard", "sound", "pending", "play", "clear", "read",
                    "save", "test", "audition", "restart", "log"):
            self.assertIn(cmd, listed)

    def listed_commands(self):
        """The names under the "commands:" heading, however they are wrapped."""
        out, collecting = [], False
        for line in self.run_cli("nope").stdout.splitlines():
            if line.startswith("commands:"):
                collecting = True
            elif collecting and line.startswith("  "):
                out += [w.strip(" ,") for w in line.split() if w.strip(" ,")]
            elif collecting and not line.strip():
                break
        self.assertTrue(out, "no commands listed in the unknown-command message")
        return out

    def test_the_list_is_wrapped_to_readable_lines(self):
        # Unwrapped, the terminal breaks it wherever it runs out — "read r"
        # on one line and "estart" on the next.
        body = self.run_cli("nope").stdout.splitlines()
        rows = [ln for ln in body if ln.startswith("  ")]
        self.assertTrue(rows)
        for row in rows:
            self.assertLessEqual(len(row), 80)
            self.assertNotEqual(row, row.rstrip() + " ")
        self.assertIn(", ", "\n".join(rows), "names are not separated")


class NoHandKeptList(CLI):
    """The list of commands is read back out of the case statement, because a
    list kept by hand is a list that goes stale."""

    def branches(self):
        with open(BIN) as fh:
            src = fh.read()
        body = src.split('case "${1:-status}" in', 1)[1].split("\nesac", 1)[0]
        out = set()
        for pattern in re.findall(r"^  ([a-z|\"*-]+)\)", body, re.MULTILINE):
            out.update(p for p in pattern.split("|")
                       if p and not p.startswith(("-", '"')) and p != "*")
        return out

    def test_the_offered_list_is_exactly_the_branches(self):
        listed = set(UnknownCommand.listed_commands(self))
        self.assertEqual(listed, self.branches())

    def test_every_command_is_in_the_help_text(self):
        # The header comment IS --help, so a branch missing from it is a
        # command nobody can discover. `help` is the documented exception.
        text = self.run_cli("--help").stdout
        for cmd in sorted(self.branches() - {"help"}):
            with self.subTest(cmd=cmd):
                self.assertRegex(text, r"\b%s\b" % re.escape(cmd))


if __name__ == "__main__":
    unittest.main()
