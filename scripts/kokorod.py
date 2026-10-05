#!/usr/bin/env python3
"""Kokoro TTS daemon — keeps the model warm and streams speech as it renders.

Listens on a unix socket for newline-delimited JSON commands:
    {"cmd": "say", "text": "...", "voice": "af_heart", "speed": 1.0,
     "session": "<id>", "label": "myproject", "mode": "queue",
     "same": "queue"}
    {"cmd": "stop"}     cancel everything, drop the queue
    {"cmd": "status"}

Two axes decide what happens when a reply lands mid-speech:

  * "mode" governs a reply from a DIFFERENT session: queue behind what is
    speaking (announced by project name), interrupt it, or be dropped.
  * "same" governs a second reply from the SAME session — a burst of subagents
    reporting back, or an answer you asked for again. "queue" reads them in
    order; "interrupt" treats the older one as stale and cuts it off.

Nothing overlaps either way.
"""

import collections
import json
import os
import socket
import subprocess
import sys
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cspaths  # noqa: E402
import csaudio  # noqa: E402
# Chunking is pure text handling, so it lives with the rest of it in cstext —
# stdlib only, and testable without the venv this daemon runs under.
from cstext import split_chunks, synth_chunks  # noqa: E402

# Waiting replies; the oldest is dropped beyond this. Eight because a reply
# runs about a minute aloud, so that is the point where the voice is far enough
# behind to be talking about work you have moved on from — it is a staleness
# bound, not a resource one. A run of subagents reporting back fits inside it.
MAX_QUEUE = 8


class Job(object):
    __slots__ = ("session", "label", "text", "voice", "speed", "same")

    def __init__(self, msg):
        self.session = msg.get("session") or "default"
        self.same = msg.get("same") or "interrupt"
        self.label = (msg.get("label") or "").strip()
        self.text = msg["text"]
        self.voice = msg.get("voice") or "af_heart"
        self.speed = float(msg.get("speed") or 1.0)


class Speaker:
    def __init__(self, kokoro):
        self.kokoro = kokoro
        self.cv = threading.Condition()
        self.queue = collections.deque()
        self.current = None
        self.cancel = False
        self.player = None
        self.last_label = None
        self.seen_labels = set()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- caller side -----------------------------------------------------

    def submit(self, job, mode="queue"):
        with self.cv:
            if job.label:
                self.seen_labels.add(job.label)
            same_session = self.current is not None and self.current.session == job.session

            # What a terminal's own reply does to the one it is still speaking
            # is "same"'s call alone: "mode" is about the other terminals, and
            # letting it interrupt here would talk over a run of subagents.
            if same_session:
                if job.same == "interrupt":
                    self._supersede_locked(job)
            elif mode == "interrupt":
                self.queue.clear()
                self._cancel_locked()
            elif mode == "drop" and (self.current is not None or self.queue):
                return "dropped"
            elif job.same == "interrupt":
                self._supersede_locked(job)

            self.queue.append(job)
            while len(self.queue) > MAX_QUEUE:
                self.queue.popleft()
            self.cv.notify()
        return "queued"

    def stop(self):
        with self.cv:
            self.queue.clear()
            self._cancel_locked()

    def status(self):
        with self.cv:
            return {"speaking": self.current.label if self.current else None,
                    "queued": [j.label for j in self.queue],
                    "sessions_seen": sorted(self.seen_labels)}

    def _supersede_locked(self, job):
        """This session's newer reply replaces its own older ones."""
        self.queue = collections.deque(
            j for j in self.queue if j.session != job.session)
        if self.current is not None and self.current.session == job.session:
            self._cancel_locked()

    def _cancel_locked(self):
        self.cancel = True
        proc, self.player = self.player, None
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # ---- worker side -----------------------------------------------------

    def _worker(self):
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                job = self.queue.popleft()
                self.current = job
                self.cancel = False
            try:
                self._speak(job)
            except Exception as exc:
                print("speak error: %r" % (exc,), file=sys.stderr, flush=True)
            with self.cv:
                self.current = None

    def _announce(self, job):
        """Name the terminal a reply came from, once the answer is ambiguous."""
        if not job.label or len(self.seen_labels) < 2 or job.label == self.last_label:
            return ""
        return "From %s. " % job.label.replace("-", " ").replace("_", " ")

    def _adopt(self, proc):
        """Hand a player to stop(), unless a stop already arrived."""
        with self.cv:
            if self.cancel:
                proc.kill()
                return False
            self.player = proc
            return True

    def _release(self, proc):
        with self.cv:
            if self.player is proc:
                self.player = None

    def _play_file(self, pcm, rate, player):
        """One chunk through a file-only player. Blocks until it has played."""
        path = csaudio.write_temp_wav(pcm, rate)
        try:
            proc = subprocess.Popen([player, path], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if not self._adopt(proc):
                return False
            proc.wait()
            self._release(proc)
            return not self.cancel
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _speak(self, job):
        text = self._announce(job) + job.text
        self.last_label = job.label
        # A streaming player takes the whole reply down one pipe; a file-only
        # one (macOS afplay) takes a chunk at a time. Decided per reply, since
        # the rate is not known until the first chunk is synthesized.
        stream, file_player = None, csaudio.file_player()
        try:
            def create(piece):
                return self.kokoro.create(piece, voice=job.voice, speed=job.speed)

            def dropped(piece, exc):          # bad voice, phonemizer hiccup
                print("synth error: %r on %r" % (exc, piece[:60]),
                      file=sys.stderr, flush=True)

            for chunk in split_chunks(text):
                if self.cancel:
                    return
                # A chunk the model refuses is halved and retried rather than
                # skipped: dropping it takes a sentence out of the middle of a
                # reply with nothing audible to mark the gap.
                for samples, rate in synth_chunks(create, chunk, dropped):
                    if self.cancel:
                        return

                    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                    cmd = csaudio.stream_cmd(rate)

                    if cmd is None:
                        if file_player is None:
                            raise RuntimeError(csaudio.NO_PLAYER)
                        if not self._play_file(pcm, rate, file_player):
                            return
                        continue

                    if stream is None:
                        stream = subprocess.Popen(
                            cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if not self._adopt(stream):
                            return
                    try:
                        stream.stdin.write(pcm)
                        stream.stdin.flush()
                    except (BrokenPipeError, ValueError, OSError):
                        return
        finally:
            if stream is not None:
                try:
                    stream.stdin.close()
                    stream.wait(timeout=300)   # let the queue wait its turn
                except (BrokenPipeError, ValueError, OSError,
                        subprocess.TimeoutExpired):
                    pass
                self._release(stream)


def handle(conn, speaker):
    with conn:
        conn.settimeout(5)
        data = b""
        try:
            while b"\n" not in data:
                part = conn.recv(65536)
                if not part:
                    break
                data += part
        except socket.timeout:
            return

        reply = b"ok\n"
        for line in data.split(b"\n"):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            cmd = msg.get("cmd")
            if cmd == "say":
                if (msg.get("text") or "").strip():
                    reply = (speaker.submit(Job(msg),
                                            msg.get("mode") or "queue") + "\n").encode()
            elif cmd == "stop":
                speaker.stop()
            elif cmd == "status":
                reply = (json.dumps(speaker.status()) + "\n").encode()
        try:
            conn.sendall(reply)
        except OSError:
            pass


def main():
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(cspaths.MODEL, cspaths.VOICES)
    kokoro.create("ready", voice="af_heart", speed=1.0)   # warm the graph
    speaker = Speaker(kokoro)

    if os.path.exists(cspaths.SOCK):
        os.unlink(cspaths.SOCK)
    os.makedirs(os.path.dirname(cspaths.SOCK), exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(cspaths.SOCK)
    os.chmod(cspaths.SOCK, 0o600)
    server.listen(8)
    print("read-aloud daemon ready on %s" % cspaths.SOCK, flush=True)

    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=handle, args=(conn, speaker), daemon=True).start()
    finally:
        server.close()
        if os.path.exists(cspaths.SOCK):
            os.unlink(cspaths.SOCK)


if __name__ == "__main__":
    main()
