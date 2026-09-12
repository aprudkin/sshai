# Project guidance

## Purpose and boundaries

- `sshai` is a Go CLI for authorized non-interactive command execution with bounded
  evidence: remote commands use system OpenSSH; explicit local commands use Bash or `pwsh`.
  Captured output is saved locally for passports, JSON results, queries, diffs, and history.
- Preserve the transport-versus-authorization boundary. Local execution is not an SSH
  fallback, readonly-policy check, or security sandbox.
- Before a material implementation change, follow the issue-discussion requirement in
  [CONTRIBUTING.md](CONTRIBUTING.md). For suspected vulnerabilities or credential exposure,
  follow private reporting in [SECURITY.md](SECURITY.md), not a public issue.
- Never commit credentials, private hostnames, captured remote output, local artifacts,
  or raw benchmark rollouts. Use synthetic values in reproductions and tests.

## Entry points and module boundaries

- `cmd/sshai/main.go` dispatches commands and owns process exit. Keep execution orchestration
  in `internal/cli/`: `run.go` for SSH, `local.go` for local execution, `query.go` and
  `misc.go` for artifact follow-up commands.
- `internal/transport/` owns OpenSSH execution, staging, and canonical transport diagnostics;
  `internal/runner/` owns bounded local process execution. Keep these paths distinct.
- `internal/shell/` wraps and parses Bash/POSIX and PowerShell execution;
  `internal/session/` persists host facts, baselines, and named shell state.
- `internal/artifact/` owns SQLite metadata, captured output, passports, and JSON contracts.
  `internal/delta/` and `internal/runlog/` provide comparison, history, and audit behavior.
- `internal/config/config.go` defines TOML defaults and runtime root selection:
  `$SSHAI_ROOT`, otherwise `~/.sshai`. `internal/policy/` implements the remote readonly policy.

## Development and validation

Use the repository root as the working directory. The Go module and tool dependency versions
are defined in `go.mod` and `go.sum`; the current Go baseline is `1.26.5`.
Remote execution needs OpenSSH and configured aliases; local execution needs its selected
interpreter on `PATH`. Shell scripts use POSIX `sh`; release packaging also uses Python 3.

```sh
go test ./...                                  # full Go suite
go test ./internal/cli -run '^TestLocalValidation$' # one existing test
go vet ./...                                   # Go static checks
go build ./...                                 # compile packages
gofmt -w path/to/changed.go                     # format changed Go files
scripts/check-release-tree.sh                   # validate binary release inputs
python3 -m unittest discover -s scripts -p 'test_release_inventory.py' # inventory tests
scripts/test_install.sh                         # builds and tests installation in temp roots
go tool govulncheck ./...                       # tool pinned through go.mod
```

- Before a pull request, run the test, vet, and build checks required by `CONTRIBUTING.md`.
  Start with affected package tests when iterating; report checks not run and any failures.
- `.github/workflows/ci.yml` also checks binary release inputs, inventory regressions, and the installer.
  `.github/workflows/security.yml` defines vulnerability checks and the pinned `gosec` setup;
  consult it when changing security tooling rather than assuming `gosec` is installed.
- Format Go with `gofmt`; keep user-facing documentation and comments in English.
- Existing CLI tests use injected transports/runners, `t.TempDir()`, `t.Setenv("SSHAI_ROOT", ...)`,
  and real temporary artifact stores. Follow these patterns to isolate tests from user state
  and keep remote execution out of unit tests. Assert observable output, saved evidence,
  and exit behavior rather than only implementation details.
- Integration tests are opt-in and outside CI. Only with explicitly authorized,
  non-production test hosts, use
  `SSHAI_TEST_LINUX_HOST=<ssh-alias> go test -tags=integration ./...`.
  Without that variable the Linux integration tests skip. For Windows validation, first read
  [docs/windows-parity.md](docs/windows-parity.md); do not infer parity from Linux tests.

## Execution and output contracts

- Before changing commands, flags, shells, or result behavior, read
  [docs/agent-usage.md](docs/agent-usage.md), the relevant `internal/cli/help.go` section,
  and [skills/sshai/SKILL.md](skills/sshai/SKILL.md), which is bundled with the CLI.
  Keep affected usage guidance consistent with implementation.
- Preserve Bash as the default remote Linux shell and explicit POSIX-shell selection.
  Remote Windows prefers PowerShell 7 with a 5.1 fallback when unavailable; explicitly
  requiring a host disables that selection fallback. Local PowerShell uses only `pwsh`.
- Keep policy denial, transport failure, Windows setup failure, local failure, and command
  non-zero exits distinguishable. Reserved CLI codes are `96` usage/local failure,
  `97` policy, `98` transport, and `99` setup; definitions are mirrored in
  `cmd/sshai/main.go` and `internal/cli/run.go`.
- Preserve canonical allowlisted transport diagnostics; never expose raw SSH errors in
  artifacts, result envelopes, or live previews. Keep exact-alias host-key acceptance and
  direct-route overrides explicit; do not weaken strict defaults.
- JSON mode emits one versioned stdout envelope, without human preview text.
  `--result-out` publishes it atomically with private permissions and rejects non-regular
  destinations. `--follow` is remote, single-host, bounded stderr JSONL; it does not replace
  the final stdout result or authoritative artifact. Read the detailed contracts in
  `docs/agent-usage.md` before modifying these paths.

## Installation and releases

- `scripts/install.sh` builds and installs the executable and bundled skill. It writes to
  `SSHAI_INSTALL_DIR` and `SSHAI_SHARE_DIR` (defaulting under `~/.local`); it is not a
  read-only validation command. Use `scripts/test_install.sh` for isolated installer tests.
- When changing the static files shipped in binary release archives, review and update
  `release/archive-files.txt`, then run the release-input check and inventory regression tests.
  Unrelated source, test, documentation, and agent-instruction changes do not require manifest
  entries. Executables and generated third-party licenses are added and validated separately.
- Binary archive inventory checks do not scan content for secrets or filter GitHub's automatic
  source archives, which contain the tracked tree. Review every committed file under the existing
  sensitive-data restrictions; see `CONTRIBUTING.md` for release review requirements.
- For release work, read `CONTRIBUTING.md` and `.github/workflows/release.yml` first.
  `scripts/package-release.sh VERSION` generates archives and checksums in `dist/` and
  clears its existing contents. The workflow generates release metadata and uses
  `scripts/render-homebrew-formula.sh` with `release/homebrew/sshai.rb.tmpl` for the formula.
  Change source scripts/templates rather than generated release assets; publishing a stable
  tag requires its commit on `main` and passed CI.
