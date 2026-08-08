# sshai — agent usage

`sshai` runs commands on remote hosts (Linux bash, Windows PowerShell) over SSH and returns a compact passport plus an on-disk artifact path instead of raw output in context. Captured output stays on disk for local querying; overflow beyond the configured stream cap is discarded and marked `truncated=1`.

- `sshai run <host> -- <command>` — execute; e.g. `sshai run web01 -- df -h`
- `sshai q <id> -- <tool> <args>` — query a stored artifact, e.g. `sshai q a17 -- grep -iE 'fatal'`
- `sshai diff <id1> <id2>` — diff two artifacts, e.g. `sshai diff a12 a17`
- `sshai log [--host H] [--grep P]` — search the run-log, e.g. `sshai log --host web01 --grep nginx`

Run `sshai help` for the full command list, `sshai help <command>` for flags.
