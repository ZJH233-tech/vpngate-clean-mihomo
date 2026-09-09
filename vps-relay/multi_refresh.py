#!/usr/bin/env python3
# 多地区出口候选刷新:从 Worker 拉纯净度列表,排除日本,按国家分组取前10国,
# 每国取前5候选写入 multi/{slot}/,供 vpngate-tunnel@{slot} 与 multi_watchdog 使用。
# 稳定性优先:只刷新候选文件,不主动重启隧道(切换由 multi_watchdog 决定)。
import sys, os, json, base64, urllib.request, time

WORKDIR = '/opt/vpngate'
SLOTS = 10
PER_COUNTRY = 5
EXCLUDE_CC = {'JP'}
OFFICIAL_PREFIX = '219.100.37.'
MIN_SPEED = 3_000_000
WORKER = 'https://vpngate-sub.REDACTED.DOMAIN/api/servers'
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}

def log(msg):
    line = time.strftime('%F %T') + ' ' + msg
    print(line)
    with open(os.path.join(WORKDIR, 'multi_refresh.log'), 'a') as f:
        f.write(line + '\n')

def ovpn_proto_tcp(cfg):
    for ln in cfg.splitlines():
        t = ln.strip().lower()
        if t.startswith('proto '):
            return t.split()[1].startswith('tcp')
    return True

def main():
    d = json.loads(urllib.request.urlopen(urllib.request.Request(WORKER, headers=UA), timeout=40).read())
    servers = d.get('servers') or []
    by_cc = {}
    for s in servers:
        cc = (s.get('countryShort') or '').upper()
        ip = s.get('ip') or ''
        if cc in EXCLUDE_CC or not cc:
            continue
        if ip.startswith(OFFICIAL_PREFIX):
            continue
        if s.get('speed') and s.get('speed') < MIN_SPEED:
            continue
        q = s.get('quality') or {}
        try:
            cfg = base64.b64decode(''.join((s.get('configDataBase64') or '').split())).decode('utf-8', 'ignore')
        except Exception:
            continue
        if '<ca>' not in cfg or not ovpn_proto_tcp(cfg):
            continue
        if ip in by_cc.get(cc, {}).get('seenips', set()):
            continue
        by_cc.setdefault(cc, {'seenips': set(), 'cands': []})
        by_cc[cc]['seenips'].add(ip)
        clean = q.get('clean')
        scam = q.get('scam')
        eff_risk = q.get('risk')
        if eff_risk is None:
            eff_risk = 60                       # 无画像的排后
        if scam is not None:
            if scam >= 75: eff_risk = max(eff_risk, 90)
            elif scam >= 50: eff_risk = max(eff_risk, 65)
            elif scam >= 25: eff_risk = max(eff_risk, 45)
        by_cc[cc]['cands'].append(dict(ip=ip, cc=cc, risk=eff_risk, speed=s.get('speed') or 0,
                                       score=s.get('score') or 0, b64=s.get('configDataBase64')))
    # 国家排序:该国最优候选的有效风险分
    ranked_cc = []
    for cc, info in by_cc.items():
        cands = sorted(info['cands'], key=lambda r: (r['risk'], -r['speed']))
        ranked_cc.append((cands[0]['risk'], cc, cands))
    ranked_cc.sort(key=lambda t: (t[0], t[1]))
    if len(ranked_cc) < SLOTS:
        log('countries=%d < slots=%d, take all' % (len(ranked_cc), SLOTS))
    chosen = ranked_cc[:SLOTS]
    # 国家不足 10 个时,用已有国家池里的下一批候选补齐槽位(不同 IP,优先同质量档)
    if len(chosen) < SLOTS:
        used = set()
        for _, _, cands in chosen:
            for r in cands[:PER_COUNTRY]:
                used.add(r['ip'])
        pool = []
        for _, _, cands in ranked_cc:
            for r in cands:
                if r['ip'] not in used:
                    pool.append(r)
        pool.sort(key=lambda r: (r['risk'], -r['speed']))
        pi = 0
        while len(chosen) < SLOTS and pi < len(pool):
            r = pool[pi]
            pi += 1
            siblings = [x for x in by_cc[r['cc']]['cands'] if x['ip'] != r['ip']]
            chosen.append((r['risk'], r['cc'] + '*', [r] + siblings[:PER_COUNTRY - 1]))
    import subprocess
    for slot, (risk, cc, cands) in enumerate(chosen, start=1):
        sd = os.path.join(WORKDIR, 'multi', str(slot))
        os.makedirs(os.path.join(sd, 'nodes'), exist_ok=True)
        for f in os.listdir(os.path.join(sd, 'nodes')):
            if f.endswith('.ovpn'):
                os.remove(os.path.join(sd, 'nodes', f))
        with open(os.path.join(sd, 'candidates.tsv'), 'w') as out:
            for i, r in enumerate(cands[:PER_COUNTRY]):
                fn = 'nodes/%02d_%s.ovpn' % (i, r['ip'])
                with open(os.path.join(sd, fn), 'w') as cf:
                    cf.write(base64.b64decode(''.join(r['b64'].split())).decode('utf-8', 'ignore'))
                out.write('%d\t%s\t%s\t%s\t%s\t%s/%s/%s\n' % (i, r['ip'], cc, r['score'], r['speed'], WORKDIR, 'multi/' + str(slot), fn))
        open(os.path.join(sd, 'cc'), 'w').write(cc)
        if not os.path.exists(os.path.join(sd, 'current.idx')):
            open(os.path.join(sd, 'current.idx'), 'w').write('0')
        try:
            subprocess.run(['systemctl', 'enable', '--now', 'vpngate-tunnel@%d.service' % slot],
                           check=False, capture_output=True)
        except Exception:
            pass
        log('slot=%d cc=%s risk=%s cands=%d' % (slot, cc, risk, min(len(cands), PER_COUNTRY)))
    log('multi refresh done')

if __name__ == '__main__':
    main()
