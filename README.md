# read-aloud

Claude Code reads its replies aloud, in a natural voice, entirely on your machine.

No API key, no cloud, no per-word billing. Speech is generated locally by
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), an 82M-parameter neural TTS
model, and it starts talking before the whole reply has finished rendering.

Built for people who'd rather listen than read a wall of text — and for anyone
whose eyes are done for the day.

```
/plugin marketplace add haraGADygyl/read-aloud
/plugin install read-aloud
```

Restart Claude Code — or run `/reload-plugins` — then fetch the voice model
(~338 MB), once:

```
/read-aloud:speak install
```

Claude Code namespaces every plugin command as `<plugin>:<skill>`, which is
why the name is that long one; the menu finds it as soon as you start typing.

Use it rather than a shell for this first run: the installer is what puts
`read-aloud` on your PATH, by linking it into `~/.local/bin`. After it
finishes, `read-aloud …` and `/read-aloud:speak …` are interchangeable.

That is the whole setup. No second restart — the Stop hook loaded with the
plugin, and it starts using the neural voice on the next reply.

### Coming from claude-speak

This plugin was called **claude-speak** until 0.10.0. Claude Code now refuses
third-party plugin names that start with `claude-`, so it was renamed. Updating
the old one will not get you here; swap it once:

```
claude plugin uninstall claude-speak@claude-speak
claude plugin marketplace remove claude-speak
claude plugin marketplace add haraGADygyl/read-aloud
claude plugin install read-aloud@read-aloud
```

Then restart Claude Code, or run `/reload-plugins`, in every open terminal.
Nothing is downloaded again. The first reply, or the first `read-aloud`
command, moves your model, settings, held replies and daemon over from the
old `claude-speak` folders, and removes the `claude-speak` command.
`/claude-speak:speak` is `/read-aloud:speak` from now on.

## Quiet by default

Nothing ever starts talking on its own. When a reply finishes you get a short
ding and a desktop notification; you hear it when *you* ask:

```
read-aloud play
```

That plays what *this* terminal produced; `play all` covers every project.
The quiet default exists because the alternative has a failure mode: a terminal
finishing mid-meeting and a voice announcing your code to a client. Two layers
prevent it.

**Replies are held.** They're stashed with a notification rather than spoken.
`read-aloud play` reads back what this terminal was working on, oldest first.
Prefer it to just talk? `read-aloud hold off`.

**The meeting guard.** Even with auto-speak on, nothing is spoken — and no ding
plays — while an application is recording from your microphone. If you're on a
call, you're not interrupted, whether or not you remembered to arm anything.

```
read-aloud guard test     # is the guard active right now, and what tripped it
read-aloud guard off      # disable it
```

It works by checking for live recording streams (PulseAudio/PipeWire source
outputs), so Zoom, Meet, Teams, Discord and browser calls all count. Playback
streams are a different thing entirely, so it never trips on its own audio.

---

## What you get

**A real voice, not a robot.** 54 Kokoro voices — American and British English
plus Spanish, French, Italian, Portuguese, Hindi, Japanese and Chinese. Hear the
best English ones and pick:

```
read-aloud audition
read-aloud voice bm_george
```

**It starts immediately.** A warm daemon keeps the model loaded, so there's no
2-second stall while something boots. The hook hands the reply off in about
30 ms and never blocks your session; long replies are then synthesized sentence
by sentence, and playback starts after the first one.

**It reads prose, not punctuation.** Code blocks become *"Code block"* instead of
sixty seconds of syntax. `src/api/parser.py:42` becomes *"parser.py line 42"*.
URLs become *"link"*. Markdown, tables, and emoji are stripped.

**You can ask for it again.** Missed the end of a reply, or the room got loud?

```
read-aloud again
```

It re-reads the last reply from this project, exactly as it was spoken —
already stripped of markdown, already cut to `maxChars`. It works whether the
reply was spoken or held, and asking twice restarts it rather than queueing.

Several terminals open in one project share that copy, so from a shell you get
whichever finished last. `/read-aloud:speak again` knows which session it was
typed in and reads that session's own last reply. Claude's one-line answer to
the slash command — *"Stopped speaking."* — is neither spoken nor remembered,
so it never becomes the reply `again` repeats.

**It reads more than replies.** Point it at a file and it reads that instead —
same voice, same markdown stripping:

```
read-aloud read RFC.md
git log -5 | read-aloud read -
```

It tells you how long the file will take before it starts, and `read-aloud
stop` ends it early. Binary files are refused rather than read aloud.

**It writes files you can take with you.** Point `save` at a document — or a
whole directory of them — and you get MP3s instead of sound:

```
read-aloud save docs/ -o ~/audio     # every .md in docs/, one MP3 each
read-aloud save RFC.md               # RFC.md -> RFC.mp3, here
read-aloud save notes.md -o trip.wav # no encoder on the machine? ask for wav
```

Nothing is played, so this is safe to run in an open-plan office. Each file
opens by speaking its own name, which is what tells you where you are when a
directory of them is playing in a car, and the model is loaded once for the
whole run. MP3 needs `ffmpeg` or `lame`; wav needs neither.

**It knows which terminal is talking.** With several Claude Code sessions open,
a reply from another project waits its turn and introduces itself — *"From api
server. The migration finished…"* — instead of cutting off whatever is speaking.
Replies from the *same* terminal queue too, so a run of subagents reporting
back is read one after another rather than each one cutting off the last —
`read-aloud same interrupt` goes back to only ever hearing the newest.

**It waits until you're back.** This is the default. Replies are stashed and
announced rather than spoken, so nothing surprises you:

```
read-aloud play              # this terminal's project only
read-aloud play all          # every project, announced by name
read-aloud play api-server   # one named project
read-aloud pending           # what's waiting, * marks this terminal's
```

`play` defaults to the project you're standing in, so a terminal only ever
reads back its own work. The project name is announced only when you're
hearing more than one.

---

## Controls

Every command works from a shell as `read-aloud …` or inside Claude Code as
`/read-aloud:speak …`.

| Command | What it does |
| --- | --- |
| `on` / `off` | Toggle reading replies aloud |
| `stop` | Shut up right now |
| `again` | Read the last reply again — you missed it |
| `speed 1.2` | 0.5 slow → 1.5 fast |
| `voice af_bella` | Switch voice (and preview it) |
| `voices` | List all 54 |
| `audition` | Play the 10 best English voices back to back |
| `test` | Speak a sample line in the current voice |
| `max 4000` | Characters read per reply before it stops |
| `hold on` / `off` | Stash replies (default) or speak them as they finish |
| `guard on` / `off` / `test` | Never speak while your microphone is in use |
| `sound <file>` | Ding when a reply lands — `off`, `default`, or a path |
| `play` / `play all` / `play <project>` | Read stashed replies — this terminal, everything, or one project |
| `pending` / `clear` | List or discard stashed replies (same scoping) |
| `read <file>` | Read any file aloud — `-` for stdin |
| `save <file\|dir>` | Write it to MP3 instead — `-o DIR` or `-o NAME.mp3` |
| `mode queue` | Multi-terminal behaviour: `queue`, `interrupt`, `drop` |
| `same queue` | One terminal's own run of replies: `queue`, `interrupt` |
| `queue` | What's speaking and what's waiting |
| `status` | Current settings |
| `restart` / `log` | Daemon control |
| `install` | Fetch the neural voice model — one time |

Inside Claude Code, `! read-aloud stop` is instant — the `!` prefix runs the
shell command without a model round trip, which matters when you want silence
*now*.

---

## Requirements

**`python3` and an audio player.** That is the whole list, and most machines
already have both.

Everything else lives in the plugin's own virtualenv: `kokoro-onnx` brings
`espeakng-loader`, which ships its own `libespeak-ng` and voice data, so
there is no system `espeak-ng` to install. The CLI does its own JSON, so there
is no `jq` either.

| | Debian/Ubuntu | macOS |
| --- | --- | --- |
| Audio player | `paplay`, `pw-play`, `aplay`, `ffplay` or `sox` — a desktop install almost always has one | `afplay`, built in |
| If none | the installer offers to `apt install pulseaudio-utils` | cannot happen |
| Virtualenv | `uv`, or `python3-venv` | `python3` is enough |
| Notifications | `notify-send` (`libnotify-bin`), optional | `osascript`, built in |
| `save` to MP3 | `ffmpeg` or `lame` — `-o name.wav` needs neither | `ffmpeg` or `lame`, via Homebrew |
| Meeting guard | works, via `pactl` | **unavailable** — no `pactl`, so the guard stays inert and hold mode is the protection |
| Daemon | systemd user service, warm across reboots | starts on demand, stays up for the login session |

The installer checks for a player before downloading anything, and offers to
install one when it is run from a terminal. It never installs packages without
asking — on Linux that needs root, and a text-to-speech plugin reaching for
`sudo` unprompted is not a thing that should happen quietly.

On macOS, `afplay` plays files rather than a stream, so each sentence becomes a
short temporary wav played to completion. Speech still starts after the first
sentence; it just is not one continuous pipe. `ffmpeg` or `sox` switches it to
the streaming path, but neither is needed.

Without the model installed, read-aloud falls back to `spd-say` / `espeak-ng`
(Linux) or `say` (macOS). It works immediately; it just sounds robotic until you
run `read-aloud install`.

---

## How it works

```
Claude finishes a reply
        │
        ▼
  Stop hook  scripts/speak-hook.py        applies your settings
        │    scripts/cstext.py            strips markdown — shared with "read"
        │
        ▼
  Client     scripts/say.py               unix socket, ~30 ms, exits immediately
        │
        ▼
  Daemon     scripts/kokorod.py           model stays warm; synthesizes chunk by
        │                                 chunk and streams straight to the player
        │    scripts/csaudio.py           picks the player and the way to feed it
        ▼
  paplay / pw-play / aplay / ffplay / sox — or afplay, one wav at a time, on macOS
```

The hook never blocks your session: it hands the text off and exits. If anything
in the chain fails, it fails silently rather than breaking Claude Code.

**Where things live** — deliberately outside the plugin directory, so a plugin
update never re-downloads 338 MB:

| Path | Contents |
| --- | --- |
| `~/.config/read-aloud/config.json` | Your settings |
| `~/.local/share/read-aloud/models/` | Kokoro model + voices |
| `~/.local/share/read-aloud/venv/` | Python environment |
| `~/.local/share/read-aloud/held.jsonl` | Replies waiting in hold mode |
| `~/.local/share/read-aloud/last/` | The most recent reply per project, and per session under `sessions/` (kept 30 days), for `again` |
| `~/.local/share/read-aloud/plugin-root` | Which plugin directory is installed, so the PATH link and the daemon survive an update |
| `$XDG_RUNTIME_DIR/read-aloud.sock` | Daemon socket |

Override with `READ_ALOUD_HOME` and `READ_ALOUD_CONFIG`.

The text cleaning is the part with edge cases — `src/api/parser.py:42` has to
shorten while `and/or` and `24/7` stay put. It has tests, and they need nothing
installed and play no audio:

```bash
python3 -m unittest discover tests
```

---

## Known limits

**Auto-play when you focus a terminal isn't supported.** It was tried and it
doesn't work reliably: gnome-terminal runs every window and tab under a single
process, so there's no way to tell which session you're looking at, and Claude
Code rewrites the terminal title continuously so a marker can't be planted there
either. Hold mode with manual playback is the honest alternative. On terminals
that use one process per window (kitty, alacritty, foot) focus detection would be
feasible — PRs welcome.

**Wayland** is untested. Nothing in the audio path is X11-specific, so it likely
works; the notification path depends on your desktop.

**One voice at a time.** Speech is serialized by design. Two replies never
overlap.

**The meeting guard needs `pactl`.** It detects recording streams through
PulseAudio/PipeWire. On a system using bare ALSA or JACK it can't see anything
and stays inert — hold mode is the fallback there.

---

## Uninstall

```
/plugin uninstall read-aloud
systemctl --user disable --now read-aloud.service
rm -rf ~/.local/share/read-aloud ~/.config/read-aloud
rm -f ~/.config/systemd/user/read-aloud.service ~/.local/bin/read-aloud
rm -f ~/.local/share/claude-speak ~/.config/claude-speak   # links left by the rename
```

---

## Credits

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad — Apache 2.0
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) by thewh1teagle — the
  ONNX runtime bindings and model releases this downloads

## License

MIT — see [LICENSE](LICENSE).
