package tunnel

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/netutil"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/scorer"
)

var (
	ErrAuthFailed  = errors.New("openvpn auth failed")
	ErrConnectFail = errors.New("openvpn connect failed")
	ErrLeak        = errors.New("egress leaked to main NAT")
	ErrNotTargetCC = errors.New("egress country mismatch")
	ErrDirtyEgress = errors.New("egress is proxy or hosting")
)

var stripLineRe = regexp.MustCompile(`(?i)^\s*(dev|dev-type|auth-user-pass|route-nopull|route-noexec|script-security|route-up|down|data-ciphers|data-ciphers-fallback)\b`)

type EgressInfo struct {
	IP        string `json:"ip"`
	Country   string `json:"country"`
	ASN       string `json:"asn"`
	ISP       string `json:"isp"`
	Proxy     bool   `json:"proxy"`
	Hosting   bool   `json:"hosting"`
	LatencyMs int64  `json:"latency_ms"`
}

type Status struct {
	Dev         string      `json:"dev"`
	Table       int         `json:"table"`
	State       string      `json:"state"` // idle | connecting | ready | dead
	EntryIP     string      `json:"entry_ip,omitempty"`
	Tier        int         `json:"tier"`
	Clean       int         `json:"clean"`
	Egress      *EgressInfo `json:"egress,omitempty"`
	ConnectedAt time.Time   `json:"connected_at,omitempty"`
	LastProbeOK time.Time   `json:"last_probe_ok,omitempty"`
	LastProbeMs int64       `json:"last_probe_ms,omitempty"`
	LastError   string      `json:"last_error,omitempty"`
	PID         int         `json:"pid,omitempty"`
}

type Slot struct {
	Dev   string
	Table int

	mu          sync.RWMutex
	state       string
	cand        *scorer.Candidate
	egress      *EgressInfo
	connectedAt time.Time
	lastProbeOK time.Time
	lastProbeMs int64
	lastErr     string
	cmd         *exec.Cmd
	deadCh      chan struct{}
}

func NewSlot(dev string, table int) *Slot {
	return &Slot{
		Dev:   dev,
		Table: table,
		state: "idle",
	}
}

func (s *Slot) DeadChan() <-chan struct{} {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.deadCh
}

func (s *Slot) IsReady() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.state == "ready"
}

func (s *Slot) EntryIP() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.cand == nil {
		return ""
	}
	return s.cand.IP
}

func (s *Slot) Snapshot() Status {
	s.mu.RLock()
	defer s.mu.RUnlock()
	st := Status{
		Dev:         s.Dev,
		Table:       s.Table,
		State:       s.state,
		ConnectedAt: s.connectedAt,
		LastProbeOK: s.lastProbeOK,
		LastProbeMs: s.lastProbeMs,
		LastError:   s.lastErr,
	}
	if s.cand != nil {
		st.EntryIP = s.cand.IP
		st.Tier = s.cand.Tier
		st.Clean = s.cand.Clean
	}
	if s.egress != nil {
		cp := *s.egress
		st.Egress = &cp
	}
	if s.cmd != nil && s.cmd.Process != nil {
		st.PID = s.cmd.Process.Pid
	}
	return st
}

func (s *Slot) RecordProbe(ok bool, lat time.Duration, errMsg string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if ok {
		s.lastProbeOK = time.Now()
		s.lastProbeMs = lat.Milliseconds()
		s.lastErr = ""
	} else {
		s.lastErr = errMsg
	}
}

func (s *Slot) buildOVPN(rawCfg, authPath string) string {
	var b strings.Builder
	for _, ln := range strings.Split(rawCfg, "\n") {
		if stripLineRe.MatchString(ln) {
			continue
		}
		b.WriteString(strings.TrimRight(ln, "\r"))
		b.WriteByte('\n')
	}
	fmt.Fprintf(&b, `dev-type tun
dev %s
auth-user-pass %s
auth-nocache
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305:AES-128-CBC:AES-256-CBC
data-ciphers-fallback AES-128-CBC
mssfix 1360
route-nopull
route-noexec
connect-retry 2
connect-timeout 8
resolv-retry 2
ping 5
ping-restart 15
mute-replay-warnings
`, s.Dev, authPath)
	return b.String()
}

func (s *Slot) Stop() {
	s.mu.Lock()
	cmd := s.cmd
	deadCh := s.deadCh
	s.cmd = nil
	s.state = "idle"
	s.egress = nil
	s.mu.Unlock()

	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Signal(syscall.SIGTERM)
		if deadCh != nil {
			select {
			case <-deadCh:
			case <-time.After(2 * time.Second):
				_ = cmd.Process.Kill()
				<-deadCh
			}
		} else {
			_ = cmd.Process.Kill()
		}
	}
}

// Start 启动 OpenVPN、配置本槽探活路由表，并完成真实出口验收（204 + 动态泄漏检测 + 出口国 == targetCC）
func (s *Slot) Start(
	ctx context.Context,
	cand scorer.Candidate,
	workDir, authPath, targetCC string,
	mainEgress netutil.MainEgress,
	requireClean bool,
) (*EgressInfo, error) {
	s.Stop()

	ovpnPath := filepath.Join(workDir, s.Dev+".ovpn")
	if err := os.WriteFile(ovpnPath, []byte(s.buildOVPN(cand.OVPNConfig, authPath)), 0o600); err != nil {
		return nil, err
	}

	cmd := exec.Command("openvpn", "--config", ovpnPath)
	pr, pw := io.Pipe()
	cmd.Stdout = pw
	cmd.Stderr = pw

	if err := cmd.Start(); err != nil {
		_ = pr.Close()
		_ = pw.Close()
		return nil, fmt.Errorf("start openvpn: %w", err)
	}

	deadCh := make(chan struct{})
	readyCh := make(chan error, 1)

	s.mu.Lock()
	cCopy := cand
	s.cand = &cCopy
	s.cmd = cmd
	s.deadCh = deadCh
	s.state = "connecting"
	s.lastErr = ""
	s.mu.Unlock()

	go func() {
		defer pr.Close()
		scanner := bufio.NewScanner(pr)
		sentReady := false
		for scanner.Scan() {
			line := scanner.Text()
			if !sentReady {
				if strings.Contains(line, "Initialization Sequence Completed") {
					sentReady = true
					readyCh <- nil
				} else if strings.Contains(line, "AUTH_FAILED") {
					sentReady = true
					readyCh <- ErrAuthFailed
				} else if strings.Contains(line, "Exiting due to fatal error") {
					sentReady = true
					readyCh <- fmt.Errorf("%w: %s", ErrConnectFail, line)
				}
			}
		}
		if !sentReady {
			readyCh <- ErrConnectFail
		}
	}()

	go func() {
		_ = cmd.Wait()
		_ = pw.Close()
		s.mu.Lock()
		if s.cmd == cmd {
			s.state = "dead"
		}
		s.mu.Unlock()
		close(deadCh)
	}()

	// 等待隧道建立（最多 15s）
	select {
	case err := <-readyCh:
		if err != nil {
			s.Stop()
			return nil, err
		}
	case <-time.After(15 * time.Second):
		s.Stop()
		return nil, fmt.Errorf("%w: handshake timeout 15s", ErrConnectFail)
	case <-ctx.Done():
		s.Stop()
		return nil, ctx.Err()
	}

	// 建立本槽探活路由表 (table 101 / 102)
	if err := netutil.SetSlotRoute(s.Dev, s.Table); err != nil {
		s.Stop()
		return nil, err
	}

	// 实测验收：204 + 真实出口 IP + 动态泄漏/国家/纯净度核验
	verifyCtx, cancel := context.WithTimeout(ctx, 18*time.Second)
	defer cancel()

	probeClient := netutil.NewBoundHTTPClient(s.Dev, 6*time.Second, false)
	code, lat, err := netutil.Probe204(verifyCtx, probeClient, "https://cp.cloudflare.com/generate_204")
	if err != nil || code != 204 {
		s.Stop()
		return nil, fmt.Errorf("%w: 204 probe failed (code=%d err=%v)", ErrConnectFail, code, err)
	}

	eip, err := netutil.ProbeEgressIP(verifyCtx, s.Dev, 6*time.Second)
	if err != nil {
		s.Stop()
		return nil, fmt.Errorf("%w: egress ip failed: %v", ErrConnectFail, err)
	}

	ipInfo, err := netutil.LookupIPInfo(verifyCtx, eip)
	if err != nil {
		s.Stop()
		return nil, fmt.Errorf("%w: ip-api verify failed: %v", ErrConnectFail, err)
	}

	if bad, reason := netutil.IsLeakOrInvalid(eip, ipInfo.CountryCode, ipInfo.ASN, targetCC, mainEgress); bad {
		s.Stop()
		if strings.HasPrefix(reason, "country_mismatch") {
			return nil, fmt.Errorf("%w: %s", ErrNotTargetCC, reason)
		}
		return nil, fmt.Errorf("%w: %s", ErrLeak, reason)
	}

	if requireClean && cand.Tier == 0 && (ipInfo.Proxy || ipInfo.Hosting) {
		s.Stop()
		return nil, fmt.Errorf("%w: egress=%s proxy=%v hosting=%v", ErrDirtyEgress, eip, ipInfo.Proxy, ipInfo.Hosting)
	}

	eg := &EgressInfo{
		IP:        eip,
		Country:   strings.ToUpper(ipInfo.CountryCode),
		ASN:       ipInfo.ASN,
		ISP:       ipInfo.ISP,
		Proxy:     ipInfo.Proxy,
		Hosting:   ipInfo.Hosting,
		LatencyMs: lat.Milliseconds(),
	}

	s.mu.Lock()
	s.state = "ready"
	s.egress = eg
	s.connectedAt = time.Now()
	s.lastProbeOK = time.Now()
	s.lastProbeMs = lat.Milliseconds()
	s.mu.Unlock()

	slog.Info("slot verified and ready",
		"dev", s.Dev, "entry", cand.IP, "egress", eg.IP,
		"cc", eg.Country, "asn", eg.ASN, "isp", eg.ISP,
		"proxy", eg.Proxy, "hosting", eg.Hosting, "lat_ms", eg.LatencyMs)
	return eg, nil
}
