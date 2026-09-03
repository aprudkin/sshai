Русская версия: [README.ru.md](README.ru.md).

# sshai

<p align="center"><img src="assets/readme/banner.svg" width="100%" alt="sshai: remote commands in, compact evidence out"></p>

`sshai` is a Go CLI for AI agents that runs non-interactive Linux commands through Bash by default
or an explicitly selected POSIX shell, and Windows PowerShell commands over SSH. PowerShell 7
(`pwsh`) is the default; Windows PowerShell 5.1 is selectable per invocation. It keeps captured
command output in a local artifact and returns a compact passport instead of flooding agent context.

Remote commands in. Compact evidence out.

## Who it is for

Use `sshai` for already-authorized, non-interactive commands on hosts reachable through
`ssh_config`, when you need evidence without replaying large results into an agent conversation.
It keeps transport, evidence, and operational authority separate.

## Pi agent skill

When using `sshai` through Pi, pair the CLI with the purpose-built
[`sshai` agent skill](https://github.com/aprudkin/pi-config/blob/main/skills/sshai/SKILL.md).
The skill gives the agent recommended invocation patterns, shell-selection guidance,
bounded-output and local-artifact workflows, safety boundaries, and follow-mode usage. The skill
is optional; the CLI also works without it.

## Capabilities

- Run commands on one or more Linux SSH aliases through Bash by default or a selected POSIX shell.
- Run commands on one or more Windows/PowerShell SSH aliases with PowerShell 7 as the default and
  Windows PowerShell 5.1 selectable per invocation.
- Store output locally and return a bounded passport with outcome and artifact location.
- Query, diff, and search artifacts without replaying whole command results.
- Use `--body-file` for multiline commands, JSON result envelopes, `--delta`, and named contexts.
- Return bounded, sanitized diagnostics for recognized SSH failures.
- Fail closed for interactive programs, secret stdin, ad-hoc identities, and unsupported two-hop
  execution.

The execution path keeps captured output local and returns only bounded evidence to agent context:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/readme/architecture-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/readme/architecture-light.svg">
  <img
    src="assets/readme/architecture-light.svg"
    width="100%"
    alt="Sequence diagram showing an AI agent calling sshai through OpenSSH, sshai saving captured output locally, and only bounded passport or queried evidence returning to agent context">
</picture>

For a responsive walkthrough of this execution and evidence model, see the
[visual execution guide](https://aprudkin.github.io/sshai/execution-model.html).

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

Linux uses Bash by default. On a Linux host such as OpenWrt that lacks Bash, select its POSIX shell
explicitly:

```bash
sshai run --posix-shell /bin/ash openwrt01 -- uname -s
```

`--posix-shell` accepts one path/token without whitespace or control characters. It affects Linux
hosts only; Windows hosts in a mixed fan-out retain their PowerShell selection. A missing selected
shell is a remote error—`sshai` never falls back to Bash.

PowerShell 7 remains the Windows default. Select Windows PowerShell 5.1 when a command requires it:

```bash
sshai run --powershell-host windows-powershell --body-file check.ps1 sccm01
```

After direct authorization for one exact alias, accept only its previously unknown host key:

```bash
sshai run --accept-new-host-key new01 new01 -- uname -a
```

Bypass a configured `ProxyJump` for one direct-route invocation without changing `ssh_config`:

```bash
sshai run --proxy-jump=none direct01 -- uname -a
```

For automation, request a versioned JSON envelope:

```bash
sshai run --result-format=json web01 -- uname -a
```

For one long-running host command, opt into bounded JSONL events on stderr while preserving the
normal final stdout result:

```bash
sshai run --follow --follow-interval 5 web01 -- long-running-check
```

Recognized SSH failures expose only a canonical `transport_diagnostic`; raw SSH stderr remains
private. An explicitly authorized host-key acceptance also returns the accepted algorithm and
SHA-256 fingerprint.

Run `sshai help` for the command inventory and `sshai help run` for its full execution contract.

## Safety boundary

`sshai` is a transport and evidence tool. It does not grant permission to access or change a
host.

- Use it only for separately authorized actions on configured aliases.
- Keep passwords, tokens, keys, certificates, and expected secret output out of command text and
  artifacts.
- Use `--accept-new-host-key HOST` only after direct authorization for that exact alias, and
  `--proxy-jump=none` only for an explicitly selected direct-route invocation.
- Use a separate approved workflow for secret stdin, file transfer, interactive programs,
  ad-hoc identity options, or unsupported two-hop execution.
- A policy denial, transport failure, and remote non-zero exit are different outcomes.
- The archived `ps_ssh.py` helper is not a fallback.

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

Version 1 provides `run`, `q`, `diff`, `log`, `hosts`, `gc`, and `help`, including selectable
Linux POSIX shells and Windows PowerShell hosts, sanitized transport diagnostics, scoped host-key
acceptance, and a one-invocation direct-route override. Linux and PowerShell 7 acceptance evidence
is recorded in this repository; live Windows PowerShell 5.1 evidence is still pending.

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
use `ceil(bytes / 4)`. The run predated scoped host-key acceptance and direct-route support; those
paths are now available without weakening the strict defaults.

### Follow-up fan-out experiment

The historical v2.1 fan-out study ended **inconclusive**. Amendment 2 is an approved measurement
candidate, not a result, and has not been frozen for measurement. See the
[protocol](docs/benchmarks/v2.1-protocol.md) and
[analyzer definition](docs/benchmarks/v2.1-analyzer.md).

## License

MIT. See [LICENSE](LICENSE).
