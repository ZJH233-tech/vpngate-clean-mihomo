#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cron 每小时兜底: 订阅重建逻辑已统一收归 sync_subscriptions.py(单一事实来源)。
本文件保留是为了兼容既有 crontab(17 * * * *)。"""
import runpy
runpy.run_path('/opt/vpngate/sync_subscriptions.py', run_name='__main__')
