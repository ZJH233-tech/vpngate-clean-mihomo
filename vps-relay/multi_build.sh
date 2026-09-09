#!/bin/bash
# 组装第 $1 槽位的 running.ovpn(独立 tun 设备 + 独立 fwmark/路由表,经 setenv 传给 ovpn-up.sh)
set -u
SLOT="$1"
DIR="/opt/vpngate"
SD="$DIR/multi/$SLOT"
cd "$SD" || exit 1
[ -f current.idx ] || echo 0 > current.idx
pick() { awk -F'\t' -v i="$1" '$1==i{print $6}' candidates.tsv; }
IDX=$(cat current.idx)
SRC=$(pick "$IDX")
if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  IDX=0; echo 0 > current.idx; SRC=$(pick 0)
fi
if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  logger -t vpngate-multi "build slot=$SLOT: no candidate"
  exit 1
fi
{
  cat "$SRC"
  if ! grep -qiE '^[[:space:]]*auth-user-pass' "$SRC"; then
    printf 'auth-user-pass %s\nauth-nocache\n' "$DIR/auth.txt"
  fi
  cat <<TAIL
dev-type tun
dev vpnm$SLOT
setenv MARK $((354 + SLOT))
setenv TABLE $((100 + SLOT))
route-nopull
route-noexec
script-security 2
route-up $DIR/ovpn-up.sh
down $DIR/ovpn-down.sh
connect-retry 3
connect-timeout 15
resolv-retry 5
mute-replay-warnings
TAIL
} > running.ovpn
echo "built slot=$SLOT idx=$IDX src=$SRC"
