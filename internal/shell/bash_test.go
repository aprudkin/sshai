// internal/shell/bash_test.go
package shell

import (
	"bytes"
	"encoding/base64"
	"os/exec"
	"strings"
	"testing"
)

func TestBashWrapShape(t *testing.T) {
	w := string(BashWrap("make test", State{Cwd: "/opt/app", Env: nil},
		map[string]string{"FOO": "a'b"}, "__SSHAI_x__"))
	for _, want := range []string{
		"trap __sshai_epilogue EXIT",
		"cd '/opt/app' 2>/dev/null || true",
		`export FOO='a'\''b'`,
		"make test",
		"'__SSHAI_x__'",
	} {
		if !strings.Contains(w, want) {
			t.Errorf("wrapper missing %q:\n%s", want, w)
		}
	}
	if strings.Index(w, "make test") < strings.Index(w, "export FOO") {
		t.Error("body must come after env restore")
	}
}

func TestBashWrapFollowCombinesBodyStderrOnlyInFollowMode(t *testing.T) {
	body := "echo out; echo err >&2"
	plain := BashWrap(body, State{}, nil, "S")
	if !bytes.Equal(plain, BashWrapFollow(body, State{}, nil, "S", "")) {
		t.Fatal("non-follow wrapper bytes changed")
	}
	follow := string(BashWrapFollow(body, State{}, nil, "S", "MARK"))
	if !strings.Contains(follow, "printf '%s\\n' 'MARK'\n{\n"+body+"\n} 2>&1") {
		t.Fatalf("follow wrapper lacks body-only stderr redirect:\n%s", follow)
	}
}

func TestBashWrapFollowExecutesRedirectAndPreservesExit(t *testing.T) {
	cmd := exec.Command("bash")
	cmd.Stdin = bytes.NewReader(BashWrapFollow("printf out; printf err >&2; exit 5", State{}, nil, "S", "MARK"))
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if code := cmd.ProcessState.ExitCode(); err == nil || code != 5 {
		t.Fatalf("err=%v exit=%d", err, code)
	}
	if stderr.Len() != 0 {
		t.Fatalf("body stderr was not combined remotely: %q", stderr.String())
	}
	out, _, ok := BashParse(stdout.Bytes(), "S")
	if !ok || !strings.Contains(string(out), "MARK\nouterr") {
		t.Fatalf("parse=%q ok=%v", out, ok)
	}
}

func TestPOSIXShellInvocation(t *testing.T) {
	if got, want := POSIXShellInvocation("/bin/ash"), `'/bin/ash' -c 'exec '\''/bin/ash'\'' -s'`; got != want {
		t.Fatalf("POSIXShellInvocation()=%q, want %q", got, want)
	}

	path := `/opt/ash;$(id)'`
	got := POSIXShellInvocation(path)
	if !strings.HasPrefix(got, shq(path)+" -c ") {
		t.Fatalf("selected shell is not quoted as one executable word: %q", got)
	}
	if !strings.Contains(got, shq("exec "+shq(path)+" -s")) {
		t.Fatalf("bootstrap does not safely exec the selected shell with -s: %q", got)
	}
	if strings.Contains(got, "bash") {
		t.Fatalf("POSIX bootstrap must not invoke bash: %q", got)
	}
}

func TestBashParseRoundTrip(t *testing.T) {
	envDump := "PATH=/usr/bin\x00NEW=hello\x00"
	raw := []byte("real output\n\n__SSHAI_x__\n/var/tmp\n" +
		base64.StdEncoding.EncodeToString([]byte(envDump)) + "\n")
	out, st, ok := BashParse(raw, "__SSHAI_x__")
	if !ok || !bytes.Equal(out, []byte("real output\n")) {
		t.Fatalf("out=%q ok=%v", out, ok)
	}
	if st.Cwd != "/var/tmp" || st.Env["NEW"] != "hello" || st.Env["PATH"] != "/usr/bin" {
		t.Fatalf("state: %+v", st)
	}
}

func TestBashParseNoSentinel(t *testing.T) {
	out, _, ok := BashParse([]byte("killed before epilogue"), "__SSHAI_x__")
	if ok || string(out) != "killed before epilogue" {
		t.Fatalf("out=%q ok=%v", out, ok)
	}
}
