# Charter: sshai

**Date:** 2026-08-06 (amended same day by `2026-08-06-sshai-design.md`, which governs on conflict)
**Issue:** [aimem#636](https://github.com/aprudkin/aimem/issues/636)
**Type:** agent-side CLI tool, no CI
**Remote:** `github.com/aprudkin/sshai` (private; intended to be open-sourced)
**Local:** `~/dev/sshai`
**Label:** `project:sshai`

## Purpose

Give an AI agent (Claude Code, Codex) one way to execute commands on Windows and Linux servers
over SSH, designed around the resource the agent runs out of first — **context**.

Raw `ssh` returns everything the remote host printed. For a human that is fine: the noise scrolls
by. For an agent every byte of output is tokens it pays for once and keeps until the end of the
session; one careless `Get-WinEvent` eats the window the whole task was supposed to fit in.
Context frugality is therefore not an optimization here but the type of the tool: `sshai` owns
transport and encodings, returns budget-bounded output upward, and leaves the full result on disk
by reference.

The second reason for a separate repository: this work already has accumulated experience living
in `~/.claude/scripts/ps_ssh.py` and the `/ps-ssh` command — UTF-8 BOM, body delivery via scp,
pwsh 7 invocation, DefaultShell detection, CLIXML filtering, head/tail truncation, a status line
as the source of truth instead of the process return code. Those are paid-for lessons. They are
ported into the project, not rediscovered.

## Form

A single executable tool with a uniform output contract for both OSes, invoked by the agent as an
ordinary shell command. Not a library, not a service.

*Amended 2026-08-06:* the tool still runs **no daemon of its own**, but on-disk state between
calls — artifacts, run-log, session state — and the ssh-managed ControlMaster background process
are in scope by design. The original "no state between calls" wording is superseded by the design
doc.

## Definition of done

**Version 1 is done when `/ps-ssh` can be declared deprecated.** That means, simultaneously:

1. **Parity.** Everything `/ps-ssh` does today on a single Windows host, `sshai` does no worse —
   including encodings, CLIXML, DefaultShell detection, and the status line.
2. **Linux.** bash targets work with the same invocation and the same output contract as Windows;
   interpreter choice is the tool's concern, not the caller's.
3. **Fan-out.** Multiple hosts in one call, no agent-side loop wrapper.
4. **Budget.** Aggregated multi-host output fits an explicit budget and carries a reference to the
   full on-disk result.

The detailed v1 scope (artifact passports, query-over-artifact, deltas, run-log, state
re-injection, readonly policy) lives in `2026-08-06-sshai-design.md`.

## Implementation language — resolved

**Go** (decided 2026-08-06; previously held open). A static binary with ~5–10 ms startup matters
at hundreds of invocations per session, and goroutines fit fan-out naturally. The price — porting
314 lines of `ps_ssh.py` (CLIXML/BOM/DefaultShell) with its control cases — was measured and
accepted. The follow-ups owed on this decision (CLAUDE.md commands section, code layout, `/sshai`
in `.gitignore`) are applied together with this amendment.

## Out of scope

- **Not** result interpretation. The tool returns text; what it means is the agent's job. Parsing
  output into structures is a separate decision, premature now.
- **Not** interactive scenarios. `Read-Host` and friends will hang; only non-interactive bodies.
- **Not** an SSH configuration replacement. Hosts, keys, and jumps are described in `ssh_config`;
  the tool reads it, never duplicates it.
- **Not** an orchestrator: no schedules, no task queues. (*Amended:* on-disk state between calls
  is now in scope — see Form above.)
- **No secrets** in the repository or in argv — locators only.
