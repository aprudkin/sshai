package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/delta"
	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

const stateCanary = "synthetic-snapshot-only-canary"

// This transport only reads the temporary upload. It never contacts a host.
type noStateTr struct {
	t                  *testing.T
	script, invocation string
	kind               string
	execs, puts        int
	budgets            []time.Duration
}

func (f *noStateTr) Put(_ string, local, _ string, timeout time.Duration) error {
	f.puts++
	f.budgets = append(f.budgets, timeout)
	b, err := os.ReadFile(local)
	if err != nil {
		f.t.Fatal(err)
	}
	f.script = string(b)
	for _, forbidden := range []string{stateCanary, "Get-Location", "Set-Location", "env:", "ToBase64String", "$__sshai_env"} {
		if strings.Contains(f.script, forbidden) {
			f.t.Fatalf("staged script contains %s", forbidden)
		}
	}
	if f.kind == "stage" {
		return &transport.TransportError{Reason: "timeout"}
	}
	return nil
}

func (f *noStateTr) Exec(host, cmd string, stdin []byte, timeout time.Duration) (transport.Result, error) {
	return f.ExecStream(host, cmd, stdin, timeout, nil)
}

func (f *noStateTr) ExecStream(_ string, cmd string, _ []byte, timeout time.Duration, emit func([]byte)) (transport.Result, error) {
	f.execs++
	f.invocation = cmd
	f.budgets = append(f.budgets, timeout)
	if cmd == "uname -s" {
		return transport.Result{Output: []byte("Linux\n")}, nil
	}
	markers := regexp.MustCompile(`Write-Output '(__SSHAI_[^']+)'`).FindAllStringSubmatch(f.script, -1)
	var marker, sentinel string
	for _, match := range markers {
		if strings.HasSuffix(match[1], "_FOLLOW") {
			marker = match[1]
		} else {
			sentinel = match[1]
		}
	}
	out := "permitted\n"
	if marker != "" {
		out = marker + "\n" + out
	}
	if f.kind != "partial" && f.kind != "truncated" && f.kind != "timeout" && f.kind != "ssh" {
		out += "\n" + sentinel + "\n"
	}
	if emit != nil {
		emit([]byte(out))
	}
	if f.kind == "timeout" || f.kind == "ssh" {
		return transport.Result{}, &transport.TransportError{Reason: f.kind}
	}
	exit := 0
	if f.kind == "nonzero" {
		exit = 7
	}
	if f.kind == "exception" {
		exit = 1
	}
	return transport.Result{Output: []byte(out), ExitCode: exit, Truncated: f.kind == "truncated"}, nil
}

func TestNoShellStatePersistenceAndEvidence(t *testing.T) {
	for _, fixture := range []string{"fresh", "existing", "unreadable-json"} {
		for _, follow := range []bool{false, true} {
			for _, kind := range []string{"success", "nonzero", "exception", "truncated", "partial", "timeout", "ssh", "stage"} {
				t.Run(fmt.Sprintf("%s/%t/%s", fixture, follow, kind), func(t *testing.T) {
					root := t.TempDir()
					t.Setenv("SSHAI_ROOT", root)
					if err := session.SaveFacts(root, "windows01", session.Facts{OS: "windows", Shell: shell.WindowsPowerShellShell, Form: "pwsh"}); err != nil {
						t.Fatal(err)
					}
					stateDir := filepath.Join(root, "state", "windows01")
					paths := []string{filepath.Join(stateDir, "test.json"), filepath.Join(stateDir, "baseline.json")}
					before := map[string][]byte{}
					if fixture != "fresh" {
						if err := session.SaveState(root, "windows01", "test", shell.State{Cwd: `C:\` + stateCanary, Env: map[string]string{"SYNTHETIC": stateCanary}}); err != nil {
							t.Fatal(err)
						}
						if err := session.SaveBaseline(root, "windows01", map[string]string{"BASE": stateCanary}); err != nil {
							t.Fatal(err)
						}
						for _, path := range paths {
							if fixture == "unreadable-json" {
								if err := os.WriteFile(path, []byte("invalid-json "+stateCanary), 0o600); err != nil {
									t.Fatal(err)
								}
							}
							var err error
							before[path], err = os.ReadFile(path)
							if err != nil {
								t.Fatal(err)
							}
						}
					}
					tr := &noStateTr{t: t, kind: kind}
					args := []string{"--no-shell-state", "--ctx", "test", "--result-format=json"}
					if follow {
						args = append(args, "--follow")
					}
					args = append(args, "windows01", "--", "Write-Output 'permitted'")
					var out, errOut bytes.Buffer
					rc := runWith(tr, args, &out, &errOut)
					wantRC := map[string]int{"nonzero": 7, "exception": 1, "timeout": exitTransport, "ssh": exitTransport, "stage": exitTransport}[kind]
					if rc != wantRC {
						t.Fatalf("rc=%d want=%d stderr=%s", rc, wantRC, &errOut)
					}
					if tr.puts != 1 || (kind == "stage" && tr.execs != 0) || (kind != "stage" && (tr.execs != 1 || !strings.Contains(tr.invocation, shell.WindowsPowerShellShell))) {
						t.Fatalf("wrong dispatch: %+v", tr)
					}
					if len(tr.budgets) == 2 && tr.budgets[1] >= tr.budgets[0] {
						t.Fatal("execution renewed deadline")
					}
					for _, path := range paths {
						after, err := os.ReadFile(path)
						if fixture == "fresh" {
							if !os.IsNotExist(err) {
								t.Fatalf("snapshot created: %s err=%v", path, err)
							}
						} else if err != nil || !bytes.Equal(after, before[path]) {
							t.Fatalf("snapshot changed: %s err=%v", path, err)
						}
					}
					store, err := artifact.OpenStore(root)
					if err != nil {
						t.Fatal(err)
					}
					defer store.Close()
					meta, path, err := store.Get("a1")
					if err != nil || meta.ShellStateMode != artifact.ShellStateNone || meta.Truncated != (kind == "truncated") {
						t.Fatalf("metadata=%+v err=%v", meta, err)
					}
					if kind == "stage" && (meta.FailurePhase != "stage" || meta.RemoteCompletion != "not_started") {
						t.Fatalf("stage evidence=%+v", meta)
					}
					if (kind == "timeout" || kind == "ssh") && (meta.FailurePhase != "exec" || meta.RemoteCompletion != "unknown") {
						t.Fatalf("exec evidence=%+v", meta)
					}
					body, err := os.ReadFile(path)
					if err != nil {
						t.Fatal(err)
					}
					var env struct {
						Version string                 `json:"schema_version"`
						Runs    []artifact.ResultEntry `json:"runs"`
					}
					if err := json.Unmarshal(out.Bytes(), &env); err != nil || env.Version != "v1" || len(env.Runs) != 1 || env.Runs[0].ShellStateMode != "none" {
						t.Fatalf("envelope=%s err=%v", &out, err)
					}
					var history, historyErr bytes.Buffer
					if rc := Log(nil, &history, &historyErr); rc != 0 || !strings.Contains(history.String(), "shell-state=none") {
						t.Fatalf("history rc=%d %s %s", rc, &history, &historyErr)
					}
					audit, err := os.ReadFile(filepath.Join(root, "audit.jsonl"))
					if err != nil {
						t.Fatal(err)
					}
					for _, data := range []string{tr.script, string(body), out.String(), errOut.String(), history.String(), string(audit)} {
						if strings.Contains(data, stateCanary) || strings.Contains(data, "SYNTHETIC") {
							t.Fatal("snapshot canary escaped")
						}
					}
					if follow {
						if !strings.Contains(errOut.String(), `"type":"completed"`) || !strings.Contains(errOut.String(), `"shell_state_mode":"none"`) {
							t.Fatalf("missing completed evidence: %s", &errOut)
						}
						if kind != "stage" && !strings.Contains(errOut.String(), `"type":"started"`) {
							t.Fatal("missing follow start")
						}
					}
				})
			}
		}
	}
}

func TestNoShellStateUnsupportedPaths(t *testing.T) {
	for _, shellName := range []string{"bash", "pwsh"} {
		var out, errOut bytes.Buffer
		rc := localWithRunner(func([]string, []byte, time.Duration, int64) runner.Result {
			t.Fatal("unsupported local body executed")
			return runner.Result{}
		}, []string{"--shell", shellName, "--no-shell-state", "--", "body"}, &out, &errOut)
		if rc != exitUsage || !strings.Contains(errOut.String(), "remote Windows") {
			t.Fatalf("rc=%d %s", rc, &errOut)
		}
	}
	for _, posix := range []bool{false, true} {
		for _, cached := range []bool{false, true} {
			t.Run(fmt.Sprintf("remote/%t/%t", posix, cached), func(t *testing.T) {
				root := t.TempDir()
				t.Setenv("SSHAI_ROOT", root)
				if cached {
					if err := session.SaveFacts(root, "linux01", session.Facts{OS: "linux"}); err != nil {
						t.Fatal(err)
					}
				}
				if err := session.SaveFacts(root, "windows01", session.Facts{OS: "windows", Shell: shell.PwshDefaultShell}); err != nil {
					t.Fatal(err)
				}
				linuxTr, winTr := &noStateTr{t: t}, &noStateTr{t: t}
				tr := noStateFanoutTr{"linux01": linuxTr, "windows01": winTr}
				args := []string{"--no-shell-state"}
				if posix {
					args = append(args, "--posix-shell", "/bin/sh")
				}
				args = append(args, "linux01", "windows01", "--", "Write-Output 'permitted'")
				var out, errOut bytes.Buffer
				if rc := runWith(tr, args, &out, &errOut); rc != exitUsage || !strings.Contains(errOut.String(), "requires remote Windows") {
					t.Fatalf("rc=%d %s", rc, &errOut)
				}
				wantProbes := 1
				if cached {
					wantProbes = 0
				}
				if linuxTr.execs != wantProbes || linuxTr.puts != 0 || winTr.execs != 1 {
					t.Fatalf("unsupported dispatch linux=%+v win=%+v", linuxTr, winTr)
				}
			})
		}
	}
}

type noStateFanoutTr map[string]*noStateTr

func (f noStateFanoutTr) Exec(h, c string, b []byte, d time.Duration) (transport.Result, error) {
	return f[h].Exec(h, c, b, d)
}
func (f noStateFanoutTr) Put(h, l, r string, d time.Duration) error { return f[h].Put(h, l, r, d) }

func TestNoShellStateComparisonsAndQuery(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "windows01", session.Facts{OS: "windows", Shell: shell.PwshDefaultShell}); err != nil {
		t.Fatal(err)
	}
	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	command := "Write-Output 'permitted'"
	key := delta.Key("windows01", "test", command)
	seed := func(mode string) string {
		m, err := store.Save(artifact.Meta{Host: "windows01", Ctx: "test", Command: command, ShellStateMode: mode, Ts: time.Now()}, key, []byte("old\n"))
		if err != nil {
			t.Fatal(err)
		}
		return m.ID
	}
	legacy := seed("")
	stateful := seed("stateful")
	run := func(wantBase string) string {
		var out, errOut bytes.Buffer
		rc := runWithStore(&noStateTr{t: t}, store, []string{"--no-shell-state", "--delta", "--ctx", "test", "windows01", "--", command}, &out, &errOut)
		if rc != 0 {
			t.Fatalf("rc=%d %s", rc, &errOut)
		}
		m, ok, err := store.LastByKeyMode(key, "none")
		if err != nil || !ok || m.DeltaBase != wantBase {
			t.Fatalf("delta=%+v ok=%t err=%v", m, ok, err)
		}
		if wantBase == "" && !strings.Contains(out.String(), "no previous run") {
			t.Fatal(out.String())
		}
		return m.ID
	}
	first := run("")
	seed("")
	latestStateful := seed("stateful")
	second := run(first)
	m, ok, err := store.LastByKey(key)
	if err != nil || !ok || m.ID != latestStateful {
		t.Fatalf("default crossed into no-state: %+v err=%v", m, err)
	}
	for _, tc := range []struct{ a, b, want string }{{stateful, second, "=stateful"}, {legacy, second, "=unknown"}, {legacy, legacy, "=unknown"}, {first, second, ""}} {
		var out, errOut bytes.Buffer
		if rc := Diff([]string{tc.a, tc.b}, &out, &errOut); rc != 0 {
			t.Fatalf("diff rc=%d %s", rc, &errOut)
		}
		if tc.want != "" && !strings.Contains(out.String(), tc.want) {
			t.Fatalf("missing mode warning: %s", &out)
		}
	}
	var out, errOut bytes.Buffer
	if rc := Q([]string{second, "--", queryTestTool(t, "grep"), "permitted"}, &out, &errOut); rc != 0 || out.String() != "permitted\n" {
		t.Fatalf("q rc=%d %s %s", rc, &out, &errOut)
	}
	old, _, err := store.Get(legacy)
	if err != nil || old.ShellStateMode != "" {
		t.Fatalf("legacy rewritten: %+v %v", old, err)
	}
}
