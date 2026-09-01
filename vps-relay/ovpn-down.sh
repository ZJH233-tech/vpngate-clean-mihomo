#!/bin/bash
# OpenVPN down：清理 v4/v6 策略路由与规则，避免残留
DEV="${dev:-tun0}"
MARK="${VPNGATE_MARK:-0x162}"
TABLE="${VPNGATE_TABLE:-100}"
ip rule del fwmark "$MARK" lookup "$TABLE" 2>/dev/null
ip rule del oif "$DEV" lookup "$TABLE" 2>/dev/null
ip route flush table "$TABLE" 2>/dev/null
ip -6 rule del fwmark "$MARK" lookup "$TABLE" 2>/dev/null
ip -6 route flush table "$TABLE" 2>/dev/null
exit 0
