# sshai — agent usage

`sshai` runs commands on remote Linux hosts through Bash by default or an explicitly selected POSIX shell, and on Windows hosts through PowerShell. `sshai local` explicitly runs local Bash or PowerShell 7 (`pwsh`) without SSH. It returns a compact passport plus an on-disk artifact path instead of raw output in context. Captured output stays on disk for local querying; overflow beyond the configured stream cap is discarded and marked `truncated=1`.

- `sshai run <host> -- <command>` — execute remotely; e.g. `sshai run web01 -- df -h`
- `sshai local --shell <bash|pwsh> -- <command>` — execute through the selected local interpreter
- `sshai q <id> -- <tool> <args>` — query a stored artifact; its path is appended as the tool's final argv argument, never sent on stdin
- `sshai diff <id1> <id2>` — diff two artifacts, e.g. `sshai diff a12 a17`
- `sshai log [--host H] [--grep P]` — search the run-log, e.g. `sshai log --host web01 --grep nginx`

Run `sshai help` for the full command list, `sshai help <command>` for flags.

## Default-use rule

Use `sshai` by default when all of these are true:

- the target is a non-interactive Linux host (Bash by default, or an explicitly selected POSIX
  shell) or Windows PowerShell host reachable by an `ssh_config` alias; Windows prefers PowerShell
  7, falls back to 5.1 when unavailable, and can require either host explicitly;
- the operation executes a command and consumes text output, rather than transferring a file or
  driving an interactive program;
- command text and expected output contain no secret value;
- the operation is already authorized independently of the transport tool.

Use `--body-file -` for multi-line commands so the body stays out of process arguments:

```bash
sshai run --body-file - <host>
```

Feed the script body on standard input through the caller's protected input mechanism. `sshai`
stores only the body hash in run metadata and audit records, but the remote staged script and
captured output can still contain body-derived data; never embed passwords, tokens, keys, or other
secret values in a body.

For a Linux host such as OpenWrt that lacks Bash, select its POSIX shell explicitly:

```bash
sshai run --posix-shell /bin/ash openwrt01 -- uname -s
```

`--posix-shell` accepts one path/token without whitespace or control characters. Omitting it keeps
Bash as the Linux default. The selector affects non-Windows hosts only, so Windows hosts in the same
fan-out retain their PowerShell selection. A missing selected shell remains a remote error; `sshai`
does not fall back to Bash.

On Windows, omitting `--powershell-host` prefers PowerShell 7 and falls back to Windows PowerShell
5.1 when PowerShell 7 is unavailable. Use `--powershell-host pwsh` to require PowerShell 7 without
fallback. For a body that requires Windows PowerShell 5.1:

```bash
sshai run --powershell-host windows-powershell --body-file check.ps1 sccm01
```

Two narrow SSH exceptions remain inside `sshai`:

- After direct user authorization for an exact alias, pass
  `--accept-new-host-key <alias>`. The alias must occur exactly once in the host list. OpenSSH adds
  only a previously unknown key, continues to reject changed known keys, and the result reports
  the accepted algorithm and SHA256 fingerprint.
- For an explicitly authorized direct route, pass `--proxy-jump=none`. It disables the configured
  `ProxyJump` only for that invocation and never edits `~/.ssh/config`.

Omitting either flag preserves the strict host-key and managed-route defaults.

## Explicit local execution

Use `sshai local` only when the task explicitly calls for execution on the machine running `sshai`:

```bash
sshai local --shell bash --body-file check.sh
sshai local --shell pwsh -- Get-Date
```

`--shell bash` and `--shell pwsh` are the only values, and each interpreter must be available on
`PATH`. Local PowerShell runs only `pwsh`; it does not fall back to Windows PowerShell 5.1. Use
`--body-file <file|->` rather than the inline form for a multiline body; the body stays out of the
interpreter argv.

This command is not SSH, a remote fallback, a readonly-policy check, an authorization layer, or a
security sandbox. Remote-only flags and `--follow` are rejected. It uses the same bounded artifacts,
passports, JSON v1 envelope, `--delta`, named state, history, local query, and retention machinery
as `run`. Synthetic targets `local-bash` and `local-pwsh` isolate state by shell and context; they
appear in results and `log`, but never in `hosts`.

A normal local shell exit is stored and mirrored. Interpreter start failure, timeout, and output
overflow are stored respectively as `local-error=start`, `local-error=timeout`, and
`local-error=output-limit`, and `sshai` exits `96`. Overflow retains only the configured stream cap
and marks `truncated=1`. A timeout or output overflow stops only the direct interpreter child;
descendant-process cleanup is not guaranteed across platforms.

## Machine-readable mode

`sshai run --result-format=json` and `sshai local --result-format=json` emit exactly one
versioned envelope (`schema_version: "v1"`) on stdout. Each saved run includes its id, target,
exit, artifact path, byte/line counts, SHA-256, and duration, with no human tail or preview text.
Remote failures may add `transport_diagnostic` and explicit host-key evidence. Local runner failures
add `runs[].local_error` and increment `summary.local_errors`; both additive fields are omitted from
normal and remote results. Use JSON when a consumer must parse the result without regexing the human
passport:

```bash
sshai run --result-format=json --body-file - pg-prod-01 <<<'Get-Date' | jq '.runs[0].exit'      # remote exit code
sshai run --result-format=json --body-file - pg-prod-01 <<<'Get-Date' | jq '.runs[0].artifact_path' # the exact stored result on disk
```

The body stays out of argv and out of the envelope: the envelope's
`runs[].command` holds `body:<sha256>[:16]` for stdin/file bodies. Every
`runs[]` entry has a real saved artifact; a host denied by the readonly
policy is counted in `summary.policy_denied` and absent from `runs[]`.

When OpenSSH output matches the strict diagnostic allowlist, a transport-error entry includes a
canonical phrase such as `host key verification failed`; the artifact stores the same phrase.
Unknown failures retain only `transport_error: "ssh"`. Raw SSH error text — including key
material, configuration excerpts, algorithm offers, and hostnames found only in that error — is
never copied into the envelope or artifact. The explicit accept-new path returns only the
algorithm and SHA256 fingerprint of the newly persisted key.

`--result-out <file>` atomically replaces one regular destination with the
same envelope bytes plus its trailing newline. The destination is mode
`0600`; symlinks, directories, and other non-regular paths are refused.

## Live follow events

For one host, `sshai run --follow --follow-interval 10 ...` writes ephemeral
JSONL v1 progress events to stderr. It emits `started` only after the remote
wrapper has begun the user body, then periodic `heartbeat` events and bounded
combined-output previews. Output events have `stream: "combined"` and UTF-8
`data`; preview payload is capped at 64 KiB and 256 lines, each event's raw
UTF-8 `data` payload is at most 4 KiB, and output events are rate-limited to
one per 100 ms. Only `heartbeat` events may be coalesced; all accepted output
precedes a possible single suppression event and then `completed`. The follow
transport combines remote stdout and stderr; the wrappers also route Bash
stderr and PowerShell logical streams into that combined output. Local OpenSSH
diagnostics are diverted to a private log and never previewed live, so
transport diagnostics remain sanitized. Result publication (including
`--result-out`) completes before the final `completed` event so its outcome
and diagnostics are truthful; that event still precedes the unchanged final
stdout result. Its `outcome` carries the result fields (including exit,
transport error, and artifact metadata when saved) plus the normal summary,
actual `process_exit`, and bounded local `diagnostics` (for example result
publication failures). Follow
previews are never stored, may be suppressed for binary/invalid UTF-8 or
preview limits, and do not replace the authoritative artifact.
`--follow-interval` is in whole seconds (default 10, minimum 1) and requires
`--follow`; fan-out is rejected.

## Explicit fallbacks

Use an explicit purpose-built workflow instead of `sshai run` or `sshai local` when the task needs any of the following:

- secret input streamed to the remote program (stdin is occupied by the script wrapper);
- `scp`, `rsync`, backup download, or another file-transfer contract;
- an interactive shell, REPL, prompt, or long-lived foreground stream;
- a Windows host where neither PowerShell 7 nor Windows PowerShell 5.1 can run non-interactively;
- an address that needs an ad-hoc identity option instead of a configured SSH alias;
- a nested two-hop command not representable by `ProxyJump`/`ssh_config`;
- a server or production mutation that has not passed its own authorization and verification
  workflow.

Record which fallback condition applied. The archived `ps_ssh.py` helper is never a fallback and
must not be restored or invoked. For secret stdin or an unsupported two-hop shape, stop and require
a separately approved workflow. Raw `ssh` is an exception in covered command-execution scenarios,
not a shorter default.
