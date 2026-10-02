package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Late overflow from an exited SCP child must stop Windows staging before
// user-body dispatch. These stand-ins execute locally without a Windows host.
func TestStageCaptureOverflowPreventsDispatch(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX fake SSH/SCP fixture")
	}
	for _, format := range []string{"human", "json"} {
		t.Run(format, func(t *testing.T) {
			store := openOutcomeTestStore(t, t.TempDir())
			dir := t.TempDir()
			marker := filepath.Join(dir, "body-dispatched")
			t.Setenv("SSHAI_STAGE_DISPATCH_MARKER", marker)
			for name, body := range map[string]string{
				"ssh": "printf dispatched > \"$SSHAI_STAGE_DISPATCH_MARKER\"\nexit 0\n",
				"scp": "printf 'before\\n'\n(sleep 0.4; printf 'after\\n') &\nexit 0\n",
			} {
				if err := os.WriteFile(filepath.Join(dir, name), []byte("#!/bin/sh\n"+body), 0o700); err != nil {
					t.Fatal(err)
				}
			}
			t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
			if err := session.SaveFacts(store.Root, "synthetic01", session.Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "cmd"}); err != nil {
				t.Fatal(err)
			}
			tr := transport.NewOpenSSH(t.TempDir(), "15m", 8, transport.OpenSSHOptions{})
			var out, errOut bytes.Buffer
			rc, outcomes := runInvocation(Deps{Tr: tr, Store: store}, []Opts{{Host: "synthetic01", Ctx: "test", Command: "synthetic body", Timeout: 2 * time.Second, Budget: 500}}, resultModeOptions{format: format}, &out, &errOut)
			if rc != exitTransport || len(outcomes) != 1 {
				t.Fatalf("rc=%d outcomes=%v stderr=%s", rc, outcomes, &errOut)
			}
			if _, err := os.Stat(marker); !os.IsNotExist(err) {
				t.Fatalf("user body dispatched after failed staging: %v", err)
			}
			meta, path, err := store.Get(outcomes[0].ArtifactID())
			if err != nil {
				t.Fatal(err)
			}
			body, err := os.ReadFile(path)
			if err != nil || string(body) != "transport diagnostic: scp output capture incomplete\n" || meta.TransportErr != "scp" || meta.TransportDiagnostic != "scp output capture incomplete" || meta.FailurePhase != "stage" || meta.RemoteCompletion != "not_started" {
				t.Fatalf("incorrect staging evidence: meta=%+v body=%q err=%v", meta, body, err)
			}
			if format == "human" {
				if !strings.Contains(out.String(), artifact.StatusLine(meta)) || strings.Contains(out.String(), "before") {
					t.Fatalf("passport disagrees or leaks raw SCP output: %q", out.String())
				}
			} else {
				var envelope struct {
					Runs []artifact.ResultEntry `json:"runs"`
				}
				if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || len(envelope.Runs) != 1 {
					t.Fatalf("JSON=%s err=%v", &out, err)
				}
				entry := envelope.Runs[0]
				if entry.TransportError != "scp" || entry.TransportDiagnostic != "scp output capture incomplete" || entry.FailurePhase != "stage" || entry.RemoteCompletion != "not_started" {
					t.Fatalf("incorrect JSON staging evidence: %+v", entry)
				}
			}
		})
	}
}
