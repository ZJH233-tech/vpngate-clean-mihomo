package netutil

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var asnRe = regexp.MustCompile(`(?i)\b(AS\d+)\b`)

type MainEgress struct {
	IP        string    `json:"ip"`
	Country   string    `json:"country"`
	ASN       string    `json:"asn"`
	ISP       string    `json:"isp"`
	CheckedAt time.Time `json:"checked_at"`
}

type IPInfo struct {
	Query       string `json:"query"`
	CountryCode string `json:"countryCode"`
	ISP         string `json:"isp"`
	Org         string `json:"org"`
	AS          string `json:"as"`
	ASN         string `json:"asn"`
	Proxy       bool   `json:"proxy"`
	Hosting     bool   `json:"hosting"`
	Mobile      bool   `json:"mobile"`
}

func ExtractASN(asField string) string {
	m := asnRe.FindStringSubmatch(asField)
	if len(m) >= 2 {
		return strings.ToUpper(m[1])
	}
	return ""
}

// IsLeakOrInvalid 依据动态主出口基线判定泄漏或不合格，不硬编码任何 IP 或网段
func IsLeakOrInvalid(egressIP, egressCC, egressASN, targetCC string, main MainEgress) (bool, string) {
	if strings.TrimSpace(egressIP) == "" {
		return true, "empty_egress_ip"
	}
	if main.IP != "" && egressIP == main.IP {
		return true, "leak_matches_main_ip"
	}
	if targetCC != "" && !strings.EqualFold(egressCC, targetCC) {
		return true, fmt.Sprintf("country_mismatch(want=%s,got=%s)", targetCC, egressCC)
	}
	if main.ASN != "" && egressASN != "" && strings.EqualFold(egressASN, main.ASN) {
		return true, fmt.Sprintf("leak_matches_main_asn(%s)", egressASN)
	}
	return false, ""
}

// NewBoundHTTPClient 构造绑定到指定网卡（SO_BINDTODEVICE）且强制 IPv4 的 HTTP 客户端
func NewBoundHTTPClient(iface string, timeout time.Duration, keepAlive bool) *http.Client {
	dialer := &net.Dialer{
		Timeout:   timeout,
		KeepAlive: 30 * time.Second,
	}
	if iface != "" {
		dialer.Control = func(network, address string, c syscall.RawConn) error {
			var sockErr error
			if err := c.Control(func(fd uintptr) {
				sockErr = syscall.SetsockoptString(int(fd), syscall.SOL_SOCKET, syscall.SO_BINDTODEVICE, iface)
			}); err != nil {
				return err
			}
			return sockErr
		}
	}
	tr := &http.Transport{
		DialContext: func(ctx context.Context, _, addr string) (net.Conn, error) {
			return dialer.DialContext(ctx, "tcp4", addr)
		},
		DisableKeepAlives:     !keepAlive,
		MaxIdleConns:          4,
		MaxIdleConnsPerHost:   2,
		IdleConnTimeout:       60 * time.Second,
		TLSHandshakeTimeout:   timeout,
		ResponseHeaderTimeout: timeout,
		ForceAttemptHTTP2:     false,
	}
	return &http.Client{
		Transport: tr,
		Timeout:   timeout,
	}
}

func CloseIdle(c *http.Client) {
	if c == nil {
		return
	}
	if tr, ok := c.Transport.(*http.Transport); ok {
		tr.CloseIdleConnections()
	}
}

func Probe204(ctx context.Context, client *http.Client, url string) (int, time.Duration, error) {
	start := time.Now()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, 0, err
	}
	req.Header.Set("User-Agent", "vpngate-relayd/2.0")
	resp, err := client.Do(req)
	if err != nil {
		return 0, time.Since(start), err
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	return resp.StatusCode, time.Since(start), nil
}

func ProbeEgressIP(ctx context.Context, iface string, timeout time.Duration) (string, error) {
	c := NewBoundHTTPClient(iface, timeout, false)
	endpoints := []string{
		"https://api.ipify.org",
		"https://ifconfig.me/ip",
	}
	var lastErr error
	for _, ep := range endpoints {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, ep, nil)
		if err != nil {
			lastErr = err
			continue
		}
		req.Header.Set("User-Agent", "curl/8.5")
		resp, err := c.Do(req)
		if err != nil {
			lastErr = err
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 128))
		resp.Body.Close()
		ip := strings.TrimSpace(string(body))
		if net.ParseIP(ip) != nil {
			return ip, nil
		}
		lastErr = fmt.Errorf("invalid ip from %s: %q", ep, ip)
	}
	return "", lastErr
}

func LookupIPInfo(ctx context.Context, ip string) (*IPInfo, error) {
	c := NewBoundHTTPClient("", 10*time.Second, false)
	u := "http://ip-api.com/json/" + ip + "?fields=query,countryCode,as,isp,org,proxy,hosting,mobile"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "vpngate-relayd/2.0")
	resp, err := c.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ip-api status %d", resp.StatusCode)
	}
	var info IPInfo
	if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
		return nil, err
	}
	info.ASN = ExtractASN(info.AS)
	return &info, nil
}

func ProbeMainEgress(ctx context.Context, prev MainEgress) MainEgress {
	info, err := LookupIPInfo(ctx, "")
	if err == nil && info.Query != "" {
		return MainEgress{
			IP:        info.Query,
			Country:   strings.ToUpper(info.CountryCode),
			ASN:       info.ASN,
			ISP:       info.ISP,
			CheckedAt: time.Now(),
		}
	}
	// ip-api 临时失败时退回 api.ipify.org，同时保留上一次识别到的 ASN
	if ip, err2 := ProbeEgressIP(ctx, "", 8*time.Second); err2 == nil {
		out := prev
		out.IP = ip
		out.CheckedAt = time.Now()
		return out
	}
	return prev
}

func IfaceIPv4(dev string) (string, bool) {
	ifi, err := net.InterfaceByName(dev)
	if err != nil {
		return "", false
	}
	addrs, err := ifi.Addrs()
	if err != nil {
		return "", false
	}
	for _, a := range addrs {
		if ipnet, ok := a.(*net.IPNet); ok {
			if v4 := ipnet.IP.To4(); v4 != nil {
				return v4.String(), true
			}
		}
	}
	return "", false
}

func runIP(args ...string) error {
	cmd := exec.Command("ip", args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("ip %s: %v (%s)", strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return nil
}

// EnsureBaseRouting 幂等建立策略路由规则（priority 100/101，完全避让 Tailscale priority 5210+）
func EnsureBaseRouting(trafficMark, trafficTable int, slotADev string, slotATable int, slotBDev string, slotBTable int) error {
	mStr := strconv.Itoa(trafficMark)
	tStr := strconv.Itoa(trafficTable)

	_ = runIP("-4", "rule", "del", "fwmark", mStr, "lookup", tStr)
	if err := runIP("-4", "rule", "add", "fwmark", mStr, "lookup", tStr, "priority", "101"); err != nil {
		return err
	}
	_ = runIP("-6", "rule", "del", "fwmark", mStr, "lookup", tStr)
	_ = runIP("-6", "rule", "add", "fwmark", mStr, "lookup", tStr, "priority", "101")
	_ = runIP("-6", "route", "replace", "blackhole", "default", "table", tStr)

	for _, slot := range []struct {
		dev   string
		table int
	}{{slotADev, slotATable}, {slotBDev, slotBTable}} {
		st := strconv.Itoa(slot.table)
		_ = runIP("-4", "rule", "del", "oif", slot.dev, "lookup", st)
		if err := runIP("-4", "rule", "add", "oif", slot.dev, "lookup", st, "priority", "100"); err != nil {
			return err
		}
	}
	return nil
}

func SetSlotRoute(dev string, table int) error {
	if err := runIP("-4", "route", "replace", "default", "dev", dev, "table", strconv.Itoa(table)); err != nil {
		return err
	}
	_ = exec.Command("sysctl", "-w", "net.ipv4.conf."+dev+".rp_filter=2").Run()
	_ = runIP("route", "flush", "cache")
	return nil
}

// SwitchActiveRoute 原子切换生产流量路由表（< 10ms，无需重启代理/隧道）
func SwitchActiveRoute(dev string, trafficTable int) error {
	if err := runIP("-4", "route", "replace", "default", "dev", dev, "table", strconv.Itoa(trafficTable)); err != nil {
		return err
	}
	_ = runIP("route", "flush", "cache")
	return nil
}

// ClearActiveRoute 清空生产表（fail-closed，防止隧道全断时泄漏回主 NAT）
func ClearActiveRoute(trafficTable int) error {
	_ = runIP("-4", "route", "flush", "table", strconv.Itoa(trafficTable))
	_ = runIP("route", "flush", "cache")
	return nil
}
