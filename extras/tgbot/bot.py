#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TG 掌上控制台 v3.1 —— 在 v3 基础上修复响应速度与重复消息:
  1) Telegram API 连接常驻 keep-alive(修复每次点击新建 TLS 握手的秒级延迟)
  2) 点击瞬间先 answerCallbackQuery 弹 toast(即时反馈)
  3) "message is not modified" 视为成功(修复重复点击弹出多条相同菜单)
  4) 1.2 秒内重复点击去重
其余机制(白名单/三级操作/告警/拨测/面板)同 v3。私有脚本仅存 VPS 本地。
"""
import base64, http.client, json, os, random, re, sqlite3, subprocess, threading, time, urllib.request

BASE = '/opt/tgbot'
OWNER = int(open(BASE + '/owner').read().strip())
TOKEN = open(BASE + '/token').read().strip()
STATE_F = BASE + '/state.json'
SUB_BASE = os.environ.get('VPNGATE_SUB_BASE', 'https://sub.example.com')
DB_F = BASE + '/data.db'
VG = '/opt/vpngate'
SVC_CORE = ['s-ui', 'xray', 'cloudflared', 'vpngate-tunnel', 'vpngate-sbexit']
LOCK = threading.RLock()

def log(msg):
    with open(BASE + '/bot.log', 'a') as f:
        f.write(time.strftime('%F %T ') + str(msg) + '\n')

def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def bar(pct, width=8):
    n = max(0, min(width, int(round(pct / 100.0 * width))))
    return '▮' * n + '░' * (width - n)

def load_state():
    with LOCK:
        try:
            return json.load(open(STATE_F))
        except Exception:
            return {}

def save_state(st):
    with LOCK:
        json.dump(st, open(STATE_F, 'w'), ensure_ascii=False, indent=1)

STATE = load_state()

def state_get(k, d=None):
    with LOCK:
        return STATE.get(k, d)

def state_set(k, v):
    with LOCK:
        STATE[k] = v
        save_state(STATE)

# ---------------- Telegram API(常驻 keep-alive 连接) ----------------
class TgApi:
    def __init__(self, token):
        self.host = 'api.telegram.org'
        self.token = token
        self.conn = None
        self.lock = threading.Lock()

    def _drop(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        self.conn = None

    def call(self, method, params=None, timeout=55):
        body = json.dumps(params or {})
        with self.lock:
            for _ in range(3):
                try:
                    if self.conn is None:
                        self.conn = http.client.HTTPSConnection(self.host, timeout=timeout)
                    self.conn.request('POST', '/bot%s/%s' % (self.token, method), body=body,
                                      headers={'Content-Type': 'application/json',
                                               'Connection': 'keep-alive'})
                    resp = self.conn.getresponse()
                    data = resp.read().decode()
                    if resp.status != 200:
                        log('tg %s HTTP %s: %s' % (method, resp.status, data[:200]))
                        if 'message is not modified' in data:
                            return {'ok': True, 'noop': True}   # 内容没变=已在目标界面,不算错误
                        self._drop()
                        if resp.status >= 500:
                            time.sleep(2)
                            continue
                        return None
                    return json.loads(data)
                except Exception as e:
                    log('tg %s conn err: %s' % (method, e))
                    self._drop()
                    time.sleep(2)
            return None

API = TgApi(TOKEN)

def send(text, kb=None):
    p = {'chat_id': OWNER, 'text': text[:4096], 'parse_mode': 'HTML',
         'link_preview_options': {'is_disabled': True}}
    if kb:
        p['reply_markup'] = {'inline_keyboard': kb}
    r = API.call('sendMessage', p)
    return (r or {}).get('result', {}).get('message_id')

def edit(mid, text, kb=None):
    if not mid:
        return False
    p = {'chat_id': OWNER, 'message_id': mid, 'text': text[:4096], 'parse_mode': 'HTML'}
    if kb:
        p['reply_markup'] = {'inline_keyboard': kb}
    r = API.call('editMessageText', p)
    return bool(r and r.get('ok'))

def ikb(rows):
    return [[{'text': t, 'callback_data': d} for t, d in row] for row in rows]

# ---------------- shell ----------------
def sh(cmd, timeout=60):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, '', str(e))

def svc_active(name):
    return sh('systemctl is-active %s' % name, 10).stdout.strip() == 'active'

def listening_ports():
    out = sh("ss -tln | awk '{print $4}' | grep -oE '[0-9]+$'", 15).stdout
    return set(int(x) for x in out.split())

def cpu_pct():
    def sample():
        for ln in open('/proc/stat'):
            if ln.startswith('cpu '):
                f = [int(x) for x in ln.split()[1:9]]
                return f[3] + f[4], sum(f)
    a = sample(); time.sleep(0.3); b = sample()
    tot = b[1] - a[1]; idl = b[0] - a[0]
    return int(100 * (1 - idl / tot)) if tot > 0 else 0

def mem_pct():
    mi = {}
    for ln in open('/proc/meminfo'):
        p = ln.split(':')
        if p[0] in ('MemTotal', 'MemAvailable'):
            mi[p[0]] = int(p[1].split()[0])
    return int(100 * (1 - mi['MemAvailable'] / mi['MemTotal']))

def disk_pct():
    st = os.statvfs('/')
    return int(100 * (1 - st.f_bfree / st.f_blocks))

def uptime_str():
    up = float(open('/proc/uptime').read().split()[0])
    return '%dh%02dm' % (int(up // 3600), int(up % 3600 // 60))

def read_exit(path):
    try:
        parts = open(path).read().split()
        return parts[0], (int(parts[1]) if len(parts) > 1 else None), (parts[3] if len(parts) > 3 else '')
    except Exception:
        return '', None, ''

def slot_cc(s):
    try:
        return open('%s/multi/%s/cc' % (VG, s)).read().strip().rstrip('*')
    except Exception:
        return '?'

def confirm_exit(st, tag, ip, cc, now, pend_ttl=300):
    """出口变更双确认去抖。同一新出口需连续两轮巡检(约40s)一致才确认,
    消除"换出去又换回/探测瞬时抖动"造成的误报。
    状态键: last_<tag>=已确认IP, lastc_<tag>=已确认实测国,
            pend_<tag>/pendc_<tag>/pendt_<tag>=待确认候选。
    返回 (event, old_cc, new_cc); event: 'country'=换国 / 'ip'=同国换IP / None=无变更。"""
    if not ip:
        return None, '', ''
    bk, ck = 'last_' + tag, 'lastc_' + tag
    pk, pck, ptk = 'pend_' + tag, 'pendc_' + tag, 'pendt_' + tag
    base = st.get(bk)
    if base is None:                       # 首次只建基线, 不报警
        st[bk], st[ck] = ip, cc or ''
        return None, '', cc or ''
    if ip == base:                        # 出口未变, 清掉待确认
        for k in (pk, pck, ptk):
            st.pop(k, None)
        return None, st.get(ck, ''), cc or ''
    if st.get(pk) == ip and (now - int(st.get(ptk, 0))) <= pend_ttl:
        old_cc = st.get(ck, '')           # 连续第二轮一致 -> 确认
        st[bk] = ip
        if cc:
            st[ck] = cc
        for k in (pk, pck, ptk):
            st.pop(k, None)
        if cc and old_cc and cc != old_cc:
            return 'country', old_cc, cc
        return 'ip', old_cc, cc or old_cc
    st[pk], st[pck], st[ptk] = ip, cc or '', now   # 新候选, 下轮确认
    return None, st.get(ck, ''), cc or ''

def main_ip():
    out = sh("ip -4 route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | cut -d' ' -f2", 10).stdout.strip()
    return out or os.environ.get('VPS_IP', '')  # 兜底: systemd EnvironmentFile 里导出 VPS_IP

# ---------------- SQLite 采样 ----------------
def db_init():
    c = sqlite3.connect(DB_F)
    c.execute('CREATE TABLE IF NOT EXISTS samples(ts INTEGER, cpu INTEGER, mem INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS traffic(ts INTEGER, dev TEXT, rx INTEGER, tx INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS traffic_daily(date TEXT, dev TEXT, rx INTEGER, tx INTEGER, PRIMARY KEY(date,dev))')
    c.commit(); c.close()

def net_counters():
    devs = {}
    for ln in open('/proc/net/dev'):
        p = ln.split(':')
        if len(p) != 2:
            continue
        dev = p[0].strip()
        if dev in ('lo',) or dev.startswith(('eth', 'ens', 'enp')) or dev == 'tun0' or re.match(r'^vpnm\d+$', dev):
            f = p[1].split()
            devs[dev] = (int(f[0]), int(f[8]))
    return devs

def sample_once():
    now = int(time.time())
    today = time.strftime('%F')
    c = cpu_pct(); m = mem_pct()
    conn = sqlite3.connect(DB_F)
    conn.execute('INSERT INTO samples(ts,cpu,mem) VALUES (?,?,?)', (now, c, m))
    conn.execute('DELETE FROM samples WHERE ts < ?', (now - 86400 * 2,))
    devs = net_counters()
    last = state_get('dev_last', {})
    for dev, (rx, tx) in devs.items():
        old = last.get(dev)
        if old:
            drx, dtx = rx - old[0], tx - old[1]
            if drx >= 0 and dtx >= 0:
                row = conn.execute('SELECT rx,tx FROM traffic_daily WHERE date=? AND dev=?', (today, dev)).fetchone()
                if row:
                    conn.execute('UPDATE traffic_daily SET rx=rx+?,tx=tx+? WHERE date=? AND dev=?', (drx, dtx, today, dev))
                else:
                    conn.execute('INSERT INTO traffic_daily(date,dev,rx,tx) VALUES (?,?,?,?)', (today, dev, drx, dtx))
    conn.commit(); conn.close()
    with LOCK:
        STATE['dev_last'] = {d: list(v) for d, v in devs.items()}
        save_state(STATE)

def fmt_bytes(n):
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return ('%.1f%s' % (n, u)) if u != 'B' else '%dB' % n
        n /= 1024.0
    return '%.1fPB' % n

def today_traffic_lines():
    today = time.strftime('%F')
    conn = sqlite3.connect(DB_F)
    rows = conn.execute('SELECT dev,rx,tx FROM traffic_daily WHERE date=? ORDER BY tx DESC', (today,)).fetchall()
    conn.close()
    out = []
    for dev, rx, tx in rows:
        if dev == 'tun0':
            nm = '🇯🇵 日本隧道'
        elif dev.startswith('vpnm'):
            nm = '🌏 ' + dev.replace('vpnm', '槽')
        else:
            nm = '🖥 主网卡'
        if tx + rx > 1024 * 1024:
            out.append('%s ↑%s ↓%s' % (nm, fmt_bytes(tx), fmt_bytes(rx)))
    return out[:6]

# ---------------- 拨测 ----------------
def dial_run(full=False):
    r = sh('python3 /opt/tgbot/dialtest.py %s' % ('--full' if full else ''), 300)
    res = []
    for ln in r.stdout.strip().splitlines():
        p = ln.split('|')
        if len(p) == 3:
            res.append((p[0], p[1], p[2]))
    return res

def dial_html(res):
    mip = main_ip()
    rows = []
    for name, code, ip in res:
        ok = code == '204'
        mark = '✅' if ok else '❌'
        exit_note = '' if ip == mip else ' · 出口 %s' % (esc(ip) if ip else '?')
        pre = '🇯🇵' if name.startswith(('N1', 'N2', 'N3', 'N4', 'N5')) else '🌏'
        rows.append('%s %-14s %s%s' % (mark, esc(name), code if not ok else '204 正常', exit_note))
    return '\n'.join(pre + ' ' + r for r in rows)

# ---------------- 报告 ----------------
def health_report(dial_lines=None):
    issues = []
    cpu, mem, disk = cpu_pct(), mem_pct(), disk_pct()
    t = ['<b>🩺 VPS 体检报告</b>  📅 ' + time.strftime('%m-%d %H:%M') + ' · 运行 ' + uptime_str()]
    t.append('🖥 CPU %s %d%%   💾 内存 %s %d%%' % (bar(cpu), cpu, bar(mem), mem))
    t.append('📦 磁盘 %s %d%%' % (bar(disk), disk))
    bad = [s for s in SVC_CORE if not svc_active(s)]
    if bad:
        t.append('❌ 服务未运行: ' + esc(', '.join(bad)))
        for s in bad:
            issues.append(('a:start:%s' % s, '▶️ 拉起 ' + s))
    else:
        t.append('✅ 核心服务 5/5 运行中')
    ports = listening_ports()
    miss = [p for p in (443, 8388, 8444, 2096) if p not in ports]
    t.append('🔌 端口监听: ' + ('✅ 全部正常' if not miss else '❌ 缺失 ' + ','.join(map(str, miss))))
    if dial_lines is not None:
        t.append('━━ 真实拨测 ━━')
        t.append(dial_lines)
        if dial_lines.count('❌'):
            issues.append(('health:fulldial', '🔄 全量重拨测(15 节点)'))
    if issues:
        t.append('━━ 发现 %d 个问题 👇' % len(issues))
    kb = [[{'text': tt, 'callback_data': cb}] for cb, tt in issues]
    kb.append([{'text': '🩺 真实拨测体检', 'callback_data': 'health:dial'},
               {'text': '🔄 重检', 'callback_data': 'health:quick'}])
    kb.append([{'text': '⬅️ 主菜单', 'callback_data': 'm:main'}])
    return '\n'.join(t), kb

# ---------------- 面板 ----------------
def panel_text():
    cpu, mem, disk = cpu_pct(), mem_pct(), disk_pct()
    e, sc, _ = read_exit(VG + '/exit-scam.txt')
    ok = sum(1 for s in range(1, 11) if svc_active('vpngate-tunnel@%d' % s))
    tr = today_traffic_lines()
    t = ['<b>📌 VPS 控制面板</b> · ' + time.strftime('%H:%M') + ' 更新 · 运行 ' + uptime_str()]
    t.append('🖥 CPU %s %d%%   💾 内存 %s %d%%' % (bar(cpu), cpu, bar(mem), mem))
    t.append('🇯🇵 日本出口: %s (欺诈分 %s)' % (esc(e or '无'), sc if sc is not None else '?'))
    t.append('🌏 多地区隧道: %d/10 正常' % ok)
    if tr:
        t.append('📊 今日流量:\n' + '\n'.join('  ' + x for x in tr))
    return '\n'.join(t)

PANEL_KB = ikb([[('🩺 体检', 'health:dial'), ('📈 趋势', 'm:trend')], [('🛠 管理', 'm:admin'), ('🔔 告警', 'm:alerts')]])

def send_panel():
    mid = send(panel_text(), PANEL_KB)
    if mid:
        state_set('panel_msg_id', mid)
        API.call('pinChatMessage', {'chat_id': OWNER, 'message_id': mid, 'disable_notification': True})
    return mid

def edit_panel():
    mid = state_get('panel_msg_id')
    if mid:
        edit(mid, panel_text(), PANEL_KB)

# ---------------- 趋势 ----------------
def trend_bars(col, hours=24, buckets=24):
    since = int(time.time()) - hours * 3600
    conn = sqlite3.connect(DB_F)
    rows = conn.execute('SELECT ts,%s FROM samples WHERE ts>=? ORDER BY ts' % col, (since,)).fetchall()
    conn.close()
    if not rows:
        return '(暂无采样数据,每5分钟自动采样)'
    step = 3600 * hours / buckets
    vals = []
    for b in range(buckets):
        lo = since + b * step
        vs = [v for ts, v in rows if lo <= ts < lo + step]
        vals.append(int(sum(vs) / len(vs)) if vs else -1)
    chars = '▁▂▃▄▅▆▇█'
    out = ''.join('·' if v < 0 else chars[min(7, v * 8 // 100)] for v in vals)
    known = [v for v in vals if v >= 0]
    return '%s\n峰值 %d%% · 最低 %d%%' % (out, max(known) if known else 0, min(known) if known else 0)

def trends_text():
    t = ['<b>📈 24 小时趋势</b>']
    t.append('🖥 CPU:\n' + trend_bars('cpu'))
    t.append('💾 内存:\n' + trend_bars('mem'))
    tr = today_traffic_lines()
    t.append('📊 今日流量:\n' + ('\n'.join('  ' + x for x in tr) if tr else '  暂无'))
    return '\n'.join(t)

# ---------------- 测速 ----------------
def speed_one(target_dev, label):
    cmd = "curl -s -o /dev/null -w '%{speed_download}' --max-time 15 %s 'https://speed.cloudflare.com/__down?bytes=30000000'" % (
        ('--interface ' + target_dev) if target_dev else '')
    r = sh(cmd, 25)
    try:
        return '%s: %.1f Mbps' % (label, float(r.stdout.strip()) * 8 / 1e6)
    except Exception:
        return '%s: 测速失败' % label

def speed_all(slot):
    out = [speed_one(None, '🚀 VPS 国际直连')]
    out.append(speed_one('tun0', '🇯🇵 日本隧道'))
    if slot:
        out.append(speed_one('vpnm%d' % slot, '🌏 槽位%d' % slot))
    return '\n'.join(out)

# ---------------- 告警引擎 ----------------
def notify(text, important=True):
    with open(BASE + '/alerts.log', 'a') as f:
        f.write(time.strftime('%F %T ') + text.replace('\n', ' | ') + '\n')
    lt = time.localtime()
    silent = state_get('silent', True) and (1 <= lt.tm_hour < 8)
    st = load_state()
    if important or (st.get('important', True) and not silent):
        send('🔔 ' + text)

def sample_alerts():
    st = load_state()
    now = time.time()
    out = sh("journalctl -u ssh --since '-40 seconds' --no-pager 2>/dev/null | grep 'Accepted'", 15).stdout
    for ln in out.strip().splitlines():
        m = re.search(r'Accepted \S+ for (\S+) from (\S+)', ln)
        if m and st.get('last_ssh_line') != ln:
            st['last_ssh_line'] = ln
            notify('🔑 SSH 新登录: %s 来自 %s' % (m.group(1), m.group(2)), important=True)
    tb = sh('fail2ban-client status sshd 2>/dev/null', 15).stdout
    m = re.search(r'Total banned:\s*(\d+)', tb)
    if m:
        cur = int(m.group(1))
        delta = cur - st.get('f2b_total', cur)
        if delta > 0:
            st['f2b_pending'] = st.get('f2b_pending', 0) + delta
        if st.get('f2b_pending', 0) > 0 and now - st.get('f2b_last', 0) >= 600:
            notify('🛡 fail2ban 新封禁 %d 个IP(累计 %d)' % (st['f2b_pending'], cur), important=False)
            st['f2b_pending'] = 0; st['f2b_last'] = now
        st['f2b_total'] = cur
    c = cpu_pct(); mp = mem_pct()
    st['cpu_hi'] = st.get('cpu_hi', 0) + 1 if c >= 80 else 0
    st['mem_hi'] = st.get('mem_hi', 0) + 1 if mp >= 80 else 0
    if st['cpu_hi'] >= 10 and now - st.get('cpu_alert_ts', 0) > 600:
        notify('⚠️ CPU 持续 5 分钟高于 80%(当前 %d%%)' % c, important=False)
        st['cpu_alert_ts'] = now
    if st['mem_hi'] >= 10 and now - st.get('mem_alert_ts', 0) > 600:
        notify('⚠️ 内存持续 5 分钟高于 80%(当前 %d%%)' % mp, important=False)
        st['mem_alert_ts'] = now
    for svc in SVC_CORE:
        n = sh('systemctl show -p NRestarts --value %s' % svc, 10).stdout.strip()
        key = 'nr_' + svc
        old = st.get(key)
        if old is not None and n.isdigit() and int(n) > int(old):
            notify('💥 %s 自动重启 %d 次(当前: %s)' % (svc, int(n) - int(old), svc_active(svc)), important=True)
        if n.isdigit():
            st[key] = n
    # 出口变更: 双确认去抖 + 只在"实测国家变化(换国)"时推送;
    # 同一国家内换 IP(候选轮换)只更新基线、不打扰(置顶面板仍实时可见)
    e, sc, ecc = read_exit(VG + '/exit-scam.txt')
    jev, jo, jn = confirm_exit(st, 'jp_exit', e, ecc, now)
    if jev == 'country':
        notify('🌏 日本住宅出口换国: %s→%s · %s (欺诈分 %s)' % (jo, jn, e, sc), important=False)
    changes = []
    silent_ip = 0
    for s in range(1, 11):
        me, msc, mcc = read_exit('%s/multi/%s/exit-scam.txt' % (VG, s))
        ev, old_c, new_c = confirm_exit(st, 'm%d' % s, me, mcc, now)
        if ev == 'country':
            changes.append('槽%d %s→%s · %s (欺诈分 %s)' % (s, old_c, new_c, me, msc))
        elif ev == 'ip':
            silent_ip += 1
    if changes and now - st.get('multi_last', 0) >= 600:
        notify('🌏 多地区出口"换国":\n' + '\n'.join(changes), important=False)
        st['multi_last'] = now
    st['multi_silent_ip'] = silent_ip
    lt = time.localtime()
    today = time.strftime('%F')
    if st.get('daily', True) and lt.tm_hour == 9 and st.get('last_daily') != today and lt.tm_min < 2:
        st['last_daily'] = today
        txt, kb = health_report()
        send('📰 <b>每日日报</b>\n' + txt, kb)
    save_state(st)

def alert_loop():
    time.sleep(8)
    st = load_state()
    e, _, ecc = read_exit(VG + '/exit-scam.txt')
    if e:
        st['last_jp_exit'] = e
        st['lastc_jp_exit'] = ecc
    for s in range(1, 11):
        me, _, mcc = read_exit('%s/multi/%s/exit-scam.txt' % (VG, s))
        if me:
            st['last_m%d' % s] = me
            st['lastc_m%d' % s] = mcc
    for svc in SVC_CORE:
        n = sh('systemctl show -p NRestarts --value %s' % svc, 10).stdout.strip()
        if n.isdigit():
            st['nr_' + svc] = n
    tb = sh('fail2ban-client status sshd 2>/dev/null', 15).stdout
    m = re.search(r'Total banned:\s*(\d+)', tb)
    if m:
        st['f2b_total'] = int(m.group(1))
    save_state(st)
    last_sample = 0.0
    while True:
        try:
            if time.time() - last_sample >= 300:
                sample_once()
                edit_panel()
                last_sample = time.time()
            sample_alerts()
        except Exception as ex:
            log('alert err: %s' % ex)
        time.sleep(20)

# ---------------- 新增:封禁/登录/诊断/备份/槽位 ----------------
def f2b_menu():
    out = sh('fail2ban-client status sshd', 15).stdout
    m = re.search(r'Total banned:\s*(\d+)', out)
    bl = re.search(r'Banned IP list:\s*(.*)', out)
    iplist = bl.group(1).split() if bl and bl.group(1).strip() else []
    lines = ['🛡 SSH 封禁管理', '当前封禁 %s 个 IP' % (m.group(1) if m else '?')]
    kb = [[('🔓 解封 ' + ip, 'q:unban:' + ip)] for ip in iplist[:10]]
    kb.append([('🔄 刷新', 'f2b')])
    kb.append([('⬅️ 返回', 'm:admin')])
    if iplist:
        lines.append('封禁列表(点下方按钮解封):')
        lines += ['  ' + ip for ip in iplist[:10]]
    else:
        lines.append('(当前无封禁)')
    return '\n'.join(lines), ikb(kb)

def ssh_log_text():
    out = sh("journalctl -u ssh --no-pager 2>/dev/null | grep 'Accepted' | tail -10", 15).stdout
    if not out.strip():
        return '🔑 暂无成功登录记录'
    lines = []
    for ln in out.strip().splitlines()[-10:]:
        m = re.search(r'(\w{3} \d+ \S+) .*Accepted \S+ for (\S+) from (\S+)', ln)
        if m:
            lines.append('%s  %s @ %s' % (m.group(1), m.group(2), m.group(3)))
    return '🔑 最近成功登录:\n' + ('\n'.join(lines) if lines else esc(out[-300:]))

def diag_text():
    targets = [('腾讯DNS', '119.29.29.29'), ('联通169骨干', '219.158.30.81'), ('阿里DNS', '223.5.5.5')]
    lines = ['🌐 线路诊断(从 VPS 测回程线路):']
    for name, ip in targets:
        r = sh('ping -c 10 -W 2 %s 2>/dev/null | tail -2' % ip, 30)
        loss = re.search(r'([\d.]+)% packet loss', r.stdout)
        avg = re.search(r'= [\d.]+/([\d.]+)/', r.stdout)
        lines.append('%s %s: 丢包 %s%% · 均值 %sms' % (name, ip, loss.group(1) if loss else '?', avg.group(1) if avg else '?'))
    lines.append('💡 联通骨干丢包高 = 晚高峰拥塞(切 N2 Argo 或等待);全部正常 = 检查节点本身')
    return '\n'.join(lines)

def top_text():
    mem = sh("ps aux --sort=-%mem | awk 'NR<=6{printf \"%-14s %s%%\\n\", $11, $4}'", 15).stdout
    cpu = sh("ps aux --sort=-%cpu | awk 'NR<=6{printf \"%-14s %s%%\\n\", $11, $3}'", 15).stdout
    return '🖥 内存占用 TOP5:\n' + mem + '\n\n🔥 CPU 占用 TOP5:\n' + cpu

def backup_file():
    ts = time.strftime('%Y%m%d-%H%M')
    f = '/opt/tgbot/vps-backup-%s.tar.gz' % ts
    sh('tar czf %s /usr/local/s-ui/db/s-ui.db /etc/xray/config.json /etc/nftables.conf '
       '/opt/vpngate/vpngate.env /opt/vpngate/sb-exit.json /opt/vpngate/auth.txt '
       '/root/kp-443.txt /root/kp-8388.txt /root/kp-8444.txt '
       '/etc/ssh/sshd_config.d/99-hardening.conf /etc/fail2ban/jail.d/sshd.local '
       '/etc/sysctl.d/ /etc/systemd/system/vpngate-tunnel@.service /etc/systemd/system/xray.service '
       '/etc/systemd/system/tgbot.service /etc/systemd/system/cloudflared.service 2>/dev/null' % f, 60)
    return f if os.path.exists(f) else None

def tg_file(path, caption):
    boundary = '----tgb%s' % ('%012x' % random.getrandbits(48))
    fn = os.path.basename(path)
    with open(path, 'rb') as fh:
        content = fh.read()
    body = b''
    for k, v in (('caption', caption), ('chat_id', str(OWNER))):
        body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode()
    body += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n' % (boundary, fn)).encode()
    body += content + ('\r\n--%s--\r\n' % boundary).encode()
    req = urllib.request.Request('https://api.telegram.org/bot%s/sendDocument' % TOKEN, data=body,
                                 headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r).get('ok', False)

def rotate_slot(s):
    sd = '%s/multi/%d' % (VG, s)
    total = int(sh("wc -l < %s/candidates.tsv" % sd, 10).stdout.strip() or 0)
    if total < 2:
        return '槽%d 候选不足(%d 个),无法轮换' % (s, total)
    try:
        idx = (int(open(sd + '/current.idx').read().strip() or 0) + 1) % total
    except Exception:
        idx = 0
    open(sd + '/current.idx', 'w').write(str(idx))
    sh('bash /opt/vpngate/multi_build.sh %d' % s, 30)
    sh('systemctl restart vpngate-tunnel@%d' % s, 30)
    time.sleep(3)
    return '槽%d 已轮换到候选 %d/%d' % (s, idx, total)

def slots_menu():
    rows = []
    for s in range(1, 11):
        cc = slot_cc(s)
        me, msc, mcc = read_exit('%s/multi/%s/exit-scam.txt' % (VG, s))
        label = cc if (not mcc or mcc == cc) else '%s/%s' % (cc, mcc)
        act = '🟢' if svc_active('vpngate-tunnel@%d' % s) else '🔴'
        rows.append([('槽%d %s %s %s' % (s, label, act, me or '无出口'), 'noop'), ('🔄', 'q:rot:%d' % s)])
    rows.append([('⬅️ 返回', 'node')])
    return ikb(rows)

# ---------------- 确认机制 ----------------
PENDING = {}

def ask_confirm(desc, fn, danger=False, warn=None):
    nonce = '%06x' % random.getrandbits(24)
    PENDING[nonce] = {'fn': fn, 'desc': desc, 'expires': time.time() + 30}
    if not danger:
        send('🟡 确认操作: <b>%s</b>' % esc(desc), ikb([[('✅ 确认执行', 'ok:' + nonce)], [('❌ 取消', 'cx:' + nonce)]]))
    else:
        if warn:
            send('⚠️ ' + warn)
        send('🟠 ' + esc(desc) + '\n\n此操作影响较大,请再次确认。',
             ikb([[('⚠️ 我已知晓,继续', 'd2:' + nonce)], [('❌ 取消', 'cx:' + nonce)]]))

def exec_pending(nonce):
    p = PENDING.pop(nonce, None)
    if not p:
        send('⏳ 确认已过期,请重新操作。')
        return
    if p.get('reboot'):
        open(BASE + '/reboot_pending', 'w').write(time.strftime('%F %T'))
        send('♻️ 正在重启整机... 恢复后机器人自动发回执。\n⚠️ 3 分钟未收到回执 → 服务商面板 VNC。')
        time.sleep(1)
        sh('systemctl reboot', 10)
        return
    send('⏳ 正在执行: <b>%s</b> ...' % esc(p['desc']))
    out = p['fn']()
    send('✅ 完成: <b>%s</b>\n%s' % (esc(p['desc']), esc(str(out))[:500]))

def do_action(key):
    if key.startswith('restart:'):
        name = key.split(':', 1)[1]
        if name == 'multi':
            ok = sum(1 for s in range(1, 11) if sh('systemctl restart vpngate-tunnel@%d' % s, 30).returncode == 0)
            return '多地区隧道重启: %d/10 成功' % ok
        sh('systemctl restart %s' % name, 60)
        return ('✅ %s 已重启' % name) if svc_active(name) else ('❌ %s 重启失败' % name)
    if key == 'startall':
        sh('systemctl start s-ui xray cloudflared vpngate-tunnel vpngate-sbexit')
        for s in range(1, 11):
            sh('systemctl start vpngate-tunnel@%d' % s, 20)
        return '全部代理已启动'
    if key == 'stopall':
        sh('systemctl stop xray vpngate-tunnel vpngate-sbexit')
        for s in range(1, 11):
            sh('systemctl stop vpngate-tunnel@%d' % s, 20)
        return '全部代理已停止'
    if key == 'memclean':
        sh('sync; echo 3 > /proc/sys/vm/drop_caches', 15)
        return '内存已清理,当前 %d%%' % mem_pct()
    if key == 'merge':
        return act_run('python3 /opt/vpngate/merge_subs.py', 120)
    if key == 'jp:strict':
        return act_run('bash /opt/vpngate/pick_strict.sh', 420)
    if key == 'multi:watch':
        return act_run('bash /opt/vpngate/multi_watchdog.sh', 420)
    return '未知动作'

def act_run(cmd, timeout=90):
    r = sh(cmd, timeout)
    return (r.stdout + r.stderr).strip()[-800:] or '(无输出)'

def sub_check():
    exp = {'all': 15, 'yuwen2026': 3, 'vless443': 1, 'vpngate-exit': 1, 'vpngate-other': 10}
    lines = []
    for name, want in exp.items():
        r = sh('curl -s -m 10 "https://sub.example.com/sub/%s"' % name, 20)
        try:
            dec = base64.b64decode(r.stdout).decode()
            n = len([l for l in dec.splitlines() if l.strip()])
            lines.append('%s /sub/%s: %d 条 %s' % ('✅' if n == want else '❌', name, n, '(期望%d)' % want if n != want else ''))
        except Exception:
            lines.append('❌ /sub/%s: 无法解析' % name)
    return '\n'.join(lines)

def node_status():
    lines = ['📡 当前全部出口']
    e, sc, _ = read_exit(VG + '/exit-scam.txt')
    lines.append('🇯🇵 日本: %s (欺诈分 %s)' % (e or '无', sc))
    for s in range(1, 11):
        me, msc, mcc = read_exit('%s/multi/%s/exit-scam.txt' % (VG, s))
        pin = slot_cc(s)
        where = pin if (not mcc or mcc == pin) else '%s→实测%s' % (pin, mcc)
        lines.append('🌐 槽%-2d %s: %s (欺诈分 %s)' % (s, where, me or '无', msc))
    return '\n'.join(lines)

# ---------------- 菜单 ----------------
M_MAIN = [[('🩺 一键体检', 'health:dial'), ('📊 实时面板', 'panel')],
          [('🛠 管理', 'm:admin'), ('📈 趋势测速', 'm:trend')],
          [('📩 订阅链接', 'subs')],
          [('🔔 告警中心', 'm:alerts'), ('ℹ️ 帮助', 'help')]]

SUB_TEXT = ('📩 <b>订阅链接</b>(粘贴到 v2rayN / Shadowrocket 后点"更新订阅")\n\n'
            '⭐ <b>全部 15 个节点(推荐用这条)</b>\n'
            '<code>https://sub.example.com/sub/all</code>\n\n'
            '<b>单独订阅</b>\n'
            '· 主节点443: <code>https://sub.example.com/sub/vless443</code>\n'
            '· 三节点组: <code>https://sub.example.com/sub/yuwen2026</code>\n'
            '· VPNGate日本: <code>https://sub.example.com/sub/vpngate-exit</code>\n'
            '· 多地区×10: <code>https://sub.example.com/sub/vpngate-other</code>\n\n'
            '💡 换新设备:把 ⭐ 那条链接粘进客户端即可\n'
            '💡 链接为 HTTPS 加密传输,可放心保存')
SUB_TEXT = SUB_TEXT.replace('https://sub.example.com', SUB_BASE)

def m_admin():
    return ikb([[('🖥 服务管理', 'svc')], [('🌏 节点切换', 'node')], [('🛡 封禁管理', 'f2b')],
                [('🌐 线路诊断', 'diag')], [('🔑 SSH登录记录', 'ssh:log')], [('🖥 进程TOP', 'top')],
                [('📩 订阅链接', 'subs')], [('📦 一键备份', 'q:backup')], [('📥 订阅自检', 'q:geocheck')],
                [('🧹 清理内存', 'q:memclean')], [('🧨 维护模式', 'm:maint')], [('⬅️ 返回', 'm:main')]])
def svc_menu():
    rows = [[('▶️ ' + s, 'q:restart:' + s)] for s in SVC_CORE]
    rows.append([('▶️ 多地区隧道全部重启', 'q:restart:multi')])
    rows.append([('⬅️ 返回', 'm:admin')])
    return ikb(rows)
def node_menu():
    return ikb([[('🇯🇵 日本: 换最干净出口', 'q:jp:strict')],
                [('🌏 多地区: 自动巡检一轮', 'q:multi:watch')],
                [('🛠 多地区槽位轮换', 'slots')],
                [('📡 查看当前全部出口', 'node:status')],
                [('⬅️ 返回', 'm:admin')]])
def maint_menu():
    return ikb([[('⛔ 停止全部代理', 'd:stopall')], [('▶️ 启动全部代理', 'a:startall')],
                [('♻️ 重启整机', 'd:reboot')], [('⬅️ 返回', 'm:admin')]])
def trend_menu():
    return ikb([[('📈 24 小时趋势', 'trend')], [('🚀 测速', 'm:speed')], [('⬅️ 返回', 'm:main')]])
def speed_menu():
    rows = [[('🚀 VPS 国际直连', 'sp:0')], [('🇯🇵 日本隧道', 'sp:tun0')]]
    row = []
    for s in range(1, 11):
        row.append(('🌏槽%d' % s, 'sp:vpnm%d' % s))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([('⬅️ 返回', 'm:trend')])
    return ikb(rows)
def alerts_menu():
    imp = '开' if state_get('important', True) else '关'
    dai = '开' if state_get('daily', True) else '关'
    sil = '开' if state_get('silent', True) else '关'
    return ikb([[('🟡 重要告警: ' + imp, 't:important')],
                [('⚪ 每日日报: ' + dai, 't:daily')],
                [('🌙 静默时段(1-8点): ' + sil, 't:silent')],
                [('📜 最近告警', 'alerts:log')], [('⬅️ 返回', 'm:main')]])
HELP = ('📖 <b>使用说明</b>\n\n'
        '🩺 <b>一键体检</b>: 真实拨测全部节点(逐节点实测 204),发现问题给修复按钮。\n\n'
        '📊 <b>实时面板</b>: 已置顶的消息每小时自动刷新,随时打开 TG 看最新状态。\n\n'
        '🛠 <b>管理</b>: 服务重启 / 换节点 / 订阅自检 / 维护模式(重启整机等高危操作)。\n\n'
        '📈 <b>趋势测速</b>: 24 小时 CPU/内存趋势、各节点真实测速。\n\n'
        '🛡 <b>封禁管理</b>: 查看/解封 fail2ban 封禁的 IP(误封自救)。\n'
        '🌐 <b>线路诊断</b>: 一键判断卡顿是线路拥塞还是 VPS 问题。\n'
        '📦 <b>一键备份</b>: 配置打包发到本对话(含凭据,勿转发)。\n\n'
        '🆘 <b>应急三步</b>:\n1. 体检 → 看哪个❌ → 点修复按钮\n'
        '2. 修不好 → 管理里重启对应服务\n'
        '3. 全没反应 → 服务商面板 VNC(死机只能这样救)\n\n'
        '⚠️ 重启整机后 3 分钟没收到机器人回执 → 服务商面板。')

# ---------------- 更新处理 ----------------
LAST_CB = {'data': '', 'ts': 0.0}
SLOW_OPS = ('health:dial', 'health:fulldial', 'trend', 'node:status', 'alerts:log', 'diag')  # + sp:/q: 前缀在 route 内处理

def route_cb(data, qid, mid):
    now = time.time()
    if data == LAST_CB['data'] and now - LAST_CB['ts'] < 1.2:
        API.call('answerCallbackQuery', {'callback_query_id': qid})
        return
    LAST_CB['data'] = data; LAST_CB['ts'] = now
    toast = None
    if data in SLOW_OPS or data.startswith('sp:'):
        toast = '⏳ 已开始,结果稍后更新'
    API.call('answerCallbackQuery', {'callback_query_id': qid, 'text': toast or '✓'})

    def show(text, kb=None):
        if not edit(mid, text, kb):
            send(text, kb)

    if data in ('m:main', '/start'):
        show('🤖 <b>TG 掌上控制台</b>\n有事先按 🩺 体检,其他的点开就会用。', ikb(M_MAIN))
    elif data == 'subs':
        send(SUB_TEXT)   # 单独发一条,方便长按复制
    elif data == 'help':
        show(HELP)
    elif data == 'health:quick':
        txt, kb = health_report()
        show(txt, kb)
    elif data == 'health:dial':
        show('⏳ <b>真实拨测中</b>(逐节点实测,约 40 秒)...')
        def run():
            res = dial_run(full=False)
            txt, kb = health_report(dial_html(res))
            edit(mid, txt, kb)
        threading.Thread(target=run, daemon=True).start()
    elif data == 'health:fulldial':
        show('⏳ <b>全量拨测中</b>(15 节点,约 90 秒)...')
        def runf():
            res = dial_run(full=True)
            txt, kb = health_report(dial_html(res))
            edit(mid, txt, kb)
        threading.Thread(target=runf, daemon=True).start()
    elif data == 'panel':
        mid2 = send_panel()
        show('📌 面板已发送并置顶(每小时自动刷新)。' if mid2 else '面板发送失败。')
    elif data == 'm:admin':
        show('🛠 管理', m_admin())
    elif data == 'm:alerts':
        show('🔔 告警中心', alerts_menu())
    elif data == 'm:maint':
        show('🧨 维护模式(高风险操作)', maint_menu())
    elif data == 'm:trend':
        show('📈 趋势与测速', trend_menu())
    elif data == 'm:speed':
        show('🚀 测速(15 秒下载实测)', speed_menu())
    elif data == 'svc':
        show('🖥 服务管理(点服务名重启)', svc_menu())
    elif data == 'node':
        show('🌏 节点切换', node_menu())
    elif data == 'node:status':
        show('⏳ 探测中...')
        threading.Thread(target=lambda: send(node_status()), daemon=True).start()
    elif data == 'trend':
        show('⏳ 统计中...')
        threading.Thread(target=lambda: send(trends_text()), daemon=True).start()
    elif data.startswith('sp:'):
        dev = data[3:]
        show('⏳ 测速中(15 秒下载实测)...')
        threading.Thread(target=lambda: send('🚀 测速结果\n' + speed_all(None if dev == '0' else dev)), daemon=True).start()
    elif data == 'f2b':
        text, kb = f2b_menu()
        show(text, kb)
    elif data == 'diag':
        show('⏳ 线路诊断中(ping 3 目标 ×10 次,约 30 秒)...')
        threading.Thread(target=lambda: send(diag_text()), daemon=True).start()
    elif data == 'ssh:log':
        show('⏳ 读取中...')
        threading.Thread(target=lambda: send(ssh_log_text()), daemon=True).start()
    elif data == 'top':
        show(top_text())
    elif data == 'slots':
        show('🛠 多地区槽位(🔄 = 轮换该槽出口)', slots_menu())
    elif data == 'noop':
        pass
    elif data.startswith('q:rot:'):
        s = int(data[6:])
        ask_confirm('轮换槽 %d 出口(切下一候选)' % s, lambda s=s: rotate_slot(s), danger=False)
    elif data.startswith('q:unban:'):
        ip = data[8:]
        ask_confirm('解封 IP ' + ip, lambda ip=ip: act_run('fail2ban-client set sshd unbanip ' + ip, 15), danger=False)
    elif data == 'q:backup':
        def do_backup():
            f = backup_file()
            if not f:
                send('❌ 备份打包失败')
                return
            okf = tg_file(f, '📦 VPS 配置备份 %s(含敏感凭据,勿转发)' % time.strftime('%F %H:%M'))
            send('✅ 备份文件已发到上方。' if okf else '❌ 备份文件发送失败')
        ask_confirm('打包关键配置并上传到此对话(含 UUID/密钥等敏感信息,勿转发)',
                    lambda: threading.Thread(target=do_backup, daemon=True).start(), danger=False)
    elif data.startswith('q:'):
        key = data[2:]
        desc = {'restart:' + s: '重启服务 ' + s for s in SVC_CORE}
        desc['restart:multi'] = '重启全部 10 条多地区隧道'
        desc['jp:strict'] = '日本槽自动挑选最干净出口(1-4 分钟)'
        desc['multi:watch'] = '多地区自动巡检一轮(1-2 分钟)'
        desc['geocheck'] = '订阅自检(HTTPS)'
        desc['memclean'] = '清理内存缓存'
        ask_confirm(desc.get(key, key), lambda k=key: do_action(k), danger=False)
    elif data.startswith('a:'):
        key = data[2:]
        if key in ('jp:strict', 'multi:watch', 'merge'):
            desc = {'jp:strict': '日本槽挑选最干净出口', 'multi:watch': '多地区自动巡检', 'merge': '重建合并订阅'}[key]
            ask_confirm(desc, lambda k=key: do_action(k), danger=False)
        elif key.startswith('start:'):
            s = key.split(':', 1)[1]
            ask_confirm('拉起服务 ' + s, lambda s=s: do_action('restart:' + s), danger=False)
        elif key == 'svc:restart-all':
            def ra():
                return do_action('restart:s-ui') + '\n' + do_action('restart:xray') + '\n' + do_action('restart:multi')
            ask_confirm('重启全部核心服务', ra, danger=False)
        elif key == 'memclean':
            ask_confirm('清理内存缓存', lambda: do_action('memclean'), danger=False)
        elif key == 'startall':
            do_action('startall')
            send('✅ 全部代理服务已启动')
    elif data.startswith('d:'):
        what = data[2:]
        if what == 'reboot':
            ask_confirm('重启整机(全部节点断线 1-2 分钟)', lambda: do_action('reboot'), danger=True,
                        warn='⚠️ 重启后 3 分钟内未收到机器人回执 → 请到服务商面板 VNC 登录救援。')
        elif what == 'stopall':
            ask_confirm('停止全部代理服务(所有节点断线,面板保留)', lambda: do_action('stopall'), danger=True)
    elif data.startswith('ok:'):
        exec_pending(data[3:])
    elif data.startswith('cx:'):
        PENDING.pop(data[3:], None)
        send('❌ 已取消。')
    elif data.startswith('d2:'):
        nonce = data[3:]
        p = PENDING.get(nonce)
        if not p or time.time() > p['expires']:
            PENDING.pop(nonce, None)
            send('⏳ 确认已过期,请重新操作。')
            return
        if p.get('reboot'):
            open(BASE + '/reboot_pending', 'w').write(time.strftime('%F %T'))
            send('♻️ 正在重启整机... 恢复后自动发回执。\n⚠️ 3 分钟未收到回执 → 服务商面板 VNC。')
            time.sleep(1)
            sh('systemctl reboot', 10)
        else:
            send('⏳ 正在执行...')
            out = p['fn']()
            send('✅ 完成:\n' + esc(str(out))[:600])
    elif data.startswith('t:'):
        key = data[2:]
        st = load_state()
        st[key] = not st.get(key, True)
        save_state(st)
        show('🔔 告警中心', alerts_menu())
    elif data == 'alerts:log':
        show('⏳ 读取中...')
        threading.Thread(target=lambda: send('📜 最近告警:\n' + esc(act_run('tail -20 %s/alerts.log 2>/dev/null || echo "(暂无告警记录)"' % BASE, 15))), daemon=True).start()
    else:
        show('未知按钮,请点 ⬅️ 返回主菜单。')

def handle_update(u):
    msg = u.get('message') or {}
    cq = u.get('callback_query') or {}
    if cq:
        chat = (cq.get('message') or {}).get('chat', {}).get('id')
        if chat != OWNER:
            return
        mid = (cq.get('message') or {}).get('message_id')
        route_cb(cq.get('data', ''), cq.get('id'), mid)
    elif msg:
        if msg.get('chat', {}).get('id') != OWNER:
            return
        txt = (msg.get('text') or '').strip()
        if '订阅' in txt or txt.lower() in ('sub', '链接'):
            send(SUB_TEXT)
        else:
            send('🤖 点按钮操作 👇\n(要订阅链接: 点 📩 或直接发"订阅")', ikb(M_MAIN))

def main():
    log('=== bot v3.1 starting ===')
    API.call('deleteWebhook')
    me = API.call('getMe')
    log('bot: %s' % (me or {}).get('result', {}).get('username', '?'))
    db_init()
    if os.path.exists(BASE + '/reboot_pending'):
        ts = open(BASE + '/reboot_pending').read()
        os.remove(BASE + '/reboot_pending')
        txt, kb = health_report()
        send('✅ <b>VPS 已重启完成</b>(重启时间: %s)\n\n' % esc(ts) + txt, kb)
    else:
        send_panel()
        send('🤖 <b>TG 掌上控制台 v3.1 已上线</b>(响应速度优化)\n点按钮操作,常用 🩺 一键体检。', ikb(M_MAIN))
    threading.Thread(target=alert_loop, daemon=True).start()
    offset = 0
    while True:
        r = API.call('getUpdates', {'offset': offset, 'timeout': 50}, timeout=60)
        if not r:
            time.sleep(5)
            continue
        for u in r.get('result', []):
            offset = u['update_id'] + 1
            try:
                handle_update(u)
            except Exception as e:
                log('update err: %s' % e)

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'health':
        txt, _ = health_report()
        print(re.sub(r'<[^>]+>', '', txt))
    else:
        main()
