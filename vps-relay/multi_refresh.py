#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 多地区出口候选刷新(v2: 槽位->国家"粘性绑定", 修复订阅国旗反复横跳)
# ------------------------------------------------------------------
# v1 问题: 每 900s 按风险给国家重新排名, 前 10 国依次占槽 -> 槽位国家每轮洗牌,
#          客户端看到的节点国旗/IP 一直变。
# v2 策略(滞后/hysteresis):
#   1) 槽位一旦绑定某国家就长期保留, 只要该国还有合格候选就不换;
#   2) 只有当绑定国家的候选池整体消失, 该槽才释放, 从"未被占用"的国家里补最优;
#   3) 国家不够 10 个时, 才用同国家不同 IP 补槽(cc 带 * 标注), 且不抢占已绑槽;
#   4) 全部排序确定性(risk -> cc), 同样输入必然同样结果;
#   5) 仅当某槽国家真的变化时才重建并重启该槽隧道, 其余槽只更新候选文件。
import sys, os, json, base64, urllib.request, time, subprocess, re

WORKDIR = '/opt/vpngate'
SLOTS = 10
PER_COUNTRY = 5
EXCLUDE_CC = {'JP'}
OFFICIAL_PREFIX = '219.100.37.'
MIN_SPEED = 3_000_000
SLOTMAP = os.path.join(WORKDIR, 'multi', 'slot-map.json')


def _load_env(path):
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
WORKER = os.environ.get('VPNGATE_WORKER', '').strip().rstrip('/')
if not WORKER.startswith('http'):
    print('VPNGATE_WORKER not configured, skip')
    sys.exit(0)
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


def valid_cc(cc):
    return bool(re.fullmatch(r'[A-Z]{2}', cc or '')) and cc not in EXCLUDE_CC


def fetch_pools():
    d = json.loads(urllib.request.urlopen(urllib.request.Request(WORKER, headers=UA), timeout=40).read())
    servers = d.get('servers') or []
    by_cc = {}
    for s in servers:
        cc = (s.get('countryShort') or '').upper()
        ip = s.get('ip') or ''
        if not valid_cc(cc):                 # 排除 JP / ZZ / 空
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
        clean = q.get('clean')
        scam = q.get('scam')
        eff_risk = q.get('risk')
        if eff_risk is None:
            eff_risk = 60
        if scam is not None:
            if scam >= 75: eff_risk = max(eff_risk, 90)
            elif scam >= 50: eff_risk = max(eff_risk, 65)
            elif scam >= 25: eff_risk = max(eff_risk, 45)
        by_cc.setdefault(cc, []).append(dict(ip=ip, cc=cc, risk=eff_risk, speed=s.get('speed') or 0,
                                             score=s.get('score') or 0, b64=s.get('configDataBase64')))
    pools = {}
    for cc, cands in by_cc.items():
        cands.sort(key=lambda r: (r['risk'], -r['speed'], r['ip']))
        pools[cc] = cands[:PER_COUNTRY]
    return pools


def write_slot(slot, cc, cands):
    sd = os.path.join(WORKDIR, 'multi', str(slot))
    nd = os.path.join(sd, 'nodes')
    os.makedirs(nd, exist_ok=True)
    for f in os.listdir(nd):
        if f.endswith('.ovpn'):
            os.remove(os.path.join(nd, f))
    with open(os.path.join(sd, 'candidates.tsv'), 'w') as out:
        for i, r in enumerate(cands):
            fn = 'nodes/%02d_%s.ovpn' % (i, r['ip'])
            with open(os.path.join(sd, fn), 'w') as cf:
                cf.write(base64.b64decode(''.join(r['b64'].split())).decode('utf-8', 'ignore'))
            out.write('%d\t%s\t%s\t%s\t%s\t%s/%s/%s\n' %
                      (i, r['ip'], cc, r['score'], r['speed'], WORKDIR, 'multi/' + str(slot), fn))
    open(os.path.join(sd, 'cc'), 'w').write(cc)
    if not os.path.exists(os.path.join(sd, 'current.idx')):
        open(os.path.join(sd, 'current.idx'), 'w').write('0')


def main():
    pools = fetch_pools()
    # 国家按"最优候选风险、国家码"确定性排序
    cc_ranked = sorted(pools.keys(), key=lambda c: (pools[c][0]['risk'], c))

    prev = {}
    try:
        prev = {int(k): str(v) for k, v in json.load(open(SLOTMAP)).items()}
    except (OSError, ValueError):
        pass

    assignment = {}            # slot -> cc
    reserved = set()

    # 第一遍: 保留粘性绑定(绑定国仍有池子)
    for slot in range(1, SLOTS + 1):
        cc = prev.get(slot, '').rstrip('*')
        if cc in pools and cc not in reserved:
            assignment[slot] = cc
            reserved.add(cc)

    # 第二遍: 空槽补"尚未被占用"的最优国家
    free = [s for s in range(1, SLOTS + 1) if s not in assignment]
    for cc in cc_ranked:
        if not free:
            break
        if cc in reserved:
            continue
        assignment[free.pop(0)] = cc
        reserved.add(cc)

    # 第三遍: 国家数不足, 用已分配国家的"不同 IP"补槽(cc 标 *)
    if free:
        fill_pool = []
        used_ips = set()
        for cc in sorted(reserved):
            for r in pools.get(cc, []):
                fill_pool.append((cc, r))
        # 已被各槽首选的 IP 不重复用
        for slot, cc in assignment.items():
            if pools.get(cc.rstrip('*')):
                used_ips.add(pools[cc.rstrip('*')][0]['ip'])
        fill_pool = [(cc, r) for cc, r in fill_pool if r['ip'] not in used_ips]
        fill_pool.sort(key=lambda t: (t[1]['risk'], t[0], -t[1]['speed']))
        for slot in free:
            if not fill_pool:
                break
            cc, r = fill_pool.pop(0)
            # 该补槽候选 = 该候选 + 同国其余候选
            sib = [x for x in pools[cc] if x['ip'] != r['ip']]
            assignment[slot] = cc + '*'
            pools[cc + '*'] = [r] + sib[:PER_COUNTRY - 1]

    # 落盘 + 仅对"国家真的变了"的槽重建重启
    changed_slots = []
    for slot in range(1, SLOTS + 1):
        cc = assignment.get(slot)
        if cc is None:
            log('slot=%d no country available, keep previous files' % slot)
            continue
        cands = pools.get(cc, [])
        if not cands:
            continue
        old_cc = (prev.get(slot, '') or '').upper()
        write_slot(slot, cc, cands)
        try:
            subprocess.run(['systemctl', 'enable', '--now', 'vpngate-tunnel@%d.service' % slot],
                           check=False, capture_output=True)
        except Exception:
            pass
        if old_cc != cc.upper():
            changed_slots.append((slot, old_cc or '-', cc))
            open(os.path.join(WORKDIR, 'multi', str(slot), 'current.idx'), 'w').write('0')
            subprocess.run(['bash', os.path.join(WORKDIR, 'multi_build.sh'), str(slot)],
                           check=False, capture_output=True)
            subprocess.run(['systemctl', 'restart', 'vpngate-tunnel@%d.service' % slot],
                           check=False, capture_output=True)
        log('slot=%d cc=%s risk=%s cands=%d%s' %
            (slot, cc, cands[0]['risk'], len(cands),
             (' CHANGED from %s' % old_cc) if old_cc != cc.upper() else ''))

    json.dump(assignment, open(SLOTMAP, 'w'), ensure_ascii=False, indent=2, sort_keys=True)
    if changed_slots:
        log('country changes this round: %s' % changed_slots)
    log('multi refresh done, pinned=%d/%d' % (len(assignment), SLOTS))


if __name__ == '__main__':
    main()
