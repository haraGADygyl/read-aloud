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
# The interpreter found is remembered in $DATA/python, and used directly while
# the file it names exists. python3 on Windows is often an alias that takes
# ~0.3 s to hand over to the real python.exe, and the hook and every CLI
# command used to pay that, more than once.
#
# -X utf8: Python on Windows reads the payload in the ANSI code page otherwise,
# and "—" reaches the queue as "â€”".

here=$(dirname "$0")

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    # As cspaths.DATA, bin/read-aloud and install.sh work it out.
    data="${READ_ALOUD_HOME:-${CLAUDE_SPEAK_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/read-aloud}}"
    py=$(cat "$data/python" 2>/dev/null)
    if [ -n "$py" ] && [ -f "$py" ]; then
      exec "$py" -X utf8 "$here/speak-hook.py"
    fi
    for name in python3 python py; do
      # </dev/null: the payload on stdin belongs to the hook, not the probe.
      py=$("$name" -c 'import sys; sys.version_info[0] == 3 and print(sys.executable)' \
             </dev/null 2>/dev/null | tr -d '\r')
      if [ -n "$py" ] && [ -f "$py" ]; then
        # Only into a $DATA that exists: creating it here would leave the
        # claude-speak directory nowhere to move to (see csmigrate.py).
        [ -d "$data" ] && printf '%s\n' "$py" > "$data/python.tmp" \
          && mv -f "$data/python.tmp" "$data/python"
        exec "$py" -X utf8 "$here/speak-hook.py"
      fi
    done
    exit 0                      # no Python: as quiet as a failing hook
    ;;
esac

exec python3 -X utf8 "$here/speak-hook.py"
