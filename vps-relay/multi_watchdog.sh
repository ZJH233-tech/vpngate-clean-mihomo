#!/bin/bash
# 多地区槽位看门狗 v2:隧道存活 + 出口国(==绑定国且非日本) + scamalytics 欺诈分
#   - 任一不达标累计 2 次 -> 该槽轮换下一候选(600s 每槽冷却)
#   - 欺诈分缓存 1 小时/槽; 实测出口国每轮实时查询
#   - v2: 实测国必须等于槽位绑定国(multi/<slot>/cc), 链式转发导致声明国≠出口国时
#         自动换候选; 一轮候选全部试过仍不符则"接受现状"(由 sync 按实测国命名),
#         写 release 让下次 multi_refresh 换国, 避免死循环;
#         全部候选隧道都打不通(unhealthy)时同样写 release(v2.1 修复: 旧逻辑此路径
#         只轮换不释放, 单候选死国家会永久空转)
#   - 每轮结束调用 sync_subscriptions.py, 让订阅名字始终跟随真实出口
DIR="/opt/vpngate"
cd "$DIR" || exit 0
[ -f "$DIR/vpngate.env" ] && . "$DIR/vpngate.env"
SCAM_MAX="${VPNGATE_SCAM_MAX:-25}"
SCAM_API="${VPNGATE_SCAM_API:-}"   # 留空则只验隧道存活与出口国,跳过欺诈分
COOLDOWN="${VPNGATE_SWITCH_COOLDOWN:-600}"
SLOTS="${VPNGATE_MULTI_SLOTS:-10}"
MAINIP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1);exit}}')
now=$(date +%s)

bump_tried() {  # 候选池刷新则计数清零, 否则+1; 输出当前已尝试数
  local SLOT=$1
  local SD="$DIR/multi/$SLOT"
  cd "$SD" || { echo 0; return; }
  local MT PMT T
  MT=$(stat -c %Y candidates.tsv 2>/dev/null || echo 0)
  PMT=$(cat cand.mtime 2>/dev/null || echo 0)
  if [ "$MT" != "$PMT" ]; then echo "$MT" > cand.mtime; echo 0 > tried.count; fi
  T=$(( $(cat tried.count 2>/dev/null || echo 0) + 1 ))
  echo "$T" > tried.count
  echo "$T"
}

rotate_slot() {  # $1=slot  轮换到下一候选并重建重启
  local SLOT=$1
  local SD="$DIR/multi/$SLOT"
  cd "$SD" || return
  local TOTAL IDX TRIED
  TOTAL=$(wc -l < candidates.tsv 2>/dev/null || echo 0)
  IDX=$(cat current.idx 2>/dev/null || echo 0)
  if [ "$TOTAL" -gt 1 ]; then
    IDX=$(( (IDX + 1) % TOTAL ))
    echo "$IDX" > current.idx
  fi
  bash "$DIR/multi_build.sh" "$SLOT" >/dev/null
  systemctl restart "vpngate-tunnel@$SLOT"
  TRIED=$(bump_tried "$SLOT")
  echo "$(date '+%F %T') slot=$SLOT rotate -> idx=$IDX tried=$TRIED/$TOTAL" >> "$DIR/multi.log"
}

for SLOT in $(seq 1 "$SLOTS"); do
  SD="$DIR/multi/$SLOT"
  [ -d "$SD" ] || continue
  cd "$SD" || continue
  DEV="vpnm$SLOT"
  SVC="vpngate-tunnel@$SLOT"
  PIN=$(cat cc 2>/dev/null | tr -d '*')
  LT=$(cat last-switch.ts 2>/dev/null || echo 0)
  [ $((now - LT)) -lt "$COOLDOWN" ] && continue

  DEVIP=$(ip -o -4 addr show dev "$DEV" 2>/dev/null | grep -oE 'inet [0-9.]+' | head -1)
  E=""
  if [ -n "$DEVIP" ] && systemctl is-active --quiet "$SVC"; then
    E=$(curl -4 -s --max-time 10 --interface "$DEV" https://api.ipify.org 2>/dev/null)
  fi

  # ---------- 1) 隧道健康 ----------
  if ! [[ "$E" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { [ -n "$MAINIP" ] && [ "$E" = "$MAINIP" ]; }; then
    N=$(($(cat fail.count 2>/dev/null || echo 0)+1)); echo $N > fail.count
    echo "$(date '+%F %T') slot=$SLOT unhealthy #$N" >> "$DIR/multi.log"
    if [ "$N" -ge 2 ]; then
      echo 0 > fail.count
      TOTAL=$(wc -l < candidates.tsv 2>/dev/null || echo 0)
      # 候选池若已全部试过仍不通, 直接释放绑定(避免单候选死隧道无限空转)
      MT=$(stat -c %Y candidates.tsv 2>/dev/null || echo 0)
      PMT=$(cat cand.mtime 2>/dev/null || echo 0)
      [ "$MT" != "$PMT" ] && { echo "$MT" > cand.mtime; echo 0 > tried.count; }
      CUR=$(cat tried.count 2>/dev/null || echo 0)
      if [ "$TOTAL" -ge 1 ] && [ $((CUR + 1)) -ge "$TOTAL" ]; then
        echo $((CUR + 1)) > tried.count
        echo "$now all-candidates-unhealthy" > release
        echo "$(date '+%F %T') slot=$SLOT accept dead pool, RELEASE pin=$PIN (all $TOTAL candidates unhealthy)" >> "$DIR/multi.log"
      else
        echo "$now" > last-switch.ts
        rotate_slot "$SLOT"
      fi
    fi
    continue
  fi
  echo 0 > fail.count

  # ---------- 2) 实测出口国 + 欺诈分(IP 变/无缓存/超 1h 则刷新) ----------
  read -r LIP LSC LTS LCC <<< "$(cat exit-scam.txt 2>/dev/null)"
  if [ "$E" != "$LIP" ] || [ -z "$LCC$LSC" ] || [ $((now - ${LTS:-0})) -gt 3600 ]; then
    CC=$(curl -s -m 8 "http://ip-api.com/line/$E?fields=countryCode")
    SC=""
    if [ -n "$SCAM_API" ]; then
      SC=$(curl -s -m 20 "${SCAM_API}${E}" 2>/dev/null | grep -oE 'score=[0-9]+' | cut -d= -f2)
    fi
    [ -z "$SC" ] && SC="$LSC"
    echo "$E ${SC:-0} $now $CC" > exit-scam.txt
  else
    SC="$LSC"; CC="$LCC"
  fi

  # ---------- 3) 纯净度判定: 非日本 + 等于绑定国 + 欺诈分达标 ----------
  REASON=""
  [ "$CC" = "JP" ] && REASON="egress-in-JP"
  [ -n "$PIN" ] && [ "$CC" != "$PIN" ] && REASON="${REASON:+$REASON,}cc-mismatch(want=$PIN got=$CC)"
  if [ -n "$SC" ] && ! [ "$SC" -lt "$SCAM_MAX" ] 2>/dev/null; then
    REASON="${REASON:+$REASON,}scam=$SC>=$SCAM_MAX"
  fi

  if [ -z "$REASON" ]; then
    echo "$(date '+%F %T') slot=$SLOT clean egress=$E cc=$CC scam=$SC pin=$PIN" >> "$DIR/multi.log"
    rm -f dirty.count
    continue
  fi

  # 候选池轮了一遍仍不合格 -> 接受现状继续跑(订阅按实测国显示),
  # 同时写 release 标志, 让下次 multi_refresh 释放该槽绑定、另换国家(30min 内不再选它)
  TOTAL=$(wc -l < candidates.tsv 2>/dev/null || echo 0)
  TRIED=$(cat tried.count 2>/dev/null || echo 0)
  if [ "$TRIED" -ge "$TOTAL" ] && [ "$TOTAL" -ge 1 ]; then
    echo "$now $REASON" > release
    echo "$(date '+%F %T') slot=$SLOT accept egress=$E cc=$CC, RELEASE pin=$PIN ($REASON, pool exhausted)" >> "$DIR/multi.log"
    rm -f dirty.count
    continue
  fi

  DN=$(($(cat dirty.count 2>/dev/null || echo 0)+1)); echo "$DN" > dirty.count
  echo "$(date '+%F %T') slot=$SLOT dirty[$REASON] #$DN" >> "$DIR/multi.log"
  [ "$DN" -lt 2 ] && continue
  echo 0 > dirty.count
  echo "$now" > last-switch.ts
  rotate_slot "$SLOT"
done

# ---------- 4) 订阅名字跟随真实出口(内部自行判断是否需要重启 s-ui) ----------
/usr/bin/python3 "$DIR/sync_subscriptions.py" >> "$DIR/sync_call.log" 2>&1 || \
  echo "$(date '+%F %T') sync_subscriptions failed" >> "$DIR/multi.log"
