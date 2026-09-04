package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/transport"
)

// TestRunResultFormatJSONSuccess covers the single-host --result-format=json
// success path (Task 4 of aimem#767): stdout is exactly one JSON object
// matching the v1 envelope, no human passport lines leak through, and the
// run was actually saved (sha256 is non-empty).
func TestRunResultFormatJSONSuccess(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "web01", "--", "true"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("stdout is not one JSON object: %v\n%s", err, out.String())
	}
	if env["schema_version"] != "v1" {
		t.Fatalf("schema_version=%v", env["schema_version"])
	}
	runs, _ := env["runs"].([]any)
	if len(runs) != 1 {
		t.Fatalf("len(runs)=%d", len(runs))
	}
	r0, _ := runs[0].(map[string]any)
	if r0["host"] != "web01" || r0["exit"].(float64) != 0 {
		t.Fatalf("runs[0]=%v", r0)
	}
	if strings.Contains(out.String(), "tail3:") {
		t.Fatalf("human tail3 leaked into JSON: %s", out.String())
	}
	if r0["sha256"] == "" {
		t.Fatal("sha256 empty on a successful run")
	}
}

func TestRunResultFormatJSONReportsAcceptedHostKey(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "new-host")
	f := &multiHostTr{
		rcs: map[string]int{"new-host": 0},
		acceptedHostKeys: map[string]transport.HostKey{
			"new-host": {Algorithm: "ssh-ed25519", Fingerprint: "SHA256:abc123"},
		},
	}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{
		"--result-format=json",
		"--accept-new-host-key", "new-host",
		"new-host", "--", "true",
	}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("stdout is not JSON: %v\n%s", err, out.String())
	}
	runs, _ := env["runs"].([]any)
	r0, _ := runs[0].(map[string]any)
	if r0["accepted_host_key_algorithm"] != "ssh-ed25519" ||
		r0["accepted_host_key_fingerprint"] != "SHA256:abc123" {
		t.Fatalf("runs[0]=%v", r0)
	}
}

// TestRunResultFormatJSONFanOutPreservesWorkerStderr covers the successful
// fan-out JSON path: each worker's post-run diagnostic remains on stderr,
// in argv order, while stdout stays one parseable JSON envelope.
func TestRunResultFormatJSONFanOutPreservesWorkerStderr(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")

	f := &multiHostTr{rcs: map[string]int{"h1": 0, "h2": 0}, failStateSave: true}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "h1", "h2", "--", "true"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d, want 0; stdout=%q stderr=%q", rc, out.String(), errB.String())
	}

	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("stdout must be one JSON document: %v; stdout=%q", err, out.String())
	}
	if strings.Contains(out.String(), "append audit") {
		t.Fatalf("worker diagnostics leaked into JSON stdout: %q", out.String())
	}
	want1 := "run: save state for h1/default:"
	want2 := "run: save state for h2/default:"
	got := errB.String()
	i1, i2 := strings.Index(got, want1), strings.Index(got, want2)
	if i1 < 0 || i2 < 0 || i1 > i2 {
		t.Fatalf("stderr diagnostics missing or not in argv order: want %q then %q, got %q", want1, want2, got)
	}
}

// envelope rendering (Task 5 of aimem#767): three hosts producing one ok,
// one non-zero exit, and one transport error all land in a single envelope
// whose summary, runs[] (in argv order), and exit code are derived from
// the per-host Metas alone, with no human passport/aggregate lines leaking
// into stdout.
func TestRunResultFormatJSONFanOutMixed(t *testing.T) {

	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2", "h3")

	f := &multiHostTr{
		rcs:          map[string]int{"h1": 0, "h2": 1},
		transportErr: map[string]string{"h3": "ssh"},
	}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "h1", "h2", "h3", "--", "true"}, &out, &errB)
	if rc != exitTransport {
		t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitTransport, errB.String())
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("stdout not one JSON object: %v", err)
	}
	sum, _ := env["summary"].(map[string]any)
	if sum["hosts"].(float64) != 3 || sum["ok"].(float64) != 1 ||
		sum["failed"].(float64) != 1 || sum["transport_errors"].(float64) != 1 ||
		sum["policy_denied"].(float64) != 0 || sum["worst_exit"].(float64) != 1 {
		t.Fatalf("summary=%v", sum)
	}
	runs, _ := env["runs"].([]any)
	if len(runs) != 3 {
		t.Fatalf("len(runs)=%d, want 3", len(runs))
	}
	// runs[] must be in argv order.
	for i, host := range []string{"h1", "h2", "h3"} {
		r, _ := runs[i].(map[string]any)
		if r["host"] != host {
			t.Fatalf("runs[%d] host=%v, want %s (argv order)", i, r["host"], host)
		}
	}
	if strings.Contains(out.String(), "hosts=3") {
		t.Fatalf("human aggregate line leaked into JSON: %s", out.String())
	}
}

// Non-zero remote exit: envelope carries exit, summary.failed=1.
func TestRunResultFormatJSONNonZeroExit(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	f := &fakeTr{rc: 2}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "web01", "--", "exit 2"}, &out, &errB)
	if rc != 2 {
		t.Fatalf("rc=%d, want 2", rc)
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	sum, _ := env["summary"].(map[string]any)
	r0, _ := env["runs"].([]any)[0].(map[string]any)
	if sum["failed"].(float64) != 1 || r0["exit"].(float64) != 2 {
		t.Fatalf("summary=%v runs[0].exit=%v", sum, r0["exit"])
	}
}

// A classified transport failure carries its stable class plus a safe
// diagnostic in JSON, and stores that same diagnostic as its artifact body.
func TestRunResultFormatJSONTransportError(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	f := &probeFailsTr{rawOutput: []byte("private.example SHA256:TOPSECRET\nHost key verification failed.")}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "web01", "--", "true"}, &out, &errB)
	if rc != exitTransport {
		t.Fatalf("rc=%d, want %d", rc, exitTransport)
	}
	if strings.Contains(out.String(), "TOPSECRET") || strings.Contains(out.String(), "private.example") {
		t.Fatalf("raw SSH output leaked into JSON: %q", out.String())
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	r0, _ := env["runs"].([]any)[0].(map[string]any)
	if r0["transport_error"] != "ssh" || r0["transport_diagnostic"] != "host key verification failed" ||
		r0["exit"].(float64) != 0 {
		t.Fatalf("runs[0]=%v", r0)
	}
	ap, _ := r0["artifact_path"].(string)
	body, err := os.ReadFile(ap)
	if err != nil {
		t.Fatalf("read transport artifact: %v", err)
	}
	if got, want := string(body), "transport diagnostic: host key verification failed\n"; got != want {
		t.Fatalf("transport artifact=%q, want %q", got, want)
	}
	sum, _ := env["summary"].(map[string]any)
	if sum["transport_errors"].(float64) != 1 {
		t.Fatalf("summary=%v", sum)
	}
}

// Policy denial has no saved artifact but remains a valid JSON result through
// summary.policy_denied. Save-failure fallback is covered separately below.
func TestRunResultFormatJSONPolicyDenied(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	toml := "[hosts.web01]\nreadonly = true\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatal(err)
	}
	var out, errB bytes.Buffer
	rc := runWith(&fakeTr{}, []string{"--result-format=json", "web01", "--", "rm", "-rf", "/tmp/x"}, &out, &errB)
	if rc != exitPolicy {
		t.Fatalf("rc=%d, want %d", rc, exitPolicy)
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	sum, _ := env["summary"].(map[string]any)
	runs, _ := env["runs"].([]any)
	if sum["policy_denied"].(float64) != 1 || sum["hosts"].(float64) != 1 || len(runs) != 0 {
		t.Fatalf("summary=%v runs=%v", sum, runs)
	}
}

// Path with spaces: artifact_path round-trips through JSON with the space
// intact and resolves to the saved artifact file.
func TestRunResultFormatJSONPathWithSpaces(t *testing.T) {
	root := filepath.Join(t.TempDir(), "with space", "sshai")
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	if rc := runWith(f, []string{"--result-format=json", "web01", "--", "true"}, &out, &errB); rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	r0, _ := env["runs"].([]any)[0].(map[string]any)
	ap, _ := r0["artifact_path"].(string)
	if !strings.Contains(ap, "with space") {
		t.Fatalf("artifact_path missing space: %q", ap)
	}
	if _, err := os.ReadFile(ap); err != nil {
		t.Fatalf("artifact_path does not resolve: %v", err)
	}
}
func TestRunResultFormatJSONBodyFileBoundary(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	body := "printf 'SECRET_TOKEN_XYZ\\n'\n"
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := w.WriteString(body); err != nil {
		t.Fatal(err)
	}
	w.Close()
	os.Stdin = r
	defer func() { os.Stdin = os.NewFile(0, os.DevNull) }()
	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "--body-file", "-", "web01"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	r0, _ := env["runs"].([]any)[0].(map[string]any)
	cmd, _ := r0["command"].(string)
	if !strings.HasPrefix(cmd, "body:") {
		t.Fatalf("command=%q, want body:<sha256>[:16]", cmd)
	}
	if strings.Contains(out.String(), "SECRET_TOKEN_XYZ") {
		t.Fatalf("body text leaked into envelope: %s", out.String())
	}
}

// Human mode byte-equivalence: default output and explicit
// --result-format=human must both match the legacy byte layout. Each
// runWith invocation assigns fresh artifact IDs and paths and records a
// wall-clock duration, so those three volatile values are normalized.
func TestRunResultFormatHumanModeByteEquivalent(t *testing.T) {
	timeRe := regexp.MustCompile(`time=[0-9.]+(?:ms|s)`)
	normalize := func(s string) string {
		lines := strings.Split(s, "\n")
		for i, line := range lines {
			if strings.HasPrefix(line, "file=") {
				lines[i] = "file=<path>"
				continue
			}
			// Replace leading "a1234 " artifact IDs on status lines with "aN ".
			idx := strings.IndexByte(line, ' ')
			if idx <= 1 || line[0] != 'a' {
				continue
			}
			allDigits := true
			for _, c := range line[1:idx] {
				if c < '0' || c > '9' {
					allDigits = false
					break
				}
			}
			if allDigits {
				// Also mask wall-clock duration ("time=12ms", "time=1.8s") — the
				// same command can report different durations across runs, which
				// would otherwise flake this byte-equivalence check. Matches the
				// exact shapes artifact.HumanDuration emits.
				lines[i] = timeRe.ReplaceAllString("aN"+line[idx:], "time=X")
			}
		}
		return strings.Join(lines, "\n")
	}

	tests := []struct {
		name   string
		hosts  []string
		rcs    map[string]int
		wantRC int
		want   string
	}{
		{
			name:   "single host",
			hosts:  []string{"h1"},
			rcs:    map[string]int{"h1": 0},
			wantRC: 0,
			want:   "aN host=h1 exit=0 lines=1 bytes=6B time=X\nfile=<path>\nhello\n",
		},
		{
			name:   "fan out",
			hosts:  []string{"h1", "h2"},
			rcs:    map[string]int{"h1": 0, "h2": 5},
			wantRC: 5,
			want: "aN host=h1 exit=0 lines=1 bytes=6B time=X\nfile=<path>\nhello\n" +
				"\n" +
				"aN host=h2 exit=5 lines=1 bytes=6B time=X\nfile=<path>\nhello\n" +
				"hosts=2 ok=1 failed=1 transport-errors=0\n",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			seedLinuxFacts(t, root, tt.hosts...)
			for _, explicit := range []bool{false, true} {
				args := append([]string(nil), tt.hosts...)
				args = append(args, "--", "true")
				if explicit {
					args = append([]string{"--result-format=human"}, args...)
				}
				var out, errB bytes.Buffer
				if rc := runWith(&multiHostTr{rcs: tt.rcs}, args, &out, &errB); rc != tt.wantRC {
					t.Fatalf("explicit=%t rc=%d stderr=%s", explicit, rc, errB.String())
				}
				if got := normalize(out.String()); got != tt.want {
					t.Fatalf("explicit=%t output mismatch\ngot:  %q\nwant: %q", explicit, got, tt.want)
				}
			}
		})
	}
}

// TestRunResultFormatJSONSaveFailure covers the single-host Save-failure
// fallback (Task 4 of aimem#767, acceptance criterion "malformed/unavailable
// artifact"): when Store.Save cannot write its artifact file, runArgs must
// not emit any JSON envelope (the run-count-vs-hosts invariant cannot hold
// for a save-failed host), must surface the Save error on stderr with the
// existing "run: save artifact:" prefix runHost already prints, and must
// exit exitUsage (96). The fallback is injected by opening a real Store on
// a t.TempDir() root and then chmod 0o500 on <root>/art so any
// artifact-file write inside it fails with EACCES — the same EACCES Save
// would see in production if <root>/art were unwritable. runArgs's own
// Store injection point is runWithStore, which mirrors the existing
// runWith transport seam; with store != nil, runArgsWithStore skips its
// own OpenStore / Close so the test holds the lifetime.
func TestRunResultFormatJSONSaveFailure(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")

	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	t.Cleanup(func() {
		store.Close()
	})
	poisonArtifactDir(t, root)

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWithStore(f, store, []string{"--result-format=json", "web01", "--", "true"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stdout=%q stderr=%q", rc, exitUsage, out.String(), errB.String())
	}
	if err := json.Unmarshal(bytes.TrimSpace(out.Bytes()), new(map[string]any)); err == nil {
		t.Fatalf("expected no JSON envelope on stdout, got %q", out.String())
	}
	if !strings.Contains(errB.String(), "run: save artifact:") {
		t.Fatalf("stderr missing the save-artifact diagnostic: %q", errB.String())
	}
}

// TestRunResultFormatJSONFanOutSaveFailure covers the fan-out Save-failure
// branch in runInvocation: any host whose Store.Save fails produces an internal
// failure for the whole invocation (the envelope's run-count
// invariant cannot hold), the shared output controller flushes the per-host human passports
// and prints the human aggregate line in place of the envelope, and the
// process exits exitUsage (96). Since runInvocation shares one Store across
// every goroutine, chmod 0o500 on <root>/art fails Save for every host;
// that is sufficient to exercise the branch — the same code path also
// covers the "only one host's Save failed" shape (the flag is a single
// OR over all hosts, not per-host), so this test asserts that fan-out
// save-failure path end to end.
func TestRunResultFormatJSONFanOutSaveFailure(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")

	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	t.Cleanup(func() {
		store.Close()
	})
	poisonArtifactDir(t, root)

	f := &multiHostTr{rcs: map[string]int{"h1": 0, "h2": 0}}
	var out, errB bytes.Buffer
	rc := runWithStore(f, store, []string{"--result-format=json", "h1", "h2", "--", "true"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stdout=%q stderr=%q", rc, exitUsage, out.String(), errB.String())
	}
	s := out.String()
	if !strings.Contains(s, "hosts=2 ok=0 failed=2 transport-errors=0") {
		t.Fatalf("human aggregate line missing or wrong: %q", s)
	}
	if !strings.Contains(errB.String(), "run: save artifact:") {
		t.Fatalf("stderr missing the save-artifact diagnostic: %q", errB.String())
	}
}

func TestRunResultFormatJSONFanoutInvariant(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")
	toml := "[hosts.h2]\nreadonly = true\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &multiHostTr{rcs: map[string]int{"h1": 0}}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "h1", "h2", "--", "true"}, &out, &errB)
	if rc != exitPolicy {
		t.Fatalf("rc=%d, want %d", rc, exitPolicy)
	}
	var env map[string]any
	if err := json.Unmarshal(out.Bytes(), &env); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	sum, _ := env["summary"].(map[string]any)
	runs, _ := env["runs"].([]any)
	hosts := int(sum["hosts"].(float64))
	denied := int(sum["policy_denied"].(float64))
	if len(runs) != hosts-denied {
		t.Fatalf("len(runs)=%d != hosts(%d)-policy_denied(%d)", len(runs), hosts, denied)
	}
}

func TestRunResultFormatJSONFailureOrdering(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "remote", "policy", "transport")
	if err := os.WriteFile(
		filepath.Join(root, "config.toml"),
		[]byte("[hosts.policy]\nreadonly = true\n"),
		0o600,
	); err != nil {
		t.Fatalf("write config: %v", err)
	}

	tr := &multiHostTr{
		rcs:          map[string]int{"remote": 23},
		transportErr: map[string]string{"transport": "ssh"},
	}
	var stdout, stderr bytes.Buffer
	rc := runWith(
		tr,
		[]string{
			"--result-format=json",
			"remote", "policy", "transport",
			"--", "rm", "-rf", "/tmp/result-mode-characterization",
		},
		&stdout,
		&stderr,
	)

	if rc != exitTransport {
		t.Fatalf("rc=%d, want transport precedence %d; stderr=%q", rc, exitTransport, stderr.String())
	}
	var envelope struct {
		Summary artifact.Summary `json:"summary"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &envelope); err != nil {
		t.Fatalf("stdout is not one JSON envelope: %v; stdout=%q", err, stdout.String())
	}
	want := artifact.Summary{
		Hosts: 3, Failed: 1, TransportErrors: 1, PolicyDenied: 1, WorstExit: 23,
	}
	if envelope.Summary != want {
		t.Fatalf("summary=%+v, want %+v", envelope.Summary, want)
	}
}

func TestResultModeExitCodeOrdering(t *testing.T) {
	tests := []struct {
		name    string
		summary artifact.Summary
		want    int
	}{
		{name: "success", summary: artifact.Summary{OK: 2}, want: 0},
		{name: "remote", summary: artifact.Summary{Failed: 2, WorstExit: 23}, want: 23},
		{name: "policy outranks remote", summary: artifact.Summary{Failed: 1, PolicyDenied: 1, WorstExit: 23}, want: exitPolicy},
		{name: "transport outranks policy", summary: artifact.Summary{TransportErrors: 1, PolicyDenied: 1, WorstExit: 23}, want: exitTransport},
		{name: "setup outranks transport", summary: artifact.Summary{SetupErrors: 1, TransportErrors: 1, PolicyDenied: 1, WorstExit: 23}, want: exitSetup},
		{name: "local outranks setup", summary: artifact.Summary{LocalErrors: 1, SetupErrors: 1}, want: exitUsage},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := resultModeExitCode(tt.summary); got != tt.want {
				t.Fatalf("resultModeExitCode(%+v)=%d, want %d", tt.summary, got, tt.want)
			}
		})
	}
}

func TestResultModeExitCodeUsesUsageForSavedLocalErrors(t *testing.T) {
	summary, _ := summarizeRunOutcomes([]RunOutcome{
		newSavedRunOutcome(artifact.Meta{ID: "a1", Host: "web01", LocalError: "timeout"}),
		newSavedRunOutcome(artifact.Meta{ID: "a2", Host: "db01", TransportErr: "ssh"}),
	})
	if summary.Failed != 1 || summary.LocalErrors != 1 {
		t.Fatalf("summary=%+v, want one failed local error", summary)
	}
	if got := resultModeExitCode(summary); got != exitUsage {
		t.Fatalf("resultModeExitCode=%d, want %d", got, exitUsage)
	}
}

func TestRenderResultEnvelopePreservesOutcomeOrder(t *testing.T) {
	outcomes := []RunOutcome{
		newSavedRunOutcome(artifact.Meta{ID: "a2", Host: "first", Exit: 7}),
		newPolicyDeniedOutcome(),
		newSavedRunOutcome(artifact.Meta{ID: "a1", Host: "third"}),
	}

	document, summary := renderResultEnvelope("/result-root", outcomes, "abatch")

	wantSummary := artifact.Summary{Hosts: 3, OK: 1, Failed: 1, PolicyDenied: 1, WorstExit: 7}
	if summary != wantSummary {
		t.Fatalf("summary=%+v, want %+v", summary, wantSummary)
	}
	var envelope struct {
		SchemaVersion string `json:"schema_version"`
		BatchID       string `json:"batch_id"`
		Runs          []struct {
			ID   string `json:"id"`
			Host string `json:"host"`
		} `json:"runs"`
	}
	if err := json.Unmarshal(document, &envelope); err != nil {
		t.Fatalf("rendered document is not JSON: %v", err)
	}
	if envelope.SchemaVersion != "v1" || envelope.BatchID != "abatch" {
		t.Fatalf("envelope version/batch=(%q, %q), want (v1, abatch)", envelope.SchemaVersion, envelope.BatchID)
	}
	if len(envelope.Runs) != 2 || envelope.Runs[0].Host != "first" || envelope.Runs[1].Host != "third" {
		t.Fatalf("runs=%+v, want saved outcomes in input order", envelope.Runs)
	}
}

func TestWriteResultModeWritesStdoutBeforePersistenceFailure(t *testing.T) {
	outcomes := []RunOutcome{newSavedRunOutcome(artifact.Meta{ID: "a1", Host: "web01"})}
	var stdout, stderr bytes.Buffer

	exitCode := writeResultMode(t.TempDir(), outcomes, t.TempDir(), &stdout, &stderr)

	if exitCode != exitUsage {
		t.Fatalf("exitCode=%d, want %d", exitCode, exitUsage)
	}
	if !bytes.HasSuffix(stdout.Bytes(), []byte("\n")) {
		t.Fatalf("stdout lacks trailing newline: %q", stdout.String())
	}
	if err := json.Unmarshal(bytes.TrimSpace(stdout.Bytes()), new(map[string]any)); err != nil {
		t.Fatalf("stdout must contain the envelope before persistence error: %v; stdout=%q", err, stdout.String())
	}
	if !strings.Contains(stderr.String(), "run: --result-out:") {
		t.Fatalf("stderr missing result-out diagnostic: %q", stderr.String())
	}
}

func TestNewBatchIDHasArtifactShapeAndVaries(t *testing.T) {
	first := newBatchID()
	second := newBatchID()
	wantShape := regexp.MustCompile(`^a[0-9a-f]{32}$`)
	if !wantShape.MatchString(first) || !wantShape.MatchString(second) {
		t.Fatalf("batch ids (%q, %q) do not match artifact shape", first, second)
	}
	if first == second {
		t.Fatalf("consecutive batch ids unexpectedly match: %q", first)
	}
}
