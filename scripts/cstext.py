#!/usr/bin/env python3
"""Settings, and turning markdown into something worth listening to.

Shared by the Stop hook and `read-aloud read`, so a file you ask for aloud is
cleaned up exactly the way a reply is. Standard library only: the hook runs on
whatever python3 the system provides.

As a filter it cleans a file, or stdin when the path is "-":

    cstext.py notes.md
    git log -5 | cstext.py -
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cspaths  # noqa: E402

DEFAULTS = {
    "enabled": True,
    "engine": "auto",           # auto | kokoro | say | spd-say | espeak-ng
    "voice": "af_heart",
    "speed": 1.0,
    "maxChars": 2500,
    "announceCode": True,       # say "Code block" instead of reading code
    "shortenPaths": True,       # /a/b/c/file.py:42 -> "file.py line 42"
    # Several Claude Code terminals sharing one daemon:
    #   queue     — finish the current reply, then the next, announcing whose it is
    #   interrupt — the newest reply always cuts off whatever is speaking
    #   drop      — ignore replies arriving while another session speaks
    "multiSession": "queue",
    # What one terminal's own replies do to each other when a second lands
    # while the first is still being read — subagents reporting back, or a
    # reply you interrupted and asked again:
    #   queue     — finish the first, then read the second
    #   interrupt — the newer reply is the one you want; cut the old one off
    "sameSession": "queue",
    # Replies are held by default: stashed with a notification rather than
    # spoken. Nothing ever starts talking unless you asked it to. Turn this off
    # (`read-aloud hold off`) to have replies read out as they finish.
    "holdReplies": True,
    "notify": True,
    # Short sound when a reply lands in hold mode. Path to an audio file, or
    # "off" for silence. Empty string picks a sensible system sound.
    "holdSound": "",
    # Never speak while something is recording from the microphone — you are on
    # a call. Applies even with holdReplies off, and suppresses the ding too, so
    # a meeting stays quiet. See `read-aloud guard`.
    "meetingGuard": True,
    # Recording apps that should NOT count as "in a meeting" (substring match,
    # case-insensitive), e.g. an always-on hotword listener.
    "meetingGuardIgnore": [],
    # Fallback engine tuning (only used when Kokoro is not installed).
    "fallbackRate": 25,
    "fallbackVoice": "female1",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(cspaths.CONFIG) as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return cfg


EMOJI = re.compile("[\U0001f000-\U0001faff←-⇿⌀-➿⬀-⯿️]")

# A file path, so it can be read as "parser.py line 42" instead of spelled out.
#
# The lookbehind is what keeps a relative path whole: without it the match
# starts at the first slash and src/api/parser.py comes out as "srcparser.py".
#
# A relative path also has to end in an extension. Nothing else separates
# src/api/parser.py from "and/or", "TCP/IP", "he/she" or "24/7", and turning
# those into "or", "IP", "she" and "7" is worse than leaving a path long.
PATH = re.compile(r"""
    (?<![\w.\-/])                       # a fresh token, never mid-path
    (?:
        (?:~|\.{1,2})?/(?:[\w.\-]+/)*   # rooted:  /a/b/   ~/a/   ./a/   ../a/
        ([\w.\-]+)                      #   any final name — /usr/bin/ls counts
      |
        (?:[\w.\-]+/)+                  # relative: src/api/
        ([\w.\-]*\.[A-Za-z]\w{0,4})     #   extension required, see above
    )
    (?::(\d+))?                         # :42
""", re.VERBOSE)


def _shorten_path(m):
    name = m.group(1) or m.group(2)
    return name + (" line " + m.group(3) if m.group(3) else "")


def clean(text, cfg):
    marker = " . Code block. " if cfg["announceCode"] else " "
    text = re.sub(r"```.*?```", marker, text, flags=re.DOTALL)
    text = re.sub(r"```.*", marker, text, flags=re.DOTALL)      # unterminated fence

    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)      # links keep the label
    text = re.sub(r"https?://\S+", " link ", text)

    text = re.sub(r"`([^`]*)`", r"\1", text)
    # Leading indentation is [ \t], never \s: under re.MULTILINE, \s* reaches
    # back over the blank line above and swallows the paragraph break, which is
    # what later becomes a full stop. "# Title\n\n> quote" ran the two together.
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*>[ \t]?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", "", text,
                  flags=re.MULTILINE)                               # --- or *****
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)", r"\1", text)

    text = re.sub(r"^[ \t]*[-*+][ \t]+", ". ", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*\d+[.)][ \t]+", ". ", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*\|.*\|[ \t]*$", " ", text, flags=re.MULTILINE)  # tables

    if cfg["shortenPaths"]:
        text = PATH.sub(_shorten_path, text)

    text = EMOJI.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"(\.\s*){2,}", ". ", text)
    # A paragraph break adds a full stop, so a line already ending in
    # punctuation gets two: "Here's the plan:." before a list.
    text = re.sub(r"([,;:!?])\s*\.", r"\1", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"^[\s.]+", "", text)     # a reply opening with a list led with ". "
    # Punctuation and nothing else is not worth a voice: a blank source
    # collapses to a lone ".", which would be held and announced like a reply.
    return text.strip() if re.search(r"\w", text) else ""


CHUNK_CHARS = 260   # synthesis granularity; playback starts after chunk 1


def split_chunks(text, limit=CHUNK_CHARS):
    """Sentence-sized pieces, in the order they appear in the text.

    Order is the whole contract. The daemon writes each piece to the player as
    soon as it is synthesized, so a piece emitted out of turn is *heard* out of
    turn — a sentence goes missing, the next one plays, then the missing one
    turns up late.
    """
    parts = re.split(r"(?<=[.!?:;])\s+", text)
    chunks, cur = [], ""
    for part in parts:
        while len(part) > limit:                  # a single monster sentence
            cut = part.rfind(" ", 0, limit)
            if cut <= 0:                          # no space to break on
                cut = limit
            if cur:
                # Flush the buffer first. Appending these slices straight to
                # chunks is what used to let a long sentence jump the queue
                # ahead of the short one before it.
                chunks.append(cur)
                cur = ""
            chunks.append(part[:cut].strip())
            part = part[cut:].lstrip()
        if len(cur) + len(part) + 1 <= limit:
            cur = (cur + " " + part).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = part.strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


MAX_SPLITS = 3      # 260 characters down to ~32 before giving up on a chunk


def split_in_two(text):
    """A chunk halved at the space nearest its middle."""
    mid = len(text) // 2
    left = text.rfind(" ", 0, mid)
    right = text.find(" ", mid)
    cut = right if left < 0 else (left if right < 0 or mid - left <= right - mid
                                  else right)
    if cut <= 0:
        cut = mid
    return text[:cut].strip(), text[cut:].strip()


def synth_chunks(create, chunk, on_error=None, depth=MAX_SPLITS):
    """Synthesize one chunk, halving it and retrying when the model refuses.

    Kokoro truncates its input at 510 phonemes and then indexes past the end
    of its own array, so a chunk that is short in characters but long in
    phonemes raises IndexError. Both callers used to skip that chunk, which
    lost a sentence or two out of the middle of a document without anything
    audible marking the gap — the worst failure this pipeline can have, since
    you cannot hear what is missing.

    Characters are a poor proxy for phonemes, so the limit cannot be enforced
    up front; halving finds it. Order is preserved, because a piece returned
    out of turn is heard out of turn — the same contract as split_chunks.
    """
    try:
        return [create(chunk)]
    except Exception as exc:
        if depth <= 0 or " " not in chunk.strip():
            if on_error is not None:
                on_error(chunk, exc)
            return []
        left, right = split_in_two(chunk)
        return (synth_chunks(create, left, on_error, depth - 1)
                + synth_chunks(create, right, on_error, depth - 1))


def read_source(path):
    """A file, or stdin for "-". Binary is refused rather than read aloud."""
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        with open(path, "rb") as fh:
            data = fh.read()
    if b"\x00" in data:
        raise ValueError("looks like a binary file")
    return data.decode("utf-8", errors="replace")


def main():
    if len(sys.argv) != 2:
        print("usage: cstext.py <file>|-", file=sys.stderr)
        return 2
    try:
        raw = read_source(sys.argv[1])
    except (OSError, ValueError) as exc:
        print("cstext: %s: %s" % (sys.argv[1], exc), file=sys.stderr)
        return 1
    sys.stdout.write(clean(raw, load_config()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
