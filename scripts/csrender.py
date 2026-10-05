#!/usr/bin/env python3
"""Render documents to audio files — the offline half of read-aloud.

Runs under the venv python, like the daemon, because it needs numpy and
kokoro_onnx. It plays nothing: every sample goes to an mp3 (or a wav) you can
copy to a phone. Reached through `read-aloud save`.

    csrender.py --voice af_heart --speed 1.0 -o ~/audio docs/ README.md

The model is loaded once for the whole run, so a directory of twenty documents
pays the two-second startup once rather than twenty times.
"""

import argparse
import contextlib
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cspaths  # noqa: E402
import csaudio  # noqa: E402
# Cleaning and chunking are stdlib-only and shared with the hook, so a saved
# file sounds exactly like the same file read aloud.
import cstext  # noqa: E402

# What counts as a document when a directory is named. Anything else in there
# is left alone rather than guessed at.
DOC_EXTS = (".md", ".markdown", ".txt", ".rst", ".mdx")


@contextlib.contextmanager
def quiet_stderr():
    """Mute fd 2 for the model load: onnxruntime greets every start with
    warnings about providers nobody asked for, and they are not this tool's
    output. Done at the file-descriptor level because the noise comes from
    native code, which never sees sys.stderr."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def hms(seconds):
    seconds = int(seconds + 0.5)
    if seconds >= 3600:
        return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)
    if seconds >= 60:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    return "%ds" % seconds


def human_size(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return "?"
    return "%.1f MB" % (size / 1048576.0) if size >= 1048576 \
        else "%d KB" % (size / 1024.0)


def title_of(path):
    """A spoken heading, so you know which document just started in the car."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.replace("-", " ").replace("_", " ").strip()


def expand(args):
    """Turn the arguments into a list of files, directories included.

    Claude Code's @-completion pastes the path with an @ in front, so
    `save @docs/plan.md` arrives here literally; the @ is dropped only when
    that is what makes the path exist, in case a file really is named @plan.
    """
    files, problems = [], []
    for arg in args:
        if arg.startswith("@") and not os.path.exists(arg) and os.path.exists(arg[1:]):
            arg = arg[1:]
        if os.path.isdir(arg):
            found = sorted(
                os.path.join(arg, name) for name in os.listdir(arg)
                if name.lower().endswith(DOC_EXTS)
                and os.path.isfile(os.path.join(arg, name)))
            if not found:
                problems.append("nothing to read in %s — no %s files in it"
                                % (arg, ", ".join(DOC_EXTS)))
            files.extend(found)
        elif os.path.isfile(arg):
            files.append(arg)
        else:
            problems.append("no such file: %s" % arg)
    return files, problems


def render(kokoro, src, out, voice, speed, say_title=True, show_progress=False):
    """One document to one audio file. Returns (path, seconds) or None."""
    try:
        raw = cstext.read_source(src)
    except (OSError, ValueError) as exc:
        print("  skipped %s: %s" % (src, exc))
        return None

    text = cstext.clean(raw, cstext.load_config())
    if not text.strip():
        print("  skipped %s: nothing to read in it" % src)
        return None
    if say_title:
        text = title_of(src) + ". " + text

    chunks = cstext.split_chunks(text)
    rec = None
    try:
        def create(piece):
            return kokoro.create(piece, voice=voice, speed=speed)

        def dropped(piece, exc):
            print("  (skipped a line of %s: %r)" % (os.path.basename(src), exc))

        for i, chunk in enumerate(chunks):
            # Halved and retried when the model refuses it — a gap in a file
            # you listen to later is one you cannot hear is there.
            for samples, rate in cstext.synth_chunks(create, chunk, dropped):
                if rec is None:               # the rate is only known now
                    rec = csaudio.Recorder(out, rate)
                rec.write((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
            if show_progress and rec is not None:
                sys.stdout.write("\r  %s  %d%%  %s"
                                 % (os.path.basename(out),
                                    (i + 1) * 100 // len(chunks),
                                    hms(rec.seconds)))
                sys.stdout.flush()
        if rec is None:
            print("  skipped %s: nothing could be synthesized" % src)
            return None
        seconds = rec.seconds
        rec.close()
    except BaseException:                     # includes Ctrl-C: no half files
        if rec is not None:
            rec.abort()
        raise
    finally:
        if show_progress:
            sys.stdout.write("\r\033[K")
    return out, seconds


def main():
    ap = argparse.ArgumentParser(
        prog="read-aloud save",
        description="Write documents to audio files you can take with you.")
    ap.add_argument("sources", nargs="+", metavar="FILE|DIR")
    ap.add_argument("-o", "--out", metavar="DEST",
                    help="a directory to fill, or one exact file name "
                         "(.wav there saves a wav instead of an mp3)")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--no-title", action="store_true",
                    help="do not speak the file name before the text")
    args = ap.parse_args()

    files, problems = expand(args.sources)
    for problem in problems:
        print(problem)
    if not files:
        return 2
    dest = args.out
    # A destination with no extension, or a trailing slash, is a directory to
    # fill; anything else is one exact file, which only works for one document.
    dest_is_dir = bool(dest) and (os.path.isdir(dest) or dest.endswith("/")
                                  or os.path.splitext(dest)[1] == "")
    if dest and not dest_is_dir and len(files) > 1:
        print("-o %s names one file, but there are %d documents — "
              "name a directory instead" % (dest, len(files)))
        return 2
    if dest_is_dir:
        os.makedirs(dest, exist_ok=True)

    ext = ".mp3" if dest_is_dir or not dest \
        else (os.path.splitext(dest)[1].lower() or ".mp3")
    if ext != ".wav" and not csaudio.encoder_available():
        print(csaudio.NO_ENCODER)
        return 1

    if not cspaths.kokoro_ready():
        print("run 'read-aloud install' first")
        return 1
    print("loading the voice model…", flush=True)
    with quiet_stderr():
        from kokoro_onnx import Kokoro
        kokoro = Kokoro(cspaths.MODEL, cspaths.VOICES)

    progress = sys.stdout.isatty()
    taken, done, total = set(), [], 0.0
    for src in files:
        out = csaudio.unique_path(csaudio.audio_path(src, dest, ext), taken)
        taken.add(out)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            result = render(kokoro, src, out, args.voice, args.speed,
                            say_title=not args.no_title, show_progress=progress)
        except KeyboardInterrupt:
            print("stopped — %d file(s) written" % len(done))
            return 130
        except (RuntimeError, OSError) as exc:
            print("  failed on %s: %s" % (src, exc))
            continue
        if result is None:
            continue
        path, seconds = result
        total += seconds
        done.append(path)
        print("  %s — %s, %s" % (path, hms(seconds), human_size(path)))

    if not done:
        print("nothing written")
        return 1
    print("%d file(s), %s of audio in %s"
          % (len(done), hms(total),
             os.path.dirname(done[0]) or os.path.abspath(".")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
