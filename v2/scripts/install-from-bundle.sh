#!/bin/bash
# 在新机器解压搬家包后执行: sudo bash install-from-bundle.sh
set -eu
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== 1. 安装基础依赖 (openvpn / curl / jq / iproute2) ==="
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openvpn curl jq ca-certificates iproute2

echo "=== 2. 检查 sing-box 与 cloudflared ==="
for b in /usr/local/bin/sing-box /usr/local/bin/cloudflared; do
  if [ ! -x "$b" ]; then
    echo "WARNING: 缺少 $b，请先安装该二进制（或确认路径）后再启动服务"
  fi
done

echo "=== 3. 配置内核 rp_filter=2 (fwmark 策略路由必需) ==="
cat >/etc/sysctl.d/99-vpngate-relay.conf <<'SYS'
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
SYS
sysctl --system >/dev/null

echo "=== 4. 安装二进制、配置与 systemd 单元 ==="
install -m 0755 "$BUNDLE_DIR/vpngate-relayd" /usr/local/bin/vpngate-relayd
install -m 0755 "$BUNDLE_DIR/run-cloudflared.sh" /usr/local/bin/vpngate-run-cloudflared.sh

mkdir -p /etc/vpngate-relay /var/lib/vpngate-relay
install -m 0644 "$BUNDLE_DIR/config.json" /etc/vpngate-relay/config.json
install -m 0600 "$BUNDLE_DIR/secrets.env" /etc/vpngate-relay/secrets.env
if [ -f "$BUNDLE_DIR/cf-tunnel.token" ]; then
  install -m 0600 "$BUNDLE_DIR/cf-tunnel.token" /etc/vpngate-relay/cf-tunnel.token
fi

# 若新机已接入 Tailscale，自动把 control_listen 更新为新机的 Tailscale IP（否则回落 127.0.0.1）
TS_IP=$(command -v tailscale >/dev/null 2>&1 && tailscale ip -4 2>/dev/null | head -n 1 || true)
LISTEN_ADDR="${TS_IP:-127.0.0.1}:19099"
jq --arg addr "$LISTEN_ADDR" '.control_listen = $addr' /etc/vpngate-relay/config.json > /etc/vpngate-relay/config.json.tmp
mv /etc/vpngate-relay/config.json.tmp /etc/vpngate-relay/config.json
echo "control_listen -> $LISTEN_ADDR"

install -m 0644 "$BUNDLE_DIR/systemd/vpngate-singbox.service" /etc/systemd/system/
install -m 0644 "$BUNDLE_DIR/systemd/vpngate-relayd.service" /etc/systemd/system/
install -m 0644 "$BUNDLE_DIR/systemd/vpngate-cloudflared.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now vpngate-singbox vpngate-relayd vpngate-cloudflared
sleep 3
systemctl --no-pager --full status vpngate-singbox vpngate-relayd vpngate-cloudflared | head -n 45
