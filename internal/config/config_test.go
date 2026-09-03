// internal/config/config_test.go
package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultsAndRootOverride(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("SSHAI_ROOT", dir)
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Root != dir || cfg.BudgetTokens != 500 || cfg.TimeoutSec != 60 ||
		cfg.StreamCapBytes != 64<<20 || cfg.RetentionDays != 7 || cfg.ControlPersist != "15m" {
		t.Fatalf("bad defaults: %+v", cfg)
	}
}

func TestConfigTomlOverridesAndHostFlags(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("SSHAI_ROOT", dir)
	toml := "budget_tokens = 900\n[hosts.dc01]\nos = \"windows\"\nreadonly = true\n"
	if err := os.WriteFile(filepath.Join(dir, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.BudgetTokens != 900 || !cfg.Hosts["dc01"].Readonly || cfg.Hosts["dc01"].OS != "windows" {
		t.Fatalf("toml not applied: %+v", cfg)
	}
}

func TestConfigTomlCannotOverrideRoot(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("SSHAI_ROOT", dir)
	toml := "root = \"/elsewhere\"\n"
	if err := os.WriteFile(filepath.Join(dir, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Root != dir {
		t.Fatalf("config.toml overrode SSHAI_ROOT-derived Root: got %q, want %q", cfg.Root, dir)
	}
}
