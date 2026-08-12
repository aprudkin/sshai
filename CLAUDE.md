# sshai

Remote execution tool for AI agents: Windows (PowerShell) and Linux (bash) over SSH, designed
around explicit agent-context frugality.

Charter (purpose, definition of done, out of scope):
`docs/superpowers/specs/2026-08-06-sshai-charter.md`
Design (architecture, CLI surface, v1 scope):
`docs/superpowers/specs/2026-08-06-sshai-design.md`
Issue: [aimem#636](https://github.com/aprudkin/aimem/issues/636), label `project:sshai`

## Repository state

v1 implemented: `run` (single host and fan-out, `--delta`, `--ctx` state re-injection), `q`,
`diff`, `log`, `hosts`, `gc`, and `help` all exist and are unit-tested. The Linux path is
integration-verified against a live host; the Windows path is integration-verified via the manual
parity gate (`.superpowers/sdd/2026-08-06-sshai-v1/task-16-report.md`).

## Layout (per the design doc; directories appear as code lands)

```
cmd/sshai/           # entrypoint, subcommand dispatch
internal/cli/        # flags, help text
internal/transport/  # Transport interface + openssh implementation
internal/shell/      # bash + pwsh adapters: wrappers, encodings, CLIXML filter
internal/session/    # state capture/re-injection, host-facts cache
internal/artifact/   # store, passport rendering, budget trimmer
internal/delta/      # command keying + local diff
internal/runlog/     # SQLite run-log + audit.jsonl
internal/policy/     # readonly allowlist (fail-closed)
docs/superpowers/specs/
```

## Build and test commands

```
go build ./...                     # build
go test ./...                      # unit tests
go test -tags=integration ./...    # integration tests (need reachable fleet hosts; never in CI)
```

## Repository-specific rules

- **No secrets in argv.** This tool connects to domain controllers by design. Passwords and tokens
  travel via stdin or files; `curl -H "Authorization: …"`, `--password` and the like hand the
  secret to every process on the machine.
- **Script bodies are never passed as an argument** — use `sshai run --body-file` or protected stdin.
- **English everywhere** — code, comments, docs, help output, error messages. The tool is intended
  to be open-sourced.
- The design doc's passport size target (<200 tokens for the metadata-only form) is a unit test,
  not a guideline.
