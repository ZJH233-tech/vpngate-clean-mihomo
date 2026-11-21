package collector

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/netutil"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/store"
)

const OfficialVPNGateURL = "https://www.vpngate.net/api/iphone/"

type FetchResult struct {
	Servers []store.RawServer
	Source  string
}

// ParseVPNGateCSV 稳健解析 VPNGate CSV（前 13 列固定，末尾为 base64；自动拒绝 ASP.NET HTML 拦截页）
func ParseVPNGateCSV(text string) ([]store.RawServer, error) {
	if strings.Contains(strings.ToLower(text), "<html") || strings.Contains(text, "__VIEWSTATE") {
		return nil, errors.New("upstream returned HTML instead of CSV (blocked/intercepted)")
	}
	var out []store.RawServer
	for _, rawLine := range strings.Split(text, "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" || line[0] == '*' || line[0] == '#' {
			continue
		}
		parts := strings.Split(rawLine, ",")
		if len(parts) < 15 {
			continue
		}
		ip := strings.TrimSpace(parts[1])
		b64 := strings.TrimSpace(parts[len(parts)-1])
		if strings.Count(ip, ".") != 3 || len(b64) < 200 {
			continue
		}
		score, _ := strconv.Atoi(strings.TrimSpace(parts[2]))
		ping, _ := strconv.Atoi(strings.TrimSpace(parts[3]))
		speed, _ := strconv.ParseInt(strings.TrimSpace(parts[4]), 10, 64)
		out = append(out, store.RawServer{
			HostName:     strings.TrimSpace(parts[0]),
			IP:           ip,
			Score:        score,
			Ping:         ping,
			Speed:        speed,
			CountryShort: strings.ToUpper(strings.TrimSpace(parts[6])),
			ConfigBase64: b64,
		})
	}
	if len(out) == 0 {
		return nil, errors.New("parsed 0 servers from CSV")
	}
	return out, nil
}

// ParseWorkerJSON 仅提取原始节点字段，忽略 Worker 侧旧 quality 评分，由目标机本地独立评分
func ParseWorkerJSON(data []byte) ([]store.RawServer, error) {
	var payload struct {
		Error   bool              `json:"error"`
		Servers []store.RawServer `json:"servers"`
	}
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, err
	}
	if payload.Error || len(payload.Servers) == 0 {
		return nil, errors.New("worker returned empty or error payload")
	}
	for i := range payload.Servers {
		payload.Servers[i].CountryShort = strings.ToUpper(strings.TrimSpace(payload.Servers[i].CountryShort))
	}
	return payload.Servers, nil
}

func fetchURL(ctx context.Context, client *http.Client, rawURL string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) vpngate-relayd/2.0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d from %s", resp.StatusCode, rawURL)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 16<<20))
}

func normalizeWorkerURL(u string) string {
	u = strings.TrimSpace(u)
	if u == "" {
		return ""
	}
	trimmed := strings.TrimRight(u, "/")
	// 若只填了根域名，默认补 /api/servers
	if strings.Count(trimmed, "/") == 2 {
		return trimmed + "/api/servers"
	}
	return u
}

// Fetch 按优先级拉取名单：
//  1. 主通道：CF Worker 直供（JSON 或 CSV）
//  2. 应急保底：仅当存在已验证存活的日本隧道（activeIface != ""）时，经活隧道自举直拉 vpngate.net
//  3. 可选冷启动本地 CSV / 上次缓存保底
func Fetch(ctx context.Context, workerURL, activeIface, bootstrapCSV string, st *store.Store) (FetchResult, error) {
	var errs []string

	if w := normalizeWorkerURL(workerURL); w != "" {
		c := netutil.NewBoundHTTPClient("", 25*time.Second, false)
		body, err := fetchURL(ctx, c, w)
		if err == nil {
			trim := strings.TrimSpace(string(body))
			var list []store.RawServer
			if strings.HasPrefix(trim, "{") {
				list, err = ParseWorkerJSON(body)
			} else {
				list, err = ParseVPNGateCSV(trim)
			}
			if err == nil && len(list) > 0 {
				if st != nil {
					st.SaveRawServers(list)
				}
				return FetchResult{Servers: list, Source: "worker"}, nil
			}
		}
		errs = append(errs, fmt.Sprintf("worker: %v", err))
	}

	// 前置条件：必须先有活隧道，才能自举；双隧道全死时 activeIface 为空，不走本分支
	if activeIface != "" {
		c := netutil.NewBoundHTTPClient(activeIface, 25*time.Second, false)
		body, err := fetchURL(ctx, c, OfficialVPNGateURL)
		if err == nil {
			list, perr := ParseVPNGateCSV(string(body))
			if perr == nil && len(list) > 0 {
				if st != nil {
					st.SaveRawServers(list)
				}
				return FetchResult{Servers: list, Source: "tunnel_bootstrap:" + activeIface}, nil
			}
			err = perr
		}
		errs = append(errs, fmt.Sprintf("tunnel_bootstrap(%s): %v", activeIface, err))
	}

	if bootstrapCSV != "" {
		if b, err := os.ReadFile(bootstrapCSV); err == nil {
			if list, perr := ParseVPNGateCSV(string(b)); perr == nil && len(list) > 0 {
				if st != nil {
					st.SaveRawServers(list)
				}
				return FetchResult{Servers: list, Source: "bootstrap_csv"}, nil
			}
		}
	}

	if st != nil {
		if cached := st.LoadRawServers(); len(cached) > 0 {
			return FetchResult{Servers: cached, Source: "local_cache"}, nil
		}
	}

	return FetchResult{}, fmt.Errorf("all collector sources failed: %s", strings.Join(errs, "; "))
}
