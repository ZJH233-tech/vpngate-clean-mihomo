package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/config"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/gateway"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/store"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/watchdog"
)

const version = "2.0.0-step3"

func main() {
	cfgPath := flag.String("config", "", "path to config.json")
	apiAddr := flag.String("api", "http://127.0.0.1:19099", "control API base URL for CLI subcommands")
	outPath := flag.String("out", "/etc/vpngate-relay/sing-box.json", "output path for render-singbox")
	showVer := flag.Bool("version", false, "print version")
	flag.Parse()

	if *showVer {
		fmt.Println("vpngate-relayd", version)
		return
	}

	sub := "run"
	if flag.NArg() > 0 {
		sub = flag.Arg(0)
	}

	switch sub {
	case "version":
		fmt.Println("vpngate-relayd", version)
		return
	case "status":
		callAPI("GET", *apiAddr+"/status")
		return
	case "switch":
		callAPI("POST", *apiAddr+"/switch")
		return
	case "refresh":
		callAPI("POST", *apiAddr+"/refresh")
		return
	case "render-singbox":
		renderSingBox(*cfgPath, *outPath)
		return
	case "run":
	default:
		fmt.Fprintf(os.Stderr, "usage: vpngate-relayd [-config path] [run|status|switch|refresh|render-singbox|version]\n")
		os.Exit(2)
	}

	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		slog.Error("load config failed", "err", err)
		os.Exit(1)
	}
	st, err := store.Open(cfg.WorkDir)
	if err != nil {
		slog.Error("open store failed", "err", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	mgr := watchdog.New(cfg, st)

	var gw *gateway.Server
	if cfg.GatewayListen != "" {
		gw, err = gateway.New(cfg, mgr)
		if err != nil {
			slog.Error("init gateway failed", "err", err)
			os.Exit(1)
		}
		gw.Start()
		defer func() {
			shCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_ = gw.Shutdown(shCtx)
		}()
	}

	if err := mgr.Run(ctx); err != nil {
		slog.Error("relayd exited with error", "err", err)
		os.Exit(1)
	}
}

func renderSingBox(cfgPath, outPath string) {
	cfg, err := config.Load(cfgPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "load config:", err)
		os.Exit(1)
	}
	if cfg.VLESSUUID == "" {
		fmt.Fprintln(os.Stderr, "ERROR: VPNGATE_VLESS_UUID / vless_uuid is empty")
		os.Exit(1)
	}
	doc := map[string]any{
		"log": map[string]any{"level": "warn", "timestamp": true},
		"inbounds": []any{
			map[string]any{
				"type": "vless", "tag": "in-jp",
				"listen": "127.0.0.1", "listen_port": 18444,
				"users":     []any{map[string]any{"name": "jp", "uuid": cfg.VLESSUUID}},
				"transport": map[string]any{"type": "ws", "path": "/ws/jp"},
			},
			map[string]any{
				"type": "vless", "tag": "in-direct",
				"listen": "127.0.0.1", "listen_port": 18080,
				"users":     []any{map[string]any{"name": "direct", "uuid": cfg.VLESSUUID}},
				"transport": map[string]any{"type": "ws", "path": "/ws/direct"},
			},
		},
		"outbounds": []any{
			map[string]any{"type": "direct", "tag": "via-jp", "routing_mark": cfg.TrafficMark},
			map[string]any{"type": "direct", "tag": "direct"},
		},
		"route": map[string]any{
			"rules": []any{
				map[string]any{"action": "resolve", "strategy": "ipv4_only"},
				map[string]any{
					"ip_cidr": []string{
						"::/0",
						"10.0.0.0/8",
						"100.64.0.0/10",
						"127.0.0.0/8",
						"169.254.0.0/16",
						"172.16.0.0/12",
						"192.168.0.0/16",
					},
					"action": "reject",
				},
				map[string]any{"inbound": []string{"in-jp"}, "outbound": "via-jp"},
				map[string]any{"inbound": []string{"in-direct"}, "outbound": "direct"},
			},
			"final": "direct",
		},
	}
	b, _ := json.MarshalIndent(doc, "", "  ")
	if err := os.WriteFile(outPath, append(b, '\n'), 0o600); err != nil {
		fmt.Fprintln(os.Stderr, "write sing-box config:", err)
		os.Exit(1)
	}
	fmt.Println("rendered sing-box config ->", outPath)
}

func callAPI(method, url string) {
	c := &http.Client{Timeout: 30 * time.Second}
	req, err := http.NewRequest(method, url, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	resp, err := c.Do(req)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	fmt.Print(string(body))
	if resp.StatusCode >= 400 {
		os.Exit(1)
	}
}
