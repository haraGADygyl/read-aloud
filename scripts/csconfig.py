#!/usr/bin/env python3
"""Config and held-reply plumbing for bin/claude-speak.

This exists so the CLI needs no `jq`. python3 is already required by every
other part of the plugin, so leaning on it removes a system package instead
of adding one — and it makes cstext.DEFAULTS the single place the settings
defaults are written down.

    csconfig.py config ensure          create or top up config.json
    csconfig.py config get <key>       one value, shell-friendly
    csconfig.py config set <key> <v>   v is JSON, else treated as a string
    csconfig.py config summary         the block `claude-speak status` prints

    csconfig.py held count [label]     "" or absent means every project
    csconfig.py held labels            "name (N waiting)" per project
    csconfig.py held pending <here>    the listing, current project marked
    csconfig.py held text [label]      one string to speak, oldest first
    csconfig.py held drop [label]      discard, "" for all

    csconfig.py last get <label>       the last reply spoken there, for `again`
    csconfig.py last drop <label>      forget it
    csconfig.py last session <id>      the last reply from one Claude session
    csconfig.py last mute <id>         its next reply answers a command: skip it

    csconfig.py suggest <typo> <cmd>…  the closest command name, or nothing
"""

import difflib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cspaths  # noqa: E402
from cstext import DEFAULTS  # noqa: E402

SUMMARY_KEYS = ("enabled", "engine", "voice", "speed", "maxChars",
                "multiSession", "sameSession", "holdReplies", "holdSound", "meetingGuard",
                "notify")

# Settings from a hand-rolled install that predates the plugin.
LEGACY = os.path.join(cspaths.HOME, ".claude", "speak.json")


def load():
    try:
        with open(cspaths.CONFIG) as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save(cfg):
    os.makedirs(os.path.dirname(cspaths.CONFIG), exist_ok=True)
    tmp = cspaths.CONFIG + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, cspaths.CONFIG)      # never leave a half-written config


def legacy_values():
    """Whatever a pre-plugin ~/.claude/speak.json can contribute."""
    try:
        with open(LEGACY) as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(old, dict):
        return {}
    out = {k: old[k] for k in DEFAULTS if k in old}
    for new, was in (("voice", "kokoroVoice"), ("speed", "kokoroSpeed")):
        if new not in out and was in old:
            out[new] = old[was]
    return out


def cmd_ensure():
    """Defaults underneath, the user's choices on top. Safe to run every time."""
    cfg = dict(DEFAULTS)
    if os.path.exists(cspaths.CONFIG):
        cfg.update(load())
    else:
        cfg.update(legacy_values())
    save(cfg)


def shell(value):
    """A value a shell can use: bare text, not JSON quoting."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def cmd_get(key):
    cfg = dict(DEFAULTS)
    cfg.update(load())
    print(shell(cfg.get(key)))


def cmd_set(key, raw):
    cfg = dict(DEFAULTS)
    cfg.update(load())
    try:
        cfg[key] = json.loads(raw)
    except ValueError:
        cfg[key] = raw                    # a bare word is a string
    save(cfg)


def cmd_summary():
    cfg = dict(DEFAULTS)
    cfg.update(load())
    print(json.dumps({k: cfg.get(k) for k in SUMMARY_KEYS}, indent=2))


def held_entries():
    out = []
    try:
        with open(cspaths.HELD) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and "text" in entry:
                    entry.setdefault("label", "")
                    entry.setdefault("ts", 0)
                    out.append(entry)
    except OSError:
        pass
    return out


def scoped(entries, label):
    return [e for e in entries if not label or e["label"] == label]


def cmd_held(argv):
    action = argv[0] if argv else "count"
    label = argv[1] if len(argv) > 1 else ""
    entries = held_entries()

    if action == "count":
        print(len(scoped(entries, label)))
    elif action == "labels":
        seen = {}
        for e in entries:
            seen[e["label"]] = seen.get(e["label"], 0) + 1
        for name in sorted(seen):
            print("  %s (%d waiting)" % (name, seen[name]))
    elif action == "pending":
        for e in entries:
            mark = " *" if e["label"] == label else "  "
            print("%s %s: %s…" % (mark, e["label"], e["text"][:66]))
    elif action == "text":
        chosen = sorted(scoped(entries, label), key=lambda e: e["ts"])
        multi = len({e["label"] for e in chosen}) > 1
        parts = []
        for e in chosen:
            if multi and e["label"]:
                parts.append("From %s. " % e["label"].replace("-", " ").replace("_", " "))
            parts.append(e["text"])
            parts.append(" ")
        print("".join(parts).strip())
    elif action == "drop":
        keep = [e for e in entries if label and e["label"] != label]
        try:
            os.makedirs(os.path.dirname(cspaths.HELD), exist_ok=True)
            with open(cspaths.HELD, "w") as fh:
                for e in keep:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            return 1
    else:
        print("unknown held action: %s" % action, file=sys.stderr)
        return 2
    return 0


def cmd_last(argv):
    action = argv[0] if argv else "get"
    label = argv[1] if len(argv) > 1 else ""
    path = cspaths.last_file(label)

    if action == "session":
        path = cspaths.session_file(label)
        action = "get"
    if action == "mute":
        path = cspaths.mute_file(label)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w"):
                pass                     # the mtime is the whole message
        except OSError:
            return 1
        return 0
    if action == "get":
        try:
            with open(path) as fh:
                sys.stdout.write(fh.read())
        except OSError:
            return 1                     # nothing spoken here yet
        return 0
    if action == "drop":
        try:
            os.unlink(path)
        except OSError:
            pass
        return 0
    print("unknown last action: %s" % action, file=sys.stderr)
    return 2


def cmd_suggest(argv):
    """The command someone probably meant, for the CLI's unknown-command line.

    difflib rather than an edit distance hand-rolled in awk — the shell
    already comes here for everything it cannot do itself. Nothing is printed
    when nothing is close: a confident wrong guess reads worse than no guess.
    """
    if len(argv) < 2:
        return 2
    hit = difflib.get_close_matches(argv[0], argv[1:], n=1, cutoff=0.6)
    if hit:
        print(hit[0])
    return 0


def main(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    group, rest = argv[0], argv[1:]
    if group == "config":
        action = rest[0] if rest else ""
        if action == "ensure":
            cmd_ensure()
        elif action == "get" and len(rest) > 1:
            cmd_get(rest[1])
        elif action == "set" and len(rest) > 2:
            cmd_set(rest[1], rest[2])
        elif action == "summary":
            cmd_summary()
        else:
            print("usage: csconfig.py config ensure|get <k>|set <k> <v>|summary",
                  file=sys.stderr)
            return 2
        return 0
    if group == "held":
        return cmd_held(rest)
    if group == "last":
        return cmd_last(rest)
    if group == "suggest":
        return cmd_suggest(rest)
    print("unknown group: %s" % group, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
