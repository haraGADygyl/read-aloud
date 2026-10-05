#!/usr/bin/env python3
"""Tests for .githooks/commit-msg — the Conventional Commits gate.

    python3 -m unittest discover tests

The hook is what stands between a typo and a rejected commit, so the cases it
must let through matter as much as the ones it must stop.
"""

import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".githooks", "commit-msg")

ACCEPTED = {
    "plain type": "feat: read any file aloud",
    "scoped": "fix(cleaner): keep the paragraph break",
    "breaking": "refactor!: drop the espeak fallback",
    "scoped breaking": "feat(cli)!: rename play to say",
    "release": "chore(release): 0.4.0",
    "scope with a space": "fix(text cleaner): shorten relative paths",
    "72 characters exactly": "feat: " + "x" * 66,
    "body": "fix: shorten relative paths\n\nThe rule matched from the first"
            " slash.\n\n- anchor to a token boundary\n",
    # Git writes these itself; rejecting them would break merge and rebase.
    "merge": "Merge branch 'main' into feature",
    "revert": 'Revert "feat: read any file aloud"',
    "fixup": "fixup! feat: read any file aloud",
    "squash": "squash! feat: read any file aloud",
    "comments stripped": "feat: add a thing\n# Please enter the commit"
                         " message\n# On branch main\n",
    "leading blank lines": "\n\nfeat: add a thing\n",
}

REJECTED = {
    "no type": "Read any file aloud with read-aloud read",
    "old repo style": "Release 0.4.0",
    "unknown type": "feature: read any file aloud",
    "no space after colon": "feat:read any file aloud",
    "no description": "feat:",
    "description is blank": "feat: ",
    "73 characters": "feat: " + "x" * 67,
    "body not separated": "feat: add a thing\nstraight into the body",
}


class CommitMsgHook(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def run_hook(self, message):
        path = os.path.join(self.dir, "COMMIT_EDITMSG")
        with open(path, "w") as fh:
            fh.write(message)
        return subprocess.run([HOOK, path], capture_output=True, text=True)

    def test_hook_is_executable(self):
        self.assertTrue(os.access(HOOK, os.X_OK),
                        "git silently ignores a commit-msg hook that is not +x")

    def test_accepted(self):
        for label, msg in ACCEPTED.items():
            with self.subTest(label):
                r = self.run_hook(msg)
                self.assertEqual(r.returncode, 0,
                                 "rejected %r\n%s" % (msg, r.stderr))

    def test_rejected(self):
        for label, msg in REJECTED.items():
            with self.subTest(label):
                self.assertEqual(self.run_hook(msg).returncode, 1,
                                 "accepted %r" % msg)

    def test_rejection_explains_itself(self):
        err = self.run_hook("Release 0.4.0").stderr
        self.assertIn("Release 0.4.0", err)        # shows what you wrote
        self.assertIn("feat:", err)                # and an example that works
        self.assertIn("--no-verify", err)          # and the escape hatch

    def test_empty_message_is_left_to_git(self):
        # git aborts an empty commit itself, with a better message than ours.
        self.assertEqual(self.run_hook("").returncode, 0)
        self.assertEqual(self.run_hook("\n# only comments\n").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
