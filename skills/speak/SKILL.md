---
description: Read Claude's replies aloud — on, off, stop, speed, voice, hold, play (TTS output, not dictation)
argument-hint: "[on|off|stop|again|status|speed 1.2|voice af_bella|voices|max 4000|mode queue|same queue|hold on|guard test|sound off|queue|pending|play|play all|clear|read FILE|save FILE|test|audition|install|restart|log]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/claude-speak:*) Bash(claude-speak:*)
---

# Spoken-reply control

Controls whether Claude Code reads its replies aloud. This is the *output* side;
Claude Code's built-in `/voice` is the *input* side (dictation).

The user ran `/claude-speak:speak $ARGUMENTS`. The command has already executed:

!`"${CLAUDE_PLUGIN_ROOT}/bin/claude-speak" --session "${CLAUDE_SESSION_ID}" $ARGUMENTS`

## What to do

Report the result in **one short line**. Examples of the whole reply:

- `Voice on — af_heart at 1.0x.`
- `Voice off.`
- `Speed now 1.2x.`
- `3 replies waiting from api-server and GitHub.`
- `Reading README.md — about 4 minutes.`
- `Saved 6 MP3s to ~/audio — 48 minutes of audio.`
- `Repeating the last reply.`

Rules:

- No preamble, no explanation, no code blocks, no bullet lists, no follow-up offers.
- If the output above is empty, an error, or still contains a literal `$ARGUMENTS`,
  run `"${CLAUDE_PLUGIN_ROOT}/bin/claude-speak" --session "${CLAUDE_SESSION_ID}" $ARGUMENTS`
  yourself with Bash and report that result instead.
- If the output says to run `claude-speak install`, say so in one line — the neural
  voice model has not been downloaded yet.
- If the argument was not recognised the output already says so, names the
  nearest command and lists the rest. Pass it on as it is, in one line — do not
  add a list of your own, which is how the two drift apart.

Reference — settings live in `~/.config/claude-speak/config.json`, the model and
held replies in `~/.local/share/claude-speak/`, and the daemon is the systemd user
service `claude-speak.service` where systemd is available.
