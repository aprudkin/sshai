# sshai — agent usage

`sshai` runs commands on remote hosts (Linux bash, Windows PowerShell) over SSH and returns a compact passport plus an on-disk artifact path instead of raw output in context. Captured output stays on disk for local querying; overflow beyond the configured stream cap is discarded and marked `truncated=1`.

- `sshai run <host> -- <command>` — execute; e.g. `sshai run web01 -- df -h`
- `sshai q <id> -- <tool> <args>` — query a stored artifact, e.g. `sshai q a17 -- grep -iE 'fatal'`
- `sshai diff <id1> <id2>` — diff two artifacts, e.g. `sshai diff a12 a17`
- `sshai log [--host H] [--grep P]` — search the run-log, e.g. `sshai log --host web01 --grep nginx`

Run `sshai help` for the full command list, `sshai help <command>` for flags.

## Default-use rule

Use `sshai` by default when all of these are true:

- the target is a non-interactive Windows PowerShell 7 or Linux bash host reachable by an
  `ssh_config` alias;
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

## Explicit fallbacks

Use the existing dedicated workflow instead of `sshai` when the task needs any of the following:

- secret input streamed to the remote program (stdin is occupied by the script wrapper);
- `scp`, `rsync`, backup download, or another file-transfer contract;
- an interactive shell, REPL, prompt, or long-lived foreground stream;
- a Windows host without PowerShell 7;
- an address that needs an ad-hoc identity option instead of a configured SSH alias;
- a nested two-hop command not representable by `ProxyJump`/`ssh_config`;
- a server or production mutation that has not passed its own authorization and verification
  workflow.

Record which fallback condition applied. Raw `ssh` is an exception in covered command-execution
scenarios, not a shorter default.
