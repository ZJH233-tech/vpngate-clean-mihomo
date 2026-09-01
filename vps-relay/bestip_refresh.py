#!/usr/bin/env python3
# Authors: @ZJH233-tech & Doubao AI（配套 vpngate-clean-mihomo Worker）
# VPNGate 实时优选器（VPS 转换层 / 可独立运行）
# 1) 数据源：优先自建 Cloudflare Worker /api/servers（已解析、带缓存、带 IP 纯净度画像），
#    未配置 Worker 或其不可用时回源 VPNGate 官方 CSV。
# 2) 默认“纯净优先”：非代理/非机房/clean>=50 的干净住宅/ISP 节点排最前，
#    官方公共集群(已知公共代理,风险较高)排候选池后部兜底；组内按 score->ping->speed。
#    设环境变量 VPNGATE_CLEAN=0 可回到速度优先。
# 3) 重写候选池 nodes/*.ovpn + candidates.tsv（供 watchdog / build_running 使用）。
# 4) 稳定性优先：定时刷新只更新候选池，绝不主动重连；仅当 watchdog 判定不健康并显式
#    --switch-top 时，才平滑切到实时榜首。
#
# 配置（环境变量，或写进 $VPNGATE_DIR/vpngate.env，每行 KEY=VALUE）：
#   VPNGATE_DIR    工作目录，默认 /opt/vpngate
#   VPNGATE_WORKER 自建 Worker 的 /api/servers 地址；留空则只用官方 CSV
#   VPNGATE_CC     限定国家(两位代码)，默认 JP；置空表示全球
#   VPNGATE_CLEAN  1=纯净优先(默认)，0=速度优先
#   POOL           候选池大小，默认 12
import sys, os, csv, json, base64, subprocess, urllib.request, io, time

WORKDIR = os.environ.get('VPNGATE_DIR', '/opt/vpngate')


def _load_env(path):
    """从 KEY=VALUE 文件加载配置（不覆盖已存在的环境变量），文件缺失则忽略。"""
    try:
        with open(path) as ef:
            for ln in ef:
                ln = ln.strip()
                if not ln or ln.startswith('#') or '=' not in ln:
                    continue
                k, v = ln.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_env(os.path.join(WORKDIR, 'vpngate.env'))
os.makedirs(WORKDIR, exist_ok=True)
os.chdir(WORKDIR)

WORKER = os.environ.get('VPNGATE_WORKER', '').strip().rstrip('/')
OFFICIAL = 'https://www.vpngate.net/api/iphone/'
OFFICIAL_PREFIX = '219.100.37.'   # VPNGate 官方日本公共集群(SoftEther 研究机构 ASN)
MIN_SPEED = 3_000_000
try:
    POOL = max(3, int(os.environ.get('POOL', '12')))
except ValueError:
    POOL = 12
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}


def log(msg):
    line = time.strftime('%F %T') + ' ' + msg
    print(line)
    with open('bestip.log', 'a') as f:
        f.write(line + '\n')


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'ignore')


def num(x):
    try:
        return float(x)
    except Exception:
        return -1.0


def load_rows():
    """返回统一结构 [{ip,cc,score,ping,speed,b64,src,risk,clean,proxy,hosting,grade,isp}]。"""
    # 1) 自建 Worker（带纯净度画像）
    if WORKER:
        try:
            d = json.loads(http_get(WORKER))
            if not d.get('error') and d.get('servers'):
                rows = []
                for s in d['servers']:
                    q = s.get('quality') or {}
                    rows.append(dict(ip=s['ip'], cc=s.get('countryShort', ''), score=num(s.get('score')),
                                     ping=num(s.get('ping')), speed=num(s.get('speed')),
                                     b64=s.get('configDataBase64', ''), src='worker',
                                     risk=q.get('risk'), clean=q.get('clean'),
                                     proxy=bool(q.get('proxy')), hosting=bool(q.get('hosting')),
                                     grade=q.get('grade'), isp=q.get('isp', '')))
                log('source=worker count=%d cached=%s' % (len(rows), d.get('cached')))
                return rows
        except Exception as e:
            log('worker failed, fallback to official: %s' % e)
    # 2) 官方 CSV 兜底
    text = http_get(OFFICIAL)
    rows = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) == 15 and r[1].count('.') == 3 and len(r[14]) > 1000:
            rows.append(dict(ip=r[1], cc=r[6], score=num(r[2]), ping=num(r[3]),
                             speed=num(r[4]), b64=r[14], src='official',
                             risk=None, clean=None, proxy=False, hosting=False))
    log('source=official count=%d' % len(rows))
    return rows


def ovpn_proto_tcp(cfg):
    for ln in cfg.splitlines():
        t = ln.strip().lower()
        if t.startswith('proto '):
            return t.split()[1].startswith('tcp')
    return True  # 缺省按 tcp 处理


def rank(rows):
    pool = []
    CC = os.environ.get('VPNGATE_CC', 'JP').upper()  # 默认日本; VPNGATE_CC= 空则全球
    for r in rows:
        if CC and (r.get('cc') or '').upper() != CC:
            continue
        if r['speed'] and r['speed'] < MIN_SPEED:
            continue
        try:
            cfg = base64.b64decode(''.join(r['b64'].split())).decode('utf-8', 'ignore')
        except Exception:
            continue
        if '<ca>' not in cfg:
            continue
        if not ovpn_proto_tcp(cfg):
            continue
        r['cfg'] = cfg
        pool.append(r)
    clean_first = os.environ.get('VPNGATE_CLEAN', '1') != '0'

    def key(r):
        ping = r['ping'] if r['ping'] and r['ping'] > 0 else 99999
        if clean_first and r.get('risk') is not None:
            clean = r.get('clean') or 0
            if (not r.get('proxy')) and (not r.get('hosting')) and clean >= 50:
                ctier = 0                      # 干净住宅/ISP
            elif clean >= 50:
                ctier = 1                      # 中等
            else:
                ctier = 2                      # 高风险(已知代理/机房), 兜底
            return (ctier, -r['score'], ping, -r['speed'])
        official = 0 if r['ip'].startswith(OFFICIAL_PREFIX) else 1
        return (official, -r['score'], ping, -r['speed'])

    pool.sort(key=key)
    seen, clean_l, safe_l = set(), [], []

    def is_clean(r):
        if not clean_first or r.get('risk') is None:
            return not r['ip'].startswith(OFFICIAL_PREFIX)
        c = r.get('clean') or 0
        return (not r.get('proxy')) and (not r.get('hosting')) and c >= 50

    for r in pool:
        if r['ip'] in seen:
            continue
        seen.add(r['ip'])
        (clean_l if is_clean(r) else safe_l).append(r)

    if clean_first:
        # 前 POOL-2 个给干净节点, 末尾保留 2 个高风险(官方集群,几乎100%可连)最终兜底
        head_n = max(1, POOL - 2)
        out = clean_l[:head_n]
        official_l = [r for r in safe_l if r['ip'].startswith(OFFICIAL_PREFIX)]
        other_safe = [r for r in safe_l if not r['ip'].startswith(OFFICIAL_PREFIX)]
        for r in official_l + other_safe:
            if len(out) >= POOL:
                break
            out.append(r)
        for r in clean_l[head_n:]:          # 干净节点不足时用剩余干净节点补齐
            if len(out) >= POOL:
                break
            out.append(r)
    else:
        out = (clean_l + safe_l)[:POOL]
    return out


def write_pool(top):
    os.makedirs('nodes', exist_ok=True)
    for f in os.listdir('nodes'):
        if f.endswith('.ovpn'):
            os.remove(os.path.join('nodes', f))
    with open('candidates.tsv', 'w') as out:
        for i, r in enumerate(top):
            fn = 'nodes/%02d_%s.ovpn' % (i, r['ip'])
            with open(fn, 'w') as cf:
                cf.write(r['cfg'])
            out.write('%02d\t%s\t%s\t%s\t%s\t%s/%s\n'
                      % (i, r['ip'], r['cc'], int(r['score']), int(r['speed']), WORKDIR, fn))


def current_remote():
    try:
        for ln in open('running.ovpn'):
            if ln.strip().startswith('remote '):
                return ln.split()[1]
    except OSError:
        pass
    return None


def apply_top(top, force_switch=False):
    # 定时刷新只维护候选池与 idx，绝不主动重连；仅 --switch-top 时切到实时榜首
    cur = current_remote()
    top_ips = [r['ip'] for r in top]
    new_idx = top_ips.index(cur) if cur in top_ips else 0
    with open('current.idx', 'w') as f:
        f.write('%02d' % new_idx)
    best = top[0]
    qtag = ('clean=%s/risk=%s' % (best.get('clean'), best.get('risk'))) if best.get('risk') is not None else 'quality=n/a'
    with open('current-best.txt', 'w') as f:
        f.write('best=%s score=%d ping=%s speed=%.1fMbps %s isp=%s cur=%s\n' % (
            best['ip'], best['score'], best['ping'], best['speed'] / 1e6, qtag, best.get('isp', ''), cur))
    if force_switch:
        target = best['ip']
        if cur == target:
            log('SWITCH skipped: already on best %s' % cur)
            return
        subprocess.run([os.path.join(WORKDIR, 'build_running.sh')], check=True)
        subprocess.run(['systemctl', 'restart', 'vpngate-tunnel'], check=True)
        log('SWITCH(FORCE) %s -> %s (best score=%d ping=%s speed=%.1fMbps)'
            % (cur, target, best['score'], best['ping'], best['speed'] / 1e6))
    else:
        where = ('rank #%d' % (new_idx + 1)) if cur in top_ips else 'out-of-pool(keep until unhealthy)'
        log('KEEP %s (%s, best=%s) pool refreshed %d, no proactive reconnect'
            % (cur, where, best['ip'], len(top)))


if __name__ == '__main__':
    force = '--switch-top' in sys.argv
    rows = load_rows()
    top = rank(rows)
    if not top:
        log('no usable candidates, keep current')
        sys.exit(0)
    write_pool(top)
    apply_top(top, force_switch=force)
