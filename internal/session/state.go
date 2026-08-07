// internal/session/state.go
package session

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Facts is a host's cached first-contact identity: which OS it runs, and
// — for a Windows host — which pwsh executable and invocation Form (see
// Probe) actually work over this host's OpenSSH DefaultShell. Form is
// empty for a Linux host, where no shell-form ambiguity exists.
type Facts struct {
	OS    string // "linux" | "windows"
	Shell string
	Form  string // "" | "cmd" | "pwsh"
}

// hostDir is the per-host state directory, <root>/state/<host>/, holding
// facts.json, baseline.json, and one <ctx>.json per shell context.
func hostDir(root, host string) string {
	return filepath.Join(root, "state", host)
}

func factsPath(root, host string) string {
	return filepath.Join(hostDir(root, host), "facts.json")
}

func statePath(root, host, ctx string) string {
	return filepath.Join(hostDir(root, host), ctx+".json")
}

func baselinePath(root, host string) string {
	return filepath.Join(hostDir(root, host), "baseline.json")
}

// LoadFacts reads the cached Facts for host under root. ok is false when
// no facts have been cached yet (first contact still pending); that is
// not an error.
func LoadFacts(root, host string) (Facts, bool, error) {
	var f Facts
	ok, err := readJSON(factsPath(root, host), &f)
	return f, ok, err
}

// SaveFacts writes host's Facts under root, atomically.
func SaveFacts(root, host string, f Facts) error {
	return writeJSON(factsPath(root, host), f)
}

// LoadState reads the cached shell.State for host's ctx under root. ok is
// false when no state has been saved yet for this (host, ctx) pair.
func LoadState(root, host, ctx string) (shell.State, bool, error) {
	var st shell.State
	ok, err := readJSON(statePath(root, host, ctx), &st)
	return st, ok, err
}

// SaveState writes host's ctx shell.State under root, atomically.
func SaveState(root, host, ctx string, st shell.State) error {
	return writeJSON(statePath(root, host, ctx), st)
}

// LoadBaseline reads host's cached environment baseline under root — the
// env snapshot captured once at first contact, per OS path, and diffed
// against on later runs (see shell's env-diff restore). ok is false when
// no baseline has been captured yet.
func LoadBaseline(root, host string) (map[string]string, bool, error) {
	var env map[string]string
	ok, err := readJSON(baselinePath(root, host), &env)
	return env, ok, err
}

// SaveBaseline writes host's environment baseline under root, atomically.
func SaveBaseline(root, host string, env map[string]string) error {
	return writeJSON(baselinePath(root, host), env)
}

// readJSON unmarshals the JSON file at path into v. ok is false and err
// is nil when the file does not exist; ok is false and err is non-nil
// when the file exists but is not valid JSON — these are distinct
// outcomes, and callers must not conflate a corrupt cache with an absent
// one.
func readJSON(path string, v any) (bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}
	if err := json.Unmarshal(data, v); err != nil {
		return false, fmt.Errorf("parse %s: %w", path, err)
	}
	return true, nil
}

// writeJSON marshals v to JSON and writes it to path atomically: the
// parent directory is created (0o700) if needed, the data is written to
// a sibling ".tmp" file (0o600), and that file is renamed into place —
// so a reader never observes a partially written state file.
func writeJSON(path string, v any) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("create dir %s: %w", dir, err)
	}
	data, err := json.Marshal(v)
	if err != nil {
		return fmt.Errorf("marshal %s: %w", path, err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return fmt.Errorf("write %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("rename %s to %s: %w", tmp, path, err)
	}
	return nil
}

// Probe determines whether host runs Linux or Windows and, for Windows,
// which invocation Form works over this host's OpenSSH DefaultShell. It
// runs `uname -s` first: an honest exit 0 whose output contains "Linux",
// "Darwin", or "BSD" is treated as Linux family. Any other outcome (a
// non-zero rc — no bash on Windows's default shell — or unrecognized
// output) falls through to the Windows path.
//
// The Windows path is ported from ps_ssh.py's ensure_remote_dir: it
// creates the remote scratch dir (shell.RemoteDir) via New-Item, trying
// each invocation form in turn ("cmd" first, then "pwsh") and returning
// as soon as one works. Resolving the form here, at first contact, rather
// than at execute time, is deliberate — ps_ssh.py's original flow ended
// this step in `|| true`, so on a pwsh-default host it failed silently,
// the scratch dir was never created, scp then failed, and the run
// aborted at transport; the "& " fallback at the execute step could never
// fire on the very hosts it existed for. A transport failure (Exec
// returning a *transport.TransportError) is returned immediately. A
// non-zero rc that does NOT carry LooksLikePwshDefault's signature is
// accepted as-is: the directory very likely already exists (ps_ssh.py's
// original form swallowed exactly this case with `|| true`), and scp is
// the real test of whether the path is writable. If both forms fail with
// the signature, the loop falls back to form "cmd" with no error,
// mirroring ensure_remote_dir's own loop-exhausted return.
func Probe(tr transport.Transport, host, pwshShell string, timeout time.Duration) (Facts, error) {
	res, err := tr.Exec(host, "uname -s", nil, timeout)
	if err != nil {
		return Facts{}, err
	}
	if res.ExitCode == 0 {
		out := string(res.Output)
		if strings.Contains(out, "Linux") || strings.Contains(out, "Darwin") || strings.Contains(out, "BSD") {
			return Facts{OS: "linux"}, nil
		}
	}

	tail := `-NoProfile -Command "New-Item -ItemType Directory -Force -Path '` + shell.RemoteDir + `' | Out-Null"`
	for _, form := range []string{"cmd", "pwsh"} {
		cmd := shell.PwshInvocation(form, pwshShell, tail)
		res, err := tr.Exec(host, cmd, nil, timeout)
		if err != nil {
			return Facts{}, err
		}
		if res.ExitCode == 0 {
			return Facts{OS: "windows", Shell: pwshShell, Form: form}, nil
		}
		if !shell.LooksLikePwshDefault(res.Output) {
			// Non-zero without the signature: the directory very likely
			// already exists. Ported from ps_ssh.py's ensure_remote_dir —
			// scp is the real writability test.
			return Facts{OS: "windows", Shell: pwshShell, Form: form}, nil
		}
	}
	// Both forms failed with the pwsh-default signature. Ported from
	// ps_ssh.py's ensure_remote_dir loop-exhausted return.
	return Facts{OS: "windows", Shell: pwshShell, Form: "cmd"}, nil
}
