Русская версия: [README.ru.md](README.ru.md).

# sshai

<p align="center"><img src="assets/readme/banner.svg" width="100%" alt="sshai: remote commands in, compact evidence out"></p>

`sshai` is a Go CLI for AI agents that runs non-interactive Linux bash and Windows PowerShell 7
commands over SSH. It keeps full command output in a local artifact and returns a compact passport
instead of flooding agent context.

Remote commands in. Compact evidence out.

## Who it is for

Use `sshai` for already-authorized, non-interactive commands on hosts reachable through
`ssh_config`, when you need evidence without replaying large results into an agent conversation.
It keeps transport, evidence, and operational authority separate.

## Capabilities

- Run commands on one or more Linux/bash or Windows/PowerShell 7 SSH aliases.
- Store output locally and return a bounded passport with outcome and artifact location.
- Query, diff, and search artifacts without replaying whole command results.
- Use body files for multiline commands, JSON result envelopes, deltas, and named contexts.
- Fail closed for interactive programs, PowerShell 5.1, secret stdin, ad-hoc identities, and
  unsupported two-hop execution.

```text
command -> SSH transport -> captured artifact -> bounded passport -> local q / diff / log
```

## Quick start

Requirements: Go `1.26.5` or newer in the `1.26` line, OpenSSH, and a locally configured SSH
alias.

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

`sshai` is a transport and evidence tool. It does not grant permission to access or change a
host.

- Use it only for separately authorized actions on configured aliases.
- Keep passwords, tokens, keys, certificates, and expected secret output out of command text and
  artifacts.
- Use `--body-file` for multiline bodies; use a separate approved workflow for secret stdin or
  file transfer.
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

Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[Code of Conduct](CODE_OF_CONDUCT.md) before opening an issue or pull request.

## Project status and benchmarks

### Project status

Version 1 provides `run`, `q`, `diff`, `log`, `hosts`, `gc`, and `help`, with Linux and Windows
acceptance evidence in this repository.

### Controlled v1.1 benchmark

The controlled v1.1 benchmark completed on 2026-08-13 with 36 read-only observations across two
Linux hosts and one Windows host, using fresh raw-SSH and `sshai` Codex sessions.

| Measurement | Raw SSH | `sshai` | Result |
| --- | ---: | ---: | ---: |
| Agent-visible marked tool output, total estimated tokens | 855,806 | 1,227 | **99.86% lower** |
| Agent-visible marked tool output, p95 estimated tokens | 262,152 | 50 | **99.98% lower** |
| Actual Codex input tokens | 2,896,077 | 1,601,293 | **44.71% lower** |
| Successful observations | 34 / 36 | 34 / 36 | equal (94.44%) |

The p95, compaction, success, and quoting-debug targets passed. The primary target of at least 80%
lower actual input tokens did not, so the recorded decision is **needs work**, not confirmed. Read
the [full result and its boundaries](docs/benchmarks/v1.1-results-2026-08-13.md).

### Production fleet collection

A separate, anonymized production run on 2026-08-25 used `sshai` as its primary control plane for
an approved PCI DSS collection across 79 Windows servers. The run produced 158 raw report files;
archives were retrieved separately, and every retrieved archive matched the SHA-256 reported by
its source.

| Production measurement | Bytes | Estimated context |
| --- | ---: | ---: |
| Full 158-file report set | 8,123,158 | ~2.03M tokens |
| Agent-visible orchestration, status, and receipt stream | 49,846 | ~12.5K tokens |

The control stream was 0.61% of the raw evidence size—about **163× smaller**, a **99.39% reduction**
in bytes visible during orchestration.

This is evidence from a real workflow, not a controlled head-to-head token benchmark. Token counts
use `ceil(bytes / 4)`, and the run used narrow raw-SSH exceptions for host-key trust and a direct
route.

### Follow-up fan-out experiment

The historical v2.1 fan-out study ended **inconclusive**. Amendment 2 is an approved measurement
candidate, not a result, and has not been frozen for measurement. See the
[protocol](docs/benchmarks/v2.1-protocol.md) and
[analyzer definition](docs/benchmarks/v2.1-analyzer.md).

## License

MIT. See [LICENSE](LICENSE).
