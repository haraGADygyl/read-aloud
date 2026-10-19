#!/usr/bin/env python3
"""Which apps are recording from the microphone — the meeting guard on Windows.

    csmic.py        print them, comma-separated; nothing when none are

Windows keeps a record per app of when it last started and stopped using the
microphone, under the user's CapabilityAccessManager consent store. It is what
lights the microphone icon in the taskbar. An app that has started and not yet
stopped is recording now.

Linux asks PulseAudio/PipeWire instead (pactl, in speak-hook.py and
bin/read-aloud), and macOS has nothing to ask. Standard library only: this runs
under the system python3, in the Stop hook.
"""

import os
import sys
import time

WINDOWS = os.name == "nt"

CONSENT = (r"Software\Microsoft\Windows\CurrentVersion"
           r"\CapabilityAccessManager\ConsentStore\microphone")

# An app that crashes mid-call never writes its stop time, and would hold every
# reply from then on. No call runs this long; a start older than this is stale.
STALE_SECONDS = 12 * 3600

# FILETIME counts 100 ns ticks from 1601; the Unix epoch is this many ticks in.
_EPOCH_TICKS = 116444736000000000


def _name(key, packaged):
    """What to call an app: Zoom.exe, not C:#Program Files#Zoom#bin#Zoom.exe."""
    if packaged:
        return key.split("_")[0]            # MSTeams_8wekyb3d8bbwe -> MSTeams
    return key.split("#")[-1]


def recording(entries, now=None):
    """Names of the apps recording now, from (key, packaged, start, stop) rows.

    Kept apart from the registry so it can be tested anywhere.
    """
    now = time.time() if now is None else now
    names = []
    for key, packaged, start, stop in entries:
        if not start or stop:
            continue
        started = (start - _EPOCH_TICKS) / 1e7
        if now - started > STALE_SECONDS:
            continue
        name = _name(key, packaged)
        if name not in names:
            names.append(name)
    return names


def _entries():
    """(key, packaged, start, stop) for every app the consent store has seen."""
    import winreg

    def subkeys(path):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with key:
            i = 0
            while True:
                try:
                    yield winreg.EnumKey(key, i)
                except OSError:
                    return
                i += 1

    def times(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                return (winreg.QueryValueEx(key, "LastUsedTimeStart")[0],
                        winreg.QueryValueEx(key, "LastUsedTimeStop")[0])
        except OSError:
            return None                     # never used it

    for name in subkeys(CONSENT):
        if name == "NonPackaged":           # desktop apps live one level down
            for exe in subkeys(CONSENT + "\\NonPackaged"):
                t = times(CONSENT + "\\NonPackaged\\" + exe)
                if t:
                    yield (exe, False) + t
            continue
        t = times(CONSENT + "\\" + name)
        if t:
            yield (name, True) + t


def windows_mic_users():
    """Apps recording from the microphone now. Empty off Windows, or on error."""
    if not WINDOWS:
        return []
    try:
        return recording(_entries())
    except (OSError, ValueError, TypeError):
        return []


if __name__ == "__main__":
    print(", ".join(windows_mic_users()))
    sys.exit(0)
