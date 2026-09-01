#!/bin/bash
# 依据 current.idx 从候选池拼出 running.ovpn；候选池缺失/为空时先自举生成，保证开机可起
set -u
DIR="${VPNGATE_DIR:-/opt/vpngate}"
cd "$DIR" || exit 1
[ -f current.idx ] || echo 0 > current.idx
if [ ! -s candidates.tsv ]; then
  logger -t vpngate "candidates missing -> bootstrap bestip_refresh"
  VPNGATE_DIR="$DIR" /usr/bin/python3 "$DIR/bestip_refresh.py" >>bestip.log 2>&1 || true
fi
pick() { awk -F'\t' -v i="$1" '$1==i{print $6}' candidates.tsv; }
IDX=$(cat current.idx)
SRC=$(pick "$IDX")
if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  IDX=0; echo 0 > current.idx; SRC=$(pick 0)
fi
if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  logger -t vpngate "build_running: no candidate available"
  exit 1
fi
{
  cat "$SRC"
  cat <<TAIL
route-nopull
route-noexec
auth-user-pass $DIR/auth.txt
auth-nocache
script-security 2
route-up $DIR/ovpn-up.sh
down $DIR/ovpn-down.sh
connect-retry 3
connect-timeout 15
resolv-retry 5
mute-replay-warnings
TAIL
} > running.ovpn
echo "built running.ovpn idx=$IDX src=$SRC"
