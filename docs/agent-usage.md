# sshai — agent usage

`sshai` runs commands on remote hosts (Linux bash, Windows PowerShell) over SSH and returns a compact passport plus an on-disk artifact path instead of raw output in context. Captured output stays on disk for local querying; overflow beyond the configured stream cap is discarded and marked `truncated=1`.

- `sshai run <host> -- <command>` — execute; e.g. `sshai run web01 -- df -h`
- `sshai q <id> -- <tool> <args>` — query a stored artifact; its path is appended as the tool's final argv argument, never sent on stdin
- `sshai diff <id1> <id2>` — diff two artifacts, e.g. `sshai diff a12 a17`
- `sshai log [--host H] [--grep P]` — search the run-log, e.g. `sshai log --host web01 --grep nginx`

Run `sshai help` for the full command list, `sshai help <command>` for flags.

## Default-use rule

Use `sshai` by default when all of these are true:

- the target is a non-interactive Linux bash or Windows PowerShell host reachable by an
  `ssh_config` alias; Windows defaults to PowerShell 7 and can explicitly select 5.1;
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

For a Windows body that requires Windows PowerShell 5.1 rather than the default PowerShell 7 host:

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

## Machine-readable mode

`sshai run --result-format=json` emits exactly one versioned envelope
(`schema_version: "v1"`) on stdout — run id, host, remote exit, artifact
path, byte/line counts, sha256, duration, an optional
`transport_diagnostic`, and optional `accepted_host_key_algorithm` /
`accepted_host_key_fingerprint` evidence — with no human tail/preview text.
Use it when a consumer must parse the result without regexing the human passport:

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


## Explicit fallbacks

Use an explicit purpose-built workflow instead of `sshai` when the task needs any of the following:

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
