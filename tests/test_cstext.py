#!/usr/bin/env python3
"""Tests for scripts/cstext.py — the markdown-to-speech cleaner.

    python3 -m unittest discover tests        # or: python3 tests/test_cstext.py

Standard library only, and no audio: nothing here reaches the daemon. The CLI
tests cover only the paths that fail before anything would be spoken.
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

CLI = os.path.join(ROOT, "scripts", "cstext.py")
BIN = os.path.join(ROOT, "bin", "read-aloud")


def cfg(**over):
    out = dict(cstext.DEFAULTS)
    out.update(over)
    return out


class ShortenPaths(unittest.TestCase):
    """A path should be read as "parser.py line 42"; a word pair should not."""

    def shorten(self, text):
        return cstext.PATH.sub(cstext._shorten_path, text)

    def test_rooted(self):
        for src, want in [
            ("/a/b/c/file.py:42", "file.py line 42"),
            ("/usr/bin/ls", "ls"),                    # no extension, still a path
            ("~/proj/x/notes.md", "notes.md"),
            ("~/notes.md", "notes.md"),
            ("./src/a/foo.py", "foo.py"),
            ("../../pkg/mod.go:9", "mod.go line 9"),
        ]:
            with self.subTest(src=src):
                self.assertEqual(self.shorten(src), want)

    def test_relative(self):
        # The reason this file exists: the match used to start at the first
        # slash, so src/api/parser.py came out as "srcparser.py".
        for src, want in [
            ("src/api/parser.py:42", "parser.py line 42"),
            ("scripts/say.py:116", "say.py line 116"),
            ("hooks/hooks.json", "hooks.json"),
            ("a/b/.env", ".env"),
            ("src/x/y.jsonl:77", "y.jsonl line 77"),
        ]:
            with self.subTest(src=src):
                self.assertEqual(self.shorten(src), want)

    def test_in_a_sentence(self):
        for src, want in [
            ("see src/a/b/foo.py:12 now", "see foo.py line 12 now"),
            ("(src/a/b.py:3)", "(b.py line 3)"),
            ("file src/x/y.py:77.", "file y.py line 77."),
            ("edit tests/t.py:1 and lib/y.ts:2",
             "edit t.py line 1 and y.ts line 2"),
        ]:
            with self.subTest(src=src):
                self.assertEqual(self.shorten(src), want)

    def test_slashed_words_are_not_paths(self):
        # Shortening these would say "or", "IP", "she", "write" and "7".
        for src in ["and/or", "TCP/IP", "he/she", "read/write access", "24/7",
                    "N/A", "w/o", "10/20/2024", "1.5/2.5", "50/50.5",
                    "input/output ratio", "client/server split", "foo.py",
                    "a / b"]:
            with self.subTest(src=src):
                self.assertEqual(self.shorten(src), src)

    def test_disabled(self):
        self.assertIn("src/api/parser.py",
                      cstext.clean("src/api/parser.py", cfg(shortenPaths=False)))


class Clean(unittest.TestCase):

    def test_code_block_announced(self):
        out = cstext.clean("Before\n\n```py\nprint(1)\n```\n\nAfter", cfg())
        self.assertEqual(out, "Before. Code block. After")

    def test_code_block_silent(self):
        out = cstext.clean("Before\n\n```py\nprint(1)\n```\n\nAfter",
                           cfg(announceCode=False))
        self.assertNotIn("Code block", out)
        self.assertNotIn("print", out)

    def test_unterminated_fence(self):
        out = cstext.clean("Here:\n\n```py\nprint(1)", cfg())
        self.assertNotIn("print", out)

    def test_links_keep_their_label(self):
        self.assertEqual(cstext.clean("See [the docs](https://x.dev).", cfg()),
                         "See the docs.")

    def test_bare_url(self):
        self.assertEqual(cstext.clean("Go to https://x.dev/a now", cfg()),
                         "Go to link now")

    def test_inline_code_and_emphasis(self):
        self.assertEqual(
            cstext.clean("The `flag` is **on** and *ready*", cfg()),
            "The flag is on and ready")

    def test_headings_quotes_rules(self):
        self.assertEqual(cstext.clean("## Title\n\n> quoted\n\n---\n\nBody", cfg()),
                         "Title. quoted. Body")

    def test_lists_become_sentences(self):
        self.assertEqual(cstext.clean("- one\n- two", cfg()), "one. two")
        self.assertEqual(cstext.clean("1. one\n2. two", cfg()), "one. two")

    def test_paragraph_break_survives_a_line_rule(self):
        # Each of these used to eat the blank line above it, running the
        # previous sentence into the next one.
        for src, want in [
            ("Done.\n\n# Next\n\nBody", "Done. Next. Body"),
            ("Done.\n\n> quoted", "Done. quoted"),
            ("Done.\n\n- one", "Done. one"),
            ("Done.\n\n1. one", "Done. one"),
            ("Done.\n\n| a | b |\n", "Done."),
        ]:
            with self.subTest(src=src):
                self.assertEqual(cstext.clean(src, cfg()), want)

    def test_colon_before_a_list(self):
        self.assertEqual(cstext.clean("The plan:\n\n- ship it", cfg()),
                         "The plan: ship it")

    def test_long_horizontal_rule(self):
        self.assertEqual(cstext.clean("a\n\n-----\n\nb", cfg()), "a. b")
        self.assertEqual(cstext.clean("a\n\n***\n\nb", cfg()), "a. b")

    def test_table_rows_dropped(self):
        out = cstext.clean("Results:\n\n| a | b |\n| - | - |\n| 1 | 2 |\n", cfg())
        self.assertEqual(out, "Results:")

    def test_emoji_stripped(self):
        self.assertEqual(cstext.clean("Done \U0001f389 now", cfg()), "Done now")

    def test_whitespace_collapses(self):
        self.assertEqual(cstext.clean("a   b\n\n\nc", cfg()), "a b. c")

    def test_empty(self):
        self.assertEqual(cstext.clean("", cfg()), "")
        self.assertEqual(cstext.clean("\n\n   \n", cfg()), "")


class SplitChunks(unittest.TestCase):
    """Chunks are played in the order they are emitted, so order is the point.

    A piece emitted out of turn is heard out of turn: a sentence goes quiet,
    the next one plays, then the quiet one turns up late.
    """

    LIMIT = cstext.CHUNK_CHARS

    def assertInOrder(self, text):
        """No character lost, duplicated or moved.

        Compared without whitespace: a word too long to break has to be cut
        mid-token, and those pieces cannot be rejoined with a space. Spacing
        between words is checked separately, below.
        """
        chunks = cstext.split_chunks(text)
        squash = lambda s: "".join(s.split())        # noqa: E731
        self.assertEqual(squash("".join(chunks)), squash(text),
                         "chunks do not reassemble into the source text")
        for c in chunks:
            self.assertLessEqual(len(c), self.LIMIT,
                                 "chunk over the %d-char limit" % self.LIMIT)
        return chunks

    def test_word_spacing_survives(self):
        text = "One. Two. " + "a long stretch of words " * 30 + "end."
        self.assertEqual(" ".join(" ".join(cstext.split_chunks(text)).split()),
                         " ".join(text.split()),
                         "a space between words was lost or added")

    def test_short_sentence_before_a_long_one(self):
        # The regression: the long sentence's first slice used to be emitted
        # before the short sentence still sitting in the buffer.
        short = "We derive a fingerprint from the approved set."
        long_ = "That covers palette and contrast, " + "and more detail " * 20 + "yes."
        chunks = self.assertInOrder(short + " " + long_)
        self.assertTrue(chunks[0].startswith("We derive"),
                        "the long sentence jumped ahead: %r" % chunks[0][:60])

    def test_many_shorts_then_a_long_one(self):
        text = ("One. Two. Three. " + "A long stretch of words " * 30 + "end.")
        self.assertInOrder(text)

    def test_alternating(self):
        long_ = "Long one " * 40 + "."
        self.assertInOrder("Start. %s Middle. %s End." % (long_, long_))

    def test_no_space_to_break_on(self):
        # rfind returns -1 here, and -1 is truthy — `or limit` never fired, so
        # the old code cut at part[:-1] and ignored the limit entirely.
        self.assertInOrder("Here it is: " + "x" * 600 + " done.")

    def test_ordinary_prose_is_untouched(self):
        self.assertEqual(cstext.split_chunks("One. Two. Three."),
                         ["One. Two. Three."])

    def test_empty(self):
        self.assertEqual(cstext.split_chunks(""), [])
        self.assertEqual(cstext.split_chunks("   \n  "), [])

    def test_a_whole_reply(self):
        with open(os.path.join(ROOT, "README.md")) as fh:
            self.assertInOrder(cstext.clean(fh.read(), cfg()))


class ReadSource(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_text_file(self):
        path = self.write("a.md", b"# Hi\n")
        self.assertEqual(cstext.read_source(path), "# Hi\n")

    def test_binary_refused(self):
        path = self.write("a.bin", b"MZ\x00\x01binary")
        with self.assertRaises(ValueError):
            cstext.read_source(path)

    def test_missing_file(self):
        with self.assertRaises(OSError):
            cstext.read_source(os.path.join(self.dir, "nope"))

    def test_invalid_utf8_survives(self):
        path = self.write("a.txt", b"caf\xe9 au lait")     # latin-1, not utf-8
        self.assertIn("au lait", cstext.read_source(path))


class LoadConfig(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.orig = cstext.cspaths.CONFIG

    def tearDown(self):
        cstext.cspaths.CONFIG = self.orig

    def point_at(self, contents):
        path = os.path.join(self.dir, "config.json")
        if contents is not None:
            with open(path, "w") as fh:
                fh.write(contents)
        cstext.cspaths.CONFIG = path

    def test_defaults_when_absent(self):
        self.point_at(None)
        self.assertEqual(cstext.load_config(), cstext.DEFAULTS)

    def test_user_values_win_and_gaps_fill(self):
        self.point_at('{"voice": "bm_george", "speed": 1.4}')
        got = cstext.load_config()
        self.assertEqual(got["voice"], "bm_george")
        self.assertEqual(got["speed"], 1.4)
        self.assertEqual(got["maxChars"], cstext.DEFAULTS["maxChars"])

    def test_corrupt_config_falls_back(self):
        self.point_at("{not json")
        self.assertEqual(cstext.load_config(), cstext.DEFAULTS)


class FilterCLI(unittest.TestCase):
    """scripts/cstext.py as the filter `read-aloud read` shells out to."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # An isolated config, so a developer's own settings can't sway a test.
        self.env = dict(os.environ,
                        READ_ALOUD_HOME=self.dir,
                        READ_ALOUD_CONFIG=os.path.join(self.dir, "config.json"))

    def run_cli(self, *args, **kw):
        return subprocess.run([sys.executable, CLI] + list(args),
                              capture_output=True, text=True, env=self.env, **kw)

    def test_file(self):
        path = os.path.join(self.dir, "a.md")
        with open(path, "w") as fh:
            fh.write("# Title\n\nSee `src/a/b.py:7`.\n")
        r = self.run_cli(path)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "Title. See b.py line 7.")

    def test_stdin(self):
        r = self.run_cli("-", input="**bold** and/or plain\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "bold and/or plain")

    def test_binary_refused(self):
        path = os.path.join(self.dir, "a.bin")
        with open(path, "wb") as fh:
            fh.write(b"\x00\x01\x02")
        r = self.run_cli(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("binary", r.stderr)

    def test_usage(self):
        self.assertEqual(self.run_cli().returncode, 2)


@unittest.skipUnless(os.path.exists("/bin/bash"), "needs bash")
class ReadCommand(unittest.TestCase):
    """`read-aloud read` — only the paths that stop before speaking."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = dict(os.environ,
                        READ_ALOUD_HOME=self.dir,
                        READ_ALOUD_CONFIG=os.path.join(self.dir, "config.json"))

    def read(self, arg):
        return subprocess.run(["/bin/bash", BIN, "read", arg],
                              capture_output=True, text=True, env=self.env)

    def test_missing_file(self):
        r = self.read(os.path.join(self.dir, "nope.md"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such file", r.stderr)

    def test_directory(self):
        r = self.read(self.dir)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a readable file", r.stderr)

    def test_binary(self):
        path = os.path.join(self.dir, "a.bin")
        with open(path, "wb") as fh:
            fh.write(b"\x00\x01\x02")
        self.assertEqual(self.read(path).returncode, 1)

    def test_empty_file_says_so(self):
        path = os.path.join(self.dir, "empty.md")
        open(path, "w").close()
        r = self.read(path)
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to read", r.stdout)

    def test_at_prefixed_path(self):
        # Claude Code's @-completion hands us "@docs/x.md". Empty on purpose:
        # reaching "nothing to read" proves the @ was stripped and the file
        # found, without synthesizing anything.
        path = os.path.join(self.dir, "notes.md")
        open(path, "w").close()
        r = self.read("@" + path)
        self.assertEqual(r.returncode, 0, "the @ prefix was not stripped")
        self.assertIn("nothing to read", r.stdout)

    def test_at_prefix_kept_when_that_file_is_real(self):
        # A file genuinely named "@weird.md" wins over the stripped form.
        # Stripping here would hit the directory and fail with exit 2.
        open(os.path.join(self.dir, "@weird.md"), "w").close()
        os.mkdir(os.path.join(self.dir, "weird.md"))
        r = self.read(os.path.join(self.dir, "@weird.md"))
        self.assertEqual(r.returncode, 0, "stripped an @ that was part of the name")
        self.assertIn("nothing to read", r.stdout)


class SynthRetry(unittest.TestCase):
    """Kokoro refuses a chunk over 510 phonemes, which characters cannot
    predict. Both callers used to drop it, losing a sentence out of the middle
    of a reply with nothing audible marking the gap."""

    def test_a_chunk_that_works_is_synthesized_once(self):
        calls = []
        out = cstext.synth_chunks(lambda c: calls.append(c) or ("pcm", 24000),
                                  "hello there")
        self.assertEqual(calls, ["hello there"])
        self.assertEqual(out, [("pcm", 24000)])

    def test_a_refused_chunk_is_halved_rather_than_dropped(self):
        seen = []

        def create(piece):
            seen.append(piece)
            if len(piece) > 12:
                raise IndexError("index 510 is out of bounds")
            return piece

        out = cstext.synth_chunks(create, "one two three four five six")
        self.assertEqual(" ".join(out), "one two three four five six")

    def test_the_pieces_come_back_in_order(self):
        # A piece returned out of turn is *heard* out of turn.
        def create(piece):
            if " " in piece:
                raise IndexError("too long")
            return piece

        self.assertEqual(cstext.synth_chunks(create, "alpha beta gamma delta"),
                         ["alpha", "beta", "gamma", "delta"])

    def test_it_gives_up_instead_of_halving_forever(self):
        tries = []

        def create(piece):
            tries.append(piece)
            raise RuntimeError("no")

        dropped = []
        out = cstext.synth_chunks(create, "a b c d e f g h",
                                  lambda piece, exc: dropped.append(piece))
        self.assertEqual(out, [])
        self.assertLessEqual(len(tries), 2 ** (cstext.MAX_SPLITS + 1))
        self.assertTrue(dropped)

    def test_a_single_unspeakable_word_is_reported_once(self):
        dropped = []

        def create(piece):
            raise RuntimeError("no")

        cstext.synth_chunks(create, "supercalifragilistic",
                            lambda piece, exc: dropped.append(piece))
        self.assertEqual(dropped, ["supercalifragilistic"])

    def test_halving_breaks_on_a_space(self):
        left, right = cstext.split_in_two("one two three four")
        self.assertEqual((left, right), ("one two", "three four"))
        self.assertNotIn("  ", left + " " + right)

    def test_halving_a_word_still_returns_two_pieces(self):
        left, right = cstext.split_in_two("abcdefgh")
        self.assertEqual(left + right, "abcdefgh")
        self.assertTrue(left and right)


if __name__ == "__main__":
    unittest.main(verbosity=2)
