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
