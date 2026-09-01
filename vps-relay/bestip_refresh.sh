#!/bin/bash
# 定时实时优选：与 watchdog 切换互斥
exec 9>/var/lock/vpngate-bestip.lock
flock 9
cd /opt/vpngate
/usr/bin/python3 /opt/vpngate/bestip_refresh.py "$@"
