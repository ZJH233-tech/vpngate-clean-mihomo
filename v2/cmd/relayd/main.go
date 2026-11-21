package main

import (
	"context"
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
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/store"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/watchdog"
)

const version = "2.0.0-step2"

func main() {
	cfgPath := flag.String("config", "", "path to config.json")
	apiAddr := flag.String("api", "http://127.0.0.1:19099", "control API base URL for CLI subcommands")
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
	case "run":
	default:
		fmt.Fprintf(os.Stderr, "usage: vpngate-relayd [-config path] [run|status|switch|refresh|version]\n")
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
	if err := mgr.Run(ctx); err != nil {
		slog.Error("relayd exited with error", "err", err)
		os.Exit(1)
	}
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
