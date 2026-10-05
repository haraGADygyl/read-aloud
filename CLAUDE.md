# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code plugin that reads Claude's replies aloud using a local neural TTS model (Kokoro via `kokoro-onnx`). Nothing leaves the machine at runtime. The repo is also its own plugin marketplace (`.claude-plugin/marketplace.json`), so **a push to `main` ships to anyone who installs or updates the plugin** — there is no separate release step.

## Commands

```bash
python3 -m unittest discover tests                      # everything (~1.5s)
python3 -m unittest tests.test_cstext.ShortenPaths      # one class
python3 -m unittest tests.test_cstext.Clean.test_lists_become_sentences   # one test

python3 scripts/cstext.py README.md      # what the cleaner produces — prints, never speaks
bash scripts/install.sh --no-service     # venv + 338 MB model, skipping the systemd unit
read-aloud restart | log | queue         # daemon control
```

No build, no linter, no CI. The tests need nothing installed and play no audio.

**Most other commands make real noise on the user's machine.** `read-aloud test|read|play|voice|speed|audition|install` and `say.py` all synthesize and play, and `read-aloud sound <file>` plays the file you hand it. Prefer `scripts/cstext.py` or the tests when verifying changes. `read-aloud save` is the exception among the model-loading commands: it renders to an mp3 and plays nothing, so it is safe to run — it just takes real seconds per file. To exercise the socket path without audio, bind a stub listener at `$XDG_RUNTIME_DIR/read-aloud.sock` — AF_UNIX paths cap near 108 bytes, so a deep scratchpad directory will fail to bind; use a short `/tmp` path.

## Two Python runtimes — the constraint that shapes everything

| Runs under | Files | May import |
| --- | --- | --- |
| System `python3` | `speak-hook.py`, `say.py`, `cstext.py`, `cspaths.py`, `csconfig.py`, `csaudio.py`, `csmigrate.py` | **stdlib only** |
| Venv `$DATA/venv/bin/python` | `kokorod.py`, `audition.py`, `csrender.py` | `numpy`, `kokoro_onnx` |

The Stop hook runs under whatever `python3` Claude Code happens to have and cannot see the venv. A third-party import anywhere in the first row breaks speech for every user — silently, for the reason below.

## Pipeline

```
Stop hook (hooks/hooks.json)
  → speak-hook.py    payload on stdin → cstext.clean() → hold or speak
  → say.py           unix-socket client, returns in ~30 ms
  → kokorod.py       warm model, sentence-chunked synthesis, streams PCM
  → paplay / pw-play / aplay / ffplay / sox — or afplay a wav at a time (macOS)
```

`speak-hook.py` wraps `main()` in a bare `except: pass` and always exits 0, because a broken hook must never break a Claude Code session. **Every failure is therefore invisible.** Debug by driving it directly:

```bash
export READ_ALOUD_HOME=/tmp/cs-test READ_ALOUD_CONFIG=/tmp/cs-test/config.json
printf '{"session_id":"s","cwd":"/tmp/proj","last_assistant_message":"hi"}' \
  | python3 scripts/speak-hook.py
```

Set those two env vars for anything that writes state. Without them you scribble on the user's real config and their queue of held replies.

They redirect `$DATA` and the config — **not `$HOME`**. `bin/read-aloud` repairs the PATH link and the systemd unit under `$HOME` from a pointer inside `$DATA`, so a scratch run with only these two set once re-pointed the live daemon at a git working tree and left it failing `203/EXEC`. `heal()` now returns early when either variable is set; redirect `HOME` and `XDG_RUNTIME_DIR` as well when driving the CLI in a test, or the socket you reach is the real daemon and it will speak.

## State lives outside the plugin directory

`cspaths.py` is the only place paths are defined. Runtime state sits in `~/.local/share/read-aloud/` and `~/.config/read-aloud/` deliberately: a plugin update replaces the plugin directory, and the 338 MB model has to survive that.

## It was claude-speak until 0.10.0

Claude Code 2.1.289 began refusing third-party plugin names that start with `claude-` — `claude plugin tag` failed validation on 0.9.2 — so everything carrying the name was renamed: plugin, marketplace, CLI, data and config directories, socket, systemd unit, and the env vars (`CLAUDE_SPEAK_HOME`/`_CONFIG` are still honoured as fallbacks). The `cs` prefix on the script modules is the old name too; it was left alone because nothing outside the repo sees it.

An existing user cannot get here by `claude plugin update` — the old marketplace no longer lists a plugin by the old name — so the README walks them through uninstall and reinstall. What they had is carried over without asking:

- `scripts/csmigrate.py` renames `~/.local/share/claude-speak` and `~/.config/claude-speak` to the new names and leaves a symlink at each old path, because a terminal that has not been restarted still runs the 0.9.2 hook and writes there. It runs first in the Stop hook (before `record_root` would create the new data directory and leave the old one nowhere to go), first in the CLI (before `ensure_cfg` would write a fresh config) and at the top of `install.sh`
- `cs_unit_migrate` in `csheal.sh` disables and deletes `claude-speak.service` and writes `read-aloud.service`, enabled only if the old one was. Its `ExecStart` uses the pointer when it names a read-aloud copy and the caller's otherwise — the carried-over pointer names the claude-speak copy until the new hook runs, and `cs_scripts_from_pointer` rejects a directory with no `bin/read-aloud`
- `cs_link_retire` removes `~/.local/bin/claude-speak` when it is the link install.sh made
- While `claude-speak@…` is still in `installed_plugins.json`, every CLI run says so: both plugins hook Stop, and every reply is read twice

All of it is skipped under a redirect, like `heal()`. `tests/test_migrate.py` drives it with a stub `systemctl` on PATH — the real unit has the same name, and reaching it would stop the user's daemon.

## The plugin directory moves on every update

Claude Code installs a plugin into a version-stamped directory — `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` — and `${CLAUDE_PLUGIN_ROOT}` changes each time it updates. The old directory is left on disk and still resolves.

Two things written at install time name that directory absolutely, and both went stale:

- `~/.local/bin/read-aloud`, the symlink that puts the CLI on the user's PATH
- `ExecStart=` in the systemd unit

The second is the one that bit: a systemd user who ran `claude plugin update` kept launching the *previous* release's `kokorod.py` while the Stop hook ran the new code, so a fix shipped in the daemon never arrived and nothing looked wrong. `install.sh` was the only thing that rewrote either, and nobody re-runs it after an update.

Several terminals are usually open, each holding the release it started with until it is restarted or `/reload-plugins` is run — so the pointer has more than one writer. The hook only overwrites it when its own version is at least the recorded one (`cspaths.plugin_version`, compared as numbers so 0.10 beats 0.9), because last-writer-wins meant an old session dragged the link and the daemon back to its release and restarted them, once per reply. A pointer naming a directory with no readable manifest is stale by definition and gets replaced.

`scripts/csheal.sh` owns both, and `bin/read-aloud` calls it on every invocation. The direction of repair is the whole design: the Stop hook writes its own location to `$DATA/plugin-root` (it is the only part that always runs from the installed copy), and repairs go **towards that pointer, never towards the caller** — an out-of-date CLI fixing things to point at itself would pin the daemon to the release it came from. A link that is not ours is still left alone.

When the repair restarts the daemon, the CLI waits for the socket to answer before going on — `systemctl restart` returns as soon as the process is exec'd, but Kokoro needs a second to map, and the `status` block a few lines later would otherwise report a healthy daemon as stopped. The wait is bounded at two seconds and only happens on the run that restarted it, which is why `cs_unit_sync` distinguishes 13 (rewritten and restarted) from 10 (rewritten).

Inside Claude Code none of this matters: a plugin's `bin/` is added to the Bash tool's PATH automatically, always at the current version.

## Adding a config setting means editing one file

`DEFAULTS` in `scripts/cstext.py`, and nothing else. `scripts/csconfig.py` writes it out, and `ensure_cfg` in `bin/read-aloud` runs `csconfig.py config ensure` on every invocation, so an upgrade adds new keys without touching choices the user already made.

The one exception is display: `read-aloud status` prints the keys named in `SUMMARY_KEYS` in `scripts/csconfig.py`, so a new setting exists and is honoured without being listed there, but stays invisible in the status block until it is added.

It used to be three files — the Python dict plus a `DEFAULT_CFG` heredoc in `bin/read-aloud` and another in `install.sh` — and they drifted, which is how `fallbackRate` and `fallbackVoice` stayed absent for four releases. `NoDuplicateDefaults` in the tests fails if a JSON heredoc reappears in either shell file.

## The shell shells out for JSON

`bin/read-aloud` uses no `jq`. Every read, write and held-reply query goes through `scripts/csconfig.py` (`config get|set|ensure|summary`, `held count|labels|pending|text|drop`, `suggest <typo> <cmd>…`). python3 is a hard dependency of the plugin already, so this removed a system package rather than adding one. `NoDuplicateDefaults.test_the_shell_does_not_need_jq` keeps it that way.

## Getting the command wrong

Bad input is a first-class output. A missing argument used to land on bash's own `${2:?...}`, which prints the script path and a line number before the usage and exits 1 rather than 2; `usage()` replaces it everywhere. An unrecognised command gets `csconfig.py suggest`, which is `difflib.get_close_matches` — near enough to turn `spedd` into `speed`, quiet when nothing is close, because a confident wrong guess reads worse than no guess.

Both print on **stdout, not stderr**. `/read-aloud:speak` reports whatever the binary printed, and a message that went only to stderr reaches the user as an empty reply.

## Behaviour that looks like a bug but is deliberate

- **Quiet by default.** `holdReplies` is true: replies are appended to `held.jsonl` (O_APPEND, so concurrent sessions don't interleave) and announced with a ding, not spoken. `read-aloud play` reads them back.
- **The meeting guard outranks every other setting.** If `pactl list source-outputs` shows a recording stream, the reply is held with *no* sound at all — the ding is suppressed too.
- **Hold scoping is by directory basename.** The hook labels each reply `basename(cwd)`; `play`/`clear`/`pending` default to that label, which is how one terminal reads back only its own project.
- **Two axes decide what waits.** `multiSession` governs a reply from *another* terminal: it queues behind and is introduced as "From \<project\>", or interrupts, or is dropped. `sameSession` governs a terminal's own next reply — `queue` by default, so a run of subagents reporting back is read in order. `mode` deliberately does not reach past `sameSession`: `multiSession: interrupt` must not talk over your own burst.
- **`again` reads a copy, not the transcript.** The hook writes each reply to `$DATA/last/<label>.txt` after cleaning and after the maxChars cut, so a repeat is the same words rather than nearly the same words — and the CLI needs no session id and no knowledge of where Claude Code keeps transcripts. `cspaths.last_file` maps the label to a filename, which is why a project called `..` cannot write outside `$DATA`.
- **The spoken queue holds eight.** `MAX_QUEUE` in `kokorod.py` caps waiting replies at 8 and drops the *oldest* beyond that, so a ninth reply arriving during a long one loses the first. The number bounds staleness rather than memory — a queued reply is a few KB of text, but eight of them are the best part of ten minutes of talking. It was 3 while a session's replies still interrupted each other; `sameSession: queue` made a burst of subagents fill it. Held replies (`held.jsonl`) have no such cap — this applies only to speech in flight.

## The text cleaner is the fragile part

`clean()` in `scripts/cstext.py` turns markdown into speech, and its regexes have caused every behavioural bug in this repo so far:

- Line-anchored rules must use `[ \t]` for indentation, **never `\s`**. Under `re.MULTILINE`, `\s*` reaches backwards over the blank line above and eats the paragraph break — the very thing that later becomes a full stop.
- The path rule must leave `and/or`, `TCP/IP` and `24/7` alone. That is why a *relative* path is only recognised when it ends in a file extension; a rooted path (`/`, `~/`, `./`, `../`) has no such requirement.

Any change here needs a case in `tests/test_cstext.py`, including the ones that must *not* change.

## Surfaces to keep in sync

Adding a user-visible command touches four places:

1. `bin/read-aloud` — the `case` branch and the header comment (which *is* the `--help` output, printed by awk up to the first non-comment line). Nothing else: the list of valid commands offered after a typo is read back out of the `case` statement by `commands()`, because the hand-kept copy that used to live in the `*)` branch went stale — `save` shipped absent from it. `NoHandKeptList` in `tests/test_cli_errors.py` holds the two in agreement, and fails if a branch is added with no help line
2. `skills/speak/SKILL.md` — `argument-hint`. This is the `/read-aloud:speak` slash command — Claude Code namespaces every plugin command as `<plugin>:<skill>`, and that full name is the only one that exists; it has `disable-model-invocation: true` and runs the binary via `!` frontmatter, so Claude's whole job is to report the result in one line
3. `README.md` — the Controls table
4. `.claude-plugin/plugin.json` — **bump `version` in the same commit.** Every release so far has done this: minor for a feature, patch for a fix

## Releasing

The version bump *is* the release. Three steps after pushing it:

```bash
# 1. tag — refuses to run unless plugin.json and the marketplace entry agree,
#    which is the check worth having, since the two are edited separately
claude plugin tag -m "read-aloud %s — <one line>" --push       # --dry-run first

# 2. release notes — write them to a file, then
gh release create read-aloud--v<version> --verify-tag \
  --title "<version> — <two or three words>" --notes-file <path>

# 3. ship it
claude plugin update read-aloud@read-aloud                   # bare name fails
```

Write the notes for someone who *uses* the plugin: what changed in what they hear, not which file moved. The commit bodies are the source — `--notes-from-tag` only picks up the one-line annotation. Past releases are the model for length and tone.

Two traps, both hit while backfilling the first seven:

- `--verify-tag` followed by an empty argument makes `gh` read `""` as an asset path and fail with `stat : no such file or directory`. Watch for empty shell variables in that position
- `gh` marks **Latest** by creation time, not version. Releasing in order is fine; a bulk backfill needs `gh release edit <tag> --latest` afterwards

Tags run back to 0.1.0 — named `claude-speak--v<version>` up to 0.9.2, from before the rename — each marking the last commit carrying that version — except `v0.3.0`, cut at `f7888ac`, because the three commits after it shipped the `read` feature without a bump and belong to the 0.4.0 range.

A session restart — or `/reload-plugins`, which reloads plugins, skills and hooks in place — is needed after step 3 for the Stop hook to pick up the new code. Nothing is needed after `read-aloud install`: the hook re-reads its config and re-checks for the model on every reply.

## Commit style

Conventional Commits, enforced by `.githooks/commit-msg`. Enable it once per clone — git does not version-control `.git/hooks`:

```bash
git config core.hooksPath .githooks
```

```
<type>[optional scope][!]: <description>     # 72 chars max, ! marks a breaking change

feat(cli): read any file aloud with read-aloud read
fix(cleaner): keep the paragraph break before a heading
chore(release): 0.4.0
```

Types: `feat fix docs style refactor perf test build ci chore revert`. Scope is free-form; `cleaner`, `hook`, `daemon`, `cli` match the layout here.

The body keeps this repo's existing shape: blank line, a paragraph on **why**, then bullets on what. Merge, revert and `fixup!`/`squash!` subjects are passed through untouched. `git commit --no-verify` bypasses the hook; `tests/test_commit_msg.py` covers it.

Commits before `d3de1cd` predate the convention and do not follow it. History is public — do not rewrite it to match.
