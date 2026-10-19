#!/usr/bin/env python3
"""Tests for how say.py reaches the daemon — a unix socket, or a loopback port.

    python3 -m unittest discover tests

Windows Python has no AF_UNIX, so there the daemon listens on 127.0.0.1 behind
a token. The loopback path is forced here on every platform, so a change that
breaks Windows fails on Linux and macOS too. A fake speaker stands in for the
model: nothing is synthesised and nothing plays.
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.modules.setdefault("numpy", types.ModuleType("numpy"))

import cspaths  # noqa: E402
import kokorod  # noqa: E402
import say  # noqa: E402


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def submit(self, job, mode="queue"):
        self.said.append(job.text)
        return "speaking"

    def stop(self):
        pass

    def status(self):
        return {"speaking": None, "queued": 0}


class Base(unittest.TestCase):
    unix = False

    def setUp(self):
        # Short and under /tmp where there is one: AF_UNIX paths cap near 108.
        self.dir = tempfile.mkdtemp(dir="/tmp" if os.path.isdir("/tmp") else None)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for name, value in [("UNIX_SOCKETS", self.unix),
                            ("ENDPOINT", os.path.join(self.dir, "daemon.port")),
                            ("SOCK", os.path.join(self.dir, "s.sock"))]:
            patcher = mock.patch.object(cspaths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.speaker = FakeSpeaker()

    def serve(self, connections=1, quit=None):
        """Start listening, answering this many connections in a thread."""
        server, where, token = kokorod.listen()
        self.addCleanup(server.close)

        def loop():
            for _ in range(connections):
                conn, _ = server.accept()
                kokorod.handle(conn, self.speaker, token, quit)

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        return where, token


class Loopback(Base):

    def test_say_reaches_the_daemon(self):
        self.serve()
        self.assertEqual(say.send({"cmd": "say", "text": "hello"},
                                  start_daemon=False), "speaking")
        self.assertEqual(self.speaker.said, ["hello"])

    def test_status_comes_back(self):
        self.serve()
        out = say.send({"cmd": "status"}, start_daemon=False)
        self.assertEqual(json.loads(out), self.speaker.status())

    def test_it_listens_on_loopback_only(self):
        server, _, token = kokorod.listen()
        self.addCleanup(server.close)
        self.assertEqual(server.getsockname()[0], "127.0.0.1")
        self.assertGreaterEqual(len(token), 32)

    def test_a_wrong_token_is_not_heard(self):
        self.serve()
        with open(cspaths.ENDPOINT) as fh:
            port = fh.read().split()[0]
        with open(cspaths.ENDPOINT, "w") as fh:
            fh.write("%s %s\n" % (port, "0" * 32))
        self.assertFalse(say.send({"cmd": "say", "text": "intruder"},
                                  start_daemon=False))
        self.assertEqual(self.speaker.said, [])

    def test_no_token_is_not_heard(self):
        where, _ = self.serve()
        with open(where) as fh:
            port = int(fh.read().split()[0])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(b'{"cmd": "say", "text": "intruder"}\n')
            self.assertEqual(conn.recv(4096), b"")
        self.assertEqual(self.speaker.said, [])

    def test_a_non_ascii_token_is_refused_quietly(self):
        where, _ = self.serve()
        with open(where) as fh:
            port = int(fh.read().split()[0])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(json.dumps({"cmd": "stop", "token": "é"}).encode()
                         + b"\n")
            self.assertEqual(conn.recv(4096), b"")

    def test_quit_answers_then_leaves(self):
        # `read-aloud restart` on Windows, where pkill cannot see the daemon.
        # The reply has to go out first, or the client cannot tell it worked.
        left = []
        self.serve(quit=lambda: left.append("gone"))
        self.assertEqual(say.send({"cmd": "quit"}, start_daemon=False), "ok")
        self.assertEqual(left, ["gone"])

    def test_quit_from_a_stranger_is_ignored(self):
        left = []
        where, _ = self.serve(quit=lambda: left.append("gone"))
        with open(where) as fh:
            port = int(fh.read().split()[0])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(b'{"cmd": "quit"}\n')
            conn.recv(4096)
        self.assertEqual(left, [])

    def test_not_running_means_no_endpoint_file(self):
        self.assertIsNone(say.send({"cmd": "status"}, start_daemon=False))

    def test_a_half_written_endpoint_file_is_not_running(self):
        with open(cspaths.ENDPOINT, "w") as fh:
            fh.write("517")
        self.assertIsNone(say.send({"cmd": "status"}, start_daemon=False))

    def test_a_stale_port_is_not_running(self):
        # The daemon died and nothing listens there now.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with open(cspaths.ENDPOINT, "w") as fh:
            fh.write("%d %s\n" % (port, "a" * 32))
        self.assertIsNone(say.send({"cmd": "status"}, start_daemon=False))


class Spawn(unittest.TestCase):
    """Starting the daemon, the hook's say.py and the ding in the background."""

    def test_it_really_starts_something(self):
        # Unmocked: on Windows this goes through the job-breakaway attempt
        # and, where the job refuses, the fallback.
        proc = cspaths.spawn([sys.executable, "-c", "raise SystemExit(7)"])
        self.assertEqual(proc.wait(timeout=30), 7)

    @unittest.skipIf(os.name == "nt", "POSIX only")
    def test_posix_gets_a_session_of_its_own(self):
        with mock.patch.object(cspaths.subprocess, "Popen") as popen:
            cspaths.spawn(["x"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(cspaths.NO_WINDOW, 0)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_opens_no_window_and_leaves_the_job(self):
        sp = cspaths.subprocess
        with mock.patch.object(sp, "Popen") as popen:
            cspaths.spawn(["x"])
        flags = popen.call_args.kwargs["creationflags"]
        for flag in (sp.CREATE_NO_WINDOW, sp.CREATE_NEW_PROCESS_GROUP,
                     sp.CREATE_BREAKAWAY_FROM_JOB):
            self.assertTrue(flags & flag, hex(flag))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_stays_in_a_job_that_forbids_leaving(self):
        sp = cspaths.subprocess
        with mock.patch.object(sp, "Popen",
                               side_effect=[PermissionError(5, "denied"), "ok"]) as popen:
            self.assertEqual(cspaths.spawn(["x"]), "ok")
        self.assertFalse(popen.call_args.kwargs["creationflags"]
                         & sp.CREATE_BREAKAWAY_FROM_JOB)

    def test_venv_python_is_where_this_platform_puts_it(self):
        want = (os.path.join("Scripts", "python.exe") if os.name == "nt"
                else os.path.join("bin", "python"))
        self.assertTrue(cspaths.VENV_PY.endswith(want), cspaths.VENV_PY)


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "no unix sockets here")
class UnixSocket(Base):
    unix = True

    def test_say_reaches_the_daemon(self):
        self.serve()
        self.assertEqual(say.send({"cmd": "say", "text": "hello"},
                                  start_daemon=False), "speaking")
        self.assertEqual(self.speaker.said, ["hello"])

    def test_no_token_and_no_endpoint_file(self):
        where, token = self.serve()
        self.assertEqual(token, "")
        self.assertEqual(where, cspaths.SOCK)
        self.assertFalse(os.path.exists(cspaths.ENDPOINT))
        say.send({"cmd": "stop"}, start_daemon=False)


if __name__ == "__main__":
    unittest.main()
