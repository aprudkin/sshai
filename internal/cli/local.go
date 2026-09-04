package cli

import (
	"bytes"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/config"
	"github.com/aprudkin/sshai/internal/delta"
	"github.com/aprudkin/sshai/internal/runlog"
	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
)

const (
	localShellBash = "bash"
	localShellPwsh = "pwsh"
)

type localRunFunc func(argv []string, stdin []byte, timeout time.Duration, capBytes int64) runner.Result

type localOpts struct {
	shell    string
	target   string
	ctx      string
	command  string
	fromFile bool
	delta    bool
	budget   int
	timeout  time.Duration
}

// Local parses and executes the `local` subcommand. It deliberately uses a
// direct local process runner rather than transport.Transport: local runner
// failures are not SSH delivery failures and never become transport_error.
func Local(args []string, stdout, stderr io.Writer) int {
	return localArgs(args, stdout, stderr, runner.Run, nil)
}

// localWithRunner is the hermetic test seam for the complete local flow.
func localWithRunner(run localRunFunc, args []string, stdout, stderr io.Writer) int {
	return localArgs(args, stdout, stderr, run, nil)
}

func localArgs(args []string, stdout, stderr io.Writer, run localRunFunc, store *artifact.Store) int {
	fs := flag.NewFlagSet("local", flag.ContinueOnError)
	fs.SetOutput(stderr)
	shellFlag := fs.String("shell", "", `required local interpreter: "bash" or "pwsh"`)
	bodyFile := fs.String("body-file", "", `read the command body from a file ("-" for stdin) instead of the "-- command" form`)
	wantDelta := fs.Bool("delta", false, "print diff vs previous local run of same (shell, ctx, command)")
	budget := fs.Int("budget", 0, "output budget in tokens (~bytes/4); default from config")
	timeoutFlag := fs.Int("timeout", 0, "timeout in seconds; default from config")
	ctxFlag := fs.String("ctx", "", `named state context; default $SSHAI_CTX or "default"`)
	resultFormat := fs.String("result-format", "human", `output format: "human" (default) or "json"`)
	resultOut := fs.String("result-out", "", `write the JSON envelope to FILE (requires --result-format=json)`)

	hadDashDash := false
	for _, arg := range args {
		if arg == "--" {
			hadDashDash = true
			break
		}
	}
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if *shellFlag != localShellBash && *shellFlag != localShellPwsh {
		fmt.Fprintf(stderr, "local: invalid --shell=%q (want bash or pwsh)\n", *shellFlag)
		return exitUsage
	}
	if *resultFormat != "human" && *resultFormat != "json" {
		fmt.Fprintf(stderr, "local: invalid --result-format=%q (want human or json)\n", *resultFormat)
		return exitUsage
	}
	if *resultOut != "" && *resultFormat != "json" {
		fmt.Fprintln(stderr, "local: --result-out requires --result-format=json")
		return exitUsage
	}

	fromFile := *bodyFile != ""
	var command string
	switch {
	case fromFile && (hadDashDash || fs.NArg() != 0):
		fmt.Fprintln(stderr, "local: --body-file conflicts with the inline command form")
		return exitUsage
	case fromFile:
		body, err := loadBody(*bodyFile)
		if err != nil {
			fmt.Fprintf(stderr, "local: %v\n", err)
			return exitUsage
		}
		command = body
	case !hadDashDash || fs.NArg() == 0:
		fmt.Fprintln(stderr, "local: a command is required: use `-- <command words>` or --body-file")
		return exitUsage
	default:
		command = strings.Join(fs.Args(), " ")
	}

	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintf(stderr, "local: load config: %v\n", err)
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
		fmt.Fprintf(stderr, "local: %v\n", err)
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

	ownsStore := false
	if store == nil {
		store, err = artifact.OpenStore(cfg.Root)
		if err != nil {
			fmt.Fprintf(stderr, "local: open store: %v\n", err)
			return exitUsage
		}
		ownsStore = true
	}
	if ownsStore {
		defer store.Close()
	}

	target := "local-" + *shellFlag
	opts := localOpts{
		shell: *shellFlag, target: target, ctx: ctx, command: command,
		fromFile: fromFile, delta: *wantDelta, budget: budgetTokens,
		timeout: time.Duration(timeoutSec) * time.Second,
	}
	var r hostRunResult
	r.outcome = runLocal(store, cfg.StreamCapBytes, run, opts, &r.stdout, &r.stderr)
	rc := writeRunResults(store.Root, []hostRunResult{r}, resultModeOptions{format: *resultFormat, resultOut: *resultOut}, stdout, stderr)
	maybeGC(store, cfg, stderr, protectSet(r.outcome.ArtifactID()))
	return rc
}

func runLocal(store *artifact.Store, streamCap int64, run localRunFunc, opts localOpts, stdout, stderr io.Writer) RunOutcome {
	st, _, err := session.LoadState(store.Root, opts.target, opts.ctx)
	if err != nil {
		fmt.Fprintf(stderr, "local: load state for %s/%s: %v\n", opts.target, opts.ctx, err)
		return newInternalFailureOutcome(exitUsage)
	}
	baseline, baseOK, err := session.LoadBaseline(store.Root, opts.target)
	if err != nil {
		fmt.Fprintf(stderr, "local: load baseline for %s: %v\n", opts.target, err)
		return newInternalFailureOutcome(exitUsage)
	}
	restore := shell.EnvRestoreSet(baseline, st.Env)
	sentinel := shell.NewSentinel()

	var argv []string
	var stdin []byte
	var tempScript string
	switch opts.shell {
	case localShellBash:
		argv = []string{"bash", "-s"}
		stdin = shell.BashWrap(opts.command, st, restore, sentinel)
	case localShellPwsh:
		tmp, err := os.CreateTemp("", "sshai-local-*.ps1")
		if err != nil {
			fmt.Fprintln(stderr, "local: create temporary PowerShell script failed")
			return newInternalFailureOutcome(exitUsage)
		}
		tempScript = tmp.Name()
		defer os.Remove(tempScript)
		if _, err := tmp.Write(shell.PwshScript(opts.command, st, restore, sentinel)); err != nil {
			_ = tmp.Close()
			fmt.Fprintln(stderr, "local: write temporary PowerShell script failed")
			return newInternalFailureOutcome(exitUsage)
		}
		if err := tmp.Close(); err != nil {
			fmt.Fprintln(stderr, "local: close temporary PowerShell script failed")
			return newInternalFailureOutcome(exitUsage)
		}
		argv = []string{"pwsh", "-NoProfile", "-File", tempScript}
	}

	started := time.Now()
	result := run(argv, stdin, opts.timeout, streamCap)
	durationMs := time.Since(started).Milliseconds()
	if tempScript != "" {
		if err := os.Remove(tempScript); err != nil && !os.IsNotExist(err) {
			fmt.Fprintln(stderr, "local: remove temporary PowerShell script failed")
		}
		tempScript = ""
	}

	localError := ""
	switch {
	case result.StartErr != nil:
		localError = "start"
		fmt.Fprintln(stderr, "local: local interpreter failed to start")
	case result.Truncated:
		localError = "output-limit"
	case result.TimedOut:
		localError = "timeout"
		fmt.Fprintln(stderr, "local: execution timed out")
	}

	out := result.Output
	var parsedSt shell.State
	parseOK := false
	if localError == "" {
		if opts.shell == localShellBash {
			out, parsedSt, parseOK = shell.BashParse(result.Output, sentinel)
		} else {
			out, parsedSt, parseOK = shell.PwshParse(result.Output, sentinel)
		}
	}
	if parseOK {
		merged := st
		if parsedSt.Cwd != "" {
			merged.Cwd = parsedSt.Cwd
		}
		if parsedSt.Env != nil {
			merged.Env = parsedSt.Env
		}
		if err := session.SaveState(store.Root, opts.target, opts.ctx, merged); err != nil {
			fmt.Fprintf(stderr, "local: save state for %s/%s: %v\n", opts.target, opts.ctx, err)
		}
		if !baseOK && parsedSt.Env != nil {
			if err := session.SaveBaseline(store.Root, opts.target, parsedSt.Env); err != nil {
				fmt.Fprintf(stderr, "local: save baseline for %s: %v\n", opts.target, err)
			}
		}
	}

	binary := bytes.IndexByte(out[:min(8192, len(out))], 0) >= 0
	commandKey := opts.command
	if opts.fromFile {
		commandKey = "body:" + sha256Hex(opts.command)[:16]
	}
	storedExit := result.ExitCode
	if localError != "" {
		// The direct child was not allowed to finish normally, so its
		// signal/OS-specific ProcessState code is not an honest shell exit.
		storedExit = 0
	}
	meta := artifact.Meta{
		Host: opts.target, Ctx: opts.ctx, Command: commandKey, Exit: storedExit,
		LocalError: localError, DurationMs: durationMs, Truncated: result.Truncated,
		Binary: binary, Ts: time.Now(),
	}
	key := delta.Key(opts.target, opts.ctx, commandKey)
	var prevMeta artifact.Meta
	var havePrev bool
	if opts.delta {
		prevMeta, havePrev, err = store.LastByKey(key)
		if err != nil {
			fmt.Fprintf(stderr, "local: delta lookup for %s: %v\n", opts.target, err)
			havePrev = false
		}
		if havePrev {
			meta.DeltaBase = prevMeta.ID
		}
	}
	savedMeta, err := store.Save(meta, key, out)
	if err != nil {
		fmt.Fprintf(stderr, "local: save artifact: %v\n", err)
		return newInternalFailureOutcome(exitUsage)
	}

	artPath := filepath.Join(store.Root, "art", savedMeta.ID)
	passport := artifact.RenderPassport(savedMeta, artPath, out, opts.budget)
	switch {
	case opts.delta && havePrev && savedMeta.Binary:
		// Binary bodies intentionally retain the metadata-only passport.
	case opts.delta && havePrev:
		prevPath := filepath.Join(store.Root, "art", prevMeta.ID)
		deltaBody, deltaErr := delta.Render(prevPath, out, prevMeta.ID, prevMeta.Ts, opts.budget)
		if deltaErr != nil {
			fmt.Fprintf(stderr, "local: render delta for %s: %v\n", opts.target, deltaErr)
		} else {
			passport = artifact.StatusLine(savedMeta) + "\nfile=" + artPath + "\n" + deltaBody
		}
	case opts.delta:
		passport += "\ndelta: no previous run for this key" // #nosec G101 -- user-facing delta status; no credential is present.
	}
	fmt.Fprintln(stdout, passport)

	if auditErr := runlog.AppendAudit(store.Root, runlog.AuditEntry{
		Ts: time.Now(), Host: opts.target, Ctx: opts.ctx, Subcommand: "local",
		CommandPreview: localAuditPreview(opts), BodySHA256: sha256Hex(opts.command),
		Verdict: "allowed", Exit: storedExit, LocalError: localError,
	}); auditErr != nil {
		fmt.Fprintf(stderr, "local: append audit: %v\n", auditErr)
	}
	return newSavedRunOutcome(savedMeta)
}

func localAuditPreview(opts localOpts) string {
	if opts.fromFile {
		return "body:" + sha256Hex(opts.command)[:16]
	}
	return runlog.Preview(opts.command)
}
