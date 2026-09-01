#!/bin/bash
# OpenVPN route-up：建立 fwmark/oif 策略路由，让被标记流量只走隧道；IPv6 黑洞防泄露
DEV="${dev:-tun0}"
MARK="${VPNGATE_MARK:-0x162}"
TABLE="${VPNGATE_TABLE:-100}"
logger -t vpngate "route-up dev=$DEV local=$ifconfig_local"
ip route flush table "$TABLE" 2>/dev/null
ip route add default dev "$DEV" table "$TABLE"
ip rule del fwmark "$MARK" lookup "$TABLE" 2>/dev/null
ip rule add fwmark "$MARK" lookup "$TABLE" priority 101
ip rule del oif "$DEV" lookup "$TABLE" 2>/dev/null
ip rule add oif "$DEV" lookup "$TABLE" priority 100
sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null
sysctl -w net.ipv4.conf."$DEV".rp_filter=2 >/dev/null
# IPv6 黑洞：隧道为 IPv4-only，被 mark 的 IPv6 一律丢弃，绝不走主网卡泄露
ip -6 route replace blackhole default table "$TABLE" 2>/dev/null
ip -6 rule del fwmark "$MARK" lookup "$TABLE" 2>/dev/null
ip -6 rule add fwmark "$MARK" lookup "$TABLE" priority 101 2>/dev/null
ip route flush cache
exit 0
