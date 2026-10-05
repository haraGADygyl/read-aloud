#!/usr/bin/env python3
"""Stop hook: read Claude's last reply aloud.

Runs on the system python (standard library only). Takes the hook payload on
stdin, extracts the final text reply, strips markdown down to something worth
listening to, and hands it off without blocking the session.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cspaths  # noqa: E402
import csmigrate  # noqa: E402
from cstext import clean, load_config  # noqa: E402


def last_assistant_text(payload):
    """Prefer the payload field; fall back to parsing the transcript."""
    direct = payload.get("last_assistant_message")
    if isinstance(direct, str) and direct.strip():
        return direct

    path = payload.get("transcript_path") or ""
    if not path or not os.path.exists(path):
        return ""

    found = ""
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            content = entry.get("message", {}).get("content")
            if not isinstance(content, list):
                continue
            chunks = [b.get("text", "") for b in content
                      if isinstance(b, dict) and b.get("type") == "text"]
            text = "\n".join(c for c in chunks if c.strip())
            if text.strip():
                found = text  # last one wins
    return found


def record_root():
    """Note the plugin directory this hook is running from.

    The hook is the only part of read-aloud that is always the installed
    version — a plugin update moves ${CLAUDE_PLUGIN_ROOT} and leaves the old
    directory on disk, still resolvable. The PATH link and the systemd unit
    are absolute paths into it, so they need to be told where it went.
    """
    want = cspaths.SCRIPTS
    have = ""
    try:
        with open(cspaths.ROOT_POINTER) as fh:
            have = fh.read().strip()
    except OSError:
        pass
    if have == want:
        return                              # the common case: nothing to do

    # Several terminals can be open on different releases, each with its own
    # copy of this hook still loaded from the version it started with. Last
    # writer used to win, so an older session dragged the PATH link and the
    # daemon back to its own release and restarted them. Newest wins instead.
    # A pointer with no readable version is stale by definition.
    if have and cspaths.plugin_version(have) > cspaths.plugin_version(want):
        return
    try:
        os.makedirs(cspaths.DATA, exist_ok=True)
        tmp = cspaths.ROOT_POINTER + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(want + "\n")
        os.replace(tmp, cspaths.ROOT_POINTER)   # never a half-written pointer
    except OSError:
        pass


def notify(title, body):
    if shutil.which("notify-send"):
        cmd = ["notify-send", "-a", "Claude Code", "-i", "utilities-terminal",
               # Collapse repeats into one notification instead of a stack.
               "-h", "string:x-canonical-private-synchronous:read-aloud",
               title, body]
    elif platform.system() == "Darwin" and shutil.which("osascript"):
        cmd = ["osascript", "-e",
               'display notification "%s" with title "%s"' % (body, title)]
    else:
        return
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


DING_CANDIDATES = [
    "/usr/share/sounds/freedesktop/stereo/message.oga",
    "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
    "/System/Library/Sounds/Tink.aiff",
]


def ding(cfg):
    """Short sound saying a reply is waiting. Deliberately not the voice."""
    path = str(cfg.get("holdSound") or "")
    if path.lower() == "off":
        return
    if not path:
        path = next((p for p in DING_CANDIDATES if os.path.exists(p)), "")
    if not path or not os.path.exists(path):
        return

    if shutil.which("paplay"):
        cmd = ["paplay", path]
    elif shutil.which("afplay"):
        cmd = ["afplay", path]
    elif shutil.which("ffplay"):
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
    elif shutil.which("aplay"):
        cmd = ["aplay", "-q", path]
    else:
        return
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def mic_in_use(cfg):
    """True when some app is recording — i.e. you are on a call.

    A PulseAudio/PipeWire 'source output' is a recording stream. Playback
    streams are sink inputs, so this never trips on our own audio.
    """
    if not cfg.get("meetingGuard", True) or not shutil.which("pactl"):
        return False
    try:
        out = subprocess.run(["pactl", "list", "source-outputs"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False

    ignore = [str(s).lower() for s in cfg.get("meetingGuardIgnore") or []]
    names = re.findall(r'application\.name = "([^"]*)"', out)
    if not names:
        # A stream with no application.name still counts as a live recording.
        return bool(re.search(r"^Source Output #", out, re.MULTILINE)) and not ignore
    return any(not any(ig in n.lower() for ig in ignore) for n in names)


def hold(text, cfg, quiet=False):
    """Stash the reply for later playback and say that it is waiting."""
    entry = {"ts": time.time(), "session": cfg["_session"],
             "label": cfg["_label"], "text": text}
    try:
        os.makedirs(os.path.dirname(cspaths.HELD), exist_ok=True)
        # O_APPEND keeps concurrent sessions from interleaving mid-line.
        fd = os.open(cspaths.HELD, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(entry, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        return

    if not cfg["notify"]:
        return
    waiting = 0
    try:
        with open(cspaths.HELD) as fh:
            waiting = sum(1 for ln in fh if ln.strip())
    except OSError:
        pass
    notify("Claude · %s" % cfg["_label"],
           "Reply ready — %d waiting. Run: read-aloud play" % waiting)
    if not quiet:
        ding(cfg)


def remember(text, cfg, session):
    """Keep this reply so `read-aloud again` can read it back.

    Stored after cleaning and after the maxChars cut, so a repeat is the same
    words in the same order rather than nearly so. One file per project, keyed
    the way play and clear are keyed, written atomically because two sessions
    in the same directory can finish at the same moment.

    And one per session, because the project file belongs to whichever session
    in that directory finished last — /read-aloud:speak again once read a
    neighbouring terminal's reply instead of its own.
    """
    paths = [cspaths.last_file(cfg["_label"])]
    if session:
        paths.append(cspaths.session_file(session))
    for path in paths:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except OSError:
            pass
    if session:
        forget_old_sessions()


SESSION_KEEP_DAYS = 30


def forget_old_sessions():
    """A file per session would otherwise pile up forever."""
    cutoff = time.time() - SESSION_KEEP_DAYS * 86400
    try:
        names = os.listdir(cspaths.SESSIONS)
    except OSError:
        return
    for name in names:
        path = os.path.join(cspaths.SESSIONS, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
        except OSError:
            pass


# Long enough for Claude to report a command in one line, even when it has to
# re-run the binary itself; short enough that a command whose reply never came
# — interrupted with Esc, so no Stop — cannot swallow a real reply much later.
MUTE_SECONDS = 120


def answers_a_command(session):
    """True for the reply that reports a /read-aloud:speak run.

    The CLI leaves a marker when the slash command runs it. That reply is
    "Stopped speaking." or "Repeating the last reply." — speaking it talks over
    the very thing just asked for, and keeping it makes it the reply `again`
    repeats next. The marker is used up either way.
    """
    if not session:
        return False
    path = cspaths.mute_file(session)
    try:
        age = time.time() - os.path.getmtime(path)
        os.unlink(path)
    except OSError:
        return False
    return age < MUTE_SECONDS


def pick_engine(cfg):
    want = cfg["engine"]
    if want != "auto":
        return want
    if cspaths.kokoro_ready():
        return "kokoro"
    if platform.system() == "Darwin" and shutil.which("say"):
        return "say"
    for candidate in ("spd-say", "espeak-ng", "espeak"):
        if shutil.which(candidate):
            return candidate
    return ""


def speak(text, cfg):
    engine = pick_engine(cfg)
    if not engine:
        return

    if engine == "kokoro":
        if cspaths.kokoro_ready():
            # The daemon streams chunk by chunk. Session identity lets it
            # keep this terminal's replies in order and queue other terminals
            # behind us.
            subprocess.Popen(
                [sys.executable, os.path.join(cspaths.SCRIPTS, "say.py"),
                 "--voice", str(cfg["voice"]),
                 "--speed", str(cfg["speed"]),
                 "--session", cfg["_session"],
                 "--label", cfg["_label"],
                 "--mode", str(cfg["multiSession"]),
                 "--same", str(cfg["sameSession"]),
                 text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            return
        engine = "say" if platform.system() == "Darwin" else "spd-say"

    # Only the daemon has a queue. say and espeak start a second voice on top
    # of the first, so the previous reply is killed whatever sameSession says —
    # overlapping speech is worse than a cut-off one. speech-dispatcher does
    # queue, so there -C is simply not sent.
    queue_it = cfg["sameSession"] == "queue"

    if engine == "say":                                  # macOS built-in
        subprocess.run(["pkill", "-x", "say"], check=False)
        args = ["say", "-r", str(int(180 * float(cfg["speed"]))), text]
    elif engine == "spd-say":
        if not queue_it:
            subprocess.run(["spd-say", "-C"], check=False)   # drop the old one
        args = ["spd-say", "-r", str(cfg["fallbackRate"]),
                "-t", str(cfg["fallbackVoice"]), "-m", "some", text]
    else:                                                # espeak-ng / espeak
        subprocess.run(["pkill", "-f", "^espeak"], check=False)
        args = [engine, "-s", str(150 + int(cfg["fallbackRate"])), text]

    subprocess.Popen(args, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}

    # The first reply after switching from claude-speak carries its state
    # over — before record_root, which would otherwise start a fresh data
    # directory where the old one, model and all, is about to be moved to.
    if not csmigrate.redirected():
        csmigrate.migrate()
    record_root()

    # Checked before `enabled`, so the reply to /read-aloud:speak off still
    # uses up its marker rather than leaving it for the next reply.
    session = str(payload.get("session_id") or "")
    if answers_a_command(session):
        return

    cfg = load_config()
    if not cfg["enabled"]:
        return

    # Identify the terminal this reply came from, so concurrent sessions can be
    # kept apart instead of talking over each other.
    cfg["_session"] = session or str(os.getppid())
    cwd = payload.get("cwd") or os.getcwd()
    cfg["_label"] = os.path.basename(cwd.rstrip("/")) or "claude"

    text = clean(last_assistant_text(payload), cfg)
    if not text:
        return

    limit = int(cfg["maxChars"])
    if len(text) > limit:
        cut = text.rfind(".", 0, limit)
        text = text[: cut + 1 if cut > limit // 2 else limit] + " . Rest is on screen."

    # Kept whatever happens next — held, spoken or swallowed by the guard.
    # "I missed that" applies to all three.
    remember(text, cfg, session)

    # A live microphone outranks every other setting: stash the reply and make
    # no sound at all, so a client call is never interrupted.
    if mic_in_use(cfg):
        hold(text, cfg, quiet=True)
    elif cfg["holdReplies"]:
        hold(text, cfg)
    else:
        speak(text, cfg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a broken hook must never break the session
    sys.exit(0)
