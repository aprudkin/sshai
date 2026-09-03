// internal/shell/envdiff.go
package shell

import "strings"

// volatileExact names are always excluded from EnvRestoreSet regardless
// of whether they changed: process- or session-scoped values that a
// fresh remote invocation legitimately sets for itself. Forcing one of
// these back onto a later, unrelated session would corrupt its context
// (a stale PWD or SHLVL) rather than restore anything meaningful.
var volatileExact = map[string]bool{
	"PWD": true, "OLDPWD": true, "SHLVL": true, "_": true,
	"TERM": true, "SHELL": true, "LANG": true,
}

// volatilePrefixes excludes whole families of connection- and
// locale-scoped variables for the same reason as volatileExact: SSH_*
// is set by the SSH connection itself, XDG_* and LC_* track the remote
// session's own runtime and locale rather than anything the wrapped
// command changed on purpose.
var volatilePrefixes = []string{"SSH_", "XDG_", "LC_"}

// EnvRestoreSet returns the entries of current that are new or changed
// relative to baseline, minus the volatile names above. This is the set
// BashWrap should re-export on the next invocation, so a later command
// sees the effects of this one (e.g. `export PATH=...`, `source
// venv/bin/activate`) without dragging along per-connection noise that
// the remote shell will set for itself anyway.
func EnvRestoreSet(baseline, current map[string]string) map[string]string {
	out := make(map[string]string)
	for name, value := range current {
		if isVolatile(name) {
			continue
		}
		if baseValue, ok := baseline[name]; !ok || baseValue != value {
			out[name] = value
		}
	}
	return out
}

// isVolatile reports whether name matches volatileExact or one of
// volatilePrefixes.
func isVolatile(name string) bool {
	if volatileExact[name] {
		return true
	}
	for _, prefix := range volatilePrefixes {
		if strings.HasPrefix(name, prefix) {
			return true
		}
	}
	return false
}
