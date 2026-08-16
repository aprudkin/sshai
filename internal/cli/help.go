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
var helpOrder = []string{"run", "q", "diff", "log", "hosts", "gc", "help"}

// helpSummary is the one-line-each inventory `sshai help` prints with no
// argument. Kept short and hand-written (not reflected off flag.FlagSet)
// so the whole screen stays comfortably under the brief's 400-estimated-
// token budget (see TestHelpDefaultListsEverySubcommandUnderTokenBudget).
var helpSummary = map[string]string{
	"run":   "run [flags] <host...> -- <command>      execute a command on one or more hosts (fan-out)",
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

Execute <command> on one or more hosts over SSH. Multiple hosts fan out
concurrently; results print in host (argv) order, followed by one
aggregate line ("hosts=N ok=X failed=Y transport-errors=Z"). The command
body is never placed in ssh/scp argv: use "-- <words>" for a short inline
command in sshai's own argv, or --body-file for anything long, multi-line,
secret, or containing characters that would need shell escaping ("-"
reads the body from stdin instead of a file).

Output is a compact passport, not raw output: a status line carrying
exactly one of exit=N or transport-error=R, then "file=<path>" pointing
at the captured result on disk, then either the full captured body (when
it fits the budget) or its last 3 lines ("tail3:"). Query the file locally
with ` + "`sshai q`" + ` or your own tools. Only captured output up to the configured stream cap
is retained; output beyond that cap is discarded and marked truncated=1.

Flags:
  --body-file FILE   read the command body from FILE ("-" for stdin)
                      instead of the "-- command" form
  --delta             print a diff against the previous run of the same
                      (host, ctx, command) key instead of this run's own
                      output; the new run is still stored in full either
                      way, so history is never lost to delta mode
  --budget N          output budget in tokens (~bytes/4); default from
                      config (factory default 500).
                      Fan-out divides this across hosts, floored at
                      100 tokens/host
  --timeout N         per-host timeout in seconds; default from config
                      (factory default 60). On
                      timeout the passport reports transport-error=timeout
  --ctx NAME          named state context: cwd and env persist per
                      (host, ctx) between calls; default $SSHAI_CTX or
                      "default"
  --result-format FORMAT  output format: "human" (default) or "json".
                          "json" emits one versioned (schema_version=v1)
                          machine-readable envelope on stdout — run id,
                          host, exit, artifact path, byte/line counts,
                          duration, sha256 — with no human tail/preview
                          text. Default "human" is unchanged.
  --result-out FILE       only with --result-format=json: atomically replace
                          FILE with one private envelope (mode 0600).
                          Existing regular files are replaced; symlinks and
                          other non-regular paths are refused.


sshai's own process exit mirrors the remote command's exit code.
Reserved: 96 usage error, 97 policy denied (host marked readonly,
command not on the allowlist), 98 transport error (delivery failed, the
command may not have run at all). A genuine remote exit of 96/97/98 is
never confused with these: the status line's exit=N vs transport-error=R
is the source of truth, not the process exit code alone.
`,
	"q": `sshai q [--budget N] <id> -- <tool> <args...>

Run a local tool (grep, jq, awk, cat, ...) against a stored artifact's
file, without pulling the raw artifact back into context: <id>'s file
path is appended as the tool's final argument. stdout and stderr are
each budget-trimmed independently. The tool's own exit code is mirrored,
except when the tool cannot be found on PATH, or dies from a signal
(negative exit code) — both fall back to exit 96 (usage), since neither
is a real exit status to mirror.

Flags:
  --budget N   output budget in tokens (~bytes/4); default 500 —
               independent of ` + "`run`" + `'s own --budget/config default

Example: sshai q a17 -- grep -iE 'fatal'
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

Search the local run-log — every run recorded by ` + "`run`" + `, whether or not
its artifact survived retention — newest first. One line per match:
  <id>  <ts>  <host>  exit=N|transport-error=R  <duration>  <command>
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
	b.WriteString("sshai — context-frugal remote execution for AI agents (SSH: Linux bash, Windows PowerShell)\n\n")
	for _, name := range helpOrder {
		b.WriteString("  " + helpSummary[name] + "\n")
	}
	b.WriteString("\nsshai help <command> for the full flag reference.\n")
	return b.String()
}
