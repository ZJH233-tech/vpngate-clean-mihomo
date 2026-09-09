#!/bin/bash
# 严格挑选:隧道建立 + 出口IP为日本 + scamalytics欺诈分 < VPNGATE_SCAM_MAX(默认25) 才接受
# 全部不达标时回落到"日本出口中欺诈分最低"的候选
DIR="${VPNGATE_DIR:-/opt/vpngate}"
cd "$DIR" || exit 1
[ -f "$DIR/vpngate.env" ] && . "$DIR/vpngate.env"
SCAM_MAX="${VPNGATE_SCAM_MAX:-25}"
SCAM_API="${VPNGATE_SCAM_API:-https://scamtest.REDACTED-USER.workers.dev/?ip=}"
TOTAL=$(wc -l < candidates.tsv)
BEST=""
for ((i=0; i<TOTAL; i++)); do
  echo "$i" > current.idx
  ./build_running.sh >/dev/null
  systemctl restart vpngate-tunnel
  sleep 14
  if ! journalctl -u vpngate-tunnel --since '-20 seconds' --no-pager | grep -q 'Initialization Sequence Completed'; then
    echo "idx=$i not established"; continue
  fi
  if journalctl -u vpngate-tunnel --since '-20 seconds' --no-pager | grep -q 'AUTH_FAILED'; then
    echo "idx=$i AUTH_FAILED"; continue
  fi
  DEV=$(ip -o -4 addr show | grep -oE 'tun[0-9]+' | head -1)
  E=$(curl -4 -s --max-time 10 --interface "$DEV" https://api.ipify.org)
  if [ -z "$E" ]; then echo "idx=$i no egress"; continue; fi
  CC=$(curl -s -m 8 "http://ip-api.com/line/$E?fields=countryCode")
  SC=$(curl -s -m 20 "${SCAM_API}${E}" | grep -oE 'score=[0-9]+' | cut -d= -f2)
  echo "idx=$i entry=$(grep '^remote ' running.ovpn | awk '{print $2}') egress=$E cc=$CC scam=$SC"
  if [ "$CC" = "JP" ] && [ -n "$SC" ] && [ "$SC" -lt "$SCAM_MAX" ] 2>/dev/null; then
    echo "ACCEPTED idx=$i egress=$E scam=$SC"
    exit 0
  fi
  # 记录备选:JP 但欺诈分略高的
  if [ "$CC" = "JP" ] && [ -n "$SC" ]; then
    if [ -z "$BEST" ] || [ "$SC" -lt "${BEST#*:}" ] 2>/dev/null; then BEST="$i:$SC"; fi
  fi
done
if [ -n "$BEST" ]; then
  echo "FALLBACK idx=${BEST%%:*} (scam=${BEST#*:})"
  echo "${BEST%%:*}" > current.idx
  ./build_running.sh >/dev/null
  systemctl restart vpngate-tunnel
  sleep 12
  systemctl is-active vpngate-tunnel
else
  echo "NONE_JP"
fi
