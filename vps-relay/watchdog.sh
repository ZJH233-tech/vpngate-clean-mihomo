#!/bin/bash
# VPNGate 看门狗 v3:隧道健康 + 出口纯净度自动切换
#   1) 隧道健康(原有的):连续2次失败 -> 自动切换
#   2) 出口纯净(新增):出口IP欺诈分(scamalytics)>= VPNGATE_SCAM_MAX 连续2次
#      -> 自动执行严格挑选(pick_strict.sh:日本出口+欺诈分<25+能连上)
#   3) 10分钟冷却防抖;同一出口IP欺诈分缓存1小时(不重复查询)
# 重构修改历史:
#   v2 idx运算强制10#十进制;health.log 超200KB截尾
#   v3 集成出口欺诈分监控与自动切换
DIR="${VPNGATE_DIR:-/opt/vpngate}"
cd "$DIR" || exit 0
[ -f "$DIR/vpngate.env" ] && . "$DIR/vpngate.env"
SCAM_MAX="${VPNGATE_SCAM_MAX:-25}"                       # 出口欺诈分阈值
SCAM_API="${VPNGATE_SCAM_API:-}"                         # 欺诈分查询接口(含 ?ip=),留空则跳过纯净度检查
COOLDOWN="${VPNGATE_SWITCH_COOLDOWN:-600}"               # 自动切换冷却(秒)

rot() {
  [ -f health.log ] || return 0
  S=$(stat -c %s health.log 2>/dev/null || echo 0)
  if [ "$S" -gt 204800 ]; then
    tail -n 500 health.log > health.log.tmp 2>/dev/null && mv health.log.tmp health.log
  fi
}

# 自动探测主网卡出口 IP(若隧道出口等于它,说明流量泄漏回主线路,判不健康)
MAINIP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1);exit}}')
DEV=$(ip -o -4 addr show 2>/dev/null | grep -oE 'tun[0-9]+' | head -1)

healthy() {
  [ -z "$DEV" ] && return 1
  local ip code
  ip=$(curl -4 -s --max-time 8 --interface "$DEV" https://api.ipify.org 2>/dev/null)
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  [ -n "$MAINIP" ] && [ "$ip" = "$MAINIP" ] && return 1
  code=$(curl -4 -s -o /dev/null -w '%{http_code}' --max-time 10 --interface "$DEV" https://www.gstatic.com/generate_204 2>/dev/null)
  [ "$code" = "204" ] || return 1
  return 0
}

do_switch() {  # $1 = 原因
  exec 9>/var/lock/vpngate-bestip.lock
  flock 9
  echo "$(date '+%F %T') auto-switch: $1" >> watchdog-switch.log
  bash "$DIR/pick_strict.sh" >>pick.log 2>&1
}

rot
now=$(date +%s)

# ---------- 1) 隧道健康 ----------
if ! healthy; then
  N=$(($(cat fail.count 2>/dev/null || echo 0)+1)); echo $N > fail.count
  echo "$(date '+%F %T') unhealthy #$N dev=$DEV" >> watchdog-switch.log
  [ "$N" -lt 2 ] && exit 0
  echo 0 > fail.count
  do_switch "tunnel down (2 consecutive failures)"
  exit 0
fi
echo 0 > fail.count

# ---------- 0) 策略路由自愈(防其他隧道 down 脚本误删日本槽规则) ----------
if ! ip rule show | grep -q 'fwmark 0x162 lookup 100'; then
  ip rule add fwmark 0x162 lookup 100 priority 101 2>/dev/null
  echo "$(date '+%F %T') self-heal: fwmark 0x162 rule re-added" >> watchdog-switch.log
fi
if ! ip route show table 100 2>/dev/null | grep -q default; then
  ip route replace default dev tun0 table 100 2>/dev/null
  echo "$(date '+%F %T') self-heal: table 100 default re-added" >> watchdog-switch.log
fi

# ---------- 2) 出口纯净度 ----------
if [ -z "$SCAM_API" ]; then
  echo "$(date '+%F %T') VPNGATE_SCAM_API not set, skip purity check" >> health.log
  exit 0
fi
E=$(curl -4 -s --max-time 10 --interface "$DEV" https://api.ipify.org 2>/dev/null)
[ -z "$E" ] && exit 0
read -r LIP LSC LTS <<< "$(cat exit-scam.txt 2>/dev/null)"
if [ "$E" != "$LIP" ] || [ -z "$LSC" ] || [ $((now - LTS)) -gt 3600 ]; then
  SC=$(curl -s -m 20 "${SCAM_API}${E}" 2>/dev/null | grep -oE 'score=[0-9]+' | cut -d= -f2)
  [ -z "$SC" ] && SC="$LSC"          # 查询失败沿用旧值,不误判
  echo "$E $SC $now" > exit-scam.txt
  echo "$(date '+%F %T') scored egress=$E scam=$SC" >> health.log
else
  SC="$LSC"
fi
[ -z "$SC" ] && exit 0
if [ "$SC" -lt "$SCAM_MAX" ] 2>/dev/null; then
  echo "$(date '+%F %T') clean egress=$E scam=$SC" >> health.log
  rm -f dirty.count
  exit 0
fi

# 脏:连续2次确认(约2.5分钟),防 scamalytics 抖动
DN=$(($(cat dirty.count 2>/dev/null || echo 0)+1)); echo $DN > dirty.count
echo "$(date '+%F %T') dirty egress=$E scam=$SC #$DN (threshold=$SCAM_MAX)" >> watchdog-switch.log
[ "$DN" -lt 2 ] && exit 0
echo 0 > dirty.count
LT=$(cat last-switch.ts 2>/dev/null || echo 0)
if [ $((now - LT)) -lt "$COOLDOWN" ]; then
  echo "$(date '+%F %T') switch suppressed (cooldown $((now - LT))s)" >> watchdog-switch.log
  exit 0
fi
echo "$now" > last-switch.ts
do_switch "egress dirty (scam=$SC >= $SCAM_MAX, 2 consecutive)"
