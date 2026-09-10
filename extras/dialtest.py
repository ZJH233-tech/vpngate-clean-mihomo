#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端拨测引擎:对全部/抽样节点发起真实代理请求,验证出口。
输出行格式: 名称|HTTP码|出口IP
用法: python3 dialtest.py [--full]
节点参数通过环境变量注入(见 README),默认全部为占位符,直接运行只会得到失败结果。
"""
import json, os, subprocess, sys, time

SB = '/usr/local/bin/sing-box'
XR = '/usr/local/bin/xray'
TMP = '/tmp/dt'

def env(k, d):
    return os.environ.get(k, d)

IP = env('DIAL_SERVER_IP', '127.0.0.1')
UUID1 = env('DIAL_UUID_N1', '00000000-0000-0000-0000-000000000000')
UUID3 = env('DIAL_UUID_N3', '00000000-0000-0000-0000-000000000000')
UUID_MULTI = env('DIAL_UUID_MULTI', '00000000-0000-0000-0000-000000000000')
HY2PASS = env('DIAL_HY2_PASS', '')
OBFS = env('DIAL_OBFS_PASS', '')
ARGO_HOST = env('DIAL_ARGO_HOST', 'your-argo-domain.example.com')
KP_DIR = env('DIAL_KP_DIR', '/root')
REALITY_SNI = env('DIAL_REALITY_SNI', 'www.ibm.com')

def kp(name):
    d = {}
    try:
        for ln in open(os.path.join(KP_DIR, 'kp-%s.txt' % name)):
            if '=' in ln:
                k, v = ln.strip().split('=', 1)
                d[k] = v
    except OSError:
        pass
    return d

KP443 = kp('443'); KP8388 = kp('8388'); KP8444 = kp('8444')

def wjson(path, obj):
    json.dump(obj, open(path, 'w'))

def sb_cfg(listen, out):
    return {'log': {'level': 'error'},
            'inbounds': [{'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': listen}],
            'outbounds': [out]}

def xr_cfg(listen, out):
    return {'log': {'loglevel': 'error'},
            'inbounds': [{'listen': '127.0.0.1', 'port': listen, 'protocol': 'socks', 'settings': {'udp': True}}],
            'outbounds': [out]}

def vless_sb(server, port, uuid, flow, tls, transport=None):
    o = {'type': 'vless', 'server': server, 'server_port': port, 'uuid': uuid}
    if flow:
        o['flow'] = flow
    if transport:
        o['transport'] = transport
    o['tls'] = tls
    return o

def reality_tls(pub, sid):
    return {'enabled': True, 'server_name': REALITY_SNI,
            'utls': {'enabled': True, 'fingerprint': 'chrome'},
            'reality': {'enabled': True, 'public_key': pub, 'short_id': sid}}

def test_one(name, engine, cfgobj, tag, lport):
    os.makedirs(TMP, exist_ok=True)
    cfgf = os.path.join(TMP, 'dt_%s.json' % tag)
    wjson(cfgf, cfgobj)
    exe = SB if engine == 'sb' else XR
    logf = os.path.join(TMP, 'dt_%s.log' % tag)
    p = subprocess.Popen([exe, 'run', '-c', cfgf], stdout=open(logf, 'w'), stderr=subprocess.STDOUT)
    time.sleep(2)
    code, ip = '000', ''
    try:
        r = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '12',
                            '-x', 'socks5h://127.0.0.1:%d' % lport,
                            'https://www.gstatic.com/generate_204'], capture_output=True, text=True, timeout=15)
        code = r.stdout.strip()
        r2 = subprocess.run(['curl', '-s', '--max-time', '12',
                             '-x', 'socks5h://127.0.0.1:%d' % lport,
                             'https://api.ipify.org'], capture_output=True, text=True, timeout=15)
        ip = r2.stdout.strip()
    except Exception:
        pass
    p.terminate()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()
    print('%s|%s|%s' % (name, code, ip), flush=True)

def main():
    full = '--full' in sys.argv
    tests = [
        ('N1 Reality-443', 'sb', sb_cfg(13001, vless_sb(IP, 443, UUID1, 'xtls-rprx-vision',
            reality_tls(KP443.get('pub443', ''), KP443.get('sid443', ''))))),
        ('N2 Argo-WS', 'sb', sb_cfg(13002, vless_sb(ARGO_HOST, 443, UUID3, '',
            {'enabled': True, 'server_name': ARGO_HOST}, {'type': 'ws', 'path': '/your-ws-path'}))),
        ('N3 XHTTP-8388', 'xr', xr_cfg(13003, {
            'protocol': 'vless',
            'settings': {'vnext': [{'address': IP, 'port': 8388, 'users': [{'id': UUID3, 'encryption': 'none', 'level': 0}]}]},
            'streamSettings': {'network': 'xhttp', 'security': 'reality',
                'realitySettings': {'serverName': REALITY_SNI, 'fingerprint': 'chrome',
                    'publicKey': KP8388.get('pub8388', ''), 'shortId': KP8388.get('sid8388', '')},
                'xhttpSettings': {'path': '/your-xhttp-path'}}})),
        ('N4 VPNGate-JP', 'sb', sb_cfg(13004, vless_sb(IP, 8444, UUID_MULTI, 'xtls-rprx-vision',
            reality_tls(KP8444.get('pub8444', ''), KP8444.get('sid8444', ''))))),
        ('N5 Hy2', 'sb', sb_cfg(13005, {
            'type': 'hysteria2', 'server': IP, 'server_port': 443, 'password': HY2PASS,
            'obfs': {'type': 'salamander', 'password': OBFS},
            'tls': {'enabled': True, 'server_name': 'your-hy2-domain.example.com', 'insecure': True, 'alpn': ['h3']}})),
    ]
    slots = list(range(1, 11)) if full else [1, 5, 9]
    for s in slots:
        tests.append(('multi-slot%d' % s, 'sb', sb_cfg(13010 + s, vless_sb(IP, 8444 + s, UUID_MULTI, 'xtls-rprx-vision',
            reality_tls(KP8444.get('pub8444', ''), KP8444.get('sid8444', ''))))))
    for name, engine, cfgobj in tests:
        port = cfgobj['inbounds'][0].get('listen_port') or cfgobj['inbounds'][0].get('port')
        test_one(name, engine, cfgobj, name.replace(' ', '_').replace('.', ''), port)

if __name__ == '__main__':
    main()
