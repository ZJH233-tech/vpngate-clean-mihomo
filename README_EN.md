# VPNGate Clean Mihomo · Cleanliness-Aware VPNGate Live Picker & Subscription

<p align="center">
  <a href="README.md">简体中文</a> · <b>English</b> · <a href="README_FA.md">فارسی</a>
</p>

A **single-file Cloudflare Worker** that fetches the public OpenVPN relay list of
[VPN Gate](https://www.vpngate.net) (the University of Tsukuba's academic experiment) in real time,
**automatically scores the cleanliness / fraud risk of every exit IP**, lets you browse and filter them
on a web page, and emits a ready-to-subscribe
[Mihomo (Clash.Meta core)](https://wiki.metacubex.one/config/proxies/openvpn/) config — including a
"clean-only" subscription that keeps residential IPs and drops known proxies / datacenters.

An **optional VPS relay layer** (`vps-relay/`) is also provided: on your own server it uses OpenVPN plus
policy routing to egress traffic through an automatically picked low-risk residential IP, with a watchdog
failover, while the client-side entry point never changes.

> Derived from [RememberOurPromise/OpenVPNGate4Mihomo](https://github.com/RememberOurPromise/OpenVPNGate4Mihomo)
> (The Unlicense / public domain). On top of its "browse + single-node conversion", this project adds
> **IP risk profiling, a clean-only subscription, a full subscribable config, and a VPS auto-picker**.
> See [Acknowledgments](#-acknowledgments).

---

## 🖼️ Demo

**① Node browser — sort by cleanliness: clean residential / carrier IPs (green 100) float to the top, known proxies / datacenters are flagged red:**

![Node browser sorted by cleanliness](assets/01-clean-dashboard.png)

**② Convert any node into a Mihomo (Clash.Meta) proxy segment with one click — copy or download:**

![One-click OpenVPN to Mihomo conversion](assets/02-mihomo-convert.png)

**③ `/sub?clean=1` returns a complete subscribable config — every node is annotated with its cleanliness score, with a built-in `url-test` auto group (certificate blocks collapsed below):**

![Full Mihomo config returned by the clean subscription](assets/03-clean-subscription.png)

---

## ✨ Features

### Added on top of the original project
- 🧼 **IP cleanliness / fraud-risk engine**: combines multiple signals per IP — `known-proxy flag,
  hosting/datacenter flag, mobile flag, ASN & ISP keywords, business type` — into a `0–100` risk score and
  a cleanliness score `clean = 100 - risk`, grouped into **Clean / Medium / High-risk**.
- 🎯 **Clean-only mode**: `clean=1` keeps only IPs that are *not* known proxies, *not* datacenters and have
  `clean ≥ 50` (i.e. residential / carrier IPs); `maxrisk` sets a hard risk ceiling.
- 🔁 **A full config you can subscribe to directly**: `/sub` emits `proxies + proxy-groups (url-test
  auto-speedtest) + rules`. Just paste one URL into your client — no manual stitching. The original project
  only converted a single node in the browser.
- 🛡️ **Hardened output**: all free-text coming from third parties (ISP names, host names, custom group
  names, etc.) is stripped of control characters to prevent YAML line-break injection.
- 🖥️ **Optional VPS relay layer**: a fixed client entry, background auto-picking of clean exits, watchdog
  failover, and an IPv6 blackhole against leaks. Supports up to 10 per-country exit slots with **sticky
  slot→country pinning** (no reshuffling on every upstream refresh) and acceptance by the **measured exit
  country**; the subscription is generated centrally so refreshing it never shuffles flags.
- 🧰 **Ops toolkit**: a full 10-step deployment guide, an end-to-end dial-test engine, and a Telegram
  control bot (under `docs/` and `extras/`).
- ⚡ Quality profiles are cached in memory for 6 hours and the node list at the edge; if the quality
  provider fails it degrades gracefully and **never blocks node output**. No KV / D1 / env vars required.

### Inherited from the original project
- Node browser: country flags, host/IP, score, colored ping dot, speed bar, sessions, uptime, cumulative
  users/traffic, operator, message, TCP/UDP badges; search / country filter / sorting / sticky header /
  card layout on narrow screens.
- Single-node OpenVPN ↔ Mihomo conversion with compatibility warnings for edge values (tap devices, rare
  ciphers, tls-auth, …); bulk export supported.

---

## 🧩 How it works

```
                         ┌──────────────────────────── Cloudflare Worker ───────────────────────────┐
 VPNGate public CSV ────▶│ parse nodes → extract exit IP → IP quality profile (ip-api batch, 6h cache)│
 (www.vpngate.net/api)   │                          → score clean/risk                               │
                         │   GET /            node browser (table with a "Cleanliness" column)       │
                         │   GET /api/servers all nodes as JSON (with a quality field)              │
                         │   GET /sub         filter/sort by params → full Mihomo subscription      │
                         └──────────────────────────────────────────────────────────────────────────┘
                                                              │ clean subscription URL
                                                              ▼
                                              Mihomo / ClashMeta client auto speed-tests
 (Optional) VPS relay layer vps-relay/:
   client ─fixed entry (VLESS/Reality, …)─▶ your VPS ─fwmark policy routing─▶ OpenVPN (tun0/vpnm1-10) ─▶ clean residential IP ─▶ Internet
                                                    ▲
     bestip/multi_refresh refresh pools on a timer (sticky country pinning, official cluster as fallback);
     watchdog accepts by measured exit country / fraud score and rotates when needed;
     sync_subscriptions builds the s-ui subscription centrally (pinned flag emoji, measured-country names, fixed order)
```

### Cleanliness scoring rules (`assessQuality`)

| Signal | Risk weight |
|---|---|
| Known public proxy (`proxy=true`) | +45 |
| Hosting / datacenter (`hosting=true`) | +25 |
| Business type `business` | +6 |
| ISP/ORG/AS matches hosting/VPN keywords | +15 |
| Mobile network (`mobile=true`) | −20 (closer to a real residential line) |
| Plain residential ISP (not proxy, not hosting) | risk capped at 10 |

- `clean = 100 - risk`; `clean ≥ 80` clean / `50–79` medium / `< 50` high-risk.
- Data comes from the free [ip-api.com](http://ip-api.com) batch endpoint (http only, 15 req/min,
  ≤100 IPs per call; the Worker batches and caches requests).
- VPNGate's **official Japan cluster (219.100.37.x, the SoftEther research ASN) is almost entirely
  `proxy=true`** — extremely fast but a well-known public proxy that risk-control sites flag easily. The
  handful of community-run residential / carrier IPs are usually "cleaner" but vary in speed and uptime.
  This project balances the two with **clean-first + official fallback**.

---

## 🚀 Quick start (Worker only, 5 minutes)

### Option A: paste in the dashboard (zero dependencies)
1. Open the [Cloudflare Dashboard](https://dash.cloudflare.com/) → Workers & Pages → Create a Worker.
2. Paste the entire contents of [`worker/worker.js`](worker/worker.js) into the online editor and deploy.
3. Open the assigned `*.workers.dev` URL — the node browser appears.

### Option B: Wrangler CLI
```bash
cd worker
cp wrangler.toml.example wrangler.toml   # optionally change name / custom domain
npm i -g wrangler && wrangler login
wrangler deploy
```

### Get your subscription URL (Mihomo / ClashMeta core)
```text
# Speed-first (default; cleanliness/risk annotated per node in comments)
https://your-domain/sub
# Clean-first: Japan only, clean residential IPs, sorted by cleanliness (recommended) ⭐
https://your-domain/sub?clean=1&cc=JP&sort=clean
```
Add that URL as a subscription in any Mihomo-core client (Mihomo Party, Clash Verge Rev, FlClash,
NekoBox, …).

---

## 🔌 Subscription parameters (`/sub`; aliases `/mihomo` `/clash` `/subscribe`)

| Param | Values | Default | Description |
|---|---|---|---|
| `n` | 1–30 | 8 | Number of nodes |
| `cc` | 2-letter country code | worldwide | e.g. `JP` / `KR` / `US` |
| `proto` | `tcp`/`udp`/`any` | `tcp` | OpenVPN transport; tcp is more stable |
| `sort` | `score`/`speed`/`ping`/`clean` | `score` | Sort order; `clean` = cleanest first |
| `min` | Mbps | 3 | Minimum speed threshold |
| `clean` | `1` | off | Clean mode: not-proxy/not-hosting and clean ≥ 50 |
| `maxrisk` | 0–100 | unlimited | Risk-score ceiling, e.g. `maxrisk=30` |
| `name` | string | VPNGate | Selector group name |
| `refresh` | `1` | – | Bypass cache and re-fetch upstream |

Example — Japan only, 10 nodes, risk ≤ 30, cleanest first:
```text
https://your-domain/sub?cc=JP&n=10&maxrisk=30&sort=clean
```

### API
- `GET /api/servers`: all nodes as JSON; each node carries
  `quality: {clean,risk,grade,proxy,hosting,mobile,isp,...}`; supports `?refresh=1`.

---

## 🖥️ (Optional) VPS relay layer: fixed entry + automatic clean-exit picking

For when you want the client config to never change while the server silently picks and switches
low-risk exits in the background. See the **[full deployment guide: docs/deploy-vps.md](docs/deploy-vps.md)**
and [`vps-relay/README.md`](vps-relay/README.md). It will:
- periodically pull the quality-annotated node list from your Worker and build a **clean-first**
  candidate pool, keeping the official cluster at the tail as a fallback (never fully disconnect);
- bring up an OpenVPN `tun` tunnel plus `fwmark` policy routing so only designated inbound traffic uses
  the tunnel and other services are untouched;
- run watchdog health probes (75 s / 120 s) and switch smoothly only after **2 consecutive failures**
  (600 s per-slot cooldown), avoiding periodic drops caused by leaderboard churn;
- run up to 10 additional per-country tunnels (separate fwmark / routing table / tun device each) with
  **sticky slot→country pinning**; the watchdog accepts a slot only when the **measured exit country**
  matches its pin (VPNGate has chained relays whose entry country ≠ exit country) and rotates otherwise;
- keep subscriptions stable via `sync_subscriptions.py`: main nodes get a pinned flag emoji (GeoIP
  databases disagree on server location, which otherwise makes flags drift), slots are named after
  their measured exit country, and the merged subscription always uses one fixed order;
- apply an IPv6 blackhole, loose rp_filter, and a main-line leak check (the tunnel is unhealthy if its
  exit equals the primary NIC IP);
- optionally add a **Telegram control bot** (`extras/tgbot/`) and an **end-to-end dial-test engine**
  (`extras/dialtest.py`).

---

## 📁 Repository layout

```
.
├── worker/
│   ├── worker.js                 # Single-file Worker (browser + API + subscription + quality engine)
│   └── wrangler.toml.example
├── vps-relay/                    # Optional: VPS auto-picker + fixed-entry relay layer
│   ├── bestip_refresh.py/.sh     # JP-slot clean picker (Worker first, official CSV fallback)
│   ├── build_running.sh          # Builds running.ovpn from candidates (self-bootstraps if missing)
│   ├── ovpn-up.sh / ovpn-down.sh # Policy routing up/down (v4 + v6)
│   ├── watchdog.sh / pick_strict.sh   # JP-slot health check / strict pick-and-switch
│   ├── multi_refresh.py          # Multi-country slot refresh with sticky slot→country pinning
│   ├── multi_build.sh / multi_watchdog.sh  # Per-slot assembly / measured-exit watchdog
│   ├── gen_sbexit.py / gen_keys.sh        # Exit-instance config / Reality keys & certs
│   ├── sync_subscriptions.py     # Single source of truth for s-ui subs: pinned flags, measured names, fixed order
│   ├── merge_subs.py / crontab.example    # cron fallback wrapper
│   ├── vpngate.env.example       # Runtime config template (country / clean mode / Worker URL)
│   ├── nftables.conf             # Firewall template
│   ├── systemd/  sysctl/         # service/timer units / rp_filter example
├── examples/                     # Server-side config templates (placeholders)
├── extras/
│   ├── dialtest.py               # End-to-end real dial-test engine (params via env vars)
│   └── tgbot/                    # Telegram control bot (hard owner allowlist)
├── docs/deploy-vps.md            # Full 10-step VPS deployment guide + pitfalls
├── assets/                       # README screenshots
├── LICENSE
└── README.md / README_EN.md / README_FA.md
```

---

## 🙏 Acknowledgments
- **[RememberOurPromise/OpenVPNGate4Mihomo](https://github.com/RememberOurPromise/OpenVPNGate4Mihomo)**
  — the starting point of this project. Its Worker fetch/parse logic, the OpenVPN→Mihomo field mapping
  and the node-browser front end are reused and extended here (released under The Unlicense / public
  domain).
- **[VPN Gate Academic Experiment](https://www.vpngate.net)**, a public-benefit academic VPN project of
  the University of Tsukuba, Japan, with relays provided by volunteers worldwide.
- **[Mihomo (Clash.Meta)](https://github.com/MetaCubeX/mihomo)** and its
  [OpenVPN configuration docs](https://wiki.metacubex.one/config/proxies/openvpn/).
- IP profiling data from [ip-api.com](https://ip-api.com).

## 👥 Authors
Designed, built and debugged jointly by [**@ZJH233-tech**](https://github.com/ZJH233-tech) and
**Doubao AI**.

## ⚠️ Disclaimer
- VPN Gate is an academic experiment; public nodes are provided by third-party volunteers and their
  **availability, speed and cleanliness fluctuate constantly**. Use at your own risk.
- The "cleanliness / risk score" is a heuristic based only on publicly observable IP-type signals and
  **does not guarantee a node's safety, legality or anonymity**. Even a clean-scored relay should **not**
  be used to log into payment, banking or other sensitive accounts.
- Please comply with the laws of your country/region and of the exit node's location. This project is for
  learning and network research only.
- This project has no official affiliation with VPN Gate, Cloudflare, ip-api or Mihomo.

## 📄 License
[MIT](./LICENSE). Derived from an Unlicense (public-domain) project; the derivative work is released
under MIT.
