#!/usr/bin/env bash
# One-time setup: python venv, Kokoro model download, optional systemd service.
# Safe to re-run; existing pieces are skipped.
set -uo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$(cd "$SCRIPTS/../bin" 2>/dev/null && pwd || true)"
DATA="${READ_ALOUD_HOME:-${CLAUDE_SPEAK_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/read-aloud}}"
CFG="${READ_ALOUD_CONFIG:-${CLAUDE_SPEAK_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/read-aloud/config.json}}"
MODELS="$DATA/models"
VENV="$DATA/venv"
RELEASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

# The PATH link and the systemd unit both name this directory absolutely, and
# it moves on every plugin update. csheal.sh owns both.
# shellcheck source=csheal.sh
. "$SCRIPTS/csheal.sh"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m warn:\033[0m %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- rename ----
# This was claude-speak until 0.10.0. Carry its venv, model, config and held
# replies over — nobody should download 338 MB again for a rename — and swap
# its daemon unit and PATH link for read-aloud's. Before anything below
# creates $DATA, which would leave the old directory nowhere to move to.
python3 "$SCRIPTS/csmigrate.py"
if ! cs_redirected; then
  cs_unit_migrate "$HOME/.config/systemd/user/claude-speak.service" \
    "$HOME/.config/systemd/user/read-aloud.service" "$VENV/bin/python" "$SCRIPTS"
  rc=$?; [[ $rc == 10 || $rc == 13 ]] && say "Replaced the claude-speak daemon unit"
  cs_link_retire "$HOME/.local/bin"
  [[ $? == 11 ]] && say "Removed the claude-speak command — it is read-aloud now"
fi

# ---------------------------------------------------------------- prereqs ---
# Deliberately short. jq is gone (csconfig.py does the JSON), and espeak-ng is
# not needed either: kokoro-onnx pulls in espeakng-loader, which ships its own
# libespeak-ng and voice data into the venv. What is left is an audio player.
say "Checking prerequisites"

MACOS=""; [[ "$(uname -s)" == "Darwin" ]] && MACOS=1

have_player() {
  local p
  for p in paplay pw-play aplay ffplay play afplay; do
    command -v "$p" >/dev/null && return 0
  done
  return 1
}

# Offer rather than assume: installing system packages needs root on Linux, and
# a TTS plugin reaching for sudo unasked is not a thing to do quietly. With no
# terminal to ask at — the slash command runs without one — just print it.
offer_install() {   # offer_install <what it is for> <package...>
  local why="$1"; shift
  local cmd
  if [[ -n "$MACOS" ]]; then
    command -v brew >/dev/null || { warn "$why — install Homebrew, then: brew install $*"; return 1; }
    cmd="brew install $*"
  else
    command -v apt-get >/dev/null || { warn "$why — install with your package manager: $*"; return 1; }
    cmd="sudo apt install -y $*"
  fi

  if [[ ! -t 0 ]]; then
    warn "$why — run: $cmd"
    return 1
  fi
  printf '  %s\n  run "%s" now? [y/N] ' "$why" "$cmd"
  local answer; read -r answer
  case "$answer" in
    [yY]*) $cmd && return 0 || { warn "that did not work — run it yourself: $cmd"; return 1; } ;;
    *)     warn "skipped — run it yourself: $cmd"; return 1 ;;
  esac
}

if ! have_player; then
  if [[ -n "$MACOS" ]]; then
    die "no audio player found, which should be impossible on macOS (afplay is built in)"
  fi
  offer_install "no audio player, so nothing can be heard" pulseaudio-utils
  # Fatal if still missing: the daemon cannot make a sound without one, and
  # finding that out after a 338 MB download is a poor way to learn it.
  have_player || die "no audio player — install one of: pulseaudio-utils, pipewire-bin, alsa-utils, ffmpeg, sox"
fi

command -v notify-send >/dev/null || [[ -n "$MACOS" ]] \
  || warn "notify-send not found — hold-mode notifications will be silent (sudo apt install libnotify-bin)"

PYBOOT=""
for c in uv python3; do command -v "$c" >/dev/null && { PYBOOT="$c"; break; }; done
[[ -n "$PYBOOT" ]] || die "need uv or python3 to build the virtualenv"

# ------------------------------------------------------------------ venv ----
if [[ -x "$VENV/bin/python" ]]; then
  say "Virtualenv already present at $VENV"
else
  say "Creating virtualenv at $VENV"
  mkdir -p "$DATA"
  if command -v uv >/dev/null; then
    uv venv "$VENV" --python 3.12 >/dev/null || die "uv venv failed"
    uv pip install --python "$VENV/bin/python" kokoro-onnx soundfile numpy >/dev/null \
      || die "dependency install failed"
  else
    python3 -m venv "$VENV" || die "python3 -m venv failed (apt install python3-venv)"
    "$VENV/bin/pip" install --quiet --upgrade pip >/dev/null
    "$VENV/bin/pip" install --quiet kokoro-onnx soundfile numpy \
      || die "dependency install failed"
  fi
fi

# ----------------------------------------------------------------- model ----
mkdir -p "$MODELS"
fetch() {  # fetch <url> <dest> <human size>
  local url="$1" dest="$2" size="$3"
  if [[ -s "$dest" ]]; then say "$(basename "$dest") already downloaded"; return; fi
  say "Downloading $(basename "$dest") ($size)"
  if command -v curl >/dev/null; then
    curl -L --fail --progress-bar -o "$dest.part" "$url" || { rm -f "$dest.part"; die "download failed"; }
  else
    wget -q --show-progress -O "$dest.part" "$url" || { rm -f "$dest.part"; die "download failed"; }
  fi
  mv "$dest.part" "$dest"
}
fetch "$RELEASE/kokoro-v1.0.onnx" "$MODELS/kokoro-v1.0.onnx" "311 MB"
fetch "$RELEASE/voices-v1.0.bin"  "$MODELS/voices-v1.0.bin"  "27 MB"

# ---------------------------------------------------------------- config ----
# One place defines the defaults — cstext.DEFAULTS — and csconfig.py writes
# them out, carrying over a hand-rolled ~/.claude/speak.json if one exists.
had_cfg=""; [[ -f "$CFG" ]] && had_cfg=1
python3 "$SCRIPTS/csconfig.py" config ensure \
  && say "$([[ -n "$had_cfg" ]] && echo "Config topped up at $CFG" || echo "Wrote config to $CFG")" \
  || warn "could not write $CFG"

# ------------------------------------------------------------------ link ----
# Nothing else puts read-aloud on PATH, so a fresh install had a CLI you
# could not type. The plugin directory is versioned and moves on every update,
# hence a symlink rather than a copy.
BINDIR="$HOME/.local/bin"
LINK="$BINDIR/read-aloud"
cs_record_root "$DATA/plugin-root" "$SCRIPTS" \
  || warn "could not record the plugin location in $DATA"

if [[ ! -x "$BIN/read-aloud" ]]; then
  warn "cannot find bin/read-aloud next to $SCRIPTS — skipping the PATH link"
else
  cs_link_sync "$BINDIR" "$SCRIPTS"
  case $? in
    0)  say "read-aloud already linked into $BINDIR" ;;
    10) say "Linked read-aloud into $BINDIR" ;;
    # An earlier release lives at the old target: plugin directories are
    # version-stamped, so every update strands the link on the copy before it.
    11) say "Re-pointed $LINK at this version" ;;
    12) warn "$LINK exists and points elsewhere — leaving it alone" ;;
    *)  warn "could not link into $BINDIR — use /read-aloud:speak in Claude Code instead" ;;
  esac
  case ":$PATH:" in
    *":$BINDIR:"*) ;;
    *) warn "$BINDIR is not on your PATH — add it, or use /read-aloud:speak in Claude Code" ;;
  esac
fi

# --------------------------------------------------------------- systemd ----
# Optional: keeps the model warm across reboots. Without it the daemon starts
# on first use and stays up for the rest of the login session.
if [[ "${1:-}" != "--no-service" ]] && command -v systemctl >/dev/null \
   && systemctl --user show-environment >/dev/null 2>&1; then
  UNIT="$HOME/.config/systemd/user/read-aloud.service"
  cs_unit_write "$UNIT" "$VENV/bin/python" "$SCRIPTS" \
    || die "could not write $UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --now read-aloud.service >/dev/null 2>&1 \
    && say "Daemon enabled (systemd user service)" \
    || warn "could not enable the systemd service; the daemon will start on demand"
else
  say "Skipping systemd service; the daemon starts on demand"
fi

# ------------------------------------------------------------------ done ----
say "Testing"
python3 "$SCRIPTS/say.py" --voice "$(python3 "$SCRIPTS/csconfig.py" config get voice)" \
  "Read aloud is installed. This is the voice your replies will use when you play them."

cat <<'DONE'

Done. Useful commands:

Replies are HELD by default -- you get a ding and a notification, and
hear them when you ask:

  read-aloud play         read this terminal's replies, oldest first
  read-aloud play all     read every project's
  read-aloud hold off     or have replies spoken automatically

Even with auto-speak on, nothing is spoken while your microphone is in
use, so a call is never interrupted (read-aloud guard test).

  read-aloud audition     hear the 10 best English voices, then pick one
  read-aloud voice bm_george
  read-aloud speed 1.2

The same controls exist in Claude Code as /read-aloud:speak (for
example "/read-aloud:speak play"). Restart Claude Code once so the
Stop hook loads.
DONE
