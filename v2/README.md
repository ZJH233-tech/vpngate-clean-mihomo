# VPNGate Relay v2（NAT + Argo 专版 · Go 单二进制）

面向无公网入站端口的 NAT/云电脑承载机，通过 Cloudflare Argo 隧道接入、本机双 OpenVPN 热备秒切，稳定输出高纯净日本住宅 IP。

## 架构组件（仅 3 个 systemd 服务）
1. `vpngate-singbox`：`sing-box 1.14+` 数据面（`127.0.0.1:18444` `/ws/jp` 打 `fwmark 354` 走日本住宅；`127.0.0.1:18080` `/ws/direct` 直连备用；拦截 IPv6/内网/Tailnet/云元数据）。
2. `vpngate-relayd`：Go 单二进制控制面 + 网关（`127.0.0.1:18000` 统一承接 Argo WS 反代与 `/sub/<TOKEN>/{uri,mihomo}` 订阅；双隧道 `vpns-a`/`vpns-b` 热备 4～8ms 原子切换；Tailscale 内网监控台 `:19099`）。
3. `vpngate-cloudflared`：Argo 隧道（`/etc/vpngate-relay/cf-tunnel.token` 存在时走正式域名，否则自动退回 TryCloudflare 临时域名）。

## 私有列表拉取 Worker（`worker-proxy/`）
- 仅暴露 `GET /csv` + 强制 `Bearer` 口令校验（`wrangler secret put API_TOKEN`），其余路径与错口令静默 `404`。
- 注意：经 API 新建 Worker 时 `workers.dev` 子域路由默认关闭（会报 1042），需在 CF 面板或 subdomain API 启用。

## 订阅地址（支持 `?cfip=<优选IP或域名>`）
- 手机小火箭 / v2rayN（Base64）：`https://<域名>/sub/<SUB_TOKEN>/uri`
- Mihomo / Clash Meta（完整 YAML）：`https://<域名>/sub/<SUB_TOKEN>/mihomo`

## 后续切正式 Argo 域名（1 分钟完成）
1. 在 Cloudflare Zero Trust 建 Tunnel，配置唯一一条公共主机名规则：`edge.yourdomain.com -> http://127.0.0.1:18000`；
2. 把 Tunnel Token 写入 `/etc/vpngate-relay/cf-tunnel.token`（`chmod 600`）；
3. 在 `/etc/vpngate-relay/config.json` 填入 `"public_host": "edge.yourdomain.com"`；
4. `sudo systemctl restart vpngate-relayd vpngate-cloudflared`。

## 5 分钟换机迁移（例如免费机到期前）
```bash
# 旧机打包（严格 600 权限，切勿上传 Git）
sudo bash ~/vpngate-clean-mihomo/v2/scripts/migrate-pack.sh /tmp/vpngate-bundle.tar.gz
# 传到新机后解压并一键恢复（复用同一个 Tunnel Token，手机订阅零改动）
mkdir -p /tmp/restore && tar -xzf /tmp/vpngate-bundle.tar.gz -C /tmp/restore
sudo bash /tmp/restore/install-from-bundle.sh
# 确认新机双槽 ready 后停掉旧机 cloudflared 即可无缝接管
```
