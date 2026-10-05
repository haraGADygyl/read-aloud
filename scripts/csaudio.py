#!/usr/bin/env python3
"""Getting PCM to a speaker, or to a file, on whatever the machine happens to have.

Two shapes, because macOS has neither of the usual players:

  streaming — paplay, aplay, ffplay, sox read raw PCM on stdin, so one
              process can play a whole reply as it is synthesized
  file      — afplay, which ships with macOS, only plays files. Each chunk
              becomes a short wav played to completion. Still chunk by
              chunk, just not down one long pipe

Saving is the third shape: `Recorder` takes the same bytes and writes an mp3
through ffmpeg or lame, or a wav when the caller asks for one by name. Nothing
is played on that path — see scripts/csrender.py.

Standard library only, and no numpy: callers hand over bytes.
"""

import io
import os
import shutil
import subprocess
import tempfile
import wave


def stream_cmd(rate):
    """A player that accepts raw s16le mono on stdin, or None if there is none."""
    if shutil.which("paplay"):
        return ["paplay", "--raw", "--format=s16le",
                "--rate=%d" % rate, "--channels=1", "--client-name=ReadAloud"]
    if shutil.which("pw-play"):
        # Ships with PipeWire itself, unlike paplay, which needs a separate
        # pulseaudio-utils package that plenty of desktops do not install.
        return ["pw-play", "--format=s16", "--rate=%d" % rate, "--channels=1", "-"]
    if shutil.which("aplay"):
        return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1"]
    if shutil.which("ffplay"):
        # No channel flag: ffplay 7 removed -ac, ffplay 4 does not know
        # -ch_layout, and the raw-pcm demuxer defaults to mono on every
        # version. Naming a channel count here breaks one end or the other.
        return ["ffplay", "-loglevel", "quiet", "-nodisp", "-autoexit",
                "-f", "s16le", "-ar", str(rate), "-"]
    if shutil.which("sox"):
        return ["play", "-q", "-t", "raw", "-r", str(rate), "-e", "signed",
                "-b", "16", "-c", "1", "-"]
    return None


def file_player():
    """A player that takes a path instead of a pipe, or None."""
    return "afplay" if shutil.which("afplay") else None


def available():
    """True when audio can be played at all. Rate is irrelevant to the check."""
    return stream_cmd(22050) is not None or file_player() is not None


NO_PLAYER = ("no audio player found — install pulseaudio-utils, pipewire-bin, "
             "alsa-utils, ffmpeg or sox (macOS has afplay already)")


def wav_bytes(pcm, rate):
    """Wrap raw s16le mono samples in a wav container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def write_temp_wav(pcm, rate):
    """A wav on disk for a file-only player. The caller unlinks it."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="read-aloud-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(wav_bytes(pcm, rate))
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def play_blocking(pcm, rate):
    """Play one buffer, returning when it has finished. Used by the audition."""
    cmd = stream_cmd(rate)
    if cmd is not None:
        subprocess.run(cmd, input=pcm, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return
    player = file_player()
    if player is None:
        raise RuntimeError(NO_PLAYER)
    path = write_temp_wav(pcm, rate)
    try:
        subprocess.run([player, path], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---- saving to a file ----------------------------------------------------

BITRATE = "64k"      # mono speech; 24 kHz Kokoro has nothing above this to keep

NO_ENCODER = ("no mp3 encoder found — install ffmpeg or lame, or ask for a "
              "wav with: -o name.wav")


def encoder_cmd(path, rate, bitrate=BITRATE):
    """A command that reads raw s16le mono on stdin and writes `path`, or None.

    ffmpeg picks its codec from the extension, so an .m4a or .ogg asked for by
    name works without another branch here; libmp3lame is named explicitly for
    .mp3 because a build without it should fail loudly rather than silently
    write something else. lame only does mp3.
    """
    ext = os.path.splitext(path)[1].lower()
    if shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "-"]
        if ext == ".mp3":
            cmd += ["-codec:a", "libmp3lame"]
        return cmd + ["-b:a", bitrate, path]
    if ext == ".mp3" and shutil.which("lame"):
        # -s is in kHz, and lame defaults to big-endian for raw input on some
        # builds, so both ends of the sample are spelled out.
        return ["lame", "--quiet", "-r", "--signed", "--little-endian",
                "-s", "%g" % (rate / 1000.0), "--bitwidth", "16", "-m", "m",
                "-b", str(int(str(bitrate).rstrip("kK") or 64)), "-", path]
    return None


def encoder_available():
    return shutil.which("ffmpeg") is not None or shutil.which("lame") is not None


class Recorder:
    """Somewhere to put PCM: an encoder on a pipe, or a wav file.

    Chunks are written as they are synthesized rather than joined at the end —
    a long document would otherwise sit in memory at 48 KB a second while it
    rendered. `close()` returns the path; `abort()` throws the partial file
    away, which is what an interrupted render wants.
    """

    def __init__(self, path, rate, bitrate=BITRATE):
        self.path = path
        self.rate = rate
        self.frames = 0
        self.wav = None
        self.proc = None
        self.errlog = None
        if os.path.splitext(path)[1].lower() == ".wav":
            self.wav = wave.open(path, "wb")
            self.wav.setnchannels(1)
            self.wav.setsampwidth(2)
            self.wav.setframerate(rate)
            return
        cmd = encoder_cmd(path, rate, bitrate)
        if cmd is None:
            raise RuntimeError(NO_ENCODER)
        # A real error goes to a temp file rather than a pipe: at these log
        # levels an encoder says nothing until it fails, and a pipe nobody
        # reads is a deadlock waiting for a verbose build.
        self.errlog = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=self.errlog)

    @property
    def seconds(self):
        return self.frames / float(self.rate or 1)

    def write(self, pcm):
        self.frames += len(pcm) // 2
        if self.wav is not None:
            self.wav.writeframes(pcm)
            return
        self.proc.stdin.write(pcm)

    def close(self):
        if self.wav is not None:
            self.wav.close()
            self.wav = None
            return self.path
        if self.proc is None:
            return self.path
        proc, self.proc = self.proc, None
        try:
            proc.stdin.close()
        except OSError:
            pass
        rc = proc.wait()
        err = ""
        if self.errlog is not None:
            self.errlog.seek(0)
            err = self.errlog.read().decode("utf-8", "replace").strip()
            self.errlog.close()
            self.errlog = None
        if rc != 0:
            raise RuntimeError(err.splitlines()[-1] if err
                               else "encoder exited with status %d" % rc)
        return self.path

    def abort(self):
        """Give up on this file and remove it. Safe to call twice."""
        if self.wav is not None:
            try:
                self.wav.close()
            except Exception:
                pass
            self.wav = None
        if self.proc is not None:
            proc, self.proc = self.proc, None
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        if self.errlog is not None:
            self.errlog.close()
            self.errlog = None
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
        else:
            self.abort()
        return False


def audio_path(src, dest=None, ext=".mp3"):
    """Where one source file's audio goes.

    No destination means the current directory; a destination that is, or ends
    like, a directory means inside it; anything else is taken as the exact file
    to write, extension included — that is how `-o notes.wav` picks wav.
    """
    stem = os.path.splitext(os.path.basename(src.rstrip("/") or "audio"))[0] or "audio"
    if not dest:
        return stem + ext
    if os.path.isdir(dest) or dest.endswith("/") or os.path.splitext(dest)[1] == "":
        return os.path.join(dest, stem + ext)
    return dest


def unique_path(path, taken):
    """`notes.mp3`, then `notes-2.mp3` — two READMEs in one run, one directory.

    Only names claimed earlier in the same run are dodged. A file already on
    disk is overwritten, because asking to save again is asking for a new copy.
    """
    if path not in taken:
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while "%s-%d%s" % (stem, n, ext) in taken:
        n += 1
    return "%s-%d%s" % (stem, n, ext)
