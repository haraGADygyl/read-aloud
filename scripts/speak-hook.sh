#!/bin/sh
# The Stop hook's entry point (hooks/hooks.json): find a Python, then run
# speak-hook.py under it. Claude Code runs hooks with sh -c, and with Git Bash
# on Windows.
#
# python3 is the name everywhere but Windows, where the python.org installer
# provides python and py but no python3, and python3 can be the Microsoft
# Store's stand-in, which exits without running anything. The hook swallows
# every failure, so a name that does not run meant every reply went unread
# with nothing to show for it. Each name is tried for real; the first that
# runs Python 3 is used. cs_python in csheal.sh does the same for the CLI —
# keep the two lists alike.
#
# -X utf8: Python on Windows reads the payload in the ANSI code page otherwise,
# and "—" reaches the queue as "â€”".

here=$(dirname "$0")

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    for py in python3 python py; do
      # </dev/null: the payload on stdin belongs to the hook, not the probe.
      if "$py" -c 'import sys; sys.exit(sys.version_info[0] != 3)' \
           </dev/null >/dev/null 2>&1; then
        exec "$py" -X utf8 "$here/speak-hook.py"
      fi
    done
    exit 0                      # no Python: as quiet as a failing hook
    ;;
esac

exec python3 -X utf8 "$here/speak-hook.py"
