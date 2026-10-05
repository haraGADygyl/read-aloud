"""Shared locations for claude-speak.

Runtime state deliberately lives outside the plugin directory: plugin updates
replace that directory, and a 338 MB model download should survive them.
"""

import json
import os
import re

HOME = os.path.expanduser("~")

_XDG_DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
_XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")

DATA = os.environ.get("CLAUDE_SPEAK_HOME") or os.path.join(_XDG_DATA, "claude-speak")
CONFIG = os.environ.get("CLAUDE_SPEAK_CONFIG") or os.path.join(
    _XDG_CONFIG, "claude-speak", "config.json")

VENV_PY = os.path.join(DATA, "venv", "bin", "python")
MODEL = os.path.join(DATA, "models", "kokoro-v1.0.onnx")
VOICES = os.path.join(DATA, "models", "voices-v1.0.bin")
HELD = os.path.join(DATA, "held.jsonl")
LAST = os.path.join(DATA, "last")           # one file per project, see last_file
SESSIONS = os.path.join(LAST, "sessions")   # and per session, see session_file
LOG = os.path.join(DATA, "daemon.log")

# Where the plugin is installed right now, recorded by the Stop hook because it
# is the only part that always runs from the current copy. Claude Code stamps
# the plugin directory with the version, so it moves on every update, and both
# the PATH link and the systemd unit name it absolutely — see scripts/csheal.sh.
ROOT_POINTER = os.path.join(DATA, "plugin-root")

# A socket under XDG_RUNTIME_DIR is cleaned up on logout; fall back for systems
# that do not set it (some containers, some BSD-ish setups).
_RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or DATA
SOCK = os.path.join(_RUNTIME, "claude-speak.sock")

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.path.join(SCRIPTS, "kokorod.py")


def version_key(text):
    """(0, 7, 1) from "0.7.1", so versions compare the way people read them.

    Anything unparseable sorts oldest: a version we cannot read is not one to
    defer to.
    """
    parts = re.findall(r"\d+", str(text or ""))[:4]
    return tuple(int(p) for p in parts)


def plugin_version(scripts_dir):
    """The version of the plugin a scripts/ directory belongs to, as a key.

    Empty when there is no readable manifest — a directory that has been
    deleted, or something that was never a plugin.
    """
    manifest = os.path.join(os.path.dirname(scripts_dir),
                            ".claude-plugin", "plugin.json")
    try:
        with open(manifest) as fh:
            return version_key(json.load(fh).get("version"))
    except (OSError, ValueError, AttributeError):
        return ()


def last_file(label):
    """Where the most recent reply from one project is kept, for `again`.

    The label is a directory basename, so it can hold anything a directory
    name can — spaces, dots, a leading dash. Everything outside a safe set
    becomes an underscore: that can land two oddly-named projects on one
    file, which is a fair price for not letting a directory called ".." write
    wherever it likes.
    """
    return os.path.join(LAST, _safe(label) + ".txt")


def session_file(session):
    """The most recent reply from one Claude Code session, for `again`.

    The project copy above is shared by every session open in that directory,
    so with two terminals in one repo it holds whichever finished last.
    /claude-speak:speak knows its own session id and asks for this instead.
    """
    return os.path.join(SESSIONS, _safe(session) + ".txt")


def mute_file(session):
    """Marks the next reply from a session as the answer to a claude-speak
    command — "Stopped speaking." — which is neither spoken nor kept."""
    return os.path.join(SESSIONS, _safe(session) + ".mute")


def _safe(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "").strip(".") or "claude"


def kokoro_ready():
    return all(os.path.exists(p) for p in (VENV_PY, MODEL, VOICES))
