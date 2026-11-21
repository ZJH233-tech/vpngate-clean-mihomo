package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

type SlotConfig struct {
	Name  string `json:"name"`
	Table int    `json:"table"`
	Mark  int    `json:"mark"`
}

type Config struct {
	WorkerURL          string     `json:"worker_url"`
	WorkerToken        string     `json:"worker_token,omitempty"`
	BootstrapCSV       string     `json:"bootstrap_csv,omitempty"`
	TargetCountry      string     `json:"target_country"`
	MinSpeedMbps       float64    `json:"min_speed_mbps"`
	RefreshIntervalSec int        `json:"refresh_interval_sec"`
	ProbeIntervalSec   int        `json:"probe_interval_sec"`
	ProbeTimeoutSec    int        `json:"probe_timeout_sec"`
	ConfirmTimeoutSec  int        `json:"confirm_timeout_sec"`
	ConfirmDelayMs     int        `json:"confirm_delay_ms"`
	StandbyCheckSec    int        `json:"standby_check_sec"`
	WorkDir            string     `json:"work_dir"`
	ControlListen      string     `json:"control_listen"`
	GatewayListen      string     `json:"gateway_listen"`
	PublicHost         string     `json:"public_host,omitempty"`
	SubToken           string     `json:"sub_token,omitempty"`
	VLESSUUID          string     `json:"vless_uuid,omitempty"`
	JPBackend          string     `json:"jp_backend"`
	DirectBackend      string     `json:"direct_backend"`
	TrafficMark        int        `json:"traffic_mark"`
	TrafficTable       int        `json:"traffic_table"`
	SlotA              SlotConfig `json:"slot_a"`
	SlotB              SlotConfig `json:"slot_b"`
}

func Default() *Config {
	return &Config{
		TargetCountry:      "JP",
		MinSpeedMbps:       3.0,
		RefreshIntervalSec: 1800,
		ProbeIntervalSec:   5,
		ProbeTimeoutSec:    4,
		ConfirmTimeoutSec:  3,
		ConfirmDelayMs:     500,
		StandbyCheckSec:    25,
		WorkDir:            "/var/lib/vpngate-relay",
		ControlListen:      "100.68.93.106:19099",
		GatewayListen:      "127.0.0.1:18000",
		JPBackend:          "http://127.0.0.1:18444",
		DirectBackend:      "http://127.0.0.1:18080",
		TrafficMark:        354,
		TrafficTable:       100,
		SlotA:              SlotConfig{Name: "vpns-a", Table: 101, Mark: 355},
		SlotB:              SlotConfig{Name: "vpns-b", Table: 102, Mark: 356},
	}
}

func Load(path string) (*Config, error) {
	cfg := Default()
	if path != "" {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("read config %s: %w", path, err)
		}
		if err := json.Unmarshal(data, cfg); err != nil {
			return nil, fmt.Errorf("parse config %s: %w", path, err)
		}
	}
	// 机密项优先由环境变量（secrets.env）注入，避免明文放进 config.json
	if v := strings.TrimSpace(os.Getenv("VPNGATE_WORKER")); v != "" {
		cfg.WorkerURL = v
	}
	if v := strings.TrimSpace(os.Getenv("VPNGATE_WORKER_TOKEN")); v != "" {
		cfg.WorkerToken = v
	}
	if v := strings.TrimSpace(os.Getenv("VPNGATE_SUB_TOKEN")); v != "" {
		cfg.SubToken = v
	}
	if v := strings.TrimSpace(os.Getenv("VPNGATE_VLESS_UUID")); v != "" {
		cfg.VLESSUUID = v
	}
	if v := strings.TrimSpace(os.Getenv("VPNGATE_PUBLIC_HOST")); v != "" {
		cfg.PublicHost = v
	}
	if v := strings.TrimSpace(os.Getenv("VPNGATE_BOOTSTRAP_CSV")); v != "" {
		cfg.BootstrapCSV = v
	}

	cfg.TargetCountry = strings.ToUpper(strings.TrimSpace(cfg.TargetCountry))
	if cfg.TargetCountry == "" {
		cfg.TargetCountry = "JP"
	}
	if cfg.ProbeIntervalSec < 2 {
		cfg.ProbeIntervalSec = 5
	}
	if cfg.ProbeTimeoutSec < 2 {
		cfg.ProbeTimeoutSec = 4
	}
	if cfg.ConfirmTimeoutSec < 2 {
		cfg.ConfirmTimeoutSec = 3
	}
	if cfg.RefreshIntervalSec < 60 {
		cfg.RefreshIntervalSec = 1800
	}
	return cfg, nil
}
