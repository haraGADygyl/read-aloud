#!/usr/bin/env python3
"""Carrying an install over from claude-speak, this plugin's name until 0.10.0.

Claude Code began refusing third-party plugin names that start with "claude-",
so everything that carried the name moved: the data and config directories,
the socket, the systemd unit and the PATH link. The 338 MB model is in the data
directory, and nobody should download it twice for a rename.

Directories are moved here; the unit and the link are bash's, in csheal.sh.
Runs on the system python, standard library only — the Stop hook calls it.

    csmigrate.py        move what can be moved, say what moved, and warn
                        while the old plugin is still installed alongside
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cspaths  # noqa: E402

LEGACY = "claude-speak"


def redirected():
    """A test or a debug run. Its state is somewhere else on purpose, and the
    real directories are not this run's to move."""
    return any(os.environ.get(p + s) for p in ("READ_ALOUD_", "CLAUDE_SPEAK_")
               for s in ("HOME", "CONFIG"))


def move_dir(old, new):
    """Rename old to new and leave a link at old. True when it moved.

    The link is for what still runs from the previous release: a terminal
    that has not been restarted keeps the claude-speak Stop hook it loaded,
    and that hook writes its held replies under the old path.
    """
    if os.path.islink(old) or not os.path.isdir(old):
        return False
    if os.path.lexists(new):
        return False                     # already moved, or begun afresh
    try:
        os.makedirs(os.path.dirname(new), exist_ok=True)
        os.rename(old, new)
    except OSError:
        return False
    try:
        os.symlink(new, old)
    except OSError:
        pass
    return True


def migrate(data_base=None, config_base=None):
    """Move the legacy directories into place. Returns the moves made.

    The bases are the XDG data and config directories, parameters only so the
    tests can point them somewhere harmless.
    """
    data_base = data_base or cspaths._XDG_DATA
    config_base = config_base or cspaths._XDG_CONFIG
    moved = []
    for base, new in ((data_base, cspaths.DATA),
                      (config_base, os.path.dirname(cspaths.CONFIG))):
        old = os.path.join(base, LEGACY)
        if move_dir(old, new):
            moved.append((old, new))
    return moved


def legacy_plugin(home=None):
    """The install id of a claude-speak plugin still installed, else "".

    Both plugins hook Stop, so with the old one still there every reply is
    read twice — by two daemons, on two sockets, talking over each other.
    """
    path = os.path.join(home or cspaths.HOME, ".claude", "plugins",
                        "installed_plugins.json")
    try:
        with open(path) as fh:
            plugins = json.load(fh).get("plugins", {})
    except (OSError, ValueError, AttributeError):
        return ""
    for key in plugins:
        if key.split("@")[0] == LEGACY:
            return key
    return ""


def tilde(path):
    home = cspaths.HOME.rstrip("/") + "/"
    return "~/" + path[len(home):] if path.startswith(home) else path


def main():
    if not redirected():
        for old, new in migrate():
            print("read-aloud: moved %s to %s" % (tilde(old), tilde(new)))
    old = legacy_plugin()
    if old:
        print("read-aloud: the old claude-speak plugin is still installed, so "
              "every reply is read twice. Remove it with:")
        print("  claude plugin uninstall %s" % old)
    return 0


if __name__ == "__main__":
    sys.exit(main())
