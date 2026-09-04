// internal/session/state.go
package session

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
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
	OS                  string // "linux" | "windows"
	Shell               string
	Form                string // "" | "cmd" | "pwsh"
	WindowsProbeVersion int    `json:"windows_probe_version,omitempty"`
}

const currentWindowsProbeVersion = 2

var stateComponentRe = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

func validateHostComponent(host string) error {
	if host == "." || host == ".." || !stateComponentRe.MatchString(host) {
		return fmt.Errorf("invalid state host %q", host)
	}
	return nil
}

func validateContextComponent(ctx string) error {
	if ctx == "." || ctx == ".." || ctx == "facts" || ctx == "baseline" || !stateComponentRe.MatchString(ctx) {
		return fmt.Errorf("invalid state context %q", ctx)
	}
	return nil
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
	if err := validateHostComponent(host); err != nil {
		return Facts{}, false, err
	}
	var f Facts
	ok, err := readJSON(factsPath(root, host), &f)
	if ok && f.OS == "windows" && f.WindowsProbeVersion != currentWindowsProbeVersion {
		return f, false, nil
	}
	return f, ok, err
}

// SaveFacts writes host's Facts under root, atomically.
func SaveFacts(root, host string, f Facts) error {
	if err := validateHostComponent(host); err != nil {
		return err
	}
	if f.OS == "windows" {
		f.WindowsProbeVersion = currentWindowsProbeVersion
	}
	return writeJSON(factsPath(root, host), f)
}

// LoadState reads the cached shell.State for host's ctx under root. ok is
// false when no state has been saved yet for this (host, ctx) pair.
func LoadState(root, host, ctx string) (shell.State, bool, error) {
	if err := validateHostComponent(host); err != nil {
		return shell.State{}, false, err
	}
	if err := validateContextComponent(ctx); err != nil {
		return shell.State{}, false, err
	}
	var st shell.State
	ok, err := readJSON(statePath(root, host, ctx), &st)
	return st, ok, err
}

// SaveState writes host's ctx shell.State under root, atomically.
func SaveState(root, host, ctx string, st shell.State) error {
	if err := validateHostComponent(host); err != nil {
		return err
	}
	if err := validateContextComponent(ctx); err != nil {
		return err
	}
	return writeJSON(statePath(root, host, ctx), st)
}

// LoadBaseline reads host's cached environment baseline under root — the
// env snapshot captured once at first contact, per OS path, and diffed
// against on later runs (see shell's env-diff restore). ok is false when
// no baseline has been captured yet.
func LoadBaseline(root, host string) (map[string]string, bool, error) {
	if err := validateHostComponent(host); err != nil {
		return nil, false, err
	}
	var env map[string]string
	ok, err := readJSON(baselinePath(root, host), &env)
	return env, ok, err
}

// SaveBaseline writes host's environment baseline under root, atomically.
func SaveBaseline(root, host string, env map[string]string) error {
	if err := validateHostComponent(host); err != nil {
		return err
	}
	return writeJSON(baselinePath(root, host), env)
}

// readJSON unmarshals the JSON file at path into v. ok is false and err
// is nil when the file does not exist; ok is false and err is non-nil
// when the file exists but is not valid JSON — these are distinct
// outcomes, and callers must not conflate a corrupt cache with an absent
// one.
func readJSON(path string, v any) (bool, error) {
	if err := validateStateParents(path); err != nil {
		return false, err
	}
	data, err := os.ReadFile(path) // #nosec G304 -- entry points validate components and parent directories before this read.
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

// writeJSON marshals v to JSON and writes it to path atomically: the parent
// directory is created (0o700) if needed, the data is written to a private
// sibling temporary file (0o600), and that file is renamed into place. An
// existing symlink or other non-regular destination is refused.
func writeJSON(path string, v any) error {
	dir := filepath.Dir(path)
	stateDir := filepath.Dir(dir)
	root := filepath.Dir(stateDir)
	if err := os.MkdirAll(root, 0o700); err != nil {
		return fmt.Errorf("create root %s: %w", root, err)
	}
	for _, candidate := range []string{root, stateDir, dir} {
		if err := ensureStateDir(candidate); err != nil {
			return fmt.Errorf("create state dir %s: %w", candidate, err)
		}
	}
	data, err := json.Marshal(v)
	if err != nil {
		return fmt.Errorf("marshal %s: %w", path, err)
	}
	if info, err := os.Lstat(path); err == nil && !info.Mode().IsRegular() {
		return fmt.Errorf("refuse non-regular state destination %s", path)
	} else if err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("inspect %s: %w", path, err)
	}
	tmp, err := os.CreateTemp(dir, ".state-*.tmp")
	if err != nil {
		return fmt.Errorf("create temp state in %s: %w", dir, err)
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("write %s: %w", tmpName, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close %s: %w", tmpName, err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("rename %s to %s: %w", tmpName, path, err)
	}
	return nil
}

func validateStateParents(path string) error {
	dir := filepath.Dir(path)
	for _, candidate := range []string{filepath.Dir(filepath.Dir(dir)), filepath.Dir(dir), dir} {
		info, err := os.Lstat(candidate)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("refuse non-directory or symlink path %s", candidate)
		}
	}
	return nil
}

func ensureStateDir(path string) error {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return os.Mkdir(path, 0o700)
	}
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("refuse non-directory or symlink path %s", path)
	}
	return nil
}

const (
	// RemoteSetupWindowsShell is the only classified remote setup failure.
	RemoteSetupWindowsShell = "windows-shell"
	// RemoteSetupDiagnostic is the fixed, safe evidence retained for it.
	RemoteSetupDiagnostic = "Windows shell setup failed"
)

// RemoteSetupError reports an exhausted Windows shell setup probe. It is
// deliberately distinct from a transport failure: SSH worked, but no supported
// PowerShell invocation could create the required remote scratch directory.
type RemoteSetupError struct{ Class string }

func (e *RemoteSetupError) Error() string { return "remote setup failed: " + e.Class }

// Diagnostic returns fixed canonical evidence without retaining probe output.
func (e *RemoteSetupError) Diagnostic() string { return RemoteSetupDiagnostic }

// Probe determines whether host runs Linux or Windows and, for Windows,
// which invocation Form works over this host's OpenSSH DefaultShell. When
// allowWindowsPowerShellFallback is true and the requested shell is PowerShell
// 7, the Windows path also tries Windows PowerShell 5.1. It runs `uname -s`
// first: an honest exit 0 whose output contains "Linux", "Darwin", or "BSD"
// is treated as Linux family. Any other outcome falls through to the Windows
// path. The Windows path creates the remote scratch dir (shell.RemoteDir) via
// New-Item, trying PowerShell executable candidates and invocation forms until
// one command succeeds. A transport failure is returned immediately. If no
// candidate can create the scratch dir, Probe returns RemoteSetupError instead
// of saving a guessed shell form or retaining remote setup output.
func Probe(tr transport.Transport, host, pwshShell string, allowWindowsPowerShellFallback bool, timeout time.Duration) (Facts, error) {
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

	candidates := []string{pwshShell}
	if allowWindowsPowerShellFallback && pwshShell == shell.PwshDefaultShell {
		candidates = append(candidates, shell.WindowsPowerShellShell)
	}

	tail := `-NoProfile -Command "New-Item -ItemType Directory -Force -Path '` + shell.RemoteDir + `' | Out-Null"`
	for _, candidate := range candidates {
		for _, form := range []string{"cmd", "pwsh"} {
			cmd := shell.PwshInvocation(form, candidate, tail)
			res, err := tr.Exec(host, cmd, nil, timeout)
			if err != nil {
				return Facts{}, err
			}
			if res.ExitCode == 0 {
				return Facts{OS: "windows", Shell: candidate, Form: form}, nil
			}
		}
	}
	return Facts{}, &RemoteSetupError{Class: RemoteSetupWindowsShell}
}
