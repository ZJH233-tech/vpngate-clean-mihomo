#!/bin/bash
# VPNGate 隧道健康检查：连续2次失败 -> 切到实时榜首；再失败则按候选顺序兜底切换
DIR="${VPNGATE_DIR:-/opt/vpngate}"
cd "$DIR" || exit 0
# 自动探测主网卡出口 IP（若隧道出口等于它，说明流量泄漏回了主线路，判不健康）
MAINIP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1);exit}}')
DEV=$(ip -o -4 addr show | grep -oE 'tun[0-9]+' | head -1)
healthy() {
  [ -z "$DEV" ] && return 1
  local ip code
  ip=$(curl -4 -s --max-time 8 --interface "$DEV" https://api.ipify.org 2>/dev/null)
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  [ -n "$MAINIP" ] && [ "$ip" = "$MAINIP" ] && return 1
  code=$(curl -4 -s -o /dev/null -w '%{http_code}' --max-time 10 --interface "$DEV" https://www.google.com/generate_204 2>/dev/null)
  [ "$code" = "204" ] || return 1
  echo "$(date '+%F %T') healthy egress=$ip dev=$DEV" >> health.log
  return 0
}
if healthy; then echo 0 > fail.count; exit 0; fi
N=$(($(cat fail.count 2>/dev/null || echo 0)+1)); echo $N > fail.count
echo "$(date '+%F %T') unhealthy #$N dev=$DEV" >> watchdog-switch.log
[ "$N" -lt 2 ] && exit 0
echo 0 > fail.count
exec 9>/var/lock/vpngate-bestip.lock
flock 9
echo "$(date '+%F %T') failover: switch to realtime best" >> watchdog-switch.log
if VPNGATE_DIR="$DIR" /usr/bin/python3 "$DIR/bestip_refresh.py" --switch-top >>bestip.log 2>&1; then
  exit 0
fi
# 兜底：优选器不可用时按候选顺序切下一个
IDX=$(cat current.idx 2>/dev/null || echo 0)
TOTAL=$(wc -l < candidates.tsv)
NEXT=$((IDX+1))
[ "$NEXT" -ge "$TOTAL" ] && NEXT=0
echo "$NEXT" > current.idx
echo "$(date '+%F %T') fallback idx $IDX -> $NEXT" >> watchdog-switch.log
"$DIR/build_running.sh"
systemctl restart vpngate-tunnel
