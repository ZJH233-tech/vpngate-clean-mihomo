package gateway

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/config"
)

type fakeStatus struct{}

func (fakeStatus) ActiveSummary() StatusSummary {
	return StatusSummary{ActiveDev: "vpns-a", EgressIP: "106.173.94.75", EgressISP: "KDDI", LatencyMs: 120}
}

func TestGatewayAuthAndSubscriptions(t *testing.T) {
	cfg := config.Default()
	cfg.SubToken = "secret-token-123"
	cfg.VLESSUUID = "11111111-2222-3333-4444-555555555555"

	srv, err := New(cfg, fakeStatus{})
	if err != nil {
		t.Fatal(err)
	}
	h := srv.Handler()

	// 1) 根路径与错 token 必须 404
	for _, p := range []string{"/", "/status", "/sub/wrong/uri", "/sub/secret-token-123/other"} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "http://demo.trycloudflare.com"+p, nil)
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusNotFound {
			t.Fatalf("path %s: got %d, want 404", p, rec.Code)
		}
	}

	// 2) 小火箭 Base64 订阅 + ?cfip= 覆盖连接地址但保留 SNI/Host
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "http://demo.trycloudflare.com/sub/secret-token-123/uri?cfip=104.16.132.229", nil)
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("uri sub got %d", rec.Code)
	}
	decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(rec.Body.String()))
	if err != nil {
		t.Fatalf("base64 decode failed: %v", err)
	}
	s := string(decoded)
	if !strings.Contains(s, "@104.16.132.229:443?") || !strings.Contains(s, "sni=demo.trycloudflare.com") || !strings.Contains(s, "path=%2Fws%2Fjp") {
		t.Fatalf("unexpected uri sub:\n%s", s)
	}

	// 3) Mihomo YAML 订阅
	rec2 := httptest.NewRecorder()
	req2 := httptest.NewRequest(http.MethodGet, "http://demo.trycloudflare.com/sub/secret-token-123/mihomo", nil)
	h.ServeHTTP(rec2, req2)
	if rec2.Code != http.StatusOK {
		t.Fatalf("mihomo sub got %d", rec2.Code)
	}
	y := rec2.Body.String()
	if !strings.Contains(y, "server: demo.trycloudflare.com") || !strings.Contains(y, "path: /ws/jp") || !strings.Contains(y, "106.173.94.75") {
		t.Fatalf("unexpected mihomo yaml:\n%s", y)
	}
}
