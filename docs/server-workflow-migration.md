# Server workflow migration

**Task:** [aimem#709](https://github.com/aprudkin/aimem/issues/709)
**Inventory date:** 2026-08-12

This inventory separates command execution that `sshai` can own from surrounding server work that
must keep an explicit fallback. It is based on current project behavior plus existing Windows
diagnostics/deployment and Linux deployment runbooks. Runbooks are evidence of real workflow
shapes, not proof that a named host or procedure is still current; live checks remain required.

## Prioritized scenarios

| Priority | Scenario | Required proof | v1 disposition |
|---|---|---|---|
| P0 | Windows single-host read-only command | Readable output, exact exit status, no CLIXML noise | Covered; refresh live proof |
| P0 | Windows multi-line PowerShell with Cyrillic/emoji | Byte-intact body/output, body absent from argv and stored metadata | Covered; refresh live proof |
| P0 | Linux single-host and multi-line bash | Readable output, exact exit status, state re-injection | Covered; refresh live proof |
| P0 | Large diagnostic output | Bounded passport, complete local artifact up to stream cap, local query | Covered; refresh on both OSes |
| P0 | Failed command and retry | Honest non-zero exit, stored evidence, later run unaffected | Covered; refresh on both OSes |
| P0 | Repeated verification | `--delta`, `log`, and `q` avoid reloading full output | Covered; refresh live proof |
| P0 | Adoption by new agent sessions | Installed binary plus default-use instruction and explicit fallback | Completed; fresh-process proof below |
| P1 | Fleet read-only check | Deterministic fan-out passports and aggregate result | Covered; live proof after P0 |
| P1 | File upload/download | Preserve `scp`/`rsync` semantics | Out of scope; explicit fallback |
| P1 | Secret streamed to a remote program | Secret never enters command body, argv, audit, or artifact | Out of scope; protected stdin fallback |
| P1 | PowerShell 5.1-only host | BOM/encoding plus selectable shell | Not covered; retain `ps-ssh` fallback |
| P1 | IP/ad-hoc identity invocation | Select identity without putting credentials in sshai config | Use an `ssh_config` alias or fallback |
| P1 | Interactive/REPL/foreground stream | Terminal semantics and operator control | Out of scope; explicit fallback |
| P1 | Nested two-hop from a Windows runner | Preserve quoting, encoding, and inner exit evidence | Prefer `ProxyJump`; otherwise fallback |

## Evidence behind the inventory

- Windows diagnostics use short reachability checks, multi-line PowerShell, event/service queries,
  large results, negative controls, and repeated checks across more than one host.
- Windows deployment uses ordered mutations, reboots, post-change verification, PowerShell module
  and non-interactive-shell quirks. `sshai` changes the transport only; it does not authorize those
  mutations or replace their runbooks.
- Linux operations use working-directory continuity, Docker/systemd status, logs, database checks,
  multi-step deploy commands, explicit identity files, and separate backup transfers.
- The current `ps-ssh` workflow additionally covers PowerShell 5.1 fallback, nested two-hop shapes,
  and error diagnosis. Those remain fallback requirements until `sshai` has equivalent verified
  coverage or the workflow is retired.

## Minimum migration gaps and result

1. Completed: install the current binary in a stable PATH location and smoke-test the installed
   artifact.
2. Completed: persist body-file commands as hash-only metadata; heuristic redaction is not a safe
   boundary for arbitrary script text.
3. Completed for Codex: route agent instructions to `sshai` by default for covered
   command-execution scenarios.
4. Completed: keep fallbacks explicit for secret stdin, file transfer, interactive work,
   PowerShell 5.1, ad-hoc identity selection, and unsupported two-hop cases.
5. Completed: run fresh safe controls on at least one Windows and one Linux host before declaring
   migration complete.

## Live acceptance sequence

For each OS, use a non-production-safe target and record only the compact passport facts:

1. a small successful command;
2. a multi-line body with Cyrillic and emoji;
3. output larger than the passport budget, followed by a local `q` query;
4. an intentional non-zero exit that cannot mutate state;
5. the same read-only command twice with `--delta`;
6. a subsequent normal command proving the failed/large run did not poison the context.

Windows and Linux controls must use configured SSH aliases. Do not add or rotate keys, change a
server, or put a secret in a command merely to satisfy this gate.

## Live acceptance evidence

Completed 2026-08-12 with the installed `~/.local/bin/sshai` binary and isolated contexts. Only
synthetic read-only commands were used; the normal Windows staging file and local sshai artifacts
were the only writes.

| Control | Windows PowerShell 7 | Linux bash |
|---|---|---|
| Small command | `a10`, exit 0, one readable line | `a17`, exit 0, `Linux` |
| Multi-line Cyrillic/emoji | `a11`, exit 0, two byte-intact lines | `a18`, exit 0, two byte-intact lines |
| Large output | `a12`, 220 lines, bounded tail passport; `q` counted 220 | `a19`, 220 lines, bounded tail passport; `q` counted 220 |
| Honest failure | `a13`, passport and local process both exit 7 | `a20`, passport and local process both exit 7 |
| Repeated delta | `a14`/`a15`, second run reported no change | `a21`/`a22`, second run reported no change |
| Post-failure health | `a24`, exit 0, `ok-after-failure` | `a23`, exit 0, `ok-after-failure` |

For the eight live body-file runs, the run-log contained eight `body:<hash>` records and zero rows
matching known body text; `audit.jsonl` also contained none of the known test body strings.

Fresh-process Codex proof loaded the applied global reference and shared PowerShell-over-SSH skill,
then selected installed `sshai` as the default while naming PowerShell 5.1 and unsupported two-hop
behavior as legacy fallback conditions. This proves instruction loading; continued real-session use
is the operational control for the default-path adoption criterion.
