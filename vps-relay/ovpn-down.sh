#!/bin/bash
# OpenVPN down:清理本隧道的 v4/v6 策略路由与规则
# v3 加固:${dev} 未定义(隧道从未建立,如认证失败)时跳过清理——
#         此时 MARK/TABLE 会回落到默认值,误删日本槽(0x162/100)的规则(实测踩坑)
if [ -z "${dev:-}" ]; then
  logger -t vpngate "down skipped: tunnel never established (no dev)"
  exit 0
fi
DEV="${dev}"
MARK="${MARK:-${VPNGATE_MARK:-0x162}}"
TABLE="${TABLE:-${VPNGATE_TABLE:-100}}"
logger -t vpngate "down dev=$DEV mark=$MARK table=$TABLE"
ip rule del fwmark "$MARK" lookup "$TABLE" 2>/dev/null
ip rule del oif "$DEV" lookup "$TABLE" 2>/dev/null
ip route flush table "$TABLE" 2>/dev/null
ip -6 rule del fwmark "$MARK" lookup "$TABLE" 2>/dev/null
ip -6 route flush table "$TABLE" 2>/dev/null
exit 0

