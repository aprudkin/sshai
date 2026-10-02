---
name: sshai
description: Execute non-interactive sshai commands on Windows PowerShell hosts and Linux-family Bash or explicitly selected POSIX-shell hosts, or explicitly selected local Bash or pwsh, with bounded output and local artifacts. Use for covered command execution through an AI coding agent.
license: MIT
compatibility: Requires the sshai CLI; remote execution needs system OpenSSH and configured ssh_config aliases, while local execution needs bash or pwsh on PATH.
---

Use the installed `sshai` binary through the agent harness's non-interactive shell execution tool. It supports Windows PowerShell 7 or 5.1 and Linux-family hosts reachable through an `ssh_config` alias, plus explicit local Bash or PowerShell 7 (`pwsh`) execution. Linux-family remote execution defaults to Bash; select an explicit POSIX shell when the host, such as OpenWrt, does not provide Bash. Confirm availability with `command -v sshai`. Read `sshai help` for command discovery and `sshai help <command>` for the relevant command whenever a flag or output contract is uncertain; the CLI does not provide a `--version` command.

Use `sshai` only when command text, script bodies, and expected output contain no secret values. Neither stdin nor a private body file makes embedded secrets safe: staged scripts and captured output can retain body-derived data. Do not embed passwords, tokens, keys, or other secret values; stop and require a separately approved, purpose-built workflow when secrets are required.

## Execute

For remote `run`, put all sshai options before the first host. Use `sshai run [flags] <host...> -- <command>` for inline commands or `sshai run [flags] --body-file <file|-> <host...>` for file/stdin bodies. For example, use `sshai run --body-file check.ps1 windows01`, not `sshai run windows01 --body-file check.ps1`. At least one host is required in either form. In the inline form, everything after the host/command separator `--` is command text, not sshai options; do not move command options ahead of the host.

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

For local project work, do not rely on the calling tool's cwd. Before the body runs, sshai restores saved cwd and selected environment values for `(local-bash|local-pwsh, ctx)` under the active `SSHAI_ROOT` (default `~/.sshai`). Context defaults to `$SSHAI_CTX` or `default`, not the project directory. Reusing a context across projects can override the caller's cwd and environment. Environment restoration re-applies new/changed exported values relative to the shell target's saved baseline, excluding volatile names; it is not a full reset and does not replay unsets.

Use a project-specific `--ctx` and begin each project-local body with an explicit absolute cwd, stopping on failure. Set required non-secret environment values in the body too; changing cwd does not reset them. These examples use a Bash-compatible caller and quoted stdin bodies. Replace the path with the project's existing absolute local path (for example, `C:\work\project-a` in a PowerShell body on Windows):

```bash
sshai local --shell bash --ctx project-a --body-file - <<'BASH'
cd -- '/absolute/path/to/project-a' || exit 1
pwd
# Run the authorized project check here, after cwd selection.
BASH
```

```bash
sshai local --shell pwsh --ctx project-a --body-file - <<'POWERSHELL'
Set-Location -LiteralPath '/absolute/path/to/project-a' -ErrorAction Stop
(Get-Location).Path
# Run the authorized project check here, after cwd selection.
POWERSHELL
```

Check the retained output for the intended cwd before interpreting relative-path results. A new context separates saved shell state but still inherits the caller's environment; it is not filesystem, process or authorization isolation. For remote `run`, `--ctx` also does not change or reset SSH control sockets. Even a project-specific context may retain a subdirectory, so repeat explicit cwd selection on subsequent project-local runs.

For remote `run`, `--timeout N` is one per-host budget in seconds, not a fresh allowance for each step. It defaults to config `timeout_sec` (factory default 60). The clock starts before loading cached facts, after local policy/selector validation, and covers probing/setup, script staging and execution. Cached facts skip probing; each fan-out host has its own deadline. Slow first-contact setup leaves less time for the body, and Windows upload has no separate two-minute allowance. Both normal and follow transports allow up to 100 ms for pipe cleanup after cancellation, plus scheduling overhead; local result persistence/publication is outside this budget. `duration_ms` includes probing and staging from that same start. A timeout reports `transport-error=timeout`, not proof that remote work stopped or never ran. Check actual remote state before replaying side effects. Local execution's timeout contract is unchanged.

The agent harness's shell-tool timeout is a separate outer deadline: increasing it does not change sshai's `--timeout` or configured default. For a long-running command, set an explicit task-appropriate `--timeout` and give the harness enough time for the whole CLI operation, including initialization, local cleanup and result persistence/publication. Do not set both deadlines to the same value. If the harness stops waiting first, the final passport or JSON result may be missing; that does not establish the remote outcome. Keep both limits finite and task-scoped, not automatic increases or retries after a timeout.

For example, if one authorized check is expected to take about four minutes, a five-minute per-host budget can leave time for setup. Set the harness's shell-tool timeout separately to, for example, 330 seconds to leave local-overhead headroom. These are illustrative limits, not a guarantee that 30 seconds always suffices; size both for the task and environment. Replace `<host>` with its configured alias and `<command>` with the authorized check:

```bash
sshai run --timeout 300 --follow <host> -- <command>
sshai run --timeout 300 --follow --follow-interval 5 <host> -- <command>
```

`--follow` enables ephemeral observation; it does not extend or reset `--timeout`. Changing `--follow-interval` only changes heartbeat frequency, not the execution budget.

Follow events are JSONL on stderr; the normal human passport or JSON v1 result remains on stdout. The interval is in seconds, defaults to `10`, and must be at least `1`. Follow mode accepts exactly one host. Treat heartbeats as truthful elapsed-time signals, not application progress. Live combined-output previews are bounded, may end with `output_suppressed`, and are not authoritative; use the saved artifact as the authoritative source for retained evidence. The stream is not persisted and does not imply polling, replay, retry, or authorization.

## Interpret and query results

For human output, treat the passport status line as the source of truth. When a consumer must parse the result, use `--result-format=json` with `run` or `local` and inspect the single stdout envelope (`schema_version: "v1"`), including `runs[]` and `summary`, rather than parsing a human passport. Policy-denied hosts are counted in `summary.policy_denied` and have no `runs[]` entry. Do not classify failures from the process exit code alone: genuine command exits can overlap reserved CLI codes. Use the passport status or JSON error fields to distinguish command exits from local, transport, and setup failures.

For example:

```bash
sshai run --result-format=json <host> -- <command>
```

When a result file is required, read `sshai help run` or `sshai help local` before using `--result-out <file>` with JSON mode; it atomically replaces a regular destination with a private envelope and rejects non-regular destinations.

For both remote and local execution, artifacts retain output only up to the configured stream cap. `truncated=1` means output was discarded; neither the artifact nor `sshai q` can recover that discarded portion. Do not claim the retained evidence is the complete command output when truncated.

A Windows host where no supported PowerShell setup form can create its scratch directory reports `setup-error=windows-shell`, returns exit `99`, and does not run the user body or cache host facts; its artifact contains only a fixed diagnostic. A transport failure is reported as `transport-error=<class>` and may include a bounded canonical diagnostic. SSH transport and Windows setup diagnostics are sanitized in human output, JSON output, and saved artifacts: raw SSH or setup output is not passed through. This is not general secret redaction for user-command output; captured command output may retain secrets if the command emits them. Explicitly authorized host-key acceptance may report the accepted algorithm and SHA256 fingerprint.

On saved remote transport/setup failures, read `failure_phase` and `remote_completion` in JSON or follow `completed.outcome`; human passports use `failure-phase` and `remote-completion`. The allowlisted phases are `probe` (discovery/setup), `stage` (Windows script upload), and `exec` (execution boundary). `remote_completion: "not_started"` means this invocation did not dispatch user-body execution: probe/setup and stage failures have this value, as does a deadline that expires before exec dispatch. It does not imply that setup/upload left no scratch state or that an earlier invocation did not run. A failed execution attempt has `remote_completion: "unknown"`: the body may not have started, may still be running, or may have completed. A timeout, canonical diagnostic, or follow `started` event does not prove completion or cancellation. Before replaying side effects, verify actual target state or the operation's existing result; these fields never authorize retries or establish a root cause. Normal command results, local runs and older records omit both fields; absence is not evidence that the body did not run.

Query a large stored result locally with `sshai q <id> -- <tool> <args>`. It invokes `<tool> <args> <artifact-path>`: the artifact path is appended as the final argv argument, never supplied on stdin. For example:

```bash
sshai q <id> -- python3 -c 'import sys; print(open(sys.argv[-1]).readline())'
```

Read `sshai help q` if query arguments or output limits are uncertain. Use `sshai diff` or `--delta` for repeated checks instead of loading or rerunning full output.

The transport never authorizes a server mutation. Retain the task's exact target, preconditions, rollback, and post-change verification. Get confirmation before remote, destructive, production, external, or hard-to-reverse actions not already authorized by the request.

## Boundaries and fallback

Do not invoke `ps_ssh.py`; it is archived and intentionally absent from active script paths.

- For file transfer, use the task's explicit `scp`, `sftp`, `rsync`, or backup workflow.
- For an interactive shell, REPL, prompt, or foreground stream, use an explicitly authorized interactive SSH workflow.
- Configure a stable `ssh_config` alias rather than passing an ad-hoc identity through a hidden helper.
- Prefer `ProxyJump`/`ssh_config` for two-hop access.
- If secret stdin or a two-hop shape is unsupported by `sshai`, stop and name the unsupported requirement. Continue only through a separately approved, purpose-built workflow; never restore or call the archived helper as an implicit fallback.

Raw `ssh` is an exception for a documented unsupported requirement, not a shorter alternative for command execution already covered by `sshai`.
