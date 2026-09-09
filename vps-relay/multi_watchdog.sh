#!/bin/bash
# 多地区槽位看门狗:逐槽检查 隧道存活 + 出口国(非日本) + scamalytics欺诈分
# 任一不达标累计2次 -> 该槽轮换下一候选(带600s每槽冷却);欺诈分缓存1小时/槽
DIR="/opt/vpngate"
cd "$DIR" || exit 0
[ -f "$DIR/vpngate.env" ] && . "$DIR/vpngate.env"
SCAM_MAX="${VPNGATE_SCAM_MAX:-25}"
SCAM_API="${VPNGATE_SCAM_API:-}"   # 留空则只验隧道存活与出口国,跳过欺诈分
COOLDOWN="${VPNGATE_SWITCH_COOLDOWN:-600}"
SLOTS="${VPNGATE_MULTI_SLOTS:-10}"
MAINIP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1);exit}}')
now=$(date +%s)

for SLOT in $(seq 1 "$SLOTS"); do
  SD="$DIR/multi/$SLOT"
  [ -d "$SD" ] || continue
  cd "$SD" || continue
  DEV="vpnm$SLOT"
  SVC="vpngate-tunnel@$SLOT"
  LT=$(cat last-switch.ts 2>/dev/null || echo 0)
  [ $((now - LT)) -lt "$COOLDOWN" ] && continue
  DEVIP=$(ip -o -4 addr show dev "$DEV" 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1)
  E=""
  if [ -n "$DEVIP" ] && systemctl is-active --quiet "$SVC"; then
    E=$(curl -4 -s --max-time 10 --interface "$DEV" https://api.ipify.org 2>/dev/null)
  fi
  if ! [[ "$E" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { [ -n "$MAINIP" ] && [ "$E" = "$MAINIP" ]; }; then
    N=$(($(cat fail.count 2>/dev/null || echo 0)+1)); echo $N > fail.count
    echo "$(date '+%F %T') slot=$SLOT unhealthy #$N" >> "$DIR/multi.log"
    if [ "$N" -ge 2 ]; then
      echo 0 > fail.count
      TOTAL=$(wc -l < candidates.tsv 2>/dev/null || echo 0)
      if [ "$TOTAL" -gt 1 ]; then
        IDX=$(( ( $(cat current.idx 2>/dev/null || echo 0) + 1 ) % TOTAL ))
        echo "$IDX" > current.idx
      fi
      echo "$(date '+%F %T') slot=$SLOT down -> rotate idx=$IDX" >> "$DIR/multi.log"
      bash "$DIR/multi_build.sh" "$SLOT" >/dev/null
      systemctl restart "$SVC"
    fi
    continue
  fi
  echo 0 > fail.count
  if [ -z "$SCAM_API" ]; then continue; fi   # 未配置欺诈分接口,跳过纯净度检查
  # 出口国 + 欺诈分(每槽缓存1小时)
  read -r LIP LSC LTS LCC <<< "$(cat exit-scam.txt 2>/dev/null)"
  if [ "$E" != "$LIP" ] || [ -z "$LSC" ] || [ $((now - LTS)) -gt 3600 ]; then
    CC=$(curl -s -m 8 "http://ip-api.com/line/$E?fields=countryCode")
    SC=$(curl -s -m 20 "${SCAM_API}${E}" 2>/dev/null | grep -oE 'score=[0-9]+' | cut -d= -f2)
    [ -z "$SC" ] && SC="$LSC"
    echo "$E $SC $now $CC" > exit-scam.txt
    echo "$(date '+%F %T') slot=$SLOT scored egress=$E cc=$CC scam=$SC" >> "$DIR/multi.log"
  else
    SC="$LSC"; CC="$LCC"
  fi
  if [ "$CC" != "JP" ] && [ -n "$SC" ] && [ "$SC" -lt "$SCAM_MAX" ] 2>/dev/null; then
    echo "$(date '+%F %T') slot=$SLOT clean egress=$E cc=$CC scam=$SC" >> "$DIR/multi.log"
    rm -f dirty.count
    continue
  fi
  DN=$(($(cat dirty.count 2>/dev/null || echo 0)+1)); echo $DN > dirty.count
  echo "$(date '+%F %T') slot=$SLOT dirty cc=$CC scam=$SC #$DN" >> "$DIR/multi.log"
  [ "$DN" -lt 2 ] && continue
  echo 0 > dirty.count
  echo "$now" > last-switch.ts
  TOTAL=$(wc -l < candidates.tsv 2>/dev/null || echo 0)
  if [ "$TOTAL" -gt 1 ]; then
    IDX=$(( ( $(cat current.idx 2>/dev/null || echo 0) + 1 ) % TOTAL ))
    echo "$IDX" > current.idx
  fi
  echo "$(date '+%F %T') slot=$SLOT dirty -> rotate idx=$IDX" >> "$DIR/multi.log"
  bash "$DIR/multi_build.sh" "$SLOT" >/dev/null
  systemctl restart "$SVC"
done
