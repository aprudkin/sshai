---
name: sshai
description: Execute non-interactive sshai commands on Windows PowerShell hosts and Linux-family Bash or explicitly selected POSIX-shell hosts, or explicitly selected local Bash or pwsh, with bounded output and local artifacts. Use for covered command execution through an AI coding agent.
license: MIT
compatibility: Requires the sshai CLI; remote execution needs system OpenSSH and configured ssh_config aliases, while local execution needs bash or pwsh on PATH.
---

Use the installed `sshai` binary through the agent harness's non-interactive shell execution tool. It supports Windows PowerShell 7 or 5.1 and Linux-family hosts reachable through an `ssh_config` alias, plus explicit local Bash or PowerShell 7 (`pwsh`) execution. Linux-family remote execution defaults to Bash; select an explicit POSIX shell when the host, such as OpenWrt, does not provide Bash. Confirm availability with `command -v sshai`. Read `sshai help`, `sshai help run`, and `sshai help local` when a command, flag, or output contract is uncertain; the CLI does not provide a `--version` command.

## Execute

For a short command:

```bash
sshai run <host> -- <command>
```

For a multi-line body, keep the body out of argv and feed it through stdin or a private temporary file created with mode `0600` and removed after use:

```bash
sshai run --body-file - <host>
sshai run --body-file check.ps1 <host>
```

For a Linux-family host such as OpenWrt that lacks Bash, explicitly select its POSIX shell:

```bash
sshai run --posix-shell /bin/ash <host> -- <command>
```

Without `--posix-shell`, Linux-family execution remains `bash -s`. The selector accepts one non-empty path/token without whitespace or control characters. `sshai` safely quotes the selected interpreter and keeps the wrapped command body on stdin; never place a multi-line body or secret values in argv. The selector affects non-Windows hosts only, so Windows hosts in a mixed fan-out retain their PowerShell path. A missing selected shell is a genuine remote-command failure; never retry by silently falling back to Bash.

For a Windows body, omitting `--powershell-host` prefers `pwsh` (PowerShell 7) and falls back to the in-box Windows PowerShell 5.1 host when PowerShell 7 is unavailable. Select a host explicitly when the command requires its semantics; an explicit `pwsh` selection does not fall back:

```bash
sshai run --powershell-host pwsh --body-file check.ps1 windows01
sshai run --powershell-host windows-powershell --body-file check.ps1 windows01
```

The only supported values are `pwsh` and `windows-powershell`; an invalid selector is a usage error. The selector affects Windows body execution; Linux hosts in the same fan-out are unaffected. Do not describe Windows PowerShell 5.1 as unsupported.

For explicitly authorized local execution, select exactly one local interpreter. Both `bash` and `pwsh` are resolved only through `PATH`; local PowerShell runs only `pwsh` and has no Windows PowerShell 5.1 fallback:

```bash
sshai local --shell bash -- <command>
sshai local --shell bash --body-file -
sshai local --shell pwsh --body-file check.ps1
```

Use `--body-file <file|->` for multiline bodies so the body stays out of interpreter argv. Local execution is not SSH, a remote fallback, a readonly-policy check, an authorization layer, or a security sandbox. It rejects remote-only flags and `--follow`. It retains the same bounded artifacts, passports, JSON v1 envelope, `--delta`, shell/context state, history, `q`, and retention behavior as remote `run`; synthetic targets `local-bash` and `local-pwsh` appear in results and `log`, but never `hosts`. A normal local shell exit is mirrored. Interpreter `start`, `timeout`, and `output-limit` failures are stored as `local-error=<value>` and return process exit `96`; overflow retains the stream cap and `truncated=1`. Timeout or output overflow stops only the direct interpreter child, so cross-platform descendant-process cleanup is not guaranteed.

For one long-running host command, request an ephemeral structured event stream explicitly:

```bash
sshai run --follow <host> -- <command>
sshai run --follow --follow-interval 5 <host> -- <command>
```

Follow events are JSONL on stderr; the normal human passport or JSON v1 result remains on stdout. The interval is in seconds, defaults to `10`, and must be at least `1`. Follow mode accepts exactly one host. Treat heartbeats as truthful elapsed-time signals, not application progress. Live combined-output previews are bounded, may end with `output_suppressed`, and are not authoritative; use the saved artifact for complete captured evidence. The stream is not persisted and does not imply polling, replay, retry, or authorization.

Treat the passport status line as the source of truth. A Windows host where no supported PowerShell setup form can create its scratch directory reports `setup-error=windows-shell`, returns exit `99`, and does not run the user body or cache host facts; its artifact contains only a fixed diagnostic. A transport failure is reported as `transport-error=<class>` and may include a bounded canonical diagnostic. In human output, JSON output, and the saved artifact, only sanitized diagnostics are exposed; raw SSH or setup output, host keys, SSH configuration, algorithm offers, identities, and secrets are never passed through. Query a large stored result locally with `sshai q <id> -- <tool> <args>`; use `sshai diff` or `--delta` for repeated checks instead of loading or rerunning full output.

The transport never authorizes a server mutation. Retain the task's exact target, preconditions, rollback, and post-change verification. Get confirmation before remote, destructive, production, external, or hard-to-reverse actions not already authorized by the request.

## Boundaries and fallback

Do not invoke `ps_ssh.py`; it is archived and intentionally absent from active script paths.

- For file transfer, use the task's explicit `scp`, `sftp`, `rsync`, or backup workflow.
- For an interactive shell, REPL, prompt, or foreground stream, use an explicitly authorized interactive SSH workflow.
- Configure a stable `ssh_config` alias rather than passing an ad-hoc identity through a hidden helper.
- Prefer `ProxyJump`/`ssh_config` for two-hop access.
- If secret stdin or a two-hop shape is unsupported by `sshai`, stop and name the unsupported requirement. Continue only through a separately approved, purpose-built workflow; never restore or call the archived helper as an implicit fallback.

Raw `ssh` is an exception for a documented unsupported requirement, not a shorter alternative for command execution already covered by `sshai`.
