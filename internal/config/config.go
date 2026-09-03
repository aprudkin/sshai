// internal/config/config.go
package config

import (
	"os"
	"path/filepath"

	"github.com/BurntSushi/toml"
)

type HostCfg struct {
	OS       string `toml:"os"`
	Readonly bool   `toml:"readonly"`
}

type Config struct {
	Root              string             `toml:"-"`
	BudgetTokens      int                `toml:"budget_tokens"`
	TimeoutSec        int                `toml:"timeout_sec"`
	StreamCapBytes    int64              `toml:"stream_cap_bytes"`
	RetentionDays     int                `toml:"retention_days"`
	RetentionMaxBytes int64              `toml:"retention_max_bytes"`
	ControlPersist    string             `toml:"control_persist"`
	Hosts             map[string]HostCfg `toml:"hosts"`
}

func Load() (Config, error) {
	cfg := Config{
		BudgetTokens: 500, TimeoutSec: 60, StreamCapBytes: 64 << 20,
		RetentionDays: 7, RetentionMaxBytes: 1 << 30, ControlPersist: "15m",
		Hosts: map[string]HostCfg{},
	}
	cfg.Root = os.Getenv("SSHAI_ROOT")
	if cfg.Root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return cfg, err
		}
		cfg.Root = filepath.Join(home, ".sshai")
	}
	path := filepath.Join(cfg.Root, "config.toml")
	if _, err := os.Stat(path); err == nil {
		if _, err := toml.DecodeFile(path, &cfg); err != nil {
			return cfg, err
		}
	}
	return cfg, nil
}
