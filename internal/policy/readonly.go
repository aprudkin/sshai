// internal/policy/readonly.go
package policy

import (
	"fmt"
	"regexp"
	"strings"
)

// splitRe splits a command into the segments chained together by shell
// control operators: &&, ||, ;, |, and newline. Every segment must clear
// the allowlist independently — otherwise a denied command could be
// smuggled in behind an innocuous leading read, e.g.
// "df -h && rm -rf /".
var splitRe = regexp.MustCompile(`\s*(?:&&|\|\||;|\||\n)\s*`)

// readonlyAllowlist is the fixed set of anchored read-only command
// prefixes permitted on a host flagged readonly = true. A segment is
// allowed only if it starts with one of these, checked exact-case. The
// PowerShell verbs/cmdlets at the tail (see psVerbs) additionally match
// case-insensitively, since PowerShell itself treats "get-service" and
// "Get-Service" as the same invocation.
var readonlyAllowlist = []string{
	"cat ", "ls", "ls ", "grep ", "zgrep ", "head ", "tail ", "wc ",
	"df", "df ", "du ", "ps", "ps ", "free", "uptime", "whoami", "id",
	"uname", "hostname", "date", "env", "printenv", "stat ", "file ",
	"find ", "journalctl", "dmesg", "ss ", "netstat", "ip ",
	"systemctl status", "systemctl list-",
	"docker ps", "docker logs", "docker inspect",
	"kubectl get", "kubectl describe",
	"Get-", "Test-", "Measure-", "Select-", "Where-", "Format-",
	"Out-String", "Write-Output", "echo ", "pwd", "Get-Location",
}

// psVerbs is the subset of readonlyAllowlist checked case-insensitively
// (see readonlyAllowlist's doc comment).
var psVerbs = map[string]bool{
	"Get-": true, "Test-": true, "Measure-": true, "Select-": true,
	"Where-": true, "Format-": true, "Out-String": true,
	"Write-Output": true, "Get-Location": true,
}

// CheckReadonly enforces the fail-closed readonly allowlist. It returns
// nil when readonly is false — the policy does not apply — or when
// every non-empty segment of command (split on &&, ||, ;, |, and
// newline) starts with an allowlisted read-only prefix. Otherwise it
// returns an error naming the first offending segment; the caller is
// responsible for redacting any secret that segment might carry before
// it reaches a log (see runlog.Redact). A command with no non-empty
// segments at all — "", whitespace-only, or made up entirely of bare
// operators — is denied: fail-closed means the absence of a command is
// not implicitly a read.
func CheckReadonly(command string, readonly bool) error {
	if !readonly {
		return nil
	}

	sawSegment := false
	for _, seg := range splitRe.Split(command, -1) {
		seg = strings.TrimSpace(seg)
		if seg == "" {
			continue
		}
		sawSegment = true
		if !allowedByPolicy(seg) {
			return fmt.Errorf("denied by readonly policy: %s", seg)
		}
	}
	if !sawSegment {
		return fmt.Errorf("denied by readonly policy: %s", strings.TrimSpace(command))
	}
	return nil
}

// allowedByPolicy reports whether seg starts with one of
// readonlyAllowlist's prefixes: exact-case first, then — for the entries
// flagged in psVerbs — strings.EqualFold against seg's leading slice of
// the same length as the prefix.
func allowedByPolicy(seg string) bool {
	for _, prefix := range readonlyAllowlist {
		if strings.HasPrefix(seg, prefix) {
			return true
		}
		if psVerbs[prefix] && len(seg) >= len(prefix) && strings.EqualFold(seg[:len(prefix)], prefix) {
			return true
		}
	}
	return false
}
