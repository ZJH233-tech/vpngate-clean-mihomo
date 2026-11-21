package gateway

import (
	"context"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/config"
)

var safeHostRe = regexp.MustCompile(`^[A-Za-z0-9._:\-]{1,253}$`)

type StatusSummary struct {
	ActiveDev string
	EgressIP  string
	EgressISP string
	LatencyMs int64
}

type StatusProvider interface {
	ActiveSummary() StatusSummary
}

type Server struct {
	cfg    *config.Config
	status StatusProvider
	jp     *httputil.ReverseProxy
	direct *httputil.ReverseProxy
	http   *http.Server
}

func newWSProxy(rawTarget string) (*httputil.ReverseProxy, error) {
	u, err := url.Parse(rawTarget)
	if err != nil {
		return nil, err
	}
	p := httputil.NewSingleHostReverseProxy(u)
	p.FlushInterval = -1
	p.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		slog.Warn("gateway proxy error", "path", r.URL.Path, "target", rawTarget, "err", err)
		http.Error(w, "Bad Gateway", http.StatusBadGateway)
	}
	return p, nil
}

func New(cfg *config.Config, sp StatusProvider) (*Server, error) {
	jp, err := newWSProxy(cfg.JPBackend)
	if err != nil {
		return nil, fmt.Errorf("parse jp_backend: %w", err)
	}
	direct, err := newWSProxy(cfg.DirectBackend)
	if err != nil {
		return nil, fmt.Errorf("parse direct_backend: %w", err)
	}
	s := &Server{
		cfg:    cfg,
		status: sp,
		jp:     jp,
		direct: direct,
	}
	s.http = &http.Server{
		Addr:              cfg.GatewayListen,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 10 * time.Second, // 绝不设 ReadTimeout/WriteTimeout，避免误断长连接 WS
	}
	return s, nil
}

func (s *Server) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/ws/jp":
			s.jp.ServeHTTP(w, r)
			return
		case r.URL.Path == "/ws/direct":
			s.direct.ServeHTTP(w, r)
			return
		case strings.HasPrefix(r.URL.Path, "/sub/"):
			s.handleSub(w, r)
			return
		default:
			http.NotFound(w, r)
		}
	})
}

func tokenMatch(got, want string) bool {
	if want == "" || len(got) != len(want) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(got), []byte(want)) == 1
}

func stripPort(hostport string) string {
	if h, _, err := net.SplitHostPort(hostport); err == nil {
		return h
	}
	return strings.Trim(hostport, "[]")
}

func sanitizeHost(h string) string {
	h = strings.TrimSpace(h)
	h = stripPort(h)
	if !safeHostRe.MatchString(h) {
		return ""
	}
	return h
}

func (s *Server) resolveHosts(r *http.Request) (sniHost, connectHost string) {
	// 优先级：?host= > config.PublicHost > 请求头 Host（TryCloudflare 零配置自动识别）
	sniHost = sanitizeHost(r.URL.Query().Get("host"))
	if sniHost == "" {
		sniHost = sanitizeHost(s.cfg.PublicHost)
	}
	if sniHost == "" {
		sniHost = sanitizeHost(r.Host)
	}
	connectHost = sanitizeHost(r.URL.Query().Get("cfip"))
	if connectHost == "" {
		connectHost = sniHost
	}
	return sniHost, connectHost
}

func (s *Server) handleSub(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.NotFound(w, r)
		return
	}
	// 路径格式: /sub/<token>/<format>  (format = uri | mihomo)
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/sub/"), "/")
	if len(parts) != 2 || !tokenMatch(parts[0], s.cfg.SubToken) {
		http.NotFound(w, r)
		return
	}
	if s.cfg.VLESSUUID == "" {
		http.Error(w, "VLESS UUID not configured", http.StatusServiceUnavailable)
		return
	}
	sniHost, connectHost := s.resolveHosts(r)
	if sniHost == "" || connectHost == "" {
		http.Error(w, "public host unavailable", http.StatusBadRequest)
		return
	}

	switch parts[1] {
	case "uri":
		body := s.BuildURISub(sniHost, connectHost)
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Profile-Update-Interval", "12")
		_, _ = w.Write([]byte(body))
	case "mihomo":
		body := s.BuildMihomoSub(sniHost, connectHost)
		w.Header().Set("Content-Type", "text/yaml; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Profile-Update-Interval", "12")
		w.Header().Set("Content-Disposition", "attachment; filename*=utf-8''vpngate-jp.yaml")
		_, _ = w.Write([]byte(body))
	default:
		http.NotFound(w, r)
	}
}

func buildVLESSURI(uuid, connectHost, sniHost, path, remark string) string {
	q := url.Values{}
	q.Set("encryption", "none")
	q.Set("security", "tls")
	q.Set("sni", sniHost)
	q.Set("type", "ws")
	q.Set("host", sniHost)
	q.Set("path", path)
	return fmt.Sprintf("vless://%s@%s:443?%s#%s", uuid, connectHost, q.Encode(), url.PathEscape(remark))
}

func (s *Server) BuildURISub(sniHost, connectHost string) string {
	lines := []string{
		buildVLESSURI(s.cfg.VLESSUUID, connectHost, sniHost, "/ws/jp", "🇯🇵 JP-Residential"),
		buildVLESSURI(s.cfg.VLESSUUID, connectHost, sniHost, "/ws/direct", "🇮🇩 Carrier-Direct"),
	}
	return base64.StdEncoding.EncodeToString([]byte(strings.Join(lines, "\n") + "\n"))
}

func (s *Server) BuildMihomoSub(sniHost, connectHost string) string {
	var note string
	if s.status != nil {
		st := s.status.ActiveSummary()
		if st.EgressIP != "" {
			note = fmt.Sprintf("# 当前活跃日本出口: %s (%s) via %s, probe=%dms\n",
				st.EgressIP, st.EgressISP, st.ActiveDev, st.LatencyMs)
		}
	}
	return fmt.Sprintf(`# VPNGate Relay v2 (Argo-WS) 实时订阅
# 生成时间(UTC): %s
%smixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false
unified-delay: true
tcp-concurrent: true
proxies:
  - name: "🇯🇵 JP-Residential"
    type: vless
    server: %s
    port: 443
    uuid: %s
    tls: true
    udp: true
    servername: %s
    network: ws
    ws-opts:
      path: /ws/jp
      headers:
        Host: %s
    client-fingerprint: chrome
  - name: "🇮🇩 Carrier-Direct"
    type: vless
    server: %s
    port: 443
    uuid: %s
    tls: true
    udp: true
    servername: %s
    network: ws
    ws-opts:
      path: /ws/direct
      headers:
        Host: %s
    client-fingerprint: chrome
proxy-groups:
  - name: "VPNGate-JP"
    type: select
    proxies: ["🇯🇵 JP-Residential", "🇮🇩 Carrier-Direct", "DIRECT"]
rules:
  - GEOIP,LAN,DIRECT,no-resolve
  - GEOIP,CN,DIRECT
  - MATCH,VPNGate-JP
`, time.Now().UTC().Format(time.RFC3339), note,
		connectHost, s.cfg.VLESSUUID, sniHost, sniHost,
		connectHost, s.cfg.VLESSUUID, sniHost, sniHost)
}

func (s *Server) Start() {
	go func() {
		slog.Info("gateway listening (Argo WS proxy + subscription)", "addr", s.cfg.GatewayListen)
		if err := s.http.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("gateway server error", "err", err)
		}
	}()
}

func (s *Server) Shutdown(ctx context.Context) error {
	return s.http.Shutdown(ctx)
}
