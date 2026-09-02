// internal/shell/pwsh.go
package shell

import (
	"bytes"
	"crypto/sha1"
	"encoding/base64"
	"encoding/hex"
	"sort"
	"strings"
)

const (
	// PwshDefaultShell is the PowerShell 7 executable used by default.
	PwshDefaultShell = `C:\Program Files\PowerShell\7\pwsh.exe`
	// WindowsPowerShellShell is the in-box Windows PowerShell 5.1 executable.
	WindowsPowerShellShell = `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
)

// RemoteDir is the remote scratch directory a staged script is uploaded
// to, relative to the SSH login's home directory: the remote file is
// <RemoteDir>/<slug>.ps1 (see BodySlug). The staging-dir + slug-filename
// convention is ported from ps_ssh.py's REMOTE_DIR (".claude-ps-ssh",
// staged as f"{REMOTE_DIR}/{slug}.ps1"); only the value is renamed here,
// to sshai's own scratch-dir convention (see ~/.sshai/ in the design
// doc), not ps_ssh.py's Claude-Code-specific one.
const RemoteDir = ".sshai"

// pwq single-quotes s for safe embedding inside a PowerShell single-quoted
// string literal, doubling any embedded single quote — the only character
// PowerShell requires escaped in that context.
func pwq(s string) string {
	return strings.ReplaceAll(s, "'", "''")
}

// PwshScript builds a pwsh script from body: a UTF-8 BOM, an encoding
// preamble, an optional Set-Location restoring st.Cwd, one
// ${env:NAME} = 'VALUE' restore line per entry in restore (sorted by
// name so the generated script is deterministic across runs with the
// same inputs), then body wrapped in a try/catch/finally epilogue. Per
// PowerShell's try/finally semantics, the finally block is designed to
// run on every exit path out of the try — normal return, a thrown
// exception, or `exit` inside body — printing, after body's own output:
// a blank line, the sentinel, the remote cwd, and a base64-encoded env
// dump, all consumed by PwshParse. (This environment has no pwsh host to
// run the script against, so that guarantee is stated as the template's
// design intent, not as something exercised here — the try/finally
// state epilogue itself is new to sshai, not part of ps_ssh.py, which
// never captured state this way.)
// The returned bytes
// are meant to be scp-staged to <RemoteDir>/<slug>.ps1 and invoked with
// -File — body is never placed in argv, so it can carry arbitrary
// content including secrets without leaking through the process table.
// Ported from ps_ssh.py (BOM + encoding preamble; the try/finally state
// epilogue is new to sshai, matching BashWrap's trap-EXIT epilogue).
func PwshScript(body string, st State, restore map[string]string, sentinel string) []byte {
	var b strings.Builder

	// pwsh over OpenSSH defaults console encoding to cp437; non-cp437 glyphs
	// become literal "?" ON THE HOST, before the pipe. Ported from ps_ssh.py.
	b.WriteString("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n")
	b.WriteString("$OutputEncoding           = [System.Text.Encoding]::UTF8\n")
	b.WriteString("# pwsh over OpenSSH defaults console encoding to cp437; non-cp437 glyphs become\n")
	b.WriteString("# literal \"?\" ON THE HOST, before the pipe. Ported from ps_ssh.py.\n")

	if st.Cwd != "" {
		b.WriteString("Set-Location -LiteralPath '" + pwq(st.Cwd) + "' -ErrorAction SilentlyContinue\n")
	}

	names := make([]string, 0, len(restore))
	for name := range restore {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		b.WriteString("${env:" + name + "} = '" + pwq(restore[name]) + "'\n")
	}

	b.WriteString("$__sshai_rc = 0\n")
	b.WriteString("try {\n")
	b.WriteString("  . {\n")
	b.WriteString(body)
	if !strings.HasSuffix(body, "\n") {
		b.WriteString("\n")
	}
	b.WriteString("  }\n")
	b.WriteString("  if (Test-Path Variable:LASTEXITCODE) { $__sshai_rc = $LASTEXITCODE }\n")
	b.WriteString("} catch {\n")
	b.WriteString("  $_ | Out-String | Write-Output\n")
	b.WriteString("  $__sshai_rc = 1\n")
	b.WriteString("} finally {\n")
	b.WriteString("  Write-Output ''\n")
	b.WriteString("  Write-Output '" + pwq(sentinel) + "'\n")
	b.WriteString("  Write-Output (Get-Location).Path\n")
	b.WriteString("  $__sshai_env = (Get-ChildItem env: | ForEach-Object { \"$($_.Name)=$($_.Value)\" }) -join \"`n\"\n")
	b.WriteString("  Write-Output ([Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($__sshai_env)))\n")
	b.WriteString("}\n")
	b.WriteString("exit $__sshai_rc\n")

	return append([]byte{0xEF, 0xBB, 0xBF}, []byte(b.String())...)
}

// PwshInvocation builds the remote command string for the resolved
// DefaultShell form. A pwsh-default host parses a bare "<shell>" as a
// string literal and chokes on the token that follows (LooksLikePwshDefault
// detects this), so the "pwsh" form leads with "& " to force invocation;
// a cmd-default host answers "& was unexpected at this time." to that
// same prefix, so the "cmd" form must never lead with it. Ported from
// ps_ssh.py's wrap_invocation.
func PwshInvocation(form, shell, tail string) string {
	prefix := ""
	suffix := ""
	if form == "pwsh" {
		prefix = "& "
		// OpenSSH's PowerShell DefaultShell otherwise collapses a native
		// child process's non-zero exit to 1. Propagate the exact pwsh.exe
		// code so callers can distinguish (for example) exit 5 from exit 1.
		suffix = "; exit $LASTEXITCODE"
	}
	return prefix + `"` + shell + `" ` + tail + suffix
}

// LooksLikePwshDefault reports whether output carries one of the
// signatures a pwsh-default host emits when it parses a bare "<shell>"
// invocation (the "cmd" form) as a string literal and chokes on the
// token that follows. Ported from ps_ssh.py's looks_like_pwsh_default.
func LooksLikePwshDefault(output []byte) bool {
	text := string(output)
	return strings.Contains(text, "Unexpected token") || strings.Contains(text, "ParserError")
}

// PwshParse splits the raw output of a PwshScript-wrapped script into the
// command's real output and the state its try/finally epilogue appended.
// It first runs raw through FilterCLIXML to drop the CLIXML wrapper pwsh
// emits around stderr/warning/verbose streams over a non-interactive SSH
// session (ported from ps_ssh.py's filter_clixml), then strips a trailing
// "\r" from every line — pwsh over OpenSSH writes CRLF, but ps_ssh.py
// never needed to correct for that because it only printed lines and
// never matched one for equality. The try/finally state epilogue that
// PwshScript adds is new to sshai, and its sentinel/cwd/env lines are
// matched exactly (splitAtSentinel) or base64-decoded, both of which
// break on a stray trailing "\r" that a plain split on "\n" leaves in
// place; stripping it here, once, keeps that fix out of splitAtSentinel
// itself, which BashParse also uses and whose bash-side input is not
// CRLF-terminated. The two lines that follow the sentinel are the cwd
// and the base64-encoded env dump — unlike bash's NUL-separated `env -0`
// dump, pwsh's dump is base64 of UTF-8 "NAME=VALUE" entries joined by
// "\n" (see the script template's $__sshai_env line), so it gets its own
// decode path (decodePwshEnvDump) rather than forcing decodeEnvDump to
// serve both formats.
//
// ok is false when sentinel never appears in the filtered lines — e.g.
// the command was killed before the epilogue ran. out is then raw
// unchanged (CLIXML noise and all — the caller sees the same raw bytes a
// non-epilogue failure path would) and st is the zero State; the caller
// simply does not update its stored state.
func PwshParse(raw []byte, sentinel string) (out []byte, st State, ok bool) {
	lines := FilterCLIXML(string(raw))
	for i, line := range lines {
		lines[i] = strings.TrimSuffix(line, "\r")
	}

	out, rest, ok := splitAtSentinel(lines, sentinel)
	if !ok {
		return raw, State{}, false
	}

	if len(rest) > 0 {
		st.Cwd = rest[0]
	}
	if len(rest) > 1 {
		st.Env = decodePwshEnvDump(rest[1])
	}
	return out, st, true
}

// decodePwshEnvDump decodes b64 — the base64 form of UTF-8 "NAME=VALUE"
// entries joined by "\n" that the script template's $__sshai_env line
// produces from `Get-ChildItem env:` — into a name->value map. Each entry
// is split on the first '=' only, so a value containing '=' round-trips
// correctly. Unlike decodeEnvDump's NUL-separated `env -0` dump, this is
// pwsh's own newline-joined format, so it gets a separate decode path
// rather than one decoder distorting either.
func decodePwshEnvDump(b64 string) map[string]string {
	decoded, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return nil
	}

	env := make(map[string]string)
	for _, entry := range strings.Split(string(decoded), "\n") {
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

// BodySlug returns the first 8 hex characters of the SHA-1 digest of
// body, normalized the same way ps_ssh.py's slug_for/normalize_body pair
// does: CRLF collapsed to LF, then trailing newlines trimmed. Two bodies
// that differ only in line-ending or trailing-newline style land on the
// same remote file (<RemoteDir>/<slug>.ps1), so an already-staged script
// is reused instead of re-uploaded. Ported from ps_ssh.py.
func BodySlug(body []byte) string {
	normalized := bytes.ReplaceAll(body, []byte("\r\n"), []byte("\n"))
	normalized = bytes.TrimRight(normalized, "\n")
	sum := sha1.Sum(normalized)
	return hex.EncodeToString(sum[:])[:8]
}
