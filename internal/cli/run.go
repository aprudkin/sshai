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
	"time"

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
)

// Deps bundles runHost's external dependencies. Tr is the only one worth
// faking in a unit test (see runWith): Store and its Root are backed by a
// real SQLite file and a real filesystem, which a t.TempDir()-rooted
// SSHAI_ROOT already gives a test for free, without touching the network.
type Deps struct {
	Tr    transport.Transport
	Store *artifact.Store
}

// Opts holds one host's resolved run parameters, after flag parsing and
// ctx/host validation.
type Opts struct {
	Host     string
	Ctx      string
	Command  string // the actual body run on the host (bash or pwsh)
	FromFile bool   // true when Command came from --body-file/stdin rather than "-- words"
	Readonly bool
	Budget   int
	Timeout  time.Duration
}

// ctxRe is the safe charset for --ctx: no "/" (so a ctx value can never
// escape <root>/state/<host>/ into a parent directory), and no characters
// that would make a confusing filename.
var ctxRe = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

// validateCtx enforces the safe ctx charset before any SaveState/LoadState
// call. Two hazards, both from earlier tasks' reviews: (1) a ctx equal to
// "facts" or "baseline" would alias session's own facts.json/baseline.json
// — all three share one directory (session.hostDir) and are told apart
// only by filename; (2) a ctx containing "/" could otherwise address a
// path outside that directory entirely. ctxRe already excludes "/", so a
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
	if !ctxRe.MatchString(ctx) {
		return fmt.Errorf("ctx %q must match %s", ctx, ctxRe.String())
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

// runArgs is the thin flag-parsing shell: it resolves flags, config,
// exactly one host, and the command body, then hands off to runHost for
// the actual per-host flow. tr is nil in production (Run), in which case
// a real OpenSSH transport is built from config; runWith passes a fake.
func runArgs(args []string, stdout, stderr io.Writer, tr transport.Transport) int {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	fs.SetOutput(stderr)
	bodyFile := fs.String("body-file", "", `read the command body from a file ("-" for stdin) instead of the "-- command" form`)
	wantDelta := fs.Bool("delta", false, "print diff vs previous run of same (host, ctx, command)")
	budget := fs.Int("budget", 0, "output budget in tokens (~bytes/4); default from config")
	timeoutFlag := fs.Int("timeout", 0, "timeout in seconds; default from config")
	ctxFlag := fs.String("ctx", "", `named state context; default $SSHAI_CTX or "default"`)

	if err := fs.Parse(args); err != nil {
		return exitUsage
	}

	if *wantDelta {
		fmt.Fprintln(stderr, "run: --delta is not wired yet (Task 14) — ignoring")
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
	if len(hosts) > 1 {
		fmt.Fprintf(stderr, "run: %d hosts given — fan-out is not supported yet (Task 12); pass exactly one host\n", len(hosts))
		return exitUsage
	}
	host := hosts[0]

	store, err := artifact.OpenStore(cfg.Root)
	if err != nil {
		fmt.Fprintf(stderr, "run: open store: %v\n", err)
		return exitUsage
	}
	defer store.Close()

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
		tr = transport.NewOpenSSH(controlDir, cfg.ControlPersist, cfg.StreamCapBytes)
	}

	opts := Opts{
		Host:     host,
		Ctx:      ctx,
		Command:  command,
		FromFile: fromFile,
		Readonly: cfg.Hosts[host].Readonly,
		Budget:   budgetTokens,
		Timeout:  timeout,
	}
	deps := Deps{Tr: tr, Store: store}

	return runHost(deps, opts, stdout, stderr)
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
	data, err := os.ReadFile(path)
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

// recordCommand returns the string used to represent opts.Command in the
// artifact store — artifact.Meta.Command (the long-lived SQLite "command"
// column) and the delta key derived from it — as opposed to execution or
// the audit log: for an inline "-- words" command it is the command
// itself, but for a --body-file/stdin body, which can be arbitrarily
// large, it is "body:"+sha256hex(body)[:16], exactly the convention
// delta.Key's own doc comment specifies for its callers. This keeps the
// DB row and its delta key self-consistent and keeps a large script body
// out of the database. It is deliberately NOT used for
// runlog.AuditEntry.CommandPreview: policy.CheckReadonly's doc comment
// expects a denial's log line to carry the (redacted) command itself, not
// an opaque hash, so audit entries call runlog.Preview(opts.Command)
// directly — Preview's own 80-rune clip already bounds a large body's
// footprint in the log.
func recordCommand(opts Opts) string {
	if !opts.FromFile {
		return opts.Command
	}
	return "body:" + sha256Hex(opts.Command)[:16]
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

// runHost runs one host's command end to end: policy check, facts
// (cached or freshly probed), state+baseline load, wrap, exec, parse,
// state/baseline save, artifact store, passport render, audit, and the
// exit code to return (mirroring the remote exit, except for the
// reserved policy/transport codes — see the package doc in
// cmd/sshai/main.go).
func runHost(deps Deps, opts Opts, stdout, stderr io.Writer) int {
	root := deps.Store.Root

	if err := policy.CheckReadonly(opts.Command, opts.Readonly); err != nil {
		fmt.Fprintf(stdout, "%s policy-denied\n", opts.Host)
		if auditErr := runlog.AppendAudit(root, runlog.AuditEntry{
			Ts: time.Now(), Host: opts.Host, Ctx: opts.Ctx, Subcommand: "run",
			CommandPreview: runlog.Preview(opts.Command), Verdict: "denied-readonly",
		}); auditErr != nil {
			fmt.Fprintf(stderr, "run: append audit: %v\n", auditErr)
		}
		return exitPolicy
	}

	facts, ok, err := session.LoadFacts(root, opts.Host)
	if err != nil {
		fmt.Fprintf(stderr, "run: load facts for %s: %v\n", opts.Host, err)
		return exitUsage
	}
	if !ok {
		facts, err = session.Probe(deps.Tr, opts.Host, shell.PwshDefaultShell, opts.Timeout)
		if err != nil {
			if te, isTE := asTransportError(err); isTE {
				return handleTransportError(deps, opts, te, stdout, stderr)
			}
			fmt.Fprintf(stderr, "run: probe %s: %v\n", opts.Host, err)
			return exitUsage
		}
		if err := session.SaveFacts(root, opts.Host, facts); err != nil {
			fmt.Fprintf(stderr, "run: save facts for %s: %v\n", opts.Host, err)
			return exitUsage
		}
	}

	st, _, err := session.LoadState(root, opts.Host, opts.Ctx)
	if err != nil {
		fmt.Fprintf(stderr, "run: load state for %s/%s: %v\n", opts.Host, opts.Ctx, err)
		return exitUsage
	}
	baseline, baseOK, err := session.LoadBaseline(root, opts.Host)
	if err != nil {
		fmt.Fprintf(stderr, "run: load baseline for %s: %v\n", opts.Host, err)
		return exitUsage
	}
	restore := shell.EnvRestoreSet(baseline, st.Env)
	sentinel := shell.NewSentinel()

	var (
		remoteExit int
		truncated  bool
		out        []byte
		parsedSt   shell.State
		parseOK    bool
	)

	start := time.Now()

	if facts.OS == "windows" {
		script := shell.PwshScript(opts.Command, st, restore, sentinel)

		tmp, err := os.CreateTemp("", "sshai-*.ps1")
		if err != nil {
			fmt.Fprintf(stderr, "run: create temp script: %v\n", err)
			return exitUsage
		}
		defer os.Remove(tmp.Name())
		if _, err := tmp.Write(script); err != nil {
			tmp.Close()
			fmt.Fprintf(stderr, "run: write temp script: %v\n", err)
			return exitUsage
		}
		if err := tmp.Close(); err != nil {
			fmt.Fprintf(stderr, "run: close temp script: %v\n", err)
			return exitUsage
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
			return exitUsage
		}

		invocation := shell.PwshInvocation(facts.Form, facts.Shell, "-NoProfile -ExecutionPolicy Bypass -File "+remotePath)
		res, err := deps.Tr.Exec(opts.Host, invocation, nil, opts.Timeout)
		if err != nil {
			if te, isTE := asTransportError(err); isTE {
				return handleTransportError(deps, opts, te, stdout, stderr)
			}
			fmt.Fprintf(stderr, "run: exec on %s: %v\n", opts.Host, err)
			return exitUsage
		}
		remoteExit, truncated = res.ExitCode, res.Truncated
		out, parsedSt, parseOK = shell.PwshParse(res.Output, sentinel)
	} else {
		wrapped := shell.BashWrap(opts.Command, st, restore, sentinel)
		res, err := deps.Tr.Exec(opts.Host, "bash -s", wrapped, opts.Timeout)
		if err != nil {
			if te, isTE := asTransportError(err); isTE {
				return handleTransportError(deps, opts, te, stdout, stderr)
			}
			fmt.Fprintf(stderr, "run: exec on %s: %v\n", opts.Host, err)
			return exitUsage
		}
		remoteExit, truncated = res.ExitCode, res.Truncated
		out, parsedSt, parseOK = shell.BashParse(res.Output, sentinel)
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
	cmdForRecord := recordCommand(opts)

	meta := artifact.Meta{
		Host: opts.Host, Ctx: opts.Ctx, Command: cmdForRecord,
		Exit: remoteExit, DurationMs: durationMs, Truncated: truncated, Binary: binary,
		Ts: time.Now(),
	}
	key := delta.Key(opts.Host, opts.Ctx, cmdForRecord)
	savedMeta, err := deps.Store.Save(meta, key, out)
	if err != nil {
		fmt.Fprintf(stderr, "run: save artifact: %v\n", err)
		return exitUsage
	}

	artPath := filepath.Join(deps.Store.Root, "art", savedMeta.ID)
	passport := artifact.RenderPassport(savedMeta, artPath, out, opts.Budget)
	if adv := artifact.PipeAdvisory(opts.Command); adv != "" {
		passport += "\n" + adv
	}
	fmt.Fprintln(stdout, passport)

	if auditErr := runlog.AppendAudit(root, runlog.AuditEntry{
		Ts: time.Now(), Host: opts.Host, Ctx: opts.Ctx, Subcommand: "run",
		CommandPreview: runlog.Preview(opts.Command), BodySHA256: sha256Hex(opts.Command),
		Verdict: "allowed", Exit: remoteExit,
	}); auditErr != nil {
		fmt.Fprintf(stderr, "run: append audit: %v\n", auditErr)
	}

	return remoteExit
}

// handleTransportError records a failed delivery (the body may not have
// run at all) the same way a completed run is recorded, minus any output:
// Meta.Exit stays 0 (the zero value), disambiguated from an honest exit 0
// by TransportErr being non-empty — the Store/Meta contract laid out in
// artifact/passport.go's StatusLine. The artifact is empty; the passport
// and audit entry are still written so the failure itself is traceable.
func handleTransportError(deps Deps, opts Opts, te *transport.TransportError, stdout, stderr io.Writer) int {
	root := deps.Store.Root
	cmdForRecord := recordCommand(opts)

	meta := artifact.Meta{
		Host: opts.Host, Ctx: opts.Ctx, Command: cmdForRecord,
		TransportErr: te.Reason, Ts: time.Now(),
	}
	key := delta.Key(opts.Host, opts.Ctx, cmdForRecord)
	savedMeta, err := deps.Store.Save(meta, key, nil)
	if err != nil {
		fmt.Fprintf(stderr, "run: save artifact after transport error: %v\n", err)
		return exitTransport
	}

	artPath := filepath.Join(deps.Store.Root, "art", savedMeta.ID)
	passport := artifact.RenderPassport(savedMeta, artPath, nil, opts.Budget)
	fmt.Fprintln(stdout, passport)

	if auditErr := runlog.AppendAudit(root, runlog.AuditEntry{
		Ts: time.Now(), Host: opts.Host, Ctx: opts.Ctx, Subcommand: "run",
		CommandPreview: runlog.Preview(opts.Command), Verdict: "allowed",
		TransportErr: te.Reason,
	}); auditErr != nil {
		fmt.Fprintf(stderr, "run: append audit: %v\n", auditErr)
	}

	return exitTransport
}
