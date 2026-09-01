// internal/shell/bash.go
package shell

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"sort"
	"strings"
)

// State captures a bash session's working directory and export
// environment. BashWrap re-injects a State's Cwd and a diff of its Env
// (see EnvRestoreSet) before running a command, and BashParse recovers a
// fresh State from that command's trap-EXIT epilogue output.
type State struct {
	Cwd string
	Env map[string]string
}

// shq single-quotes s for safe embedding as one word in a POSIX shell
// command line, closing and reopening the quote around any embedded
// single quote (the standard close-quote/backslash-quote/open-quote
// idiom). No other character needs escaping inside single quotes, so
// this is safe for arbitrary s.
func shq(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// POSIXShellInvocation builds the remote bootstrap for an explicitly selected
// POSIX shell. The wrapped command remains on stdin: the bootstrap uses -c
// only to exec the selected shell again with -s, so no command body enters SSH
// argv.
func POSIXShellInvocation(path string) string {
	return shq(path) + " -c " + shq("exec "+shq(path)+" -s")
}

// NewSentinel returns a fresh, unpredictable marker line used to
// delimit a wrapped script's real output from the state its epilogue
// appends (see BashWrap, BashParse). The random suffix keeps a sentinel
// from colliding with a line the wrapped command happens to print.
func NewSentinel() string {
	buf := make([]byte, 8)
	if _, err := rand.Read(buf); err != nil {
		panic("shell: crypto/rand unavailable: " + err.Error())
	}
	return "__SSHAI_" + hex.EncodeToString(buf) + "__"
}

// BashWrap wraps body in a trap-EXIT epilogue that runs on every exit
// path — normal return, `exit`, or a signal — and prints, after the
// command's own output: a blank line, the sentinel, the remote cwd, and
// a base64-encoded `env -0` dump, all consumed by BashParse. Before body
// runs, it restores st.Cwd (when non-empty) and re-exports every
// variable in restore, sorted by name so the generated script is
// deterministic across runs with the same inputs. The returned bytes are
// meant for `bash -s` on stdin — body is never placed in argv, so it can
// carry arbitrary content including secrets without leaking through the
// process table.
func BashWrap(body string, st State, restore map[string]string, sentinel string) []byte {
	var b strings.Builder

	b.WriteString("__sshai_epilogue() {\n")
	b.WriteString("  __sshai_rc=$?\n")
	b.WriteString("  printf '\\n%s\\n' " + shq(sentinel) + "\n")
	b.WriteString("  pwd\n")
	b.WriteString("  env -0 | base64 | tr -d '\\n'\n")
	b.WriteString("  printf '\\n'\n")
	b.WriteString("  exit $__sshai_rc\n")
	b.WriteString("}\n")
	b.WriteString("trap __sshai_epilogue EXIT\n")

	if st.Cwd != "" {
		b.WriteString("cd " + shq(st.Cwd) + " 2>/dev/null || true\n")
	}

	names := make([]string, 0, len(restore))
	for name := range restore {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		b.WriteString("export " + name + "=" + shq(restore[name]) + "\n")
	}

	b.WriteString(body)

	return []byte(b.String())
}

// BashParse splits the raw output of a BashWrap-wrapped script into the
// command's real output and the state its trap-EXIT epilogue appended.
// It scans from the end for the last line exactly equal to sentinel —
// the epilogue always runs last, so this finds the real marker even if
// the command's own output happens to contain the same text. out is
// everything before that line, with the epilogue's own leading blank
// line (from its `printf '\n%s\n'`) removed. The two lines that follow
// the sentinel are the cwd and the base64 env -0 dump; a missing
// trailing newline after either is tolerated since it only changes
// whether an extra empty line trails the split.
//
// ok is false when sentinel never appears in raw — e.g. the command was
// killed before the epilogue ran. out is then raw unchanged and st is
// the zero State; the caller simply does not update its stored state.
func BashParse(raw []byte, sentinel string) (out []byte, st State, ok bool) {
	lines := strings.Split(string(raw), "\n")

	out, rest, ok := splitAtSentinel(lines, sentinel)
	if !ok {
		return raw, State{}, false
	}

	if len(rest) > 0 {
		st.Cwd = rest[0]
	}
	if len(rest) > 1 {
		st.Env = decodeEnvDump(rest[1])
	}
	return out, st, true
}

// splitAtSentinel scans lines from the end for the last line exactly
// equal to sentinel — an epilogue always runs last, so this finds the
// real marker even if the wrapped command's own output happens to
// contain the same text. It returns everything before that line
// re-joined with "\n" as out, and the lines that follow the sentinel
// (state lines: cwd first, then the encoded env dump) as rest. ok is
// false when sentinel never appears in lines, in which case out and rest
// are both nil — shared by BashParse and PwshParse, whose state lines
// carry differently-encoded env dumps but split off the same way.
func splitAtSentinel(lines []string, sentinel string) (out []byte, rest []string, ok bool) {
	idx := -1
	for i := len(lines) - 1; i >= 0; i-- {
		if lines[i] == sentinel {
			idx = i
			break
		}
	}
	if idx == -1 {
		return nil, nil, false
	}
	return []byte(strings.Join(lines[:idx], "\n")), lines[idx+1:], true
}

// decodeEnvDump decodes b64 — the base64 form of a NUL-separated
// `env -0` dump — into a name->value map. Each entry is split on the
// first '=' only, so a value containing '=' round-trips correctly. The
// dump's trailing NUL produces one empty entry after splitting on
// '\x00'; it is skipped rather than treated as a malformed variable.
func decodeEnvDump(b64 string) map[string]string {
	decoded, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return nil
	}

	env := make(map[string]string)
	for _, entry := range strings.Split(string(decoded), "\x00") {
		if entry == "" {
			continue
		}
		name, value, found := strings.Cut(entry, "=")
		if !found {
			continue
		}
		env[name] = value
	}
	return env
}
