package watchdog

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/collector"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/config"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/gateway"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/netutil"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/scorer"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/store"
	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/tunnel"
)

const (
	PrimaryProbeURL = "https://cp.cloudflare.com/generate_204"
	BackupProbeURL  = "https://www.gstatic.com/generate_204"
)

type SwitchEvent struct {
	At       time.Time `json:"at"`
	FromDev  string    `json:"from_dev"`
	ToDev    string    `json:"to_dev"`
	FromIP   string    `json:"from_ip"`
	ToIP     string    `json:"to_ip"`
	Reason   string    `json:"reason"`
	SwitchMs int64     `json:"switch_ms"`
	WarmCut  bool      `json:"warm_cut"`
}

type Manager struct {
	cfg   *config.Config
	store *store.Store

	mu          sync.RWMutex
	slotA       *tunnel.Slot
	slotB       *tunnel.Slot
	active      *tunnel.Slot
	standby     *tunnel.Slot
	mainEgress  netutil.MainEgress
	candidates  []scorer.Candidate
	lastFetchAt time.Time
	lastSource  string
	switches    []SwitchEvent

	activeProbeMu     sync.Mutex
	activeProbeDev    string
	activeProbeClient *http.Client

	standbyWake chan struct{}
	authPath    string
}

func New(cfg *config.Config, st *store.Store) *Manager {
	slotA := tunnel.NewSlot(cfg.SlotA.Name, cfg.SlotA.Table)
	slotB := tunnel.NewSlot(cfg.SlotB.Name, cfg.SlotB.Table)
	return &Manager{
		cfg:         cfg,
		store:       st,
		slotA:       slotA,
		slotB:       slotB,
		active:      slotA,
		standby:     slotB,
		standbyWake: make(chan struct{}, 1),
		authPath:    filepath.Join(cfg.WorkDir, "auth.txt"),
	}
}

func (m *Manager) ensureAuthFile() error {
	return os.WriteFile(m.authPath, []byte("vpn\nvpn\n"), 0o600)
}

func (m *Manager) getActiveProbeClient(dev string) *http.Client {
	m.activeProbeMu.Lock()
	defer m.activeProbeMu.Unlock()
	if m.activeProbeClient != nil && m.activeProbeDev == dev {
		return m.activeProbeClient
	}
	netutil.CloseIdle(m.activeProbeClient)
	m.activeProbeDev = dev
	m.activeProbeClient = netutil.NewBoundHTTPClient(dev, time.Duration(m.cfg.ProbeTimeoutSec)*time.Second, true)
	return m.activeProbeClient
}

func (m *Manager) resetActiveProbeClient() {
	m.activeProbeMu.Lock()
	defer m.activeProbeMu.Unlock()
	netutil.CloseIdle(m.activeProbeClient)
	m.activeProbeClient = nil
	m.activeProbeDev = ""
}

func (m *Manager) wakeStandby() {
	select {
	case m.standbyWake <- struct{}{}:
	default:
	}
}

func (m *Manager) RefreshPool(ctx context.Context) error {
	m.mu.RLock()
	prevMain := m.mainEgress
	activeIface := ""
	if m.active != nil && m.active.IsReady() {
		activeIface = m.active.Dev
	}
	m.mu.RUnlock()

	mainEg := netutil.ProbeMainEgress(ctx, prevMain)
	res, err := collector.Fetch(ctx, m.cfg.WorkerURL, m.cfg.WorkerToken, activeIface, m.cfg.BootstrapCSV, m.store)
	if err != nil {
		return err
	}
	ranked := scorer.Rank(ctx, res.Servers, m.cfg.TargetCountry, m.cfg.MinSpeedMbps, m.store)

	m.mu.Lock()
	m.mainEgress = mainEg
	m.candidates = ranked
	m.lastFetchAt = time.Now()
	m.lastSource = res.Source
	m.mu.Unlock()

	slog.Info("candidate pool refreshed",
		"source", res.Source, "raw_total", len(res.Servers),
		"jp_ranked", len(ranked), "main_ip", mainEg.IP, "main_asn", mainEg.ASN)
	return nil
}

func (m *Manager) recordDialFailure(ip string, err error) {
	switch {
	case errors.Is(err, tunnel.ErrAuthFailed):
		m.store.RecordFail(ip, "auth", 30*time.Minute)
	case errors.Is(err, tunnel.ErrNotTargetCC):
		m.store.RecordFail(ip, "non_jp", 2*time.Hour)
	case errors.Is(err, tunnel.ErrDirtyEgress):
		m.store.RecordFail(ip, "dirty", 1*time.Hour)
	case errors.Is(err, tunnel.ErrLeak):
		m.store.RecordFail(ip, "leak", 1*time.Hour)
	default:
		m.store.RecordFail(ip, "conn", 15*time.Minute)
	}
}

// connectSlot 从候选池依次尝试拉起目标槽位（排除 excludeIP，避免双槽撞同一个出口）
func (m *Manager) connectSlot(ctx context.Context, slot *tunnel.Slot, excludeIP string) error {
	m.mu.RLock()
	cands := append([]scorer.Candidate(nil), m.candidates...)
	mainEg := m.mainEgress
	m.mu.RUnlock()

	if len(cands) == 0 {
		return errors.New("no candidates available")
	}

	// 先过滤冷却与排重 IP，最多尝试 8 个候选
	var tryList []scorer.Candidate
	for _, c := range cands {
		if c.IP == excludeIP || m.store.IsCoolingDown(c.IP) {
			continue
		}
		tryList = append(tryList, c)
		if len(tryList) >= 8 {
			break
		}
	}
	if len(tryList) == 0 {
		for _, c := range cands {
			if c.IP != excludeIP {
				tryList = append(tryList, c)
				if len(tryList) >= 4 {
					break
				}
			}
		}
	}

	for _, cand := range tryList {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		requireClean := cand.Tier == 0
		slog.Info("dialing candidate", "dev", slot.Dev, "ip", cand.IP, "tier", cand.Tier, "clean", cand.Clean, "isp", cand.ISP)
		_, err := slot.Start(ctx, cand, m.cfg.WorkDir, m.authPath, m.cfg.TargetCountry, mainEg, requireClean)
		if err == nil {
			m.store.RecordSuccess(cand.IP)
			return nil
		}
		slog.Warn("candidate rejected", "dev", slot.Dev, "ip", cand.IP, "err", err)
		m.recordDialFailure(cand.IP, err)
	}
	return errors.New("all tried candidates failed")
}

// Failover 故障秒切（零冷却！若 Standby 就绪则 <10ms 原子切换路由表）
func (m *Manager) Failover(ctx context.Context, reason string, markZombie bool) error {
	start := time.Now()

	m.mu.Lock()
	oldActive := m.active
	standby := m.standby
	oldIP := oldActive.EntryIP()
	standbyReady := standby != nil && standby.IsReady()
	if standbyReady {
		if _, ok := netutil.IfaceIPv4(standby.Dev); !ok {
			standbyReady = false
		}
	}

	if standbyReady {
		if err := netutil.SwitchActiveRoute(standby.Dev, m.cfg.TrafficTable); err != nil {
			m.mu.Unlock()
			return fmt.Errorf("atomic route switch failed: %w", err)
		}
		m.active = standby
		m.standby = oldActive
		newIP := standby.EntryIP()
		ev := SwitchEvent{
			At:       time.Now(),
			FromDev:  oldActive.Dev,
			ToDev:    standby.Dev,
			FromIP:   oldIP,
			ToIP:     newIP,
			Reason:   reason,
			SwitchMs: time.Since(start).Milliseconds(),
			WarmCut:  true,
		}
		m.appendSwitchLocked(ev)
		m.mu.Unlock()

		m.resetActiveProbeClient()
		if markZombie && oldIP != "" {
			m.store.RecordFail(oldIP, "zombie", 20*time.Minute)
		}
		slog.Warn("WARM FAILOVER COMPLETED",
			"from", oldActive.Dev, "to", standby.Dev,
			"from_ip", oldIP, "to_ip", newIP,
			"reason", reason, "switch_ms", ev.SwitchMs)

		// 异步停止已失效旧槽并立即补建新热备
		go func() {
			oldActive.Stop()
			m.wakeStandby()
		}()
		return nil
	}
	m.mu.Unlock()

	// 极端情况：Standby 未就绪，先 fail-closed 清空 table 100 防泄漏，再立刻冷恢复
	slog.Error("standby not ready during failover; clearing table 100 (fail-closed) and cold recovering", "reason", reason)
	_ = netutil.ClearActiveRoute(m.cfg.TrafficTable)
	m.resetActiveProbeClient()
	if markZombie && oldIP != "" {
		m.store.RecordFail(oldIP, "zombie", 20*time.Minute)
	}
	oldActive.Stop()

	if err := m.connectSlot(ctx, oldActive, ""); err != nil {
		// 刷新一次名单再试（此时双隧道全死，只会走 Worker 或本地缓存）
		_ = m.RefreshPool(ctx)
		if err2 := m.connectSlot(ctx, oldActive, ""); err2 != nil {
			return fmt.Errorf("cold recovery failed: %w", err2)
		}
	}
	if err := netutil.SwitchActiveRoute(oldActive.Dev, m.cfg.TrafficTable); err != nil {
		return err
	}
	ev := SwitchEvent{
		At:       time.Now(),
		FromDev:  oldActive.Dev,
		ToDev:    oldActive.Dev,
		FromIP:   oldIP,
		ToIP:     oldActive.EntryIP(),
		Reason:   reason + " (cold)",
		SwitchMs: time.Since(start).Milliseconds(),
		WarmCut:  false,
	}
	m.mu.Lock()
	m.appendSwitchLocked(ev)
	m.mu.Unlock()
	m.wakeStandby()
	return nil
}

func (m *Manager) appendSwitchLocked(ev SwitchEvent) {
	m.switches = append(m.switches, ev)
	if len(m.switches) > 30 {
		m.switches = m.switches[len(m.switches)-30:]
	}
}

// probeActiveOnce 高频 Keep-Alive 探活 + 500ms 备用源快速复测（总判定 6-8s）
func (m *Manager) probeActiveOnce(ctx context.Context) (bool, string) {
	m.mu.RLock()
	active := m.active
	m.mu.RUnlock()

	if active == nil || !active.IsReady() {
		return false, "active_not_ready"
	}
	if _, ok := netutil.IfaceIPv4(active.Dev); !ok {
		return false, "iface_down"
	}

	// 第 1 次：Keep-Alive 复用连接探主源（超时 ProbeTimeoutSec=4s）
	c1 := m.getActiveProbeClient(active.Dev)
	ctx1, cancel1 := context.WithTimeout(ctx, time.Duration(m.cfg.ProbeTimeoutSec)*time.Second)
	code1, lat1, err1 := netutil.Probe204(ctx1, c1, PrimaryProbeURL)
	cancel1()
	if err1 == nil && code1 == 204 {
		active.RecordProbe(true, lat1, "")
		return true, ""
	}

	// 首次失败：清掉可能半开的 Keep-Alive 连接，短暂等待后用新连接探备用 204 源
	slog.Warn("active primary probe failed, fast-confirming on backup 204",
		"dev", active.Dev, "code", code1, "err", err1)
	netutil.CloseIdle(c1)

	select {
	case <-time.After(time.Duration(m.cfg.ConfirmDelayMs) * time.Millisecond):
	case <-ctx.Done():
		return true, ""
	}

	c2 := netutil.NewBoundHTTPClient(active.Dev, time.Duration(m.cfg.ConfirmTimeoutSec)*time.Second, false)
	ctx2, cancel2 := context.WithTimeout(ctx, time.Duration(m.cfg.ConfirmTimeoutSec)*time.Second)
	code2, lat2, err2 := netutil.Probe204(ctx2, c2, BackupProbeURL)
	cancel2()
	if err2 == nil && code2 == 204 {
		active.RecordProbe(true, lat2, "")
		return true, ""
	}

	reason := fmt.Sprintf("double_probe_failed(p1=%d/%v, p2=%d/%v)", code1, err1, code2, err2)
	active.RecordProbe(false, 0, reason)
	return false, reason
}

func (m *Manager) standbyWorker(ctx context.Context) {
	ticker := time.NewTicker(time.Duration(m.cfg.StandbyCheckSec) * time.Second)
	defer ticker.Stop()

	checkAndBuild := func() {
		m.mu.RLock()
		standby := m.standby
		active := m.active
		activeIP := ""
		if active != nil {
			activeIP = active.EntryIP()
		}
		m.mu.RUnlock()

		if standby == nil {
			return
		}
		if standby.IsReady() {
			if _, ok := netutil.IfaceIPv4(standby.Dev); ok {
				c := netutil.NewBoundHTTPClient(standby.Dev, 4*time.Second, false)
				pCtx, cancel := context.WithTimeout(ctx, 4*time.Second)
				code, lat, err := netutil.Probe204(pCtx, c, PrimaryProbeURL)
				cancel()
				if err == nil && code == 204 {
					standby.RecordProbe(true, lat, "")
					return
				}
				slog.Warn("standby unhealthy, rebuilding", "dev", standby.Dev, "code", code, "err", err)
				if ip := standby.EntryIP(); ip != "" {
					m.store.RecordFail(ip, "zombie", 20*time.Minute)
				}
			}
		}

		if err := m.connectSlot(ctx, standby, activeIP); err != nil {
			slog.Warn("standby build failed, will retry next cycle", "dev", standby.Dev, "err", err)
		}
	}

	for {
		select {
		case <-ctx.Done():
			return
		case <-m.standbyWake:
			checkAndBuild()
		case <-ticker.C:
			checkAndBuild()
		}
	}
}

func (m *Manager) verifyActivePurity(ctx context.Context) {
	m.mu.RLock()
	active := m.active
	mainEg := m.mainEgress
	m.mu.RUnlock()

	if active == nil || !active.IsReady() {
		return
	}
	vCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()

	eip, err := netutil.ProbeEgressIP(vCtx, active.Dev, 5*time.Second)
	if err != nil {
		return
	}
	info, err := netutil.LookupIPInfo(vCtx, eip)
	if err != nil {
		return
	}
	if bad, reason := netutil.IsLeakOrInvalid(eip, info.CountryCode, info.ASN, m.cfg.TargetCountry, mainEg); bad {
		slog.Warn("active egress failed periodic leak/country check", "dev", active.Dev, "egress", eip, "reason", reason)
		_ = m.Failover(ctx, "purity_leak:"+reason, true)
	}
}

func (m *Manager) ActiveSummary() gateway.StatusSummary {
	m.mu.RLock()
	active := m.active
	m.mu.RUnlock()
	if active == nil {
		return gateway.StatusSummary{}
	}
	snap := active.Snapshot()
	out := gateway.StatusSummary{
		ActiveDev: snap.Dev,
		LatencyMs: snap.LastProbeMs,
	}
	if snap.Egress != nil {
		out.EgressIP = snap.Egress.IP
		out.EgressISP = snap.Egress.ISP
	}
	return out
}

const dashboardHTML = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VPNGate Relay v2 · Tailscale 监控台</title>
<style>
body{background:#0b0f17;color:#e5edf7;font-family:system-ui,-apple-system,sans-serif;max-width:920px;margin:24px auto;padding:0 16px}
h1{font-size:20px;margin:0 0 12px;color:#2dd4bf}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
button{background:#1b2434;color:#e5edf7;border:1px solid #2b3952;padding:8px 14px;border-radius:8px;cursor:pointer}
button:hover{border-color:#2dd4bf}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.card{background:#131a26;border:1px solid #263145;border-radius:10px;padding:14px}
.card.active{border-color:#2dd4bf}
.tag{font-size:12px;padding:2px 8px;border-radius:99px;background:#1b2434;color:#93c5fd}
.ok{color:#4ade80}.bad{color:#f87171}
pre{background:#131a26;border:1px solid #263145;border-radius:10px;padding:12px;overflow:auto;font-size:12px}
</style></head><body>
<h1>VPNGate Relay v2 · 雅加达承载 → 日本住宅出口</h1>
<div class="bar">
  <button onclick="load()">↻ 刷新</button>
  <button onclick="act('/switch')">⚡ 手动热切 Active↔Standby</button>
  <button onclick="act('/refresh')">📥 立即刷新候选池</button>
  <span id="meta" style="align-self:center;color:#94a3b8;font-size:13px"></span>
</div>
<div class="grid" id="slots"></div>
<pre id="switches">loading...</pre>
<script>
async function act(p){await fetch(p,{method:'POST'}); setTimeout(load,600);}
function card(s,role){
  const eg=s.egress||{};
  const cls=s.state==='ready'?'ok':'bad';
  return '<div class="card '+(role==='ACTIVE'?'active':'')+'">'+
    '<div><b>'+s.dev+'</b> <span class="tag">'+role+'</span> <span class="'+cls+'">● '+s.state+'</span></div>'+
    '<p>出口: <b>'+(eg.ip||'-')+'</b> ('+(eg.country||'-')+' / '+(eg.asn||'-')+')</p>'+
    '<p>ISP: '+(eg.isp||'-')+' | proxy='+eg.proxy+' hosting='+eg.hosting+'</p>'+
    '<p>探针: '+(s.last_probe_ms||0)+' ms | PID: '+(s.pid||'-')+'</p></div>';
}
async function load(){
  const d=await (await fetch('/status')).json();
  document.getElementById('meta').textContent='源: '+d.last_source+' | 候选池: '+d.pool_count+' | 主NAT: '+(d.main_egress.ip||'-')+' ('+(d.main_egress.asn||'-')+')';
  document.getElementById('slots').innerHTML=
    card(d.slot_a, d.active_dev===d.slot_a.dev?'ACTIVE':'STANDBY')+
    card(d.slot_b, d.active_dev===d.slot_b.dev?'ACTIVE':'STANDBY');
  document.getElementById('switches').textContent='最近切换记录:
'+JSON.stringify((d.switches||[]).slice(-8).reverse(),null,2);
}
load(); setInterval(load, 5000);
</script></body></html>`

func (m *Manager) StatusSnapshot() map[string]any {
	m.mu.RLock()
	defer m.mu.RUnlock()
	topN := len(m.candidates)
	if topN > 10 {
		topN = 10
	}
	activeDev := ""
	standbyDev := ""
	if m.active != nil {
		activeDev = m.active.Dev
	}
	if m.standby != nil {
		standbyDev = m.standby.Dev
	}
	return map[string]any{
		"active_dev":    activeDev,
		"standby_dev":   standbyDev,
		"slot_a":        m.slotA.Snapshot(),
		"slot_b":        m.slotB.Snapshot(),
		"main_egress":   m.mainEgress,
		"last_fetch_at": m.lastFetchAt,
		"last_source":   m.lastSource,
		"pool_count":    len(m.candidates),
		"pool_top":      m.candidates[:topN],
		"switches":      m.switches,
	}
}

func (m *Manager) startControlAPI(ctx context.Context) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write([]byte(dashboardHTML))
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("GET /status", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		enc := json.NewEncoder(w)
		enc.SetIndent("", "  ")
		_ = enc.Encode(m.StatusSnapshot())
	})
	mux.HandleFunc("POST /switch", func(w http.ResponseWriter, r *http.Request) {
		if err := m.Failover(ctx, "manual_api_switch", false); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_ = json.NewEncoder(w).Encode(m.StatusSnapshot())
	})
	mux.HandleFunc("POST /refresh", func(w http.ResponseWriter, r *http.Request) {
		if err := m.RefreshPool(ctx); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "pool_count": len(m.candidates)})
	})

	srv := &http.Server{
		Addr:    m.cfg.ControlListen,
		Handler: mux,
	}
	go func() {
		slog.Info("control API listening", "addr", m.cfg.ControlListen)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("control API error", "err", err)
		}
	}()
	return srv
}

func (m *Manager) Run(ctx context.Context) error {
	if err := m.ensureAuthFile(); err != nil {
		return err
	}
	if err := netutil.EnsureBaseRouting(
		m.cfg.TrafficMark, m.cfg.TrafficTable,
		m.cfg.SlotA.Name, m.cfg.SlotA.Table,
		m.cfg.SlotB.Name, m.cfg.SlotB.Table,
	); err != nil {
		return fmt.Errorf("ensure base routing: %w", err)
	}

	if err := m.RefreshPool(ctx); err != nil {
		return fmt.Errorf("initial pool refresh: %w", err)
	}

	// 启动控制面 API，便于随时观测启动进度
	apiSrv := m.startControlAPI(ctx)
	defer func() {
		shCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = apiSrv.Shutdown(shCtx)
	}()

	// 先建立 Active (vpns-a) 并切换生产表 100
	if err := m.connectSlot(ctx, m.active, ""); err != nil {
		return fmt.Errorf("initial active connect failed: %w", err)
	}
	if err := netutil.SwitchActiveRoute(m.active.Dev, m.cfg.TrafficTable); err != nil {
		return fmt.Errorf("set initial active route: %w", err)
	}
	slog.Info("initial active tunnel online", "dev", m.active.Dev, "ip", m.active.EntryIP())

	// 启动后台 Standby 维护协程并立即拉起 vpns-b 热备
	go m.standbyWorker(ctx)
	m.wakeStandby()

	probeTicker := time.NewTicker(time.Duration(m.cfg.ProbeIntervalSec) * time.Second)
	defer probeTicker.Stop()
	refreshTicker := time.NewTicker(time.Duration(m.cfg.RefreshIntervalSec) * time.Second)
	defer refreshTicker.Stop()
	purityTicker := time.NewTicker(3 * time.Minute)
	defer purityTicker.Stop()

	for {
		m.mu.RLock()
		var deadCh <-chan struct{}
		if m.active != nil && m.active.IsReady() {
			deadCh = m.active.DeadChan()
		}
		m.mu.RUnlock()

		select {
		case <-ctx.Done():
			m.Shutdown()
			return nil
		case <-deadCh:
			slog.Warn("active openvpn process exited — triggering instant failover")
			if err := m.Failover(ctx, "process_exit", true); err != nil {
				slog.Error("failover on process exit failed", "err", err)
			}
		case <-probeTicker.C:
			if ok, reason := m.probeActiveOnce(ctx); !ok {
				if err := m.Failover(ctx, reason, true); err != nil {
					slog.Error("failover on probe failure failed", "err", err)
				}
			}
		case <-purityTicker.C:
			m.verifyActivePurity(ctx)
		case <-refreshTicker.C:
			if err := m.RefreshPool(ctx); err != nil {
				slog.Warn("periodic pool refresh failed", "err", err)
			}
		}
	}
}

func (m *Manager) Shutdown() {
	slog.Info("shutting down vpngate-relayd")
	m.resetActiveProbeClient()
	_ = netutil.ClearActiveRoute(m.cfg.TrafficTable)
	m.slotA.Stop()
	m.slotB.Stop()
}
