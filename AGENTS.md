# Repository Guidelines

## Project Overview

`sshai` is a single Go CLI for running non-interactive commands over system OpenSSH on Linux
(bash) and Windows PowerShell. PowerShell 7 is the compatible default; callers can select Windows
PowerShell 5.1 per invocation. The CLI returns budget-bounded human passports or a versioned JSON
envelope while retaining captured output locally for `q`, `diff`, `log`, and `--delta`.

The tool is transport and evidence infrastructure, not an authorization layer. It can target
sensitive hosts, including domain controllers, so preserve the distinction between readonly policy
denials, transport failures, and honest remote exit codes. File transfer, interactive sessions,
secret-bearing stdin, ad-hoc identity options, and unsupported two-hop execution require another
approved workflow.

## Architecture & Data Flow

`cmd/sshai/main.go` is a thin command dispatcher. `internal/cli` composes package-level services;
keep business logic out of the entrypoint.

```text
arguments/body
  -> config + readonly policy
  -> cached host facts and named shell state
  -> bash wrapper or staged PowerShell script
  -> system ssh/scp transport
  -> bounded capture + SQLite metadata + local artifact
  -> state/baseline update + delta lookup + audit
  -> human passport or JSON v1 result
```

For `run`, `internal/cli/run.go` resolves flags and configuration, opens the artifact store and
OpenSSH transport, then orchestrates each host. Host runs probe or load OS facts, restore the
selected context, execute, parse the state epilogue, persist evidence, and render the result.
Fan-out uses goroutines with a shared transport/store, divides the output budget across hosts, and
buffers per-host output so results still flush in argument order.

`q`, `diff`, `log`, `hosts`, and `gc` are local follow-up paths over the same state. Runtime data
lives under `$SSHAI_ROOT` or `~/.sshai`: `config.toml`, `art/`, `db.sqlite`, `audit.jsonl`,
per-host `state/`, and OpenSSH ControlMaster sockets under `cm/` when the local OpenSSH client
supports connection sharing.

## Key Directories

- `cmd/sshai/`: executable entrypoint and subcommand adapters.
- `internal/cli/`: flag parsing, help, dependency composition, run/fan-out orchestration, result
  modes, local query/diff/history/host/GC commands.
- `internal/transport/`, `internal/shell/`, `internal/session/`: OpenSSH execution and staging,
  bash/PowerShell wrapping and parsing, host facts, baselines, and named context state.
- `internal/artifact/`, `internal/delta/`, `internal/runlog/`: SQLite/artifact persistence,
  passports and JSON results, command keying/diffs, history, redaction, and audit.
- `internal/policy/`, `internal/config/`: fail-closed readonly allowlist and TOML/default loading.
- `scripts/`: guarded local installer plus Python benchmark analyzers and their standalone tests.
- `docs/`: agent usage, operational evidence, benchmark protocols/results, and governing
  architecture specifications under `docs/superpowers/specs/`.

## Development Commands

```sh
go build ./...                              # compile all Go packages
go test ./...                               # package-local unit suite
go test ./internal/cli -run 'TestRunResultFormatJSON' # focused test while iterating
go run ./cmd/sshai help                     # run the checkout
gofmt -w path/to/changed.go                 # format changed Go files
go vet ./...                                # standard static check; no custom linter is configured
```

Real-host integration is opt-in and never a routine or CI check:

```sh
SSHAI_TEST_LINUX_HOST=<ssh-alias> go test -tags=integration ./...
```

The alias must already work through OpenSSH. Missing `SSHAI_TEST_LINUX_HOST` skips the integration
tests; an unreachable configured host fails them. Windows parity is a manual gate documented in
`docs/windows-parity.md`.

```sh
python3 scripts/test_benchmark_v2_1.py       # current frozen analyzer controls
python3 scripts/test_benchmark_v1_1.py       # historical v1.1 analyzer controls
scripts/install.sh                           # atomic install to ~/.local/bin/sshai
```

`scripts/install.sh` honors `SSHAI_INSTALL_DIR`, refuses unsafe/symlink destinations, builds
`./cmd/sshai` with `-trimpath`, atomically replaces the binary, and smoke-checks `help`.

## Code Conventions & Common Patterns

- Use idiomatic, `gofmt`-formatted Go: lowercase package names, role-focused files, documented
  exported symbols, and `%w` when adding error context.
- CLI functions accept arguments plus injected stdout/stderr writers and return an exit code.
  Semantic output belongs on stdout; diagnostics belong on stderr. Preserve reserved exit classes:
  `96` usage/local invocation, `97` policy denial, and `98` transport failure.
- Keep dependency composition in `internal/cli`. Use the narrow `transport.Transport` interface,
  injected runners/writers, and existing fakes rather than adding a DI framework or global state.
- Concurrency must remain deterministic: isolate per-host buffers, share only concurrency-safe
  services, wait for every host, and emit results in caller order. Do not multiply the configured
  token budget per fan-out host.
- Treat persistence as security-sensitive. Existing code uses transactions or temporary-file plus
  rename publication, parameterized SQL, `0700` directories, and `0600` state/artifact/result
  files. Refuse symlink or non-regular result destinations rather than weakening checks.
- Preserve fail-closed behavior. Readonly parsing requires every command segment to match the
  anchored allowlist; unknown syntax is denied. Audit previews must remain bounded and redacted.
- Transport diagnostics must remain a fixed canonical allowlist. Never pass through raw SSH/scp
  stderr: it may contain hostnames, key fingerprints, algorithm offers, paths, or configuration.
- Never put passwords, tokens, keys, or multiline/quote-heavy script bodies in argv. Use
  `sshai run --body-file <file|-> ...` or protected stdin; keep secrets out of expected output.
- Keep output stable and bounded. Sort map-derived output, preserve remote non-zero exits, cap
  captured streams, suppress binary inline bodies, and save the artifact before rendering a
  passport/delta. The factory inline threshold is 500 estimated tokens (`ceil(bytes/4)`); the
  metadata-only fallback has a separate `<200`-token test contract.
- Code, comments, documentation, help text, and error messages are English.
- Benchmark analyzers are frozen, fail-closed measurement code. Do not treat manifests, old
  revisions, or absolute paths in benchmark reports as runtime configuration.

## Important Files

- `cmd/sshai/main.go`: command inventory and process exit ownership.
- `internal/cli/run.go`: primary execution pipeline; start here for `run`, fan-out, state, policy,
  delta, and audit changes.
- `internal/cli/result_mode.go`: deterministic human/JSON invocation output and `--result-out`.
- `internal/transport/transport.go` and `openssh.go`: transport contract, system OpenSSH behavior,
  timeouts, optional ControlMaster settings, and stream caps.
- `internal/artifact/store.go`, `passport.go`, and `result.go`: persistence and output contracts.
- `internal/session/state.go` and `internal/shell/{bash,pwsh,envdiff}.go`: cross-shell state
  continuity and encoding/parsing.
- `internal/config/config.go`: `$SSHAI_ROOT`, TOML schema, and runtime defaults.
- `go.mod` / `go.sum`: Go version, module identity, and dependency graph.
- `README.md` and `docs/agent-usage.md`: current user-facing behavior and safety boundary.
- `docs/superpowers/specs/2026-08-06-sshai-design.md`: governing architecture/CLI design; it
  resolves conflicts with the older charter and planning material.

## Runtime/Tooling Preferences

- Go `1.26.5` is required by `go.mod`; the module is `github.com/aprudkin/sshai`.
- Production uses system `ssh` and `scp`, configured SSH aliases, Linux bash, and Windows
  PowerShell. `pwsh`/PowerShell 7 is the default; `--powershell-host windows-powershell` selects
  Windows PowerShell 5.1. There is one executable and no daemon.
- Runtime configuration is optional TOML at `<root>/config.toml`; defaults are defined in
  `internal/config/config.go`. Important environment variables are `SSHAI_ROOT`, `SSHAI_CTX`,
  `SSHAI_INSTALL_DIR`, and integration-only `SSHAI_TEST_LINUX_HOST`.
- There is no Makefile, Taskfile, CI workflow, configured linter/formatter, release tool, container
  build, or Python package manifest. Prefer standard Go tooling. `scripts/install.sh` requires
  POSIX `sh`; the benchmark scripts use only the Python 3 standard library.
- Do not add secrets to argv, logs, prompts, fixtures, or durable artifacts. Runtime OpenSSH uses
  batch mode and existing user SSH configuration; do not invent alternate credential plumbing.

## Coupled Agent Skill

When a change to this repository affects sshai's user-facing CLI, commands, flags, output
contracts, supported shells, transport or safety behavior, or recommended usage, review the local
Pi skill at `~/.pi/agent/skills/sshai/SKILL.md` in the same task when that file exists. Update it
when its guidance would otherwise become incomplete or stale; internal-only changes that do not
affect agent usage do not require a skill edit. The skill update itself does not trigger another
review. Report whether the skill was reviewed and whether it changed.

Treat the skill's owning Git working tree as separate from this repository: preserve unrelated
work, stage and commit only skill-related files there, and do not push it unless explicitly
requested.

## Testing & QA

Go tests are colocated `*_test.go` files using the standard `testing` package. Follow the existing
contract-focused style: table-driven cases where useful, direct byte/string/JSON assertions,
`t.TempDir`, `t.Setenv`, cleanup/closed stores, and fake transports or runners. Unit tests must not
touch the network, user SSH configuration, or real `~/.sshai` state.

Add or update tests for observable contracts, especially exit classes, exact CLI/JSON output,
fan-out ordering, transport-diagnostic allowlisting, PowerShell-host selection, redaction,
readonly denials, atomic permissions/publication, state corruption and continuity,
CLIXML/Unicode handling, timeouts, and output budgets. Do not replace these with source text or
implementation-detail assertions.

Integration tests are restricted to the `integration` build tag and a real Linux SSH alias.
Windows remains a documented manual built-binary parity gate. Python benchmark tests are
standalone assertion programs, not pytest/unittest discovery. No repository CI or coverage
threshold is currently configured; run the changed package tests first, then `go test ./...`.
