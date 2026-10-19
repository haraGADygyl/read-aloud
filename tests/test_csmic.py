#!/usr/bin/env python3
"""Tests for scripts/csmic.py — the meeting guard's view of the microphone on
Windows — and for the hook's use of it.

    python3 -m unittest discover tests

Windows records, per app, when it last started and stopped using the
microphone; an app started and not stopped is on a call. The rows are fed in
by hand here, so this runs anywhere and never touches a microphone. The one
test that reads the real registry only reads it.
"""

import importlib.util
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import csmic  # noqa: E402

NOW = 1792435000.0


def ticks(seconds_ago):
    """A FILETIME for that many seconds before NOW."""
    return int((NOW - seconds_ago) * 1e7) + csmic._EPOCH_TICKS


def load_hook():
    spec = importlib.util.spec_from_file_location(
        "speak_hook", os.path.join(ROOT, "scripts", "speak-hook.py"))
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


class Recording(unittest.TestCase):

    def test_started_and_not_stopped_is_recording(self):
        rows = [("MSTeams_8wekyb3d8bbwe", True, ticks(60), 0)]
        self.assertEqual(csmic.recording(rows, NOW), ["MSTeams"])

    def test_a_desktop_app_is_named_by_its_exe(self):
        rows = [("C:#Program Files#Zoom#bin#Zoom.exe", False, ticks(60), 0)]
        self.assertEqual(csmic.recording(rows, NOW), ["Zoom.exe"])

    def test_stopped_is_not(self):
        rows = [("MSTeams_8wekyb3d8bbwe", True, ticks(600), ticks(60))]
        self.assertEqual(csmic.recording(rows, NOW), [])

    def test_never_started_is_not(self):
        self.assertEqual(csmic.recording([("x_1", True, 0, 0)], NOW), [])

    def test_a_crashed_app_does_not_hold_replies_forever(self):
        # It never wrote a stop time. A day later, it is not on a call.
        rows = [("C:#app#crashed.exe", False, ticks(24 * 3600), 0)]
        self.assertEqual(csmic.recording(rows, NOW), [])

    def test_a_long_call_still_counts(self):
        rows = [("C:#app#Zoom.exe", False, ticks(3 * 3600), 0)]
        self.assertEqual(csmic.recording(rows, NOW), ["Zoom.exe"])

    def test_each_app_once(self):
        rows = [("C:#a#Zoom.exe", False, ticks(5), 0),
                ("C:#b#Zoom.exe", False, ticks(9), 0)]
        self.assertEqual(csmic.recording(rows, NOW), ["Zoom.exe"])

    def test_nothing_off_windows(self):
        with mock.patch.object(csmic, "WINDOWS", False):
            self.assertEqual(csmic.windows_mic_users(), [])

    @unittest.skipUnless(os.name == "nt", "the registry is Windows-only")
    def test_the_real_registry_reads_without_error(self):
        users = csmic.windows_mic_users()
        self.assertIsInstance(users, list)
        rows = list(csmic._entries())
        self.assertTrue(all(len(r) == 4 for r in rows))


class TheHook(unittest.TestCase):
    """The hook holds a reply, silently, while anything is recording."""

    def setUp(self):
        self.hook = load_hook()
        patches = [mock.patch.object(csmic, "WINDOWS", True),
                   mock.patch.object(csmic, "windows_mic_users",
                                     lambda: ["Zoom.exe"])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_a_call_is_seen(self):
        self.assertTrue(self.hook.mic_in_use({"meetingGuard": True}))

    def test_the_guard_can_be_turned_off(self):
        self.assertFalse(self.hook.mic_in_use({"meetingGuard": False}))

    def test_an_ignored_app_is_not_a_call(self):
        # meetingGuardIgnore matches part of the name, ignoring case, as it
        # does for PulseAudio's application.name on Linux.
        self.assertFalse(self.hook.mic_in_use(
            {"meetingGuard": True, "meetingGuardIgnore": ["zoom"]}))

    def test_nothing_recording_is_no_call(self):
        with mock.patch.object(csmic, "windows_mic_users", lambda: []):
            self.assertFalse(self.hook.mic_in_use({"meetingGuard": True}))

    def test_windows_never_asks_pactl(self):
        with mock.patch.object(self.hook.shutil, "which",
                               side_effect=AssertionError("asked for pactl")):
            self.hook.mic_in_use({"meetingGuard": True})


if __name__ == "__main__":
    unittest.main()
