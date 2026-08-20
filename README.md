# sshai

<p align="center"><img src="assets/readme/banner.svg" width="100%" alt="sshai: remote commands in, compact evidence out"></p>

`sshai` is a Go CLI for AI agents that runs non-interactive Linux bash and Windows PowerShell 7 commands over SSH. It keeps full command output in a local artifact and returns a compact passport instead of flooding agent context.

Remote commands in. Compact evidence out. Русская версия: [README.ru.md](README.ru.md).

## Who it is for

Use `sshai` for already-authorized, non-interactive commands on hosts reachable through `ssh_config`, when you need evidence without replaying large results into an agent conversation. It keeps transport, evidence, and operational authority separate.

## Capabilities

- Run commands on one or more Linux/bash or Windows/PowerShell 7 SSH aliases.
- Store output locally and return a bounded passport with outcome and artifact location.
- Query, diff, and search artifacts without replaying whole command results.
- Use body files for multiline commands, JSON result envelopes, deltas, and named contexts.
- Fail closed for interactive programs, PowerShell 5.1, secret stdin, ad-hoc identities, and unsupported two-hop execution.

```text
command -> SSH transport -> captured artifact -> bounded passport -> local q / diff / log
```

## Quick start

Requirements: Go `1.26.5` or newer in the `1.26` line, OpenSSH, and a locally configured SSH alias.

```bash
git clone https://github.com/aprudkin/sshai.git
cd sshai
go test ./...
scripts/install.sh
sshai run web01 -- df -h
```

Keep a multiline body out of process arguments:

```bash
sshai run --body-file check.ps1 win01
```

For automation, request a versioned JSON envelope:

```bash
sshai run --result-format=json web01 -- uname -a
```

Run `sshai help` for the command inventory and `sshai help run` for its full execution contract.

## Safety boundary

`sshai` is a transport and evidence tool. It does not grant permission to access or change a host.

- Use it only for separately authorized actions on configured aliases.
- Keep passwords, tokens, keys, certificates, and expected secret output out of command text and artifacts.
- Use `--body-file` for multiline bodies; use a separate approved workflow for secret stdin or file transfer.
- A policy denial, transport failure, and remote non-zero exit are different outcomes.

See [agent usage](docs/agent-usage.md) for default-use and fallback rules.

## Development and contributing

```bash
go test ./...
go vet ./...
go build ./...
```

Integration tests require reachable test hosts and are intentionally outside CI:

```bash
go test -tags=integration ./...
```

Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [Code of Conduct](CODE_OF_CONDUCT.md) before opening an issue or pull request.

## Project status and benchmarks

Version 1 provides `run`, `q`, `diff`, `log`, `hosts`, `gc`, and `help`, with Linux and Windows acceptance evidence in this repository.

The controlled v1.1 benchmark found a 99.86% reduction in visible tool-output estimates, but only a 44.71% reduction in total Codex input tokens. Its decision is **needs work**, not confirmed. Read the [full result and boundaries](docs/benchmarks/v1.1-results-2026-08-13.md).

The historical v2.1 fan-out study ended **inconclusive**. Amendment 2 is an approved measurement candidate, not a result, and has not been frozen for measurement. See the [protocol](docs/benchmarks/v2.1-protocol.md) and [analyzer definition](docs/benchmarks/v2.1-analyzer.md).

## License

MIT. See [LICENSE](LICENSE).
