#!/usr/bin/env python3
"""Client for the read-aloud daemon. Standard library only, so the Stop hook
can call it with whatever python3 is on the system.

    say.py "some text"              speak it
    say.py < notes.txt              speak a pipe or redirect
    say.py --stop                   cancel current speech
    say.py --status                 what is speaking / waiting
    say.py --voice af_bella "…"     override voice
"""

import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cspaths  # noqa: E402

SERVICE = "read-aloud.service"


def connect(timeout=1.0):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(cspaths.SOCK)
    return sock


def ensure_daemon(wait=60.0):
    """Return a connection, starting the daemon if it isn't up yet."""
    try:
        return connect()
    except (OSError, socket.timeout):
        pass
    if not cspaths.kokoro_ready():
        return None

    started = False
    try:
        if subprocess.run(["systemctl", "--user", "is-enabled", "--quiet", SERVICE],
                          capture_output=True).returncode == 0:
            subprocess.run(["systemctl", "--user", "start", SERVICE],
                           capture_output=True)
            started = True
    except OSError:
        pass  # no systemd on this box

    if not started:
        try:
            os.makedirs(cspaths.DATA, exist_ok=True)
            with open(cspaths.LOG, "ab") as log:
                subprocess.Popen([cspaths.VENV_PY, cspaths.DAEMON],
                                 stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        except OSError:
            return None

    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            return connect()
        except (OSError, socket.timeout):
            time.sleep(0.25)
    return None


def send(msg, start_daemon=True):
    conn = ensure_daemon() if start_daemon else None
    if conn is None and not start_daemon:
        try:
            conn = connect()
        except (OSError, socket.timeout):
            return None
    if conn is None:
        return None
    with conn:
        try:
            conn.sendall((json.dumps(msg) + "\n").encode())
            return conn.recv(4096).decode().strip()
        except OSError:
            return None


def main():
    args = sys.argv[1:]

    if args and args[0] == "--stop":
        send({"cmd": "stop"}, start_daemon=False)   # never boot it just to hush it
        sys.exit(0)

    if args and args[0] == "--status":
        # Checking on the daemon must never be the thing that starts it.
        out = send({"cmd": "status"}, start_daemon=False)
        print(out if out else "daemon not running")
        sys.exit(0 if out else 1)

    voice = os.environ.get("READ_ALOUD_VOICE", "af_heart")
    speed, session, label, mode = 1.0, "cli", "", "queue"
    same = "interrupt"      # a second `read` or `play` replaces the first
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--voice":
            voice = args.pop(0)
        elif flag == "--speed":
            speed = float(args.pop(0))
        elif flag == "--session":
            session = args.pop(0)
        elif flag == "--label":
            label = args.pop(0)
        elif flag == "--mode":
            mode = args.pop(0)
        elif flag == "--same":
            same = args.pop(0)

    # Anything left is the text; with none, take it from a pipe or redirect.
    # A bare `say.py` on a terminal has nothing to say, so it stops there rather
    # than blocking on a tty that will never send anything.
    if args:
        text = " ".join(args)
    elif sys.stdin.isatty():
        sys.exit(1)
    else:
        text = sys.stdin.read()

    if not text.strip():
        sys.exit(0)
    ok = send({"cmd": "say", "text": text, "voice": voice, "speed": speed,
               "session": session, "label": label, "mode": mode,
               "same": same})
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
