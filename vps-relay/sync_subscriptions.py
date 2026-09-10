#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订阅同步器（唯一权威）——修复"小火箭每次刷新国旗/IP 都变"的问题
=================================================================
设计原则:
1. 主节点(443/8388/Hy2/Argo)永远固定: URI 不动, 国旗用 emoji 钉死(各 GeoIP 库对 VPS
   归属判定不一致, 客户端自己查 IP 会导致主节点国旗乱跳)。
2. VPNGate 槽位 1..10 对应本机固定端口 8445..8454(槽位号=身份, 永不变);
   备注里的国家码以"实测出口 IP 的归属"为准(看门狗每 2 分钟经隧道实测写入
   multi/<slot>/exit-scam.txt), 而不是 VPNGate 上游自己声明的国家(其公共节点存在
   链式转发, 声明国≠真实出口国)。实测缺失时回退到槽位声明国 multi/<slot>/cc。
3. 合并订阅 /sub/all 顺序固定: 4 主节点 -> 日本出口 -> 槽位 1..10, 绝不重排。
4. 只在内容真正变化时才写库并重启 s-ui(数据面 xray/sing-box 不受影响)。
5. 不硬编码任何 UUID/密钥: 全部从数据库里现存链接继承, 只改端口与备注。

被以下组件调用: multi_watchdog.sh(每轮)、merge_subs.py(cron 每小时兜底)、人工。
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import base64
import urllib.request
import urllib.parse

WORKDIR = '/opt/vpngate'
DB = '/usr/local/s-ui/db/s-ui.db'
SLOTS = 10
BASE_PORT = 8444                 # 8444=日本出口, 8444+i=槽位 i
LOG = os.path.join(WORKDIR, 'sync_subs.log')

# 主节点固定国旗: VPS 固定在日本机房(ip-api=JP/Akile), 统一钉 🇯🇵
MAIN_FLAG = 'JP'
# 主节点分组(只改备注加国旗, URI 原样保留)及其固定输出顺序
MAIN_GROUPS = ['yuwen2026', 'vless443']
MERGE_SOURCES = ['yuwen2026', 'vless443', 'vpngate-exit', 'vpngate-other']
MERGE_NAME = 'all'

FLAG_RE = re.compile(
    '[\U0001F1E6-\U0001F1FF\U0001F310\U0001F7E6-\U0001F7FF\u2600-\u27BF\ufe0f]+'
)


def log(msg):
    line = time.strftime('%F %T') + ' ' + msg
    print(line)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def flag_of(cc):
    """国家码 -> emoji 国旗; 未知给 🌐"""
    cc = (cc or '').strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        return '\U0001F310'  # globe
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)


def strip_flag(name):
    """去掉备注开头已有的 emoji 国旗, 保证幂等不堆叠"""
    s = (name or '').strip()
    s = FLAG_RE.sub('', s).strip()
    return s.lstrip('|- ').strip()


def named(uri, new_name):
    """替换 URI 的 #fragment 为新备注(保留其余全部参数)"""
    u = urllib.parse.urlsplit(uri)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, u.query, new_name))


def re_port(uri, new_port):
    """替换 URI 主机端口(只改 @host:port 的端口)"""
    u = urllib.parse.urlsplit(uri)
    host = u.hostname or ''
    userinfo = ''
    if u.username is not None:
        userinfo = urllib.parse.unquote(u.username)
        if u.password is not None:
            userinfo += ':' + urllib.parse.unquote(u.password)
        userinfo += '@'
    netloc = f'{userinfo}{host}:{new_port}'
    return urllib.parse.urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))


def measured_cc(slot):
    """槽位真实出口国: 优先 exit-scam.txt 第4列(实测), 回退 cc 文件(声明)"""
    p = os.path.join(WORKDIR, 'multi', str(slot), 'exit-scam.txt')
    try:
        parts = open(p, encoding='utf-8').read().split()
        if len(parts) >= 4 and re.fullmatch(r'[A-Za-z]{2}', parts[3]):
            return parts[3].upper(), 'measured'
    except OSError:
        pass
    try:
        cc = open(os.path.join(WORKDIR, 'multi', str(slot), 'cc'), encoding='utf-8').read().strip()
        cc = cc.rstrip('*').upper()
        if re.fullmatch(r'[A-Z]{2}', cc):
            return cc, 'declared'
    except OSError:
        pass
    return '??', 'unknown'


def load_items(db, name):
    row = db.execute('SELECT links FROM clients WHERE name=?', (name,)).fetchone()
    if not row or not row[0]:
        return []
    return json.loads(bytes(row[0]).decode())


def dump_items(items):
    return sqlite3.Binary(json.dumps(items, ensure_ascii=False).encode())


def build_main(items):
    """主节点: URI 不动, 备注统一钉国旗"""
    out = []
    for it in items:
        base = strip_flag(it.get('remark', '') or urllib.parse.unquote(
            urllib.parse.urlsplit(it.get('uri', '')).fragment or ''))
        remark = f'{flag_of(MAIN_FLAG)} {base}'
        uri = named(it['uri'], remark)
        out.append({'remark': remark, 'type': it.get('type', 'local'), 'uri': uri})
    return out


def build_exit_jp(template_items):
    it = template_items[0]
    base = strip_flag(it.get('remark', '')) or 'VPNGate-JP-Exit'
    remark = f'{flag_of("JP")} VPNGate-JP-Exit'
    return [{'remark': remark, 'type': 'local', 'uri': named(it['uri'], remark)}]


def build_other(template_items):
    """10 槽位: 模板继承(取任一现存链接的参数骨架), 端口固定 8445..8454,
    国家名以实测出口为准"""
    assert template_items, 'vpngate-other template missing'
    skeleton = template_items[0]['uri']
    out = []
    for i in range(1, SLOTS + 1):
        cc, src = measured_cc(i)
        remark = f'{flag_of(cc)} VPNGate-{cc}-Exit-{i}'
        uri = re_port(skeleton, BASE_PORT + i)
        uri = named(uri, remark)
        out.append({'remark': remark, 'type': 'local', 'uri': uri})
    return out


def restart_and_verify():
    subprocess.run(['systemctl', 'restart', 's-ui'], check=True, timeout=60)
    time.sleep(3)
    body = urllib.request.urlopen('http://127.0.0.1:2096/sub/' + MERGE_NAME, timeout=20).read()
    dec = base64.b64decode(body).decode('utf-8', 'replace')
    n = len([l for l in dec.splitlines() if l.strip()])
    return n, dec


def main():
    db = sqlite3.connect(DB)
    changed = []

    # 1) 主节点组: 只钉国旗
    for g in MAIN_GROUPS:
        old = load_items(db, g)
        new = build_main(old)
        if json.dumps(old, ensure_ascii=False, sort_keys=True) != \
           json.dumps(new, ensure_ascii=False, sort_keys=True):
            db.execute('UPDATE clients SET links=? WHERE name=?', (dump_items(new), g))
            changed.append(g)

    # 2) 日本出口(8444)
    jp_tpl = load_items(db, 'vpngate-exit')
    if jp_tpl:
        new_jp = build_exit_jp(jp_tpl)
        old_jp = jp_tpl
        if json.dumps(old_jp, ensure_ascii=False, sort_keys=True) != \
           json.dumps(new_jp, ensure_ascii=False, sort_keys=True):
            db.execute('UPDATE clients SET links=? WHERE name=?', (dump_items(new_jp), 'vpngate-exit'))
            changed.append('vpngate-exit')

    # 3) 多地区槽位(8445-8454, 国家=实测出口)
    other_tpl = load_items(db, 'vpngate-other')
    if not other_tpl:
        log('[ERROR] vpngate-other template absent, abort slot rebuild')
        db.close()
        sys.exit(2)
    old_other = other_tpl
    new_other = build_other(other_tpl)
    old_map = {strip_flag(x.get('remark', '')): x.get('remark', '') for x in old_other}
    for x in new_other:
        pre = old_map.get(strip_flag(x['remark']), '')
        if pre != x['remark']:
            log(f'name: {pre or "(none)"} -> {x["remark"]}')
    if json.dumps(old_other, ensure_ascii=False, sort_keys=True) != \
       json.dumps(new_other, ensure_ascii=False, sort_keys=True):
        db.execute('UPDATE clients SET links=? WHERE name=?', (dump_items(new_other), 'vpngate-other'))
        changed.append('vpngate-other')

    # 4) 合并订阅 all: 固定顺序
    old_all = load_items(db, MERGE_NAME)
    merged = []
    seen = set()
    for g in MERGE_SOURCES:
        for it in load_items(db, g):
            if it['uri'] not in seen:
                seen.add(it['uri'])
                merged.append(it)
    all_changed = json.dumps(old_all, ensure_ascii=False, sort_keys=True) != \
        json.dumps(merged, ensure_ascii=False, sort_keys=True)
    row = db.execute("SELECT config FROM clients WHERE name='vpngate-exit'").fetchone()
    config = row[0] if row else b'{}'
    db.execute("DELETE FROM clients WHERE name=?", (MERGE_NAME,))
    db.execute(
        "INSERT INTO clients (enable,name,config,inbounds,links,desc) VALUES (1,?,?,?,?,'merged: all nodes (fixed order)')",
        (MERGE_NAME, config, sqlite3.Binary(b'[]'), dump_items(merged)))
    db.commit()
    db.close()

    # 5) 仅当名字/链接/合并内容真的变了才重启 s-ui(数据面 xray/sing-box 不受影响)
    structural = [c for c in changed if not c.startswith('all')]
    if structural or all_changed:
        n, _ = restart_and_verify()
        log(f'changed={structural} all_changed={all_changed}; s-ui restarted; /sub/{MERGE_NAME} links={n}')
    else:
        log('no remark/link change, s-ui not restarted; all rebuilt in place')
    # 输出当前最终名单, 便于日志/调用方核对
    for it in merged:
        log('  ' + it['remark'])


if __name__ == '__main__':
    main()
