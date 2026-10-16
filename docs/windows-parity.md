# Windows parity gate

Live target: Windows fleet host with PowerShell DefaultShell. Date: 2026-08-08.

The Task 16 manual controls from the v1 plan were run through the built `sshai` binary:

1. `Get-Date` returned `exit=0` with readable output and no CLIXML noise.
2. A body file returned `эмодзи 🚀 и кириллица` byte-intact.
3. `Set-Location C:\Windows` persisted across a second call in the same context.
4. `exit 5` initially exposed loss of the native child exit code in a PowerShell DefaultShell.
   After adding explicit `$LASTEXITCODE` propagation, the passport and local process both returned
   `5`; a following call confirmed that `C:\Windows` state survived the failed run.
5. `Get-Service` returned a tail passport and stored the complete captured artifact: the passport
   reported 209 lines and a line-inclusive local count reported 209. The plan's literal
   `grep -c '.'` probe reported 208 because standard `Get-Service` output contains one empty line;
   `grep .` excludes empty lines, so it is not a valid total-line counter for this output.

No production state was changed by these controls beyond sshai's normal remote staging file and
local run artifacts/state.

## Issue #42: no-shell-state live verification

Date: 2026-10-16. Tested revision: `5e2393edfeabcf49ebcee381f94829cadb8a0025`.
The operator explicitly authorized one non-production Windows target. A temporary local build
was used without installation, with a separate `SSHAI_ROOT`, ordinary configured OpenSSH routing
and strict host-key behavior. Target identifiers and captured remote output are intentionally
excluded from this record.

Verified interpreter versions on that target:

- Windows PowerShell **5.1.20348.5622**, Desktop edition.
- PowerShell **7.6.3**.

The following live controls passed through `sshai run --no-shell-state`:

1. Twelve cases: success (exit 0), explicit exit 7 and a synthetic terminating exception (exit 1),
   each with and without `--follow`, under each explicitly selected interpreter.
2. A fresh invocation created host facts but no shell-state or baseline snapshot. Subsequent
   calls left pre-created synthetic cwd/environment/baseline snapshots byte-for-byte unchanged;
   the body checked that its task-specific synthetic replay variable was absent.
3. Bodies set a synthetic process-environment canary without printing it and used a disposable
   working directory. Canary values were absent from retained command output, JSON envelopes,
   audit/history and follow events, including checks for base64-encoded state dumps.
4. JSON v1, `shell_state_mode: "none"`, exact exit codes and result-file/stdout equality were
   preserved. Ordinary command failures did not acquire transport/setup failure fields.
5. Follow emitted `started`, heartbeat, output and `completed` events with the expected process
   exit and mode. Wrapper control markers did not appear in retained output or follow previews.
6. Repeating an identical no-state body selected the first run as the second run's delta base.
   Local `q` and history queries returned the expected permitted output and mode metadata.
7. Each body removed its own temporary directory and staged script in `finally`. A final scoped
   check found none of the 14 unique preceding task-owned staging filenames remaining; that
   cleanup script also removed itself. No wildcard cleanup of remote staging was used.

No application data, services, permissions or server configuration were changed. The ordinary
inherited environment was not enumerated or saved. The client was not installed on either machine.
This is actual Windows PowerShell 5.1 runtime evidence, not parity inferred from PowerShell 7.

Live timeout, truncation, transport/setup failures, cross-mode/legacy comparisons and default
stateful capture were not exercised on this target. Those paths retain the isolated synthetic
wrapper/orchestration regression coverage from #42; no real environment snapshot was collected
merely to repeat stateful tests. This gate covers the listed versions and scenarios, not every
Windows build or OpenSSH configuration.
