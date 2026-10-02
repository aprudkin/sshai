package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Wording guards for the public body-input contract, not secret detection or
// redaction tests. Keeping text out of argv must not be advertised as a secret
// input channel. The issue explicitly requires help/contract regression checks.
func TestBodyInputSecretContract(t *testing.T) {
	for _, topic := range []string{"run", "local"} {
		t.Run("help/"+topic, func(t *testing.T) {
			var out, errOut bytes.Buffer
			if rc := Help([]string{topic}, &out, &errOut); rc != 0 {
				t.Fatalf("help returned %d: %s", rc, &errOut)
			}
			text := strings.Join(strings.Fields(out.String()), " ")
			for _, want := range []string{
				"Do not embed secret values", "argv", "captured output",
				"does not redact", "separately authorized, purpose-built workflow",
				"not an implicit raw-SSH or archived-helper fallback",
			} {
				if !strings.Contains(text, want) {
					t.Errorf("help %s missing body-input boundary %q", topic, want)
				}
			}
			if strings.Contains(text, "secret, or containing characters") {
				t.Error("help recommends body-file for secret content")
			}
		})
	}

	for _, tc := range []struct {
		path string
		want []string
	}{
		{"skills/sshai/SKILL.md", []string{"out of argv", "no secret values", "staged scripts", "captured output", "separately approved, purpose-built workflow"}},
		{"docs/agent-usage.md", []string{"out of argv", "no secret values", "staged scripts", "captured output", "separately approved, purpose-built workflow"}},
		{"internal/shell/bash.go", []string{"argv", "Do not embed secret values", "captured output"}},
		{"internal/shell/pwsh.go", []string{"argv", "Do not embed secret values", "staged scripts", "captured output"}},
	} {
		t.Run(tc.path, func(t *testing.T) {
			data, err := os.ReadFile(filepath.Join("..", "..", tc.path))
			if err != nil {
				t.Fatal(err)
			}
			text := strings.Join(strings.Fields(strings.ReplaceAll(string(data), "//", "")), " ")
			for _, want := range tc.want {
				if !strings.Contains(text, want) {
					t.Errorf("missing body-input boundary %q", want)
				}
			}
			if strings.Contains(text, "including secrets without leaking") {
				t.Error("wrapper comment still advertises secret bodies")
			}
		})
	}
}
