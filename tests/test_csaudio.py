#!/usr/bin/env python3
"""Tests for scripts/csaudio.py — picking and feeding an audio player, and
writing the same samples to a file instead.

    python3 -m unittest discover tests

Nothing here plays a sound. `which` is stubbed so a machine's real players
cannot decide the outcome, which is also how the macOS path gets exercised
from Linux: afplay only plays files, so it needs the wav route rather than a
pipe, and that route has to be testable without a Mac.
"""

import os
import shutil
import sys
import tempfile
import unittest
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import csaudio  # noqa: E402

RATE = 24000
PCM = b"\x00\x01" * 1200          # 1200 frames of s16le mono


class FakeWhich:
    """Stands in for shutil.which: only the named tools exist."""

    def __init__(self, *present):
        self.present = set(present)

    def __call__(self, name):
        return "/usr/bin/" + name if name in self.present else None


class PlayerSelection(unittest.TestCase):

    def setUp(self):
        self.real_which = csaudio.shutil.which

    def tearDown(self):
        csaudio.shutil.which = self.real_which

    def only(self, *tools):
        csaudio.shutil.which = FakeWhich(*tools)

    def test_pipewire_preferred(self):
        self.only("paplay", "aplay", "ffplay")
        self.assertEqual(csaudio.stream_cmd(RATE)[0], "paplay")

    def test_falls_down_the_list(self):
        for tools, want in [(("aplay", "ffplay"), "aplay"),
                            (("ffplay",), "ffplay"),
                            (("sox",), "play")]:      # sox installs as `play`
            with self.subTest(tools):
                self.only(*tools)
                self.assertEqual(csaudio.stream_cmd(RATE)[0], want)

    def test_rate_reaches_the_command(self):
        self.only("aplay")
        self.assertIn(str(RATE), csaudio.stream_cmd(RATE))

    def test_macos(self):
        # A stock Mac: afplay and nothing else. There is no pipe player, so
        # the caller has to take the file route.
        self.only("afplay")
        self.assertIsNone(csaudio.stream_cmd(RATE))
        self.assertEqual(csaudio.file_player(), "afplay")
        self.assertTrue(csaudio.available())

    def test_afplay_never_used_as_a_pipe(self):
        # It cannot read stdin. Treating it as a streaming player would look
        # like it worked and produce silence.
        self.only("afplay")
        self.assertIsNone(csaudio.stream_cmd(RATE))

    def test_ffplay_names_no_channel_count(self):
        # ffplay 7 removed -ac — the player exits with "Option not found",
        # the daemon reads the broken pipe as the reply being cancelled, and
        # the result is pure silence. Older ffplay lacks -ch_layout, the
        # replacement. The raw-pcm demuxer defaults to mono everywhere, so
        # the command must name no channel count at all.
        self.only("ffplay")
        cmd = csaudio.stream_cmd(RATE)
        self.assertNotIn("-ac", cmd)
        self.assertNotIn("-ch_layout", cmd)

    def test_bare_machine(self):
        self.only()
        self.assertIsNone(csaudio.stream_cmd(RATE))
        self.assertIsNone(csaudio.file_player())
        self.assertFalse(csaudio.available())

    def test_bare_machine_raises_rather_than_going_quiet(self):
        self.only()
        with self.assertRaises(RuntimeError):
            csaudio.play_blocking(PCM, RATE)


class WavContainer(unittest.TestCase):

    def test_header_matches_the_samples(self):
        path = csaudio.write_temp_wav(PCM, RATE)
        try:
            with wave.open(path) as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getsampwidth(), 2)
                self.assertEqual(w.getframerate(), RATE)
                self.assertEqual(w.getnframes(), len(PCM) // 2)
                self.assertEqual(w.readframes(w.getnframes()), PCM)
        finally:
            os.unlink(path)

    def test_bytes_and_file_agree(self):
        path = csaudio.write_temp_wav(PCM, RATE)
        try:
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), csaudio.wav_bytes(PCM, RATE))
        finally:
            os.unlink(path)

    def test_temp_file_is_a_wav_on_disk(self):
        path = csaudio.write_temp_wav(PCM, RATE)
        try:
            self.assertTrue(path.endswith(".wav"))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(4), b"RIFF")
        finally:
            os.unlink(path)


class EncoderSelection(unittest.TestCase):
    """`read-aloud save` — which encoder, and how it is fed."""

    def setUp(self):
        self.real_which = csaudio.shutil.which

    def tearDown(self):
        csaudio.shutil.which = self.real_which

    def only(self, *tools):
        csaudio.shutil.which = FakeWhich(*tools)

    def test_ffmpeg_is_told_the_input_is_raw_mono(self):
        # It has no header to read: get the rate or the channel count wrong
        # and the file is chipmunks, not an error.
        self.only("ffmpeg")
        cmd = csaudio.encoder_cmd("talk.mp3", RATE)
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("s16le", cmd)
        self.assertIn(str(RATE), cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")
        self.assertEqual(cmd[-1], "talk.mp3")

    def test_mp3_names_its_codec(self):
        self.only("ffmpeg")
        self.assertIn("libmp3lame", csaudio.encoder_cmd("talk.mp3", RATE))

    def test_other_containers_are_left_to_ffmpeg(self):
        # -codec:a libmp3lame in an .m4a is an error; the extension already
        # says what to write.
        self.only("ffmpeg")
        self.assertNotIn("libmp3lame", csaudio.encoder_cmd("talk.m4a", RATE))

    def test_lame_only_gets_mp3(self):
        self.only("lame")
        self.assertEqual(csaudio.encoder_cmd("talk.mp3", RATE)[0], "lame")
        self.assertIsNone(csaudio.encoder_cmd("talk.m4a", RATE))

    def test_lame_is_told_the_byte_order(self):
        # Raw input defaults to big-endian on some builds, which is static.
        self.only("lame")
        cmd = csaudio.encoder_cmd("talk.mp3", RATE)
        self.assertIn("--little-endian", cmd)
        self.assertIn("--signed", cmd)
        self.assertEqual(cmd[cmd.index("-s") + 1], "24")   # kHz, not Hz

    def test_bare_machine_has_no_encoder(self):
        self.only()
        self.assertIsNone(csaudio.encoder_cmd("talk.mp3", RATE))
        self.assertFalse(csaudio.encoder_available())

    def test_recorder_refuses_rather_than_writing_nothing(self):
        self.only()
        with self.assertRaises(RuntimeError):
            csaudio.Recorder("talk.mp3", RATE)


class RecordingToWav(unittest.TestCase):
    """The wav route needs no encoder, so it is the one that can be tested
    on any machine — and it is what `-o name.wav` gives a machine without
    ffmpeg."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cs-save-")
        self.path = os.path.join(self.dir, "talk.wav")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_chunks_are_appended_in_order(self):
        with csaudio.Recorder(self.path, RATE) as rec:
            rec.write(b"\x01\x00" * 10)
            rec.write(b"\x02\x00" * 10)
        with wave.open(self.path) as w:
            self.assertEqual(w.getnframes(), 20)
            self.assertEqual(w.readframes(20), b"\x01\x00" * 10 + b"\x02\x00" * 10)

    def test_seconds_counts_what_was_written(self):
        rec = csaudio.Recorder(self.path, RATE)
        rec.write(b"\x00\x00" * RATE)
        self.assertAlmostEqual(rec.seconds, 1.0)
        rec.close()

    def test_abort_leaves_no_half_file(self):
        # Ctrl-C during a long document must not leave something that looks
        # like a finished recording.
        rec = csaudio.Recorder(self.path, RATE)
        rec.write(PCM)
        rec.abort()
        self.assertFalse(os.path.exists(self.path))

    def test_abort_is_safe_twice(self):
        rec = csaudio.Recorder(self.path, RATE)
        rec.abort()
        rec.abort()


class OutputNaming(unittest.TestCase):

    def test_no_destination_writes_beside_you(self):
        self.assertEqual(csaudio.audio_path("docs/plan.md"), "plan.mp3")

    def test_a_directory_is_filled(self):
        self.assertEqual(csaudio.audio_path("docs/plan.md", "audio"),
                         os.path.join("audio", "plan.mp3"))
        self.assertEqual(csaudio.audio_path("docs/plan.md", "audio/"),
                         os.path.join("audio", "plan.mp3"))

    def test_a_file_name_is_taken_literally(self):
        # Including the extension: that is how wav is asked for.
        self.assertEqual(csaudio.audio_path("docs/plan.md", "drive.wav"), "drive.wav")

    def test_extension_follows_the_caller(self):
        self.assertEqual(csaudio.audio_path("docs/plan.md", "audio", ".wav"),
                         os.path.join("audio", "plan.wav"))

    def test_two_readmes_do_not_become_one_file(self):
        taken = {"README.mp3"}
        self.assertEqual(csaudio.unique_path("README.mp3", taken), "README-2.mp3")
        taken.add("README-2.mp3")
        self.assertEqual(csaudio.unique_path("README.mp3", taken), "README-3.mp3")

    def test_a_free_name_is_left_alone(self):
        self.assertEqual(csaudio.unique_path("plan.mp3", set()), "plan.mp3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
