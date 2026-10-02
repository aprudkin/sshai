// internal/cli/help.go
package cli

import (
	"fmt"
	"io"
	"strings"
)

// helpOrder lists every sshai subcommand in the exact order the default
// screen prints them — main.go's dispatch inventory (run, q, diff, log,
// hosts, gc) plus help itself, so the R5 progressive-disclosure surface
// (design doc) never drifts from what cmd/sshai/main.go actually wires.
var helpOrder = []string{"run", "local", "q", "diff", "log", "hosts", "gc", "help"}

// helpSummary is the one-line-each inventory `sshai help` prints with no
// argument. Kept short and hand-written (not reflected off flag.FlagSet)
// so the whole screen stays comfortably under the brief's 400-estimated-
// token budget (see TestHelpDefaultListsEverySubcommandUnderTokenBudget).
var helpSummary = map[string]string{
	"run":   "run [flags] <host...> -- <command>      execute a command on one or more hosts (fan-out)",
	"local": "local [flags] --shell S -- <command>     execute through an explicit local bash or pwsh",
	"q":     "q [--budget N] <id> -- <tool> <args...>  run a local tool over a stored artifact",
	"diff":  "diff [--budget N] <id1> <id2>            unified diff between two artifacts",
	"log":   "log [--host H] [--since T] [--grep P]    search the run-log",
	"hosts": "hosts                                    list known host aliases, detected OS, readonly flag",
	"gc":    "gc                                       prune artifacts per retention policy",
	"help":  "help [command]                           this screen, or the full reference for one command",
}

// helpDetail is `sshai help <command>`'s full flag reference — the flags
// and semantics Tasks 11-15 actually implemented (run.go, query.go,
// misc.go), written out for an agent to read once rather than reflected
// off flag.FlagSet: the code in those files remains the source of truth,
// and this text is checked against it (see help_test.go's flag-presence
// assertions) rather than generated from it, so drift is caught by a
// failing test instead of silently inheriting flag.FlagSet's own terse
// usage strings.
var helpDetail = map[string]string{
	"run": `sshai run [flags] <host...> -- <command>
sshai run [flags] --body-file <file|-> <host...>

Put all sshai options before the first host; at least one host is required.
For example: sshai run --body-file check.ps1 windows01
Not:         sshai run windows01 --body-file check.ps1
In the inline form, everything after the host/command separator -- is command
text, not sshai options. Misplaced known options are rejected, not reordered.

Execute <command> on one or more hosts over SSH. Multiple hosts fan out
concurrently; results print in host (argv) order, followed by one
aggregate line ("hosts=N ok=X failed=Y transport-errors=Z", with
"setup-errors=N" only when applicable). The command
body is never placed in ssh/scp argv: use "-- <words>" for a short inline
command in sshai's own argv, or --body-file for anything long, multi-line,
secret, or containing characters that would need shell escaping ("-"
reads the body from stdin instead of a file).

Output is a compact passport, not raw output: a status line carrying
exactly one of exit=N, setup-error=windows-shell, or transport-error=R, then "file=<path>" pointing
at the captured result on disk, then either the full captured body (when
it fits the budget) or a suffix of its last 3 lines ("tail3:"). The body
preview is bounded to 4 bytes per budget token, clipping at UTF-8 boundaries;
framing and the "preview omitted" query notice add at most 80 bytes, excluding
the status/path metadata and final newline. Preview omission does not change
the retained artifact or imply truncated=1. Recognized SSH failures
store only a safe canonical diagnostic in that artifact; raw SSH error text
is never exposed. Only captured output up to the configured stream cap is
retained; output beyond that cap is discarded and marked truncated=1.
Query the file locally with ` + "`sshai q`" + ` or your own tools.

Saved transport/setup failures add failure-phase=probe|stage|exec and
remote-completion=not_started|unknown to the status line (JSON fields
failure_phase and remote_completion, also in follow completed outcomes).
probe covers discovery/setup; stage covers Windows script upload;
exec covers the execution boundary, including deadline expiry before dispatch.
not_started means this invocation did not dispatch user-body execution;
unknown means execution was attempted but completion is unconfirmed.
Probe/setup and stage failures are not_started. An exec failure is unknown
unless the deadline expired before dispatch. These fields do not diagnose the
root cause or prove cancellation. Check actual remote state before replaying side effects.
Normal command results, local runs and older records omit the fields.
Missing fields are not evidence that the body did not run.

Flags:
  --body-file FILE   read the command body from FILE ("-" for stdin)
                      instead of the "-- command" form
  --posix-shell PATH select the Linux interpreter, for example /bin/ash
                      on OpenWrt. Bash remains the default when omitted;
                      Windows hosts are unaffected
  --powershell-host HOST
                      explicitly require "pwsh" (PowerShell 7) or
                      "windows-powershell" (Windows PowerShell 5.1).
                      When omitted, prefer PowerShell 7 and fall back to 5.1
                      if unavailable. Linux fan-out hosts are unaffected
  --accept-new-host-key HOST
                      only after direct user authorization for this exact
                      alias: add a previously unknown key for HOST, while
                      still refusing changed known keys. HOST must occur
                      exactly once in this invocation; a newly added key's
                      algorithm and SHA256 fingerprint are returned
  --proxy-jump none   disable the configured ProxyJump for this invocation
                      only. ssh_config is not modified; omitting the flag
                      preserves the configured route
  --delta             print a diff against the previous run of the same
                      (host, ctx, command) key instead of this run's own
                      output; the new run is still stored in full either
                      way, so history is never lost to delta mode
  --budget N          output budget in tokens (~bytes/4); default from
                      config (factory default 500).
                      Fan-out divides this across hosts, floored at
                      100 tokens/host
  --timeout N         per-host budget in seconds; default from config timeout_sec
                      (factory default 60). Starts before cached facts are
                      loaded, after local policy/selector validation; covers
                      probing/setup, script staging, execution and output
                      draining to EOF, even after SSH exits.
                      Each later step gets only the remaining time; fan-out
                      hosts have independent budgets. --follow does not
                      extend it. Timeout reports transport-error=timeout,
                      not guaranteed remote cancellation. Local SSH/SCP
                      pipe cleanup allows up to 100ms extra plus scheduling;
                      result persistence/publication is outside this budget
  --ctx NAME          named state context: cwd and env persist per
                      (host, ctx) between calls; default $SSHAI_CTX or
                      "default"
  --result-format FORMAT  output format: "human" (default) or "json".
                          "json" emits one versioned (schema_version=v1)
                          machine-readable envelope on stdout — run id,
                          host, exit, artifact path, byte/line counts,
                          duration, sha256, optional safe transport
                          diagnostic, and optional explicitly accepted host
                          key algorithm/fingerprint — with no human
                          tail/preview text. Default "human" is unchanged.
  --result-out FILE       only with --result-format=json: atomically replace
                          FILE with one private envelope (mode 0600).
                          Existing regular files are replaced; symlinks and
                          other non-regular paths are refused.
  --follow                for exactly one host, write ephemeral live JSONL v1
                          events to stderr. Output is combined UTF-8, capped at
                          64 KiB/256 lines and 4 KiB per raw data payload, with
                          at most one output event per 100ms. Final stdout and
                          --result-out remain unchanged
                          and are written only on completion
  --follow-interval N     heartbeat interval in seconds while following;
                          default 10, minimum 1, requires --follow


Long-running example (replace web01 and long-running-check with an authorized
alias and command expected to take about four minutes):
  sshai run --timeout 300 --follow --follow-interval 5 web01 -- long-running-check

The agent harness's shell-tool timeout is a separate outer deadline. Increasing
it does not change sshai's --timeout or configured default. Set it separately
with room for the whole CLI operation: initialization, the per-host budget,
local cleanup and result persistence/publication. For this example, 330 seconds
leaves 30 seconds of local-overhead headroom, not a guarantee for slow local I/O.
Do not set both deadlines equal; choose finite task-appropriate limits before
execution, not automatic increases or retries after a timeout. If the harness
stops waiting first, the final passport or JSON result may be missing; that does
not establish the remote outcome or guarantee cancellation.
--follow does not extend or reset --timeout. --follow-interval only changes
heartbeat frequency: heartbeats show elapsed time, not application progress.
Live previews are non-authoritative; assess the final result and retained artifact.

sshai's own process exit mirrors the remote command's exit code.
Reserved: 96 usage error, 97 policy denied (host marked readonly,
command not on the allowlist), 98 transport error (delivery or observation
failed; execution may have occurred), 99 Windows shell setup error (SSH
connected but neither supported PowerShell setup form worked). A genuine remote
exit of 96/97/98/99 is never confused with these: the status line's exit=N,
setup-error=R, or transport-error=R is the source of truth, not the process
exit code alone.
`,
	"local": `sshai local [flags] --shell <bash|pwsh> -- <command>
sshai local [flags] --shell <bash|pwsh> --body-file <file|->

Execute arbitrary code through the explicitly selected interpreter on the
machine running sshai. This is not SSH, a remote fallback, an authorization
layer, or a security sandbox. Only the direct interpreter process is stopped
on timeout or output overflow; cross-platform descendant-process cleanup is
outside this command's contract.

The body never enters interpreter argv. Bash runs as "bash -s" with the
wrapped body on stdin. PowerShell runs only "pwsh -NoProfile -File" with a
private temporary script; there is no Windows PowerShell 5.1 fallback.
Results use the same bounded artifacts, passports, JSON v1 envelope, state,
delta, history, query, and retention machinery as remote runs. Stable targets
"local-bash" and "local-pwsh" isolate shell state and appear in results and
logs, but never in ` + "`hosts`" + `.
The body preview uses at most 4 bytes per budget token plus at most 80 bytes
of framing/omission notice, excluding status/path metadata and the final
newline. Oversized tails are clipped at UTF-8 boundaries; "preview omitted"
points to retained output via sshai q and does not imply truncated=1.

Flags:
  --shell SHELL       required: "bash" or "pwsh"
  --body-file FILE    read body from FILE ("-" for stdin) instead of the
                      "-- command" form; use this for multiline bodies that must stay out of argv
  --delta             diff against the previous matching local shell/context/body
  --budget N          passport output budget in tokens (~bytes/4); default from config
  --timeout N         interpreter and output-drain timeout in seconds;
                      default from config; child exit alone does not end capture
  --ctx NAME          named state context; default $SSHAI_CTX or "default"
  --result-format FORMAT
                      "human" (default) or a JSON v1 envelope
  --result-out FILE   atomically write the private JSON envelope; requires
                      --result-format=json

A normal shell exit with complete capture is stored and mirrored. Output is
drained to EOF within the same timeout, including descendant output after the
shell exits and after its state epilogue. A pipe still open at the deadline is
a timeout even if the shell exited zero.
Start failure, timeout, output overflow, and other capture failure are stored
as local-error=start, local-error=timeout, local-error=output-limit, or
local-error=capture and sshai exits 96. Failures retain partial raw output,
skip shell-state updates, and use metadata/JSON exit=0 only as a placeholder
paired with local_error, not success. Overflow retains exactly the configured
stream cap and marks truncated=1; truncated=false does not prove complete
capture on a failed run. Pipe cleanup allows up to 100ms extra plus scheduling.
Remote-only flags and --follow are rejected.
`,
	"q": `sshai q [--budget N] <id> -- <tool> <args...>

Run a local tool against a stored artifact without pulling its raw bytes
back into context. The artifact is not sent on stdin. q invokes:
  <tool> <args...> <artifact-path>
so the artifact path is always the final argv element. stdout and stderr
are each budget-trimmed independently. The tool's own exit code is mirrored,
except when the tool cannot be found on PATH, or dies from a signal
(negative exit code) — both fall back to exit 96 (usage), since neither
is a real exit status to mirror.

Flags:
  --budget N   output budget in tokens (~bytes/4); default 500 —
               independent of ` + "`run`" + `'s own --budget/config default

Example (Python consumes the final argv path):
  sshai q a17 -- python3 -c 'import sys; print(open(sys.argv[-1]).readline())'
`,
	"diff": `sshai diff [--budget N] <id1> <id2>

Unified diff (3 lines of context), computed locally between two stored
artifacts — works across hosts and across runs. Identical artifacts
print "no difference" instead of an empty diff.

Flags:
  --budget N   output budget in tokens (~bytes/4); default 500

Exit code is 0 in both cases — whether or not the artifacts differ, not
just when they don't. This is a query tool for an agent to read text
from, not diff(1): a caller expecting diff(1)'s conventional exit 1 on a
non-empty diff will not get it; the distinction is already visible in
the printed text itself.

Example: sshai diff a12 a17
`,
	"log": `sshai log [--host H] [--since T] [--grep P] [--limit N]

Search the local run-log — every run recorded by ` + "`run`" + ` or ` + "`local`" + `, whether
or not its artifact survived retention — newest first. One line per match:
  <id>  <ts>  <host>  exit=N|setup-error=R|transport-error=R|local-error=R  <duration>  <command>
<command> is clipped to 60 runes with a trailing "…" marker.

Flags:
  --host H    filter by host
  --since T   only runs at or after T: a duration ("2h", "30m", "7d")
              or a date ("2026-08-01")
  --grep P    filter: substring match on command text
  --limit N   maximum number of runs to print; default 20

Example: sshai log --host web01 --since 2h --grep nginx
`,
	"hosts": `sshai hosts

List every host alias sshai knows: the union of every non-wildcard Host
pattern in $HOME/.ssh/config and every [hosts.X] entry in config.toml,
sorted, one line each:
  <name>  os=<linux|windows|->  readonly=<true|false>

os is read from the per-host facts cached by the most recent ` + "`run`" + `
against that host if any, else config.toml's [hosts.X].os, else shown
as "-" — hosts never probes a host on demand, it only reports what is
already known.
`,
	"gc": `sshai gc

Prune stored artifacts past config.toml's retention policy: rows older
than retention_days, then (whatever remains) the oldest artifacts by
size until under retention_max_bytes, plus any orphaned temp file left
by a crashed write. Prints "pruned N artifacts, freed X".

Run-log rows are never deleted by pruning — only the artifact file is
reclaimed. After that, ` + "`sshai q`" + `/` + "`diff`" + ` against a pruned id report
"artifact pruned" (metadata intact, data reclaimed).

` + "`run`" + ` also triggers this same pruning opportunistically when the store
grows past the size cap; the artifact(s) that triggering run itself just
wrote are always exempt from that opportunistic pass. A standalone
` + "`sshai gc`" + ` invocation, like this one, has no such exemption — the
oldest artifacts go first with no exceptions, including the newest one
if that's what it takes to get under cap.
`,
	"help": `sshai help [command]

With no argument: one line per subcommand. With a command name: the
full flag reference and semantics for that command.
`,
}

// Help implements `sshai help [command]`. Deliberately the one subcommand
// that never calls config.Load or artifact.OpenStore: a fresh agent's very
// first invocation may well be `sshai help`, before ~/.sshai exists at all
// (see TestHelpNeverTouchesStoreOrConfig), and R5's whole point (design
// doc) is that this information is available for free, with no setup cost.
func Help(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprint(stdout, renderHelpDefault())
		return 0
	}
	if len(args) != 1 {
		fmt.Fprintln(stderr, "help: usage: sshai help [command]")
		return exitUsage
	}
	topic := args[0]
	detail, ok := helpDetail[topic]
	if !ok {
		fmt.Fprintf(stderr, "help: unknown command %q\n", topic)
		return exitUsage
	}
	fmt.Fprint(stdout, detail)
	return 0
}

// renderHelpDefault builds the bare `sshai help` screen: one line per
// helpOrder entry, from helpSummary.
func renderHelpDefault() string {
	var b strings.Builder
	b.WriteString("sshai — context-frugal remote and explicit local execution for AI agents\n\n")
	for _, name := range helpOrder {
		b.WriteString("  " + helpSummary[name] + "\n")
	}
	b.WriteString("\nsshai help <command> for the full flag reference.\n")
	return b.String()
}
