// internal/cli/run.go
package cli

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/config"
	"github.com/aprudkin/sshai/internal/delta"
	"github.com/aprudkin/sshai/internal/policy"
	"github.com/aprudkin/sshai/internal/runlog"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Reserved exit codes, mirrored from cmd/sshai/main.go (a separate package
// can't share unexported constants, so the values are duplicated — see
// that file's own copies).
const (
	exitUsage     = 96
	exitPolicy    = 97
	exitTransport = 98
	exitSetup     = 99

	powerShellHostPwsh              = "pwsh"
	powerShellHostWindowsPowerShell = "windows-powershell"
)

// Deps bundles runHost's external dependencies. Tr is the only one worth
// faking in a unit test (see runWith): Store and its Root are backed by a
// real SQLite file and a real filesystem, which a t.TempDir()-rooted
// SSHAI_ROOT already gives a test for free, without touching the network.
type Deps struct {
	Tr     transport.Transport
	Store  *artifact.Store
	Follow *followEmitter // nil outside --follow
}

// Opts holds one host's resolved run parameters, after flag parsing and
// ctx/host validation.
type Opts struct {
	Host             string
	Ctx              string
	Command          string // the actual body run on the host (bash or PowerShell)
	FromFile         bool   // true when Command came from --body-file/stdin rather than "-- words"
	Readonly         bool
	Delta            bool // --delta: diff against the previous run of the same (host, ctx, command) key
	AcceptNewHostKey bool // explicit accept-new scope applies to this exact host only
	Budget           int
	Timeout          time.Duration
	PowerShellHost   string // "" (default pwsh) | "pwsh" | "windows-powershell"
	PosixShell       string // optional Linux shell path/token; empty keeps the bash default
}

func powerShellExecutable(host string) (string, bool) {
	switch host {
	case "", powerShellHostPwsh:
		return shell.PwshDefaultShell, true
	case powerShellHostWindowsPowerShell:
		return shell.WindowsPowerShellShell, true
	default:
		return "", false
	}
}

func validatePOSIXShell(path string) error {
	if path == "" {
		return fmt.Errorf("invalid --posix-shell=%q (want one path without whitespace or control characters)", path)
	}
	for _, r := range path {
		if unicode.IsSpace(r) || unicode.IsControl(r) {
			return fmt.Errorf("invalid --posix-shell=%q (want one path without whitespace or control characters)", path)
		}
	}
	return nil
}

// safeNameRe is the portable charset for host and context path components.
// For --ctx it excludes "/" (so a ctx value can never
// escape <root>/state/<host>/ into a parent directory), and no characters
// that would make a confusing filename.
var safeNameRe = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

// validateHost keeps an SSH alias usable as exactly one local state-directory
// component on every supported platform.
func validateHost(host string) error {
	if host == "." || host == ".." || !safeNameRe.MatchString(host) {
		return fmt.Errorf("host %q must match %s and not be . or ..", host, safeNameRe.String())
	}
	return nil
}

// validateCtx enforces the safe ctx charset before any SaveState/LoadState
// call. Two hazards, both from earlier tasks' reviews: (1) a ctx equal to
// "facts" or "baseline" would alias session's own facts.json/baseline.json
// — all three share one directory (session.hostDir) and are told apart
// only by filename; (2) a ctx containing "/" could otherwise address a
// path outside that directory entirely. safeNameRe already excludes "/", so a
// ".." component can never form — the explicit checks below for "." and
// ".." standing alone as the whole ctx are just belt-and-braces, since
// neither is a meaningful context name either way.
func validateCtx(ctx string) error {
	if ctx == "facts" || ctx == "baseline" {
		return fmt.Errorf("ctx %q is reserved", ctx)
	}
	if ctx == "." || ctx == ".." {
		return fmt.Errorf("ctx %q is not a valid context name", ctx)
	}
	if !safeNameRe.MatchString(ctx) {
		return fmt.Errorf("ctx %q must match %s", ctx, safeNameRe.String())
	}
	return nil
}

// Run parses argv for the `run` subcommand and executes it against a real
// OpenSSH transport. See runArgs for the full flow.
func Run(args []string, stdout, stderr io.Writer) int {
	return runArgs(args, stdout, stderr, nil)
}

// runWith is runArgs with an injected transport.Transport, letting unit
// tests exercise the full flow (flags, policy, facts, wrap, exec, parse,
// store, passport, audit) against a fake that never touches the network —
// see fakeTr in run_test.go.
func runWith(tr transport.Transport, args []string, stdout, stderr io.Writer) int {
	return runArgs(args, stdout, stderr, tr)
}

// runWithStore mirrors runWith but additionally accepts an already-open
// *artifact.Store. It exists so tests can drive runArgs's Save-failure
// fallback without any in-memory mocking: pass a real Store whose
// artifact directory has been poisoned beforehand (e.g. chmod 0o500 on
// <root>/art, or replaced with a regular file), and the caller's
// Store.Save returns a real error at the same call site production hits.
// store may be nil — runArgsWithStore then opens one from config and
// closes it on return, exactly like runArgs today.
func runWithStore(tr transport.Transport, store *artifact.Store, args []string, stdout, stderr io.Writer) int {
	return runArgsWithStore(args, stdout, stderr, tr, store)
}

// runArgs is the thin flag-parsing shell: it resolves flags, config,
// exactly one host, and the command body, then hands off to runHost for
// the actual per-host flow. tr is nil in production (Run), in which case
// a real OpenSSH transport is built from config; runWith passes a fake.
// The artifact.Store is built from config here; runArgsWithStore is the
// form that lets a caller inject one (used by runWithStore and any future
// test that needs to exercise Store.Save's failure modes).
func runArgs(args []string, stdout, stderr io.Writer, tr transport.Transport) int {
	return runArgsWithStore(args, stdout, stderr, tr, nil)
}

// runArgsWithStore is runArgs with an optional pre-built *artifact.Store.
// When store is nil, runArgsWithStore opens one from cfg.Root and defers
// Close — the original runArgs behavior, preserved verbatim. When store
// is non-nil, runArgsWithStore uses it directly and never calls Close;
// the caller (typically a test) owns its lifetime.
func runArgsWithStore(args []string, stdout, stderr io.Writer, tr transport.Transport, store *artifact.Store) int {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	fs.SetOutput(stderr)
	bodyFile := fs.String("body-file", "", `read the command body from a file ("-" for stdin) instead of the "-- command" form`)
	posixShell := fs.String("posix-shell", "", "Linux shell path; empty keeps bash")
	powerShellHost := fs.String("powershell-host", "", `Windows interpreter: "pwsh" (default) or "windows-powershell"`)
	acceptNewHostKey := fs.String("accept-new-host-key", "", "exact host alias allowed to add one previously unknown host key")
	proxyJump := fs.String("proxy-jump", "", `one-invocation jump-route override: "none"`)
	wantDelta := fs.Bool("delta", false, "print diff vs previous run of same (host, ctx, command)")
	budget := fs.Int("budget", 0, "output budget in tokens (~bytes/4); default from config")
	timeoutFlag := fs.Int("timeout", 0, "timeout in seconds; default from config")
	ctxFlag := fs.String("ctx", "", `named state context; default $SSHAI_CTX or "default"`)
	resultFormat := fs.String("result-format", "human", `output format: "human" (default) or "json"`)
	resultOut := fs.String("result-out", "", `write the JSON envelope to FILE (requires --result-format=json)`)
	follow := fs.Bool("follow", false, "write live JSONL progress events to stderr")
	followInterval := fs.Int("follow-interval", 10, "seconds between --follow heartbeat events (minimum 1)")

	if err := fs.Parse(args); err != nil {
		return exitUsage
	}

	posixShellSet := false
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "posix-shell" {
			posixShellSet = true
		}
	})
	if *resultFormat != "human" && *resultFormat != "json" {
		fmt.Fprintf(stderr, "run: invalid --result-format=%q (want human or json)\n", *resultFormat)
		return exitUsage
	}
	if *followInterval < 1 {
		fmt.Fprintln(stderr, "run: --follow-interval must be at least 1")
		return exitUsage
	}
	followIntervalSet := false
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "follow-interval" {
			followIntervalSet = true
		}
	})
	if followIntervalSet && !*follow {
		fmt.Fprintln(stderr, "run: --follow-interval requires --follow")
		return exitUsage
	}
	if *resultOut != "" && *resultFormat != "json" {
		fmt.Fprintln(stderr, "run: --result-out requires --result-format=json")
		return exitUsage
	}
	if _, ok := powerShellExecutable(*powerShellHost); !ok {
		fmt.Fprintf(stderr, "run: invalid --powershell-host=%q (want pwsh or windows-powershell)\n", *powerShellHost)
		return exitUsage
	}
	if posixShellSet {
		if err := validatePOSIXShell(*posixShell); err != nil {
			fmt.Fprintf(stderr, "run: %v\n", err)
			return exitUsage
		}
	}
	if *proxyJump != "" && *proxyJump != "none" {
		fmt.Fprintf(stderr, "run: invalid --proxy-jump=%q (want none)\n", *proxyJump)
		return exitUsage
	}

	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintf(stderr, "run: load config: %v\n", err)
		return exitUsage
	}

	ctx := *ctxFlag
	if ctx == "" {
		ctx = os.Getenv("SSHAI_CTX")
	}
	if ctx == "" {
		ctx = "default"
	}
	if err := validateCtx(ctx); err != nil {
		fmt.Fprintf(stderr, "run: %v\n", err)
		return exitUsage
	}

	budgetTokens := *budget
	if budgetTokens <= 0 {
		budgetTokens = cfg.BudgetTokens
	}
	timeoutSec := *timeoutFlag
	if timeoutSec <= 0 {
		timeoutSec = cfg.TimeoutSec
	}
	timeout := time.Duration(timeoutSec) * time.Second

	rest := fs.Args()

	var hosts []string
	var command string
	fromFile := *bodyFile != ""
	if fromFile {
		hosts = rest
		body, err := loadBody(*bodyFile)
		if err != nil {
			fmt.Fprintf(stderr, "run: %v\n", err)
			return exitUsage
		}
		command = body
	} else {
		before, after, found := splitAtDashDash(rest)
		if !found {
			fmt.Fprintln(stderr, "run: a command is required: use `-- <command words>` or --body-file")
			return exitUsage
		}
		hosts = before
		command = strings.Join(after, " ")
	}

	if len(hosts) == 0 {
		fmt.Fprintln(stderr, "run: at least one host is required")
		return exitUsage
	}
	for _, host := range hosts {
		if err := validateHost(host); err != nil {
			fmt.Fprintf(stderr, "run: %v\n", err)
			return exitUsage
		}
	}
	if *follow && len(hosts) != 1 {
		fmt.Fprintln(stderr, "run: --follow supports exactly one host")
		return exitUsage
	}
	if *acceptNewHostKey != "" {
		matches := 0
		for _, host := range hosts {
			if host == *acceptNewHostKey {
				matches++
			}
		}
		if matches != 1 {
			fmt.Fprintln(stderr, "run: --accept-new-host-key must name exactly one host alias in this invocation")
			return exitUsage
		}
	}

	// Resolve the artifact store: a caller-injected one (runWithStore)
	// is used as-is and never closed here — the caller owns its lifetime.
	// The nil path reproduces runArgs's prior behavior (OpenStore +
	// defer Close) verbatim.
	storeOwns := false
	if store == nil {
		s, err := artifact.OpenStore(cfg.Root)
		if err != nil {
			fmt.Fprintf(stderr, "run: open store: %v\n", err)
			return exitUsage
		}
		store = s
		storeOwns = true
	}
	if storeOwns {
		defer store.Close()
	}

	if tr == nil {
		// ssh refuses to bind its ControlMaster socket under a directory
		// that doesn't exist yet ("cannot bind to path ...: No such file
		// or directory", surfaced as a plain connection failure — rc 255,
		// TransportError{"ssh"}) — NewOpenSSH itself never creates
		// controlDir (see its doc comment: "pointed at a socket directory
		// under controlDir", not "creates"), so that's this call site's
		// job, same as OpenStore already does for <root>/art.
		controlDir := filepath.Join(cfg.Root, "cm")
		if err := os.MkdirAll(controlDir, 0o700); err != nil {
			fmt.Fprintf(stderr, "run: create control dir: %v\n", err)
			return exitUsage
		}
		tr = transport.NewOpenSSH(controlDir, cfg.ControlPersist, cfg.StreamCapBytes, transport.OpenSSHOptions{
			AcceptNewHostKey: *acceptNewHostKey,
			ProxyJumpNone:    *proxyJump == "none",
		})
	}

	deps := Deps{Tr: tr, Store: store}

	perHostBudget := budgetTokens
	if len(hosts) > 1 {
		perHostBudget = fanoutBudget(budgetTokens, len(hosts))
	}
	hostOpts := make([]Opts, len(hosts))
	for i, host := range hosts {
		hostOpts[i] = Opts{
			Host:             host,
			Ctx:              ctx,
			Command:          command,
			FromFile:         fromFile,
			Readonly:         cfg.Hosts[host].Readonly,
			Delta:            *wantDelta,
			AcceptNewHostKey: host == *acceptNewHostKey,
			Budget:           perHostBudget,
			Timeout:          timeout,
			PowerShellHost:   *powerShellHost,
			PosixShell:       *posixShell,
		}
	}
	mode := resultModeOptions{format: *resultFormat, resultOut: *resultOut}
	var rc int
	var outcomes []RunOutcome
	if *follow {
		sentinel := shell.NewSentinel()
		marker := sentinel + "_FOLLOW"
		deps.Follow = newFollowEmitter(stderr, hosts[0], marker, sentinel)
		rc, outcomes = runInvocationFollow(deps, hostOpts, mode, stdout, stderr, time.Duration(*followInterval)*time.Second)
	} else {
		rc, outcomes = runInvocation(deps, hostOpts, mode, stdout, stderr)
	}
	ids := make([]string, len(outcomes))
	for i, outcome := range outcomes {
		ids[i] = outcome.ArtifactID()
	}
	maybeGC(deps.Store, cfg, stderr, protectSet(ids...))
	return rc
}

// protectSet builds gcStore's protect set from the artifact ids this
// invocation of `run` itself just wrote — one for the single-host path,
// up to N for fan-out. An empty id (a host that never reached Save, e.g.
// policy-denied, or a Save that itself failed) is skipped rather than
// protecting the empty string, which is never a real art_id.
func protectSet(ids ...string) map[string]bool {
	protect := make(map[string]bool, len(ids))
	for _, id := range ids {
		if id != "" {
			protect[id] = true
		}
	}
	return protect
}

// maybeGC runs gc opportunistically, at most once per `run` invocation —
// called here once after every host in this invocation has already run
// (never per host inside runInvocation) — when the artifact store's
// total non-pruned size has grown past cfg.RetentionMaxBytes. The size
// check itself is a single SUM(bytes) query rather than a filesystem walk:
// the "cheap check" the task brief calls for. Pruning itself goes through
// the same gcStore the `gc` command uses (misc.go), with the identical
// cutoff computation (retentionCutoff) so an opportunistic run and an
// explicit `sshai gc` agree on what counts as prunable.
//
// This is deliberately non-fatal: by the time this runs, the passport has
// already been written to stdout and the exit code already decided, so a
// gc failure here must never change either — only a stderr note, never
// the return value.
//
// protect carries every artifact id this very invocation of `run` just
// wrote (built by protectSet, above, from runInvocation's
// return values) — one id for the single-host path, up to N for
// fan-out. This call runs immediately after those Save(s), so every id
// in protect is always among the newest live rows in the store at this
// point; gcStore exempts all of them from BOTH its age and size passes
// (see gcStore's own doc comment for why age too, not just size).
// Without this, a misconfigured RetentionMaxBytes (or RetentionDays=0)
// smaller than this very invocation's own output could delete the
// artifact(s) whose ids this run's own passport(s) just printed
// ("file=...") before a caller ever gets to read them back — and in
// fan-out, ALL N of this invocation's artifacts need protecting, not
// just one, since gcStore has no other way to single out "written by
// this invocation" from "written a moment earlier by an unrelated one".
func maybeGC(store *artifact.Store, cfg config.Config, stderr io.Writer, protect map[string]bool) {
	if cfg.RetentionMaxBytes <= 0 {
		return
	}
	var total int64
	if err := store.DB.QueryRow(`SELECT COALESCE(SUM(bytes),0) FROM runs WHERE pruned=0`).Scan(&total); err != nil {
		fmt.Fprintf(stderr, "run: gc size check: %v\n", err)
		return
	}
	if total <= cfg.RetentionMaxBytes {
		return
	}
	if _, _, err := gcStore(store, retentionCutoff(cfg.RetentionDays, time.Now()), cfg.RetentionMaxBytes, protect); err != nil {
		fmt.Fprintf(stderr, "run: opportunistic gc: %v\n", err)
	}
}

// fanoutBudget divides total evenly across n hosts (integer division),
// flooring at 100 tokens per host — the brief's Step 3 rule, so a large
// host count never squeezes a single passport down to nothing useful.
func fanoutBudget(total, n int) int {
	per := total / n
	if per < 100 {
		per = 100
	}
	return per
}

// runInvocation runs hostOpts (already in argv order) concurrently against the
// shared deps, then hands every buffered host result to the single invocation
// output controller. The same path handles one host and fan-out; only the
// human renderer decides whether an aggregate line is needed.
//
// Concurrency shape, per the task brief's verified facts:
//   - deps.Tr (transport.OpenSSH in production) is documented safe for
//     concurrent Exec/Put on one instance — no shared mutable state
//     between calls (see transport/openssh.go's capWriter doc comment).
//   - deps.Store is one *artifact.Store shared across all goroutines, not
//     one per goroutine: its SQLite connection runs in WAL mode with a
//     busy_timeout (Task 4), and Save's own artifact file write derives
//     its path from the row's autoincrement id, so concurrent Save calls
//     never collide on a filename.
//   - runlog.AppendAudit opens O_APPEND and issues exactly one Write of
//     the whole line per call — the "single-write" shape its own doc
//     comment already calls out as safe for concurrent processes to
//     interleave at the line level — so each goroutine calling it via its
//     own runHost, unmodified, needs no extra serialization on this side.
//
// What is NOT shared: each goroutine gets its own bytes.Buffer for both
// stdout and stderr, since runWith's real stdout/stderr can themselves be
// a *bytes.Buffer in tests (not safe for concurrent writes) and, even
// against a real file, concurrent unsynchronized writers would defeat
// the "results collected by index — deterministic print order = argv
// order" requirement. The controller writes every buffer to the real
// stdout/stderr only after WaitGroup.Wait, strictly in argv order.
func runInvocation(deps Deps, hostOpts []Opts, mode resultModeOptions, stdout, stderr io.Writer) (int, []RunOutcome) {
	n := len(hostOpts)
	runs := make([]hostRunResult, n)

	var wg sync.WaitGroup
	for i, opts := range hostOpts {
		wg.Go(func() {
			runs[i].outcome = runHost(deps, opts, &runs[i].stdout, &runs[i].stderr)
		})
	}
	wg.Wait()

	outcomes := make([]RunOutcome, n)
	for i := range runs {
		outcomes[i] = runs[i].outcome
	}
	return writeRunResults(deps.Store.Root, runs, mode, stdout, stderr), outcomes
}

// runInvocationFollow keeps normal final rendering untouched while emitting its
// ephemeral terminal event after persistence and before that rendering.
func runInvocationFollow(deps Deps, hostOpts []Opts, mode resultModeOptions, stdout, stderr io.Writer, interval time.Duration) (int, []RunOutcome) {
	deps.Follow.setHeartbeatInterval(interval)
	var r hostRunResult
	done := make(chan struct{})
	go func() { r.outcome = runHost(deps, hostOpts[0], &r.stdout, &r.stderr); close(done) }()
	var ticker *time.Ticker
	var heartbeat <-chan time.Time
	started := deps.Follow.startedSignal
	defer func() {
		if ticker != nil {
			ticker.Stop()
		}
	}()
	for {
		select {
		case <-done:
			// Final rendering/publication must complete before the terminal
			// follow record so --result-out failures are represented without
			// contaminating follow stderr with plain diagnostics.
			var finalOut, diagnostics bytes.Buffer
			rc := writeRunResults(deps.Store.Root, []hostRunResult{r}, mode, &finalOut, &diagnostics)
			deps.Follow.completed(deps.Store.Root, r.outcome, rc, diagnostics.String())
			_, _ = stdout.Write(finalOut.Bytes())
			return rc, []RunOutcome{r.outcome}
		case <-started:
			ticker = time.NewTicker(interval)
			heartbeat = ticker.C
			started = nil
		case <-heartbeat:
			deps.Follow.heartbeat()
		}
	}
}

// loadBody reads the command body from path — "-" means stdin, matching
// the CLI surface doc's "body from file or stdin (never argv)".
func loadBody(path string) (string, error) {
	if path == "-" {
		data, err := io.ReadAll(os.Stdin)
		if err != nil {
			return "", fmt.Errorf("read body from stdin: %w", err)
		}
		return string(data), nil
	}
	data, err := os.ReadFile(path) // #nosec G304 -- --body-file explicitly authorizes reading this caller-provided path.
	if err != nil {
		return "", fmt.Errorf("read body file %s: %w", path, err)
	}
	return string(data), nil
}

// splitAtDashDash splits args at the first "--" element: before is
// everything ahead of it (the host list), after is everything behind it
// (the command words). found is false when no "--" element exists at all.
func splitAtDashDash(args []string) (before, after []string, found bool) {
	for i, a := range args {
		if a == "--" {
			return args[:i], args[i+1:], true
		}
	}
	return nil, nil, false
}

// deltaKeyCommand returns the string passed as delta.Key's third argument
// (and, for a --body-file/stdin run, doubles as the persisted artifact
// command metadata): for an inline "-- words" command it is the command
// itself, but for a body — which can be arbitrarily large — it is
// "body:"+sha256hex(body)[:16], exactly the convention delta.Key's own doc
// comment specifies for its callers, and matching the design doc's Deltas
// section ("body-file runs key on the body's sha256").
// stripFollowMarker removes only the wrapper control line from final output;
// the random marker cannot collide with normal user output in practice.
func stripFollowMarker(out []byte, marker string) []byte {
	for _, ending := range []string{"\n", "\r\n"} {
		needle := []byte(marker + ending)
		if i := bytes.Index(out, needle); i >= 0 {
			return append(append([]byte(nil), out[:i]...), out[i+len(needle):]...)
		}
	}
	return out
}

func deltaKeyCommand(opts Opts) string {
	if !opts.FromFile {
		return opts.Command
	}
	return "body:" + sha256Hex(opts.Command)[:16]
}

// auditCommandPreview follows the same body-file boundary as the artifact
// command metadata:
// body text is hash-only, while inline commands keep the existing redacted
// and clipped preview used by audit readers and readonly denials.
func auditCommandPreview(opts Opts) string {
	if opts.FromFile {
		return deltaKeyCommand(opts)
	}
	return runlog.Preview(opts.Command)
}

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// asTransportError reports whether err is a *transport.TransportError —
// per the Transport interface's contract, Exec and Put never return any
// other kind of error, but this is checked rather than assumed.
func asTransportError(err error) (*transport.TransportError, bool) {
	var te *transport.TransportError
	ok := errors.As(err, &te)
	return te, ok
}

func asRemoteSetupError(err error) (*session.RemoteSetupError, bool) {
	var se *session.RemoteSetupError
	ok := errors.As(err, &se)
	return se, ok
}

func acceptedHostKeyEvidence(tr transport.Transport, host string, enabled bool, stderr io.Writer) (string, string) {
	if !enabled {
		return "", ""
	}
	reporter, ok := tr.(transport.HostKeyReporter)
	if !ok {
		return "", ""
	}
	key, accepted, err := reporter.AcceptedHostKey(host)
	if err != nil {
		fmt.Fprintf(stderr, "run: accepted host-key evidence for %s is unavailable\n", host)
		return "", ""
	}
	if !accepted {
		return "", ""
	}
	if key.Algorithm == "" || key.Fingerprint == "" {
		fmt.Fprintf(stderr, "run: accepted host-key evidence for %s is incomplete\n", host)
		return "", ""
	}
	return key.Algorithm, key.Fingerprint
}

// runHost runs one host's command end to end: policy check, facts (cached or
// freshly probed), state+baseline load, wrap, exec, parse, state/baseline save,
// artifact store, passport render, and audit. Its RunOutcome explicitly
// distinguishes saved success/remote/transport results from policy denial and
// unsaved internal failure.
func runHost(deps Deps, opts Opts, stdout, stderr io.Writer) RunOutcome {
	root := deps.Store.Root

	if err := policy.CheckReadonly(opts.Command, opts.Readonly); err != nil {
		fmt.Fprintf(stdout, "%s policy-denied\n", opts.Host)
		if auditErr := runlog.AppendAudit(root, runlog.AuditEntry{
			Ts: time.Now(), Host: opts.Host, Ctx: opts.Ctx, Subcommand: "run",
			CommandPreview: auditCommandPreview(opts), BodySHA256: sha256Hex(opts.Command),
			Verdict: "denied-readonly",
		}); auditErr != nil {
			fmt.Fprintf(stderr, "run: append audit: %v\n", auditErr)
		}
		return newPolicyDeniedOutcome()
	}

	selectedPowerShell, ok := powerShellExecutable(opts.PowerShellHost)
	if !ok {
		fmt.Fprintf(stderr, "run: invalid PowerShell host %q\n", opts.PowerShellHost)
		return newInternalFailureOutcome(exitUsage)
	}

	facts, ok, err := session.LoadFacts(root, opts.Host)
	if err != nil {
		fmt.Fprintf(stderr, "run: load facts for %s: %v\n", opts.Host, err)
		return newInternalFailureOutcome(exitUsage)
	}
	if !ok {
		probeStart := time.Now()
		facts, err = session.Probe(deps.Tr, opts.Host, selectedPowerShell, opts.PowerShellHost == "", opts.Timeout)
		if err != nil {
			if se, isSetup := asRemoteSetupError(err); isSetup {
				return handleSetupError(deps, opts, se, time.Since(probeStart).Milliseconds(), stdout, stderr)
			}
			if te, isTE := asTransportError(err); isTE {
				return handleTransportError(deps, opts, te, stdout, stderr)
			}
			fmt.Fprintf(stderr, "run: probe %s: %v\n", opts.Host, err)
			return newInternalFailureOutcome(exitUsage)
		}
		if err := session.SaveFacts(root, opts.Host, facts); err != nil {
			fmt.Fprintf(stderr, "run: save facts for %s: %v\n", opts.Host, err)
			return newInternalFailureOutcome(exitUsage)
		}
	}

	st, _, err := session.LoadState(root, opts.Host, opts.Ctx)
	if err != nil {
		fmt.Fprintf(stderr, "run: load state for %s/%s: %v\n", opts.Host, opts.Ctx, err)
		return newInternalFailureOutcome(exitUsage)
	}
	baseline, baseOK, err := session.LoadBaseline(root, opts.Host)
	if err != nil {
		fmt.Fprintf(stderr, "run: load baseline for %s: %v\n", opts.Host, err)
		return newInternalFailureOutcome(exitUsage)
	}
	restore := shell.EnvRestoreSet(baseline, st.Env)
	sentinel := shell.NewSentinel()
	if deps.Follow != nil {
		sentinel = deps.Follow.sentinel
	}

	var (
		remoteExit int
		truncated  bool
		out        []byte
		parsedSt   shell.State
		parseOK    bool
	)

	start := time.Now()
	if deps.Follow != nil {
		if _, ok := deps.Tr.(transport.StreamingTransport); !ok {
			return newInternalFailureOutcome(followUnavailable(stderr))
		}
	}

	if facts.OS == "windows" {
		powerShell := facts.Shell
		if opts.PowerShellHost != "" || powerShell == "" {
			powerShell = selectedPowerShell
		}
		marker := ""
		if deps.Follow != nil {
			marker = deps.Follow.marker
		}
		script := shell.PwshScriptFollow(opts.Command, st, restore, sentinel, marker)

		tmp, err := os.CreateTemp("", "sshai-*.ps1")
		if err != nil {
			fmt.Fprintf(stderr, "run: create temp script: %v\n", err)
			return newInternalFailureOutcome(exitUsage)
		}
		defer os.Remove(tmp.Name())
		if _, err := tmp.Write(script); err != nil {
			_ = tmp.Close()
			fmt.Fprintf(stderr, "run: write temp script: %v\n", err)
			return newInternalFailureOutcome(exitUsage)
		}
		if err := tmp.Close(); err != nil {
			fmt.Fprintf(stderr, "run: close temp script: %v\n", err)
			return newInternalFailureOutcome(exitUsage)
		}

		// Slug the raw command, not the wrapped script: the wrapper embeds
		// a fresh random sentinel every run, so slugging script would
		// defeat BodySlug's own documented purpose (same body -> same
		// remote filename, so re-running the same command overwrites its
		// prior staged file instead of accumulating a new one in
		// RemoteDir on every single call).
		slug := shell.BodySlug([]byte(opts.Command))
		remotePath := shell.RemoteDir + "/" + slug + ".ps1"
		if err := deps.Tr.Put(opts.Host, tmp.Name(), remotePath); err != nil {
			if te, isTE := asTransportError(err); isTE {
				return handleTransportError(deps, opts, te, stdout, stderr)
			}
			fmt.Fprintf(stderr, "run: put script to %s: %v\n", opts.Host, err)
			return newInternalFailureOutcome(exitUsage)
		}

		invocation := shell.PwshInvocation(facts.Form, powerShell, "-NoProfile -ExecutionPolicy Bypass -File "+remotePath)
		var res transport.Result
		if deps.Follow != nil {
			res, err = deps.Tr.(transport.StreamingTransport).ExecStream(opts.Host, invocation, nil, opts.Timeout, deps.Follow.output)
		} else {
			res, err = deps.Tr.Exec(opts.Host, invocation, nil, opts.Timeout)
		}
		if err != nil {
			if te, isTE := asTransportError(err); isTE {
				return handleTransportError(deps, opts, te, stdout, stderr)
			}
			fmt.Fprintf(stderr, "run: exec on %s: %v\n", opts.Host, err)
			return newInternalFailureOutcome(exitUsage)
		}
		remoteExit, truncated = res.ExitCode, res.Truncated
		out, parsedSt, parseOK = shell.PwshParse(res.Output, sentinel)
		if deps.Follow != nil {
			out = stripFollowMarker(out, deps.Follow.marker)
		}
	} else {
		marker := ""
		if deps.Follow != nil {
			marker = deps.Follow.marker
		}
		wrapped := shell.BashWrapFollow(opts.Command, st, restore, sentinel, marker)
		invocation := "bash -s"
		if opts.PosixShell != "" {
			invocation = shell.POSIXShellInvocation(opts.PosixShell)
		}
		var res transport.Result
		if deps.Follow != nil {
			res, err = deps.Tr.(transport.StreamingTransport).ExecStream(opts.Host, invocation, wrapped, opts.Timeout, deps.Follow.output)
		} else {
			res, err = deps.Tr.Exec(opts.Host, invocation, wrapped, opts.Timeout)
		}
		if err != nil {
			if te, isTE := asTransportError(err); isTE {
				return handleTransportError(deps, opts, te, stdout, stderr)
			}
			fmt.Fprintf(stderr, "run: exec on %s: %v\n", opts.Host, err)
			return newInternalFailureOutcome(exitUsage)
		}
		remoteExit, truncated = res.ExitCode, res.Truncated
		out, parsedSt, parseOK = shell.BashParse(res.Output, sentinel)
		if deps.Follow != nil {
			out = stripFollowMarker(out, deps.Follow.marker)
		}
	}

	durationMs := time.Since(start).Milliseconds()

	if parseOK {
		// Partial-epilogue merge: BashParse/PwshParse can return ok=true
		// with Cwd=="" or Env==nil when the epilogue's output was
		// truncated or otherwise malformed — that is "not updated", not
		// "cleared". Keep the previously loaded field whenever the freshly
		// parsed one is empty/nil, so a known-good Cwd/Env is never
		// clobbered by a partial parse.
		merged := st
		if parsedSt.Cwd != "" {
			merged.Cwd = parsedSt.Cwd
		}
		if parsedSt.Env != nil {
			merged.Env = parsedSt.Env
		}
		if err := session.SaveState(root, opts.Host, opts.Ctx, merged); err != nil {
			fmt.Fprintf(stderr, "run: save state for %s/%s: %v\n", opts.Host, opts.Ctx, err)
		}

		// First-contact baseline simplification: rather than a dedicated
		// "probe the environment" round trip before running anything, the
		// env this very first run parsed becomes the baseline outright.
		// Any variable the run's own body exported is therefore baked into
		// the baseline and will not show up as "changed" on a later run —
		// an accepted simplification, not an oversight.
		if !baseOK && parsedSt.Env != nil {
			if err := session.SaveBaseline(root, opts.Host, parsedSt.Env); err != nil {
				fmt.Fprintf(stderr, "run: save baseline for %s: %v\n", opts.Host, err)
			}
		}
	}

	binary := bytes.IndexByte(out[:min(8192, len(out))], 0) >= 0
	acceptedHostKeyAlgorithm, acceptedHostKeyFingerprint := acceptedHostKeyEvidence(
		deps.Tr, opts.Host, opts.AcceptNewHostKey, stderr)

	meta := artifact.Meta{
		Host: opts.Host, Ctx: opts.Ctx, Command: deltaKeyCommand(opts),
		Exit: remoteExit, DurationMs: durationMs, Truncated: truncated, Binary: binary,
		AcceptedHostKeyAlgorithm:   acceptedHostKeyAlgorithm,
		AcceptedHostKeyFingerprint: acceptedHostKeyFingerprint,
		Ts:                         time.Now(),
	}
	key := delta.Key(opts.Host, opts.Ctx, deltaKeyCommand(opts))

	// --delta's lookup runs BEFORE Save, not after: LastByKey queries the
	// table as it stands right now, so the row this call is about to
	// insert is not there yet to exclude — no "art_id != ?" variant
	// needed. This ordering is also what lets Meta.DeltaBase be set
	// before Save, so the status line's own "delta=aN" comes from the
	// same Save call as everything else instead of a second write.
	// Nothing here skips Save in non-delta or no-previous-run cases: the
	// full artifact is always stored first, so history is never lost to
	// delta mode (the brief's invariant) — including when the lookup
	// itself fails. The remote command has already run by this point, so
	// a transient LastByKey error (e.g. a busy SQLite under fan-out) must
	// degrade to "no previous run" rather than abort: the alternative
	// would throw away a completed run's output over a lookup failure,
	// which is exactly the loss the invariant forbids.
	var prevMeta artifact.Meta
	var havePrev bool
	if opts.Delta {
		var lookupErr error
		prevMeta, havePrev, lookupErr = deps.Store.LastByKey(key)
		if lookupErr != nil {
			fmt.Fprintf(stderr, "run: delta lookup for %s: %v\n", opts.Host, lookupErr)
			havePrev = false
		}
		if havePrev {
			meta.DeltaBase = prevMeta.ID
		}
	}

	savedMeta, err := deps.Store.Save(meta, key, out)
	if err != nil {
		fmt.Fprintf(stderr, "run: save artifact: %v\n", err)
		return newInternalFailureOutcome(exitUsage)
	}

	artPath := filepath.Join(deps.Store.Root, "art", savedMeta.ID)

	var passport string
	switch {
	case opts.Delta && havePrev && savedMeta.Binary:
		// Binary output: RenderPassport's own m.Binary suppression
		// (passport.go) is exactly the right shape here too — status
		// line + file=, no body — so this routes through it directly
		// rather than attempting delta.Render at all. A text unified
		// diff of raw binary bytes would be garbled noise, and this is
		// a distinct branch (not folded into the render-error fallback
		// below) precisely so it can never fall through to the
		// no-previous-run case and print the false "no previous run"
		// line when a previous run in fact exists. "Previous exists"
		// is still communicated: savedMeta.DeltaBase was set before
		// Save, so StatusLine still renders delta=aN here.
		passport = artifact.RenderPassport(savedMeta, artPath, out, opts.Budget)
	case opts.Delta && havePrev:
		prevPath := filepath.Join(deps.Store.Root, "art", prevMeta.ID)
		deltaBody, derr := delta.Render(prevPath, out, prevMeta.ID, prevMeta.Ts, opts.Budget)
		if derr != nil {
			// The artifact is already saved (savedMeta.DeltaBase is
			// already persisted, so the status line keeps its
			// delta=aN) — only the delta *rendering* failed (e.g. the
			// previous artifact file is gone despite pruned=0, a gc
			// race or manual removal under art/). Per the repo's own
			// pattern for post-exec failures (SaveState, SaveBaseline,
			// AppendAudit all print-and-continue), fall back to the
			// normal passport instead of discarding an already-saved,
			// already-completed run.
			fmt.Fprintf(stderr, "run: render delta for %s: %v\n", opts.Host, derr)
			passport = artifact.RenderPassport(savedMeta, artPath, out, opts.Budget)
		} else {
			passport = artifact.StatusLine(savedMeta) + "\nfile=" + artPath + "\n" + deltaBody
		}
	case opts.Delta:
		passport = artifact.RenderPassport(savedMeta, artPath, out, opts.Budget) + "\ndelta: no previous run for this key"
	default:
		passport = artifact.RenderPassport(savedMeta, artPath, out, opts.Budget)
	}
	if adv := artifact.PipeAdvisory(opts.Command); adv != "" {
		passport += "\n" + adv
	}
	fmt.Fprintln(stdout, passport)

	if auditErr := runlog.AppendAudit(root, runlog.AuditEntry{
		Ts: time.Now(), Host: opts.Host, Ctx: opts.Ctx, Subcommand: "run",
		CommandPreview: auditCommandPreview(opts), BodySHA256: sha256Hex(opts.Command),
		Verdict: "allowed", Exit: remoteExit,
	}); auditErr != nil {
		fmt.Fprintf(stderr, "run: append audit: %v\n", auditErr)
	}

	return newSavedRunOutcome(savedMeta)
}

// handleTransportError records a failed delivery (the body may not have run
// at all) the same way a completed run is recorded. A safe allowlisted
// diagnostic, when available, becomes both the stored artifact body and
// human/JSON metadata; raw SSH output is never persisted or rendered.
// Meta.Exit stays 0 (the zero value), disambiguated from an honest exit 0 by
// TransportErr being non-empty.
// A Save failure becomes an explicit internal failure while retaining the
// transport process exit used by the existing human-mode path.
func handleSetupError(deps Deps, opts Opts, setupErr *session.RemoteSetupError, durationMs int64, stdout, stderr io.Writer) RunOutcome {
	root := deps.Store.Root
	diagnostic := setupErr.Diagnostic()
	acceptedHostKeyAlgorithm, acceptedHostKeyFingerprint := acceptedHostKeyEvidence(
		deps.Tr, opts.Host, opts.AcceptNewHostKey, stderr)
	meta := artifact.Meta{
		Host: opts.Host, Ctx: opts.Ctx, Command: deltaKeyCommand(opts),
		SetupErr: setupErr.Class, SetupDiagnostic: diagnostic,
		AcceptedHostKeyAlgorithm:   acceptedHostKeyAlgorithm,
		AcceptedHostKeyFingerprint: acceptedHostKeyFingerprint,
		DurationMs:                 durationMs,
		Ts:                         time.Now(),
	}
	body := []byte("setup diagnostic: " + diagnostic + "\n")
	savedMeta, err := deps.Store.Save(meta, delta.Key(opts.Host, opts.Ctx, deltaKeyCommand(opts)), body)
	if err != nil {
		fmt.Fprintf(stderr, "run: save artifact after setup error: %v\n", err)
		return newInternalFailureOutcome(exitSetup)
	}
	fmt.Fprintln(stdout, artifact.RenderPassport(savedMeta, filepath.Join(root, "art", savedMeta.ID), body, opts.Budget))
	if auditErr := runlog.AppendAudit(root, runlog.AuditEntry{
		Ts: time.Now(), Host: opts.Host, Ctx: opts.Ctx, Subcommand: "run",
		CommandPreview: auditCommandPreview(opts), BodySHA256: sha256Hex(opts.Command),
		Verdict: "allowed", SetupErr: savedMeta.SetupErr,
	}); auditErr != nil {
		fmt.Fprintf(stderr, "run: append audit: %v\n", auditErr)
	}
	return newSavedRunOutcome(savedMeta)
}

func handleTransportError(deps Deps, opts Opts, te *transport.TransportError, stdout, stderr io.Writer) RunOutcome {
	root := deps.Store.Root

	diagnostic := te.Diagnostic()
	acceptedHostKeyAlgorithm, acceptedHostKeyFingerprint := acceptedHostKeyEvidence(
		deps.Tr, opts.Host, opts.AcceptNewHostKey, stderr)
	meta := artifact.Meta{
		Host: opts.Host, Ctx: opts.Ctx, Command: deltaKeyCommand(opts),
		TransportErr: te.Reason, TransportDiagnostic: diagnostic, Ts: time.Now(),
		AcceptedHostKeyAlgorithm:   acceptedHostKeyAlgorithm,
		AcceptedHostKeyFingerprint: acceptedHostKeyFingerprint,
	}
	key := delta.Key(opts.Host, opts.Ctx, deltaKeyCommand(opts))
	var body []byte
	if diagnostic != "" {
		body = []byte("transport diagnostic: " + diagnostic + "\n")
	}
	savedMeta, err := deps.Store.Save(meta, key, body)
	if err != nil {
		fmt.Fprintf(stderr, "run: save artifact after transport error: %v\n", err)
		return newInternalFailureOutcome(exitTransport)
	}

	artPath := filepath.Join(deps.Store.Root, "art", savedMeta.ID)
	passport := artifact.RenderPassport(savedMeta, artPath, body, opts.Budget)
	fmt.Fprintln(stdout, passport)

	if auditErr := runlog.AppendAudit(root, runlog.AuditEntry{
		Ts: time.Now(), Host: opts.Host, Ctx: opts.Ctx, Subcommand: "run",
		CommandPreview: auditCommandPreview(opts), BodySHA256: sha256Hex(opts.Command),
		Verdict:      "allowed",
		TransportErr: te.Reason,
	}); auditErr != nil {
		fmt.Fprintf(stderr, "run: append audit: %v\n", auditErr)
	}

	return newSavedRunOutcome(savedMeta)
}
