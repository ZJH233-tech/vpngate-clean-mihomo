package scorer

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/netutil"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/store"
)

const (
	OfficialPrefix = "219.100.37."
	IPCacheTTL     = 24 * time.Hour
)

var (
	jpResidentialISP = regexp.MustCompile(`(?i)(ntt|ocn|kddi|softbank|bbtec|so-net|sony network|biglobe|nifty|jcom|jupiter|nuro|iij|plala|asahi-net|wakwak|optage|commufa|ctc|pikara|qtnet|vectant|ucom|arteria)`)
	hostingKW        = regexp.MustCompile(`(?i)(vpn|proxy|softether|hosting|datacenter|data.?center|cloud|vps|colocation|digitalocean|ovh|hetzner|vultr|linode|amazon|aws|google|microsoft|azure|oracle|alibaba|tencent|server)`)
)

type Candidate struct {
	IP         string `json:"ip"`
	Country    string `json:"country"`
	Score      int    `json:"score"`
	Ping       int    `json:"ping"`
	Speed      int64  `json:"speed"`
	OVPNConfig string `json:"-"`
	ISP        string `json:"isp"`
	Org        string `json:"org"`
	ASN        string `json:"asn"`
	Proxy      bool   `json:"proxy"`
	Hosting    bool   `json:"hosting"`
	Mobile     bool   `json:"mobile"`
	ResISP     bool   `json:"res_isp"`
	Official   bool   `json:"official"`
	Clean      int    `json:"clean"`
	Tier       int    `json:"tier"` // 0=干净住宅, 1=中等非官方, 2=官方兜底
	Penalty    int    `json:"penalty"`
}

func isOVPNTCP(cfg string) bool {
	for _, ln := range strings.Split(cfg, "\n") {
		t := strings.ToLower(strings.TrimSpace(ln))
		if strings.HasPrefix(t, "proto ") {
			f := strings.Fields(t)
			return len(f) >= 2 && strings.HasPrefix(f[1], "tcp")
		}
	}
	return true
}

func decodeOVPN(b64 string) (string, bool) {
	clean := strings.Map(func(r rune) rune {
		if r == ' ' || r == '\n' || r == '\r' || r == '\t' {
			return -1
		}
		return r
	}, b64)
	b, err := base64.StdEncoding.DecodeString(clean)
	if err != nil {
		return "", false
	}
	cfg := string(b)
	if !strings.Contains(cfg, "<ca>") || !isOVPNTCP(cfg) {
		return "", false
	}
	return cfg, true
}

func batchLookupIPAPI(ctx context.Context, ips []string) map[string]store.IPQuality {
	out := make(map[string]store.IPQuality, len(ips))
	client := netutil.NewBoundHTTPClient("", 20*time.Second, false)
	for i := 0; i < len(ips); i += 100 {
		end := i + 100
		if end > len(ips) {
			end = len(ips)
		}
		chunk := ips[i:end]
		type qReq struct {
			Query string `json:"query"`
		}
		reqs := make([]qReq, len(chunk))
		for j, ip := range chunk {
			reqs[j] = qReq{Query: ip}
		}
		payload, _ := json.Marshal(reqs)
		req, err := http.NewRequestWithContext(ctx, http.MethodPost,
			"http://ip-api.com/batch?fields=query,countryCode,isp,org,as,type,mobile,proxy,hosting",
			bytes.NewReader(payload))
		if err != nil {
			continue
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("User-Agent", "vpngate-relayd/2.0")
		resp, err := client.Do(req)
		if err != nil {
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			continue
		}
		var rows []struct {
			Query       string `json:"query"`
			CountryCode string `json:"countryCode"`
			ISP         string `json:"isp"`
			Org         string `json:"org"`
			AS          string `json:"as"`
			Proxy       bool   `json:"proxy"`
			Hosting     bool   `json:"hosting"`
			Mobile      bool   `json:"mobile"`
		}
		if err := json.Unmarshal(body, &rows); err != nil {
			continue
		}
		now := time.Now()
		for _, r := range rows {
			if r.Query == "" {
				continue
			}
			out[r.Query] = store.IPQuality{
				IP:        r.Query,
				Country:   strings.ToUpper(r.CountryCode),
				ISP:       r.ISP,
				Org:       r.Org,
				AS:        r.AS,
				ASN:       netutil.ExtractASN(r.AS),
				Proxy:     r.Proxy,
				Hosting:   r.Hosting,
				Mobile:    r.Mobile,
				UpdatedAt: now,
			}
		}
	}
	return out
}

func ScoreOne(r store.RawServer, cfg string, q *store.IPQuality, fb store.NodeFeedback) Candidate {
	isOff := strings.HasPrefix(r.IP, OfficialPrefix)
	c := Candidate{
		IP:         r.IP,
		Country:    r.CountryShort,
		Score:      r.Score,
		Ping:       r.Ping,
		Speed:      r.Speed,
		OVPNConfig: cfg,
		Official:   isOff,
	}
	risk := 30 // 无画像时给中性偏保守值
	if q != nil {
		c.ISP = q.ISP
		c.Org = q.Org
		c.ASN = q.ASN
		c.Proxy = q.Proxy
		c.Hosting = q.Hosting
		c.Mobile = q.Mobile
		orgText := strings.Join([]string{q.ISP, q.Org, q.AS}, " ")
		c.ResISP = jpResidentialISP.MatchString(orgText)
		isHostKW := hostingKW.MatchString(orgText)

		risk = 0
		if q.Proxy {
			risk += 45
		}
		if q.Hosting {
			risk += 25
		}
		if isHostKW {
			risk += 15
		}
		if q.Mobile {
			risk -= 20
		}
		if c.ResISP && !q.Proxy && !q.Hosting {
			risk -= 10
		}
	}
	if isOff {
		if risk < 85 {
			risk = 85
		}
	}
	// 历史运行反馈惩罚
	c.Penalty = fb.AuthFails*15 + fb.NonJPCount*25 + fb.ZombieCount*10 + fb.ConnFails*5
	risk += c.Penalty
	if risk < 0 {
		risk = 0
	}
	if risk > 100 {
		risk = 100
	}
	c.Clean = 100 - risk

	if !isOff && q != nil && !q.Proxy && !q.Hosting && c.Clean >= 80 {
		c.Tier = 0
	} else if !isOff {
		c.Tier = 1
	} else {
		c.Tier = 2
	}
	return c
}

// Rank 筛选目标国 TCP 节点、增量补齐本机 ip-api 24h 缓存，按纯净住宅优先排序，末尾保留官方集群兜底
func Rank(ctx context.Context, raw []store.RawServer, targetCC string, minSpeedMbps float64, st *store.Store) []Candidate {
	minSpeed := int64(minSpeedMbps * 1e6)
	filterPass := func(floor int64) []struct {
		r   store.RawServer
		cfg string
	} {
		seen := make(map[string]bool)
		var out []struct {
			r   store.RawServer
			cfg string
		}
		for _, r := range raw {
			if targetCC != "" && !strings.EqualFold(r.CountryShort, targetCC) {
				continue
			}
			if r.IP == "" || seen[r.IP] || r.Speed < floor {
				continue
			}
			cfg, ok := decodeOVPN(r.ConfigBase64)
			if !ok {
				continue
			}
			seen[r.IP] = true
			out = append(out, struct {
				r   store.RawServer
				cfg string
			}{r: r, cfg: cfg})
		}
		return out
	}

	valid := filterPass(minSpeed)
	if len(valid) < 5 && minSpeed > 1_000_000 {
		valid = filterPass(1_000_000)
	}

	var needLookup []string
	qmap := make(map[string]store.IPQuality, len(valid))
	for _, v := range valid {
		if st != nil {
			if q, ok := st.GetIPQuality(v.r.IP, IPCacheTTL); ok {
				qmap[v.r.IP] = q
				continue
			}
		}
		needLookup = append(needLookup, v.r.IP)
	}
	if len(needLookup) > 0 {
		fetched := batchLookupIPAPI(ctx, needLookup)
		if st != nil && len(fetched) > 0 {
			st.PutIPQualities(fetched)
		}
		for ip, q := range fetched {
			qmap[ip] = q
		}
	}

	var all []Candidate
	for _, v := range valid {
		var qp *store.IPQuality
		if q, ok := qmap[v.r.IP]; ok {
			qq := q
			qp = &qq
		}
		var fb store.NodeFeedback
		if st != nil {
			fb = st.GetFeedback(v.r.IP)
		}
		all = append(all, ScoreOne(v.r, v.cfg, qp, fb))
	}

	sort.SliceStable(all, func(i, j int) bool {
		a, b := all[i], all[j]
		if a.Tier != b.Tier {
			return a.Tier < b.Tier
		}
		if a.Clean != b.Clean {
			return a.Clean > b.Clean
		}
		if a.ResISP != b.ResISP {
			return a.ResISP
		}
		if a.Score != b.Score {
			return a.Score > b.Score
		}
		return a.Speed > b.Speed
	})

	// 过滤掉冷却中的节点，末尾保留 2 个官方集群兜底
	var clean, mid, official []Candidate
	for _, c := range all {
		if st != nil && st.IsCoolingDown(c.IP) {
			continue
		}
		switch c.Tier {
		case 0:
			clean = append(clean, c)
		case 1:
			mid = append(mid, c)
		default:
			official = append(official, c)
		}
	}
	if len(official) > 2 {
		official = official[:2]
	}
	result := append(append(clean, mid...), official...)
	if len(result) == 0 {
		// 全部在冷却期时放宽回全集，避免彻底无节点可用
		return all
	}
	return result
}
