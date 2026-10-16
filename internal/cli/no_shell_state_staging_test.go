package cli

import (
	"bytes"
	"encoding/base64"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Force the exact upload/execute interleaving without scheduling or networking:
// upload no-state -> complete stateful invocation -> execute no-state upload.
type interleavedStateTr struct {
	t               *testing.T
	files           map[string]string
	afterUpload     func()
	paths, executed []string
}

func (f *interleavedStateTr) Put(_, local, remote string, _ time.Duration) error {
	data, err := os.ReadFile(local)
	if err != nil {
		f.t.Fatal(err)
	}
	f.files[remote] = string(data)
	f.paths = append(f.paths, remote)
	if f.afterUpload != nil {
		call := f.afterUpload
		f.afterUpload = nil
		call()
	}
	return nil
}
func (f *interleavedStateTr) Exec(_ string, cmd string, _ []byte, _ time.Duration) (transport.Result, error) {
	_, path, ok := strings.Cut(cmd, "-File ")
	if !ok {
		f.t.Fatalf("unexpected invocation: %s", cmd)
	}
	script, ok := f.files[path]
	if !ok {
		f.t.Fatalf("unstaged invocation: %s", path)
	}
	f.executed = append(f.executed, script)
	sentinel := sentinelFromStdin([]byte(script))
	out := "permitted\n\n" + sentinel + "\n"
	if strings.Contains(script, "Get-ChildItem env:") {
		out += "C:\\synthetic\n" + base64.StdEncoding.EncodeToString([]byte("SYNTHETIC="+stateCanary)) + "\n"
	}
	return transport.Result{Output: []byte(out)}, nil
}

func TestNoShellStateStagingCannotBeOverwrittenByStatefulRun(t *testing.T) {
	root := t.TempDir()
	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := session.SaveFacts(root, "windows01", session.Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "cmd"}); err != nil {
		t.Fatal(err)
	}
	if err := session.SaveState(root, "windows01", "test", shell.State{Cwd: `C:\synthetic`, Env: map[string]string{"SYNTHETIC": stateCanary}}); err != nil {
		t.Fatal(err)
	}
	if err := session.SaveBaseline(root, "windows01", map[string]string{}); err != nil {
		t.Fatal(err)
	}
	tr := &interleavedStateTr{t: t, files: map[string]string{}}
	deps := Deps{Tr: tr, Store: store}
	opts := Opts{Host: "windows01", Ctx: "test", Command: "Write-Output 'permitted'", Timeout: time.Second, Budget: 100}
	tr.afterUpload = func() {
		var out, errOut bytes.Buffer
		statefulOpts := opts
		statefulOpts.NoShellState = false
		result := runHost(deps, statefulOpts, &out, &errOut)
		meta, ok := result.Meta()
		if !ok || meta.Exit != 0 || meta.ShellStateMode != "stateful" {
			t.Fatalf("stateful interleave failed: %+v %s", result, &errOut)
		}
	}
	opts.NoShellState = true
	var out, errOut bytes.Buffer
	result := runHost(deps, opts, &out, &errOut)
	meta, ok := result.Meta()
	if !ok || meta.ShellStateMode != "none" {
		t.Fatalf("no-state result=%+v %s", result, &errOut)
	}
	if len(tr.paths) != 2 || tr.paths[0] == tr.paths[1] {
		t.Fatalf("modes share staging path: %v", tr.paths)
	}
	if want := shell.RemoteDir + "/" + shell.BodySlug([]byte(opts.Command)) + ".ps1"; tr.paths[1] != want {
		t.Fatalf("changed default staging path: %s", tr.paths[1])
	}
	if len(tr.executed) != 2 || strings.Contains(tr.executed[1], "Get-ChildItem env:") || strings.Contains(tr.executed[1], stateCanary) {
		t.Fatal("no-state executed overwritten stateful wrapper")
	}
	body, err := os.ReadFile(root + "/art/" + meta.ID)
	if err != nil || string(body) != "permitted\n" {
		t.Fatalf("no-state artifact=%q err=%v", body, err)
	}
}
