"""Running the shell side of the plugin from the tests, on any platform.

bin/read-aloud, csheal.sh and the commit hook are bash. Linux and macOS run
them by their shebang. Windows cannot, and a plain "bash" there is usually
WSL's, whose Linux side shares nothing with the Windows Python running these
tests — so on Windows they go through Git Bash, as Claude Code runs them.
"""

import os
import shutil
import tempfile
import unittest

WINDOWS = os.name == "nt"


def _git_bash():
    if not WINDOWS:
        return None
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")]
    git = shutil.which("git")
    if git:                                  # Git\cmd\git.exe or Git\mingw64\bin
        here = os.path.dirname(git)
        candidates += [os.path.join(here, "..", "bin", "bash.exe"),
                       os.path.join(here, "..", "..", "bin", "bash.exe")]
    candidates.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                   "Git", "bin", "bash.exe"))
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.normpath(path)
    return None


GIT_BASH = _git_bash()
BASH = GIT_BASH or "bash"


def script(path, *args):
    """An argv that runs a bash script: by its shebang, or under Git Bash."""
    return ([BASH, sh_path(path)] if WINDOWS else [path]) + list(args)


def sh_path(path):
    """A path to splice into a bash command line. Backslashes are escapes to
    bash, so on Windows it gets C:/Users/..., which Git Bash reads fine."""
    return path.replace("\\", "/") if WINDOWS else path


def _can_symlink():
    where = tempfile.mkdtemp()
    try:
        os.symlink(where, os.path.join(where, "link"))
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        shutil.rmtree(where, ignore_errors=True)


needs_symlinks = unittest.skipUnless(
    _can_symlink(), "symlinks need Developer Mode or admin rights on Windows")

# The PATH link, the systemd unit and their repair are what Linux and macOS
# have instead of Windows' read-aloud.cmd; Git Bash takes the other branch.
posix_only = unittest.skipIf(WINDOWS, "Linux and macOS behaviour; Windows has read-aloud.cmd")
