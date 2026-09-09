#!/usr/bin/env python3
"""生成 sb-exit.json:1个日本入站(8444) + 10个多地区入站(8445-8454)。
每入站独立直连出站(routing_mark 354+slot),IPv6 一律黑洞。"""
import json

kv = {}
for line in open('/root/kp-8444.txt'):
    if '=' in line:
        k, v = line.strip().split('=', 1)
        kv[k] = v
UUID = 'REDACTED-UUID'
TLS = {
    'enabled': True,
    'server_name': 'www.ibm.com',
    'reality': {
        'enabled': True,
        'handshake': {'server': 'www.ibm.com', 'server_port': 443},
        'private_key': kv['priv8444'],
        'short_id': [kv['sid8444']],
        'max_time_difference': '1m',
    },
}

inbounds = [{
    'type': 'vless', 'tag': 'exit-in', 'listen': '::', 'listen_port': 8444,
    'users': [{'name': 'u4', 'uuid': UUID, 'flow': 'xtls-rprx-vision'}],
    'tls': TLS,
}]
outbounds = [
    {'type': 'direct', 'tag': 'via-tun', 'routing_mark': 354},
    {'type': 'block', 'tag': 'block6'},
]
rules = [
    {'inbound': ['exit-in'], 'ip_cidr': ['::/0'], 'outbound': 'block6'},
    {'inbound': ['exit-in'], 'outbound': 'via-tun'},
]
for i in range(1, 11):
    tag = 'multi-%d' % i
    inbounds.append({
        'type': 'vless', 'tag': tag, 'listen': '::', 'listen_port': 8444 + i,
        'users': [{'name': 'u4', 'uuid': UUID, 'flow': 'xtls-rprx-vision'}],
        'tls': TLS,
    })
    outbounds.append({'type': 'direct', 'tag': 'vt%d' % i, 'routing_mark': 354 + i})
    rules.append({'inbound': [tag], 'ip_cidr': ['::/0'], 'outbound': 'block6'})
    rules.append({'inbound': [tag], 'outbound': 'vt%d' % i})

cfg = {
    'log': {'level': 'warn', 'output': '/opt/vpngate/sbexit.log', 'timestamp': True},
    'inbounds': inbounds,
    'outbounds': outbounds,
    'route': {'rules': rules},
}
json.dump(cfg, open('/opt/vpngate/sb-exit.json', 'w'), ensure_ascii=False, indent=2)
print('sb-exit.json written: inbounds=%d outbounds=%d rules=%d' % (len(inbounds), len(outbounds), len(rules)))
