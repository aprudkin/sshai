# sshai

Remote command execution on Windows and Linux servers for AI agents (Claude Code, Codex CLI),
designed around one constraint: **agent context is more expensive than CPU cycles**.

Raw `ssh host 'powershell -c "…"'` returns everything to the agent — banners, CLIXML wrappers,
blank lines, a hundred-thousand-line `Get-EventLog`. The agent pays a token for every byte and
keeps it until the end of the session. `sshai` owns transport and encodings, returns
budget-bounded output upward, and leaves the full result on disk — by reference, not in context.

The agent gets a compact *passport* instead of raw output:

```
$ sshai run pg-prod-01 -- journalctl -u postgres --since -1h
a17 host=pg-prod-01 exit=0 lines=8412 bytes=612K time=1.8s
file=~/.sshai/art/a17
tail3:
  ...
```

and then queries the artifact locally (`sshai q a17 -- grep -iE 'fatal'`, or its own grep — the
artifact is a plain local file). Repeat runs can return just the diff (`--delta`); a local run-log
survives context compaction.

## Status

Design approved, implementation pending. Architecture, CLI surface, and v1 scope:
`docs/superpowers/specs/2026-08-06-sshai-design.md`. Purpose and definition of done:
`docs/superpowers/specs/2026-08-06-sshai-charter.md`.

## Origin

A generalization of `/ps-ssh` (`~/.claude/scripts/ps_ssh.py`) — one Windows host, one shot. It
already paid for UTF-8 BOM, scp body delivery, pwsh 7 invocation, DefaultShell detection, CLIXML
filtering, head/tail truncation, and a status line as the source of truth. That experience is
ported, not rediscovered.

What `/ps-ssh` lacks and what this project exists for: Linux/bash targets, multiple hosts per
call, persistent session state (cwd/env) between calls, artifact-by-reference output with local
querying, deltas, and a searchable run-log.

## Implementation language

Go. A single static binary with millisecond startup — the agent invokes the tool hundreds of
times per session.

## CI

None yet. The repository is private on GitHub; unit-test CI and a license choice are queued for
the moment it goes public.
