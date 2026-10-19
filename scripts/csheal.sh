# Keeping the PATH link and the systemd unit pointed at the plugin in use.
#
# Both name an absolute path inside the plugin directory, and Claude Code
# stamps that directory with the version:
#
#   ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/
#
# An update writes a new one and leaves the old one on disk, so a link or a
# unit written at install time keeps resolving — to the previous release. The
# daemon is the one that hurts: a fix in kokorod.py never reaches a systemd
# user whose ExecStart still names the version they first installed, and the
# Stop hook goes on running the new code, so nothing looks broken.
#
# Repairs go *towards* $DATA/plugin-root, which the Stop hook rewrites
# whenever it runs from a directory it has not seen, and never towards the
# caller: an out-of-date read-aloud repairing things to point at itself
# would pin the daemon to the release it came from.
#
# Sourced by bin/read-aloud and scripts/install.sh. Nothing here prints;
# callers report in their own voice, from the status codes:
#
#   0  already correct     11  repaired
#   10 created             12  left alone, not ours
#   13 unit rewritten and the daemon restarted with it

# A test or a debug run: its state is redirected on purpose, while the link
# and the unit are always the real ones under $HOME. The CLAUDE_SPEAK_ names
# are from before the rename, and a redirect is a redirect whichever is used.
cs_redirected() {
  [[ -n "${READ_ALOUD_HOME:-}${READ_ALOUD_CONFIG:-}${CLAUDE_SPEAK_HOME:-}${CLAUDE_SPEAK_CONFIG:-}" ]]
}

# Git Bash on Windows — MSYS2 underneath, and Cygwin is the same for our
# purposes. There is no systemd, a symlink is a copy unless Developer Mode is
# on, and a venv keeps its interpreter somewhere else.
cs_windows() {
  case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; esac
  return 1
}

# The interpreter for the stdlib-only scripts. python3, except on Windows,
# where the python.org installer gives python and py but no python3, and
# python3 can be the Microsoft Store's stand-in that runs nothing — so each
# is tried for real. scripts/speak-hook.sh does the same for the hook; keep
# the two lists alike. Fails when nothing runs.
cs_python() {
  cs_windows || { printf 'python3'; return 0; }
  local py
  for py in python3 python py; do
    if "$py" -c 'import sys; sys.exit(sys.version_info[0] != 3)' \
         </dev/null >/dev/null 2>&1; then
      printf '%s' "$py"
      return 0
    fi
  done
  return 1
}

cs_venv_py() {                       # <venv dir> -> its python on stdout
  if cs_windows; then printf '%s/Scripts/python.exe' "$1"
  else printf '%s/bin/python' "$1"; fi
}

cs_scripts_from_pointer() {          # <pointer file> -> scripts dir on stdout
  local pointer="$1" root
  [[ -s "$pointer" ]] || return 1
  root="$(head -n 1 "$pointer" 2>/dev/null)"
  # A pointer carried over from claude-speak names a directory that has
  # kokorod.py but not this CLI — stale until the hook next runs.
  [[ -n "$root" && -f "$root/kokorod.py" && -x "$root/../bin/read-aloud" ]] || return 1
  printf '%s' "$root"
}

cs_record_root() {                   # <pointer file> <scripts dir>
  local root="$2"
  # In the hook's spelling, C:\Users\..., so the two writers agree byte for
  # byte and Python can read what bash wrote.
  cs_windows && root="$(cygpath -w "$root")"
  mkdir -p "$(dirname "$1")" 2>/dev/null || return 1
  printf '%s\n' "$root" > "$1"
}

# ------------------------------------------------------ Command Prompt ----
# Windows has no symlink to put on PATH, so it gets a read-aloud.cmd instead,
# and one that names no plugin directory: it reads the pointer at run time and
# hands over to that release's bin\read-aloud.cmd. An update moves the plugin
# and the shim follows without being rewritten, which is why cs_link_sync is
# not needed here.
CS_SHIM_MARK="rem read-aloud shim, written by install.sh"

cs_shim_text() {                     # <pointer file>
  # cmd wants CRLF: a label can be missed when a goto scans LF-only lines.
  sed 's/$/\r/' <<SHIMEOF
@echo off
$CS_SHIM_MARK
rem It finds the plugin through the pointer the Stop hook keeps up to date.
setlocal
set "ROOT="
set /p ROOT=<"$(cygpath -w "$1")" 2>nul
if not defined ROOT (
  echo read-aloud: cannot find the plugin. Restart Claude Code and let it finish one reply.
  exit /b 1
)
call "%ROOT%\\..\\bin\\read-aloud.cmd" %*
exit /b %ERRORLEVEL%
SHIMEOF
}

cs_shim_sync() {                     # <bin dir> <pointer file>
  local shim="$1/read-aloud.cmd"
  if [[ -f "$shim" ]]; then
    grep -qF "$CS_SHIM_MARK" "$shim" || return 12   # somebody else's
    [[ "$(cs_shim_text "$2")" == "$(cat "$shim")" ]] && return 0
    cs_shim_text "$2" > "$shim" || return 1
    return 11
  fi
  mkdir -p "$1" 2>/dev/null || return 1
  cs_shim_text "$2" > "$shim" || return 1
  return 10
}

# One copy of the unit. It used to be written only by install.sh; the CLI
# needs it too now, and two heredocs is how the config defaults drifted.
cs_unit_text() {                     # <venv python> <scripts dir>
  cat <<UNITEOF
[Unit]
Description=read-aloud TTS daemon (Kokoro)
After=pipewire.service pulseaudio.service

[Service]
Type=simple
ExecStart=$1 $2/kokorod.py
Restart=on-failure
RestartSec=3
Nice=5

[Install]
WantedBy=default.target
UNITEOF
}

cs_unit_write() {                    # <unit path> <venv python> <scripts dir>
  mkdir -p "$(dirname "$1")" 2>/dev/null || return 1
  cs_unit_text "$2" "$3" > "$1"
}

cs_unit_stale() {                    # true only when a unit exists and has drifted
  [[ -f "$1" ]] || return 1
  ! grep -qxF "ExecStart=$2 $3/kokorod.py" "$1"
}

cs_unit_sync() {                     # <unit path> <venv python> <scripts dir>
  cs_unit_stale "$@" || return 0
  cs_unit_write "$@" || return 1
  command -v systemctl >/dev/null 2>&1 || return 10
  systemctl --user daemon-reload >/dev/null 2>&1
  # Nothing to restart when the unit is not enabled: the daemon starts on the
  # next reply, and saying it is stopped is then the honest answer.
  systemctl --user is-enabled --quiet read-aloud.service 2>/dev/null || return 10
  systemctl --user restart read-aloud.service >/dev/null 2>&1 || return 10
  return 13
}

# A link worth repairing: one pointing into a read-aloud plugin directory,
# including one that has since been deleted. Anything else is somebody's own
# read-aloud and is left where it is.
cs_link_is_ours() {                  # <literal link target>
  local target="$1"
  [[ -f "$(dirname "$target")/../scripts/cspaths.py" ]] && return 0
  [[ ! -e "$target" && "$target" == *read-aloud*/bin/read-aloud ]] && return 0
  return 1
}

cs_link_sync() {                     # <bin dir> <scripts dir>
  local bindir="$1" want link current
  want="$(dirname "$2")/bin/read-aloud"
  [[ -x "$want" ]] || return 1
  link="$bindir/read-aloud"

  if [[ ! -e "$link" && ! -L "$link" ]]; then
    mkdir -p "$bindir" 2>/dev/null || return 1
    ln -s "$want" "$link" 2>/dev/null || return 1
    return 10
  fi

  # readlink is empty for a regular file, which is never ours to replace.
  current="$(readlink "$link" 2>/dev/null || true)"
  [[ -n "$current" ]] || current="$link"
  [[ "$current" == "$want" ]] && return 0
  cs_link_is_ours "$current" || return 12
  ln -sfn "$want" "$link" 2>/dev/null || return 1
  return 11
}

# ---------------------------------------------------------- the rename ----
# The plugin was claude-speak until 0.10.0 (see scripts/csmigrate.py, which
# moves the directories). Its unit and its PATH link are retired here.

# Stop and remove the claude-speak unit, and put read-aloud's in its place —
# enabled only if the old one was, so nobody gains a service they had declined.
cs_unit_migrate() {                  # <old unit> <new unit> <venv python> <scripts dir>
  local old="$1" new="$2" was=""
  [[ -f "$old" ]] || return 0
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user is-enabled --quiet "$(basename "$old")" 2>/dev/null && was=1
    systemctl --user disable --now "$(basename "$old")" >/dev/null 2>&1
  fi
  rm -f "$old" || return 1
  [[ -f "$new" ]] || cs_unit_write "$new" "$3" "$4" || return 1
  command -v systemctl >/dev/null 2>&1 || return 10
  systemctl --user daemon-reload >/dev/null 2>&1
  [[ -n "$was" ]] || return 10
  systemctl --user enable --now "$(basename "$new")" >/dev/null 2>&1 || return 10
  return 13
}

# Remove ~/.local/bin/claude-speak when it is the link install.sh made. One
# pointing anywhere else is somebody's own and stays.
cs_link_retire() {                   # <bin dir>
  local link="$1/claude-speak" target
  [[ -L "$link" ]] || return 0
  target="$(readlink "$link" 2>/dev/null)"
  [[ -f "$(dirname "$target")/../scripts/cspaths.py" \
     || ( ! -e "$target" && "$target" == *claude-speak*/bin/claude-speak ) ]] || return 12
  rm -f "$link" || return 1
  return 11
}
