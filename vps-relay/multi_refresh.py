#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 多地区出口候选刷新(v2: 槽位->国家"粘性绑定", 修复订阅国旗反复横跳)
# ------------------------------------------------------------------
# v1 问题: 每 900s 按风险给国家重新排名, 前 10 国依次占槽 -> 槽位国家每轮洗牌,
#          客户端看到的节点国旗/IP 一直变。
# v2 策略(滞后/hysteresis):
#   1) 槽位一旦绑定某国家就长期保留, 只要该国还有合格候选就不换;
#   2) 只有当绑定国家的候选池整体消失, 该槽才释放, 从"未被占用"的国家里补最优;
#   3) 国家不够 10 个时, 才用同国家不同 IP 补槽(cc 带 * 标注); 主绑定(不带*)比补槽
#      优先保留, 补槽绝不能把主绑定的国家"抢走"(v2.2 修复: 旧实现按槽号顺序保留,
#      当 KR* 槽号在 KR 主槽之前时会误占 KR, 导致主槽被换国、订阅仍抖动);
#      同国多槽候选按槽错位轮转, 保证各自首选 IP 不同;
#   4) 全部排序确定性(风险层 -> 候选池深度 -> risk -> cc), 同样输入必然同样结果;
#      新补槽优先候选池更深的国家(单候选国一旦该志愿者下线就又要换旗, 深池更稳);
#   5) 仅当某槽国家真的变化时才重建并重启该槽隧道, 其余槽只更新候选文件。
import sys, os, json, base64, urllib.request, time, subprocess, re

WORKDIR = '/opt/vpngate'
SLOTS = 10
PER_COUNTRY = 5
EXCLUDE_CC = {'JP', 'ZZ'}   # JP=日本留给主隧道; ZZ=VPNGate 未知国家码
OFFICIAL_PREFIX = '219.100.37.'
MIN_SPEED = 3_000_000
SLOTMAP = os.path.join(WORKDIR, 'multi', 'slot-map.json')
SKIPFILE = os.path.join(WORKDIR, 'multi', 'skip-until.json')
SKIP_TTL = 1800          # 被看门狗释放的国家 30 分钟内不再分配


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


def risk_bucket(r):
    # 风险分层: 0 干净 / 1 中 / 2 偏高 / 3 高; 同层内优先候选池更深(更不容易整池消失->换旗)
    return 0 if r < 25 else (1 if r < 50 else (2 if r < 75 else 3))


def main():
    pools = fetch_pools()
    now = int(time.time())
    # 国家确定性排序: 风险层 -> 候选池深度(深优先, 粘性更稳) -> 最优风险 -> 国家码
    cc_ranked = sorted(pools.keys(),
                       key=lambda c: (risk_bucket(pools[c][0]['risk']), -len(pools[c]),
                                      pools[c][0]['risk'], c))

    prev = {}
    try:
        prev = {int(k): str(v) for k, v in json.load(open(SLOTMAP)).items()}
    except (OSError, ValueError):
        pass
    skip_until = {}
    try:
        skip_until = {k: int(v) for k, v in json.load(open(SKIPFILE)).items()}
    except (OSError, ValueError):
        pass

    def skipped(cc):
        return skip_until.get(cc, 0) > now

    assignment = {}            # slot -> 基础国家码(不带 *)
    is_dup = set()             # 哪些槽是"同国异 IP 补槽"(落盘时 cc 带 *)
    reserved = set()           # 已被"主绑定"(非补槽)持有的国家

    # 第零遍: 先消费所有槽的 release 标记(看门狗判定整池不合格 -> 该国冷却)
    for slot in range(1, SLOTS + 1):
        base = prev.get(slot, '').rstrip('*')
        relf = os.path.join(WORKDIR, 'multi', str(slot), 'release')
        if os.path.exists(relf) and base:
            skip_until[base] = now + SKIP_TTL
            log('slot=%d release pin %s (watchdog rejected whole pool), skip %ds'
                % (slot, base, SKIP_TTL))
            try:
                os.remove(relf)
            except OSError:
                pass

    # 第一遍(A): 优先保留"主绑定"(旧值不带 *)粘性, 它们对国家有优先占用权
    for slot in range(1, SLOTS + 1):
        p = prev.get(slot, '')
        if not p or p.endswith('*'):
            continue
        if p in pools and p not in reserved and not skipped(p):
            assignment[slot] = p
            reserved.add(p)

    # 第一遍(B): 再保留"补槽"(旧值带 *); 同国可多槽共存, 因此不占用 reserved
    for slot in range(1, SLOTS + 1):
        p = prev.get(slot, '')
        if not p.endswith('*'):
            continue
        base = p.rstrip('*')
        if base in pools and not skipped(base):
            assignment[slot] = base
            is_dup.add(slot)

    # 第二遍: 空槽补"尚未被主绑定占用/未跳过"的最优独立国家
    free = [s for s in range(1, SLOTS + 1) if s not in assignment]
    for cc in cc_ranked:
        if not free:
            break
        if cc in reserved or skipped(cc):
            continue
        assignment[free.pop(0)] = cc
        reserved.add(cc)

    # 第三遍: 独立国家不够, 用已持有国家的"不同 IP"补槽(深池优先, cc 标 *)
    free = [s for s in range(1, SLOTS + 1) if s not in assignment]
    if free:
        bases = sorted(reserved,
                       key=lambda c: (-len(pools[c]), risk_bucket(pools[c][0]['risk']), c))
        cursor = 0
        for slot in free:
            picked = None
            for _ in range(max(1, len(bases))):
                if not bases:
                    break
                base = bases[cursor % len(bases)]
                cursor += 1
                if pools.get(base) and not skipped(base):
                    picked = base
                    break
            if picked is None:
                log('slot=%d no country available, keep previous files' % slot)
                continue
            assignment[slot] = picked
            is_dup.add(slot)

    # 为每个国家的多个槽分配"错位候选": 主绑定拿最优序列, 补槽依次轮转, 首 IP 互不相同
    slot_plan = {}
    members = {}
    for slot, base in assignment.items():
        members.setdefault(base, []).append(slot)
    for base, slots in members.items():
        pool = pools[base]
        prim = sorted(s for s in slots if s not in is_dup)
        dups = sorted(s for s in slots if s in is_dup)
        for k, slot in enumerate(prim + dups):
            label = base if slot not in is_dup else base + '*'
            rotated = pool[k:] + pool[:k]
            slot_plan[slot] = (label, rotated[:PER_COUNTRY])

    # 落盘 + 仅对"国家真的变了"的槽重建重启
    changed_slots = []
    for slot in range(1, SLOTS + 1):
        if slot not in slot_plan:
            continue
        cc, cands = slot_plan[slot]
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

    final_map = {s: slot_plan[s][0] for s in sorted(slot_plan)}

    json.dump(final_map, open(SLOTMAP, 'w'), ensure_ascii=False, indent=2, sort_keys=True)
    json.dump({k: v for k, v in skip_until.items() if v > now},
              open(SKIPFILE, 'w'), ensure_ascii=False, indent=2, sort_keys=True)
    if changed_slots:
        log('country changes this round: %s' % changed_slots)
    log('multi refresh done, pinned=%d/%d' % (len(final_map), SLOTS))


if __name__ == '__main__':
    main()
