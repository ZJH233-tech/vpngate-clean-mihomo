#!/bin/bash
# 一键导出五分钟搬家包（含敏感 secrets.env，产物严格 600 权限，切勿提交到 Git）
set -eu
OUT="${1:-/root/vpngate-relay-bundle-$(date +%Y%m%d-%H%M%S).tar.gz}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
V2_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/systemd"
cp /usr/local/bin/vpngate-relayd "$STAGE/vpngate-relayd"
cp "$V2_DIR/scripts/run-cloudflared.sh" "$STAGE/run-cloudflared.sh"
cp "$V2_DIR/scripts/install-from-bundle.sh" "$STAGE/install-from-bundle.sh"
cp "$V2_DIR/systemd/"*.service "$STAGE/systemd/"
cp /etc/vpngate-relay/config.json "$STAGE/config.json"
cp /etc/vpngate-relay/secrets.env "$STAGE/secrets.env"
[ -f /etc/vpngate-relay/cf-tunnel.token ] && cp /etc/vpngate-relay/cf-tunnel.token "$STAGE/cf-tunnel.token"

umask 077
tar -czf "$OUT" -C "$STAGE" .
chmod 600 "$OUT"
echo "BUNDLE_CREATED=$OUT ($(du -h "$OUT" | awk '{print $1}'))"
