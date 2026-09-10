# VPS 完整部署教程

> 从一台全新 Debian 12 VPS,到"多地区 VPNGate 住宅出口 + 真实拨测体检 + TG 掌上控制台"的完整复刻路径。
> 所有示例中的 `YOUR_*` / `example.com` 请替换为你自己的值。**切勿把真实 IP/UUID/密钥提交到公开仓库。**

## 目录

- [0. 前置要求](#0-前置要求)
- [1. 系统加固](#1-系统加固)
- [2. 节点服务(s-ui + Reality)](#2-节点服务s-ui--reality)
- [3. 密钥与证书](#3-密钥与证书)
- [4. Xray 旁路挂载 XHTTP 节点(可选)](#4-xray-旁路挂载-xhttp-节点可选)
- [5. vps-relay 部署(核心)](#5-vps-relay-部署核心)
- [6. 出口节点与多地区槽位](#6-出口节点与多地区槽位)
- [7. Argo 隧道(可选)](#7-argo-隧道可选)
- [8. TG 掌上控制台(可选)](#8-tg-掌上控制台可选)
- [9. 校验](#9-校验)
- [10. 日常运维](#10-日常运维)
- [踩坑记录(务必读)](#踩坑记录务必读)

---

## 0. 前置要求

- 一台境外 VPS(推荐日本/韩国等离大陆近的地区),Debian 12,root 权限
- 一个托管在 Cloudflare 的域名(Worker 订阅与 Argo 隧道用)
- 本地能 SSH 的终端;一个 Mihomo 系客户端(v2rayN / Clash Verge / Shadowrocket)
- 你已经完成 [Worker 部署](../README.md#一快速开始worker-订阅5-分钟)(vps-relay 要从它拉纯净榜)

---

## 1. 系统加固

```bash
# 基础包
apt-get update
apt-get install -y fail2ban nftables sqlite3 bc ethtool dnsutils chrony \
  unattended-upgrades openvpn python3 curl wget

# 内核参数:BBR + fq + 大 UDP 缓冲(Hysteria2 需要)+ 关键 rp_filter
cat > /etc/sysctl.d/99-proxy-optimize.conf <<'EOF'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 25165824
net.core.wmem_max = 25165824
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_mtu_probing = 1
vm.swappiness = 10
EOF
cat > /etc/sysctl.d/99-vpngate.conf <<'EOF'
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
EOF
sysctl --system

# 1G swap(小内存 VPS 必备)
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 时区 + 自动安全更新
timedatectl set-timezone Asia/Shanghai
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now chrony unattended-upgrades
```

**SSH 改非标端口 + fail2ban**(示例改 52913,先双端口过渡,验证新端口能登录后再关 22):

```bash
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
Port 22
Port 52913
MaxAuthTries 4
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
X11Forwarding no
UseDNS no
EOF
sshd -t && systemctl restart ssh

cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
port    = 52913
maxretry = 5
findtime = 600
bantime  = 3600
bantime.increment = true
bantime.maxtime   = 1w
backend = systemd
EOF
```

**防火墙:入站默认 drop,只放行业务端口**(示例端口按需改;订阅端口见第 8 步,建议不对公网):

```bash
cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
flush ruleset
table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;
        iif "lo" accept
        ct state established,related accept
        ct state invalid drop
        ip protocol icmp accept
        meta l4proto ipv6-icmp accept
        tcp dport 52913 accept          # SSH
        tcp dport 443 accept            # VLESS-Reality
        udp dport 443 accept            # Hysteria2
        tcp dport 8388 accept           # VLESS-XHTTP
        tcp dport 8444-8454 accept      # VPNGate 出口节点
        udp dport 20000-50000 accept    # Hy2 端口跳跃
    }
    chain forward { type filter hook forward priority filter; policy accept; }
    chain output  { type filter hook output priority filter; policy accept; }
}
table inet nat {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        udp dport 20000-50000 redirect to :443   # Hy2 端口跳跃
    }
}
EOF
systemctl enable --now nftables fail2ban
```

> ⚠️ **顺序警告**:先让新 SSH 端口在防火墙里放行并**实际登录验证成功**,再从配置中删掉 22 端口并重载防火墙。否则会把自己锁在门外。

---

## 2. 节点服务(s-ui + Reality)

安装 [s-ui](https://github.com/alireza0/s-ui)(内嵌 sing-box,Web 面板管理节点+自带订阅):

```bash
curl -Ls https://raw.githubusercontent.com/alireza0/s-ui/master/install.sh -o /tmp/sui.sh
bash /tmp/sui.sh </dev/null        # 非交互安装(会生成随机管理员凭据,记得改)
```

要点(踩过坑):

- **面板只绑本机**:`sqlite3 /usr/local/s-ui/db/s-ui.db "INSERT OR REPLACE INTO settings(key,value) VALUES('webListen','127.0.0.1');"` 后重启 s-ui。管理走 SSH 隧道:`ssh -L 2095:127.0.0.1:2095 root@VPS`
- 面板里建 3 个对象:**TLS(Reality)**、**客户端**、**入站**。Reality 握手目标建议大厂站(如 `www.ibm.com:443`)
- 注意 s-ui 两个已知坑:
  1. **无 TLS 对象的入站**(如 Argo-WS),生成的订阅链接会漏 `security=tls` 参数 —— 手工修 `clients.links`(改完记得 `CAST(... AS BLOB)`,见踩坑记录)
  2. clients 表的 `links`/`inbounds` 列是 BLOB,直接写 TEXT 会让订阅接口 400

---

## 3. 密钥与证书

用 [`vps-relay/gen_keys.sh`](../vps-relay/gen_keys.sh) 一次生成 3 组 Reality 密钥对(443/8388/8444)+ Hy2 自签证书:

```bash
bash gen_keys.sh
# 输出 /root/kp-443.txt /root/kp-8388.txt /root/kp-8444.txt(600 权限)
# 前提:独立 sing-box 在 /usr/local/bin/sing-box(面板内嵌版不带 CLI)
```

把公钥/短ID 填入面板的 Reality TLS 对象;私钥面板自动生成无需手填。

---

## 4. Xray 旁路挂载 XHTTP 节点(可选)

**背景**:XHTTP 是 Xray 独有传输,sing-box(s-ui)不支持。想要 XHTTP 节点,需要在面板之外跑一个 Xray 实例。

模板见 [`examples/xray-xhttp-reality-server.json`](../examples/xray-xhttp-reality-server.json):
- VLESS + XHTTP + Reality,端口 8388,`xhttpSettings.path = /grpcreal`
- 客户端 UUID 与面板客户端保持一致即可(订阅里这条节点用静态链接,见第 8 步)

systemd 托管:

```ini
# /etc/systemd/system/xray.service
[Service]
ExecStart=/usr/local/bin/xray run -c /etc/xray/config.json
Restart=always
RestartSec=5
```

> ⚠️ **vision 流控只能配裸 TCP**,XHTTP/WS/gRPC 均不支持,不要叠加。

---

## 5. vps-relay 部署(核心)

```bash
apt-get install -y git
git clone https://github.com/ZJH233-tech/vpngate-clean-mihomo.git /opt/src
cp -r /opt/src/vps-relay /opt/vpngate
cp /opt/src/extras/dialtest.py /opt/tgbot/dialtest.py 2>/dev/null || true

cd /opt/vpngate
cp vpngate.env.example vpngate.env
nano vpngate.env    # 填 Worker 地址 / 国家 / 阈值
```

`vpngate.env` 关键项:

```ini
VPNGATE_WORKER=https://你的-worker域名/api/servers
VPNGATE_CC=JP            # 日本槽位的国家;多地区槽位自动排除此国
VPNGATE_CLEAN=1          # 纯净优先
VPNGATE_SCAM_MAX=25      # 出口欺诈分阈值
VPNGATE_SCAM_API=https://你的欺诈分查询接口/?ip=
VPNGATE_SWITCH_COOLDOWN=600
VPNGATE_MULTI_SLOTS=10
```

启动(日本槽):

```bash
chmod 755 /opt/vpngate/*.sh /opt/vpngate/*.py
systemctl daemon-reload
systemctl enable --now vpngate-tunnel     # ExecStartPre 会自举候选池
systemctl enable --now vpngate-bestip.timer vpngate-watchdog.timer
```

**日本出口节点**(把隧道出口暴露给客户端):`gen_sbexit.py` 生成一个独立 sing-box 实例
(入站 8444 Reality → 出站 `routing_mark 354` → fwmark 策略路由 → tun):

```bash
VPNGATE_MULTI_UUID=$(cat /proc/sys/kernel/random/uuid)   # 记到 vpngate.env
python3 gen_sbexit.py && sing-box check -c sb-exit.json
systemctl enable --now vpngate-sbexit
```

> 原理:出口实例给流量打 `fwmark 354`,`ip rule fwmark 0x162 lookup 100` → `default dev tun0`。
> 隧道断开时表内无路由 = **安全失败**(不泄漏到主线路),看门狗负责恢复。

---

## 6. 出口节点与多地区槽位

多地区 = 除日本外,10 个国家各一条独立隧道(`vpngate-tunnel@1..10` 模板单元),
每槽独立 `fwmark 355-364` / 路由表 `101-110` / 独立 tun 设备(`vpnm1-10`):

```bash
systemctl enable --now vpngate-multi-bestip.timer   # 15 分钟:刷新候选池(粘性绑定,见下)
systemctl enable --now vpngate-multi-watchdog.timer # 2 分钟:逐槽健康+纯净度巡检+订阅同步
```

**v2 的稳定性设计(解决"客户端每次刷新订阅,国旗/节点都在变"):**

- `multi_refresh.py` 采用**槽位→国家粘性绑定**(`multi/slot-map.json`):槽位一旦绑定某国,
  只要该国还有合格候选就一直保留,绝不在每轮刷新时整体重排;只有绑定国候选池整体消失才释放重分,
  确定性排序保证同样输入必然同样结果。国家不够 10 个时才用同国不同 IP 补位(国名带 `*`)。
- `multi_watchdog.sh` 逐槽检查 隧道存活 + **实测出口国必须等于绑定国(且≠日本)** + 欺诈分;
  不达标连续 2 次 → 轮换下一候选(每槽 600s 冷却)。若一整个候选池轮完仍不合格
  (例如该国所有中继都是链式出口),写 `release` 标志,下次刷新释放该槽、30 分钟内不再选这个国家。
- **槽位号 = 固定身份**(槽 i 永远监听 8444+i,URI 永不变);变的只是背后国家,且只在真实切换时才变。

验收标准(重要):**入口 IP ≠ 出口 IP 的"链式服务器"在 VPNGate 里真实存在**(入口日本、出口可能南美)。
所以看门狗按**实测出口国 + 实测出口欺诈分**验收,不是只看候选池评分。

### 6.1 订阅生成:`sync_subscriptions.py`(关键)

s-ui 面板的订阅链接由 `sync_subscriptions.py` 统一生成,它是订阅内容的**唯一权威**:

- **主节点**(自建 Reality/XHTTP/Hy2/Argo):URI 原样保留,备注用 emoji 国旗钉死。
  各 GeoIP 库对同一台 VPS 的归属判定可能不一致(例如有的说 JP 有的说 KR),
  客户端自己查 IP 归属会让主节点国旗乱跳;备注前缀写死国旗后客户端按备注显示,不再漂移。
- **VPNGate 槽位**:国家码以看门狗**实测出口**(`multi/<槽>/exit-scam.txt` 第 4 列)为准,
  实测缺失才回退到声明国;命名 `🇰🇷 VPNGate-KR-Exit-2`,国旗与真实出口一致。
- **合并订阅 `/sub/all` 顺序固定**:主节点在前 → 日本出口 → 槽位 1..10,绝不重排。
- 只在内容真正变化时才重启 s-ui(数据面 xray/sing-box 不受影响)。
- 看门狗每轮结束自动调用一次;再用 cron 每小时兜底:

```bash
# crontab -e
17 * * * * /usr/bin/python3 /opt/vpngate/merge_subs.py >/dev/null 2>&1
# merge_subs.py 是 sync_subscriptions.py 的薄封装(见 vps-relay/crontab.example)
```

> 首次部署时,先在面板里手工建好 4 个订阅分组(`yuwen2026`/`vless443` 放主节点静态链接、
> `vpngate-exit` 放 8444 一条、`vpngate-other` 放 8445-8454 十条骨架),之后全部交给同步器维护;
> 同步器从现存链接继承 UUID/密钥参数,只改端口与备注,代码里不写死任何密钥。

---

## 7. Argo 隧道(可选)

给节点套 Cloudflare 边缘(隐藏源站、绕路由拥塞)。前提:CF 账号里已建 Cloudflare Tunnel。

```bash
# 用 Global Key 或 API Token 从 CF API 取 tunnel token,写入:
install -m 600 /path/to/token /etc/cloudflared/token
# systemd:ExecStart=/usr/local/bin/cloudflared tunnel run --token-file /etc/cloudflared/token
```

- ingress 在 CF 控制台/API 配置:`节点域名 → http://localhost:18080`
- **2026 版 cloudflared 已移除 `--edge-ip-version` / `--protocol` 参数**(旧教程会报错)
- 对应入站:VLESS-WS(无 TLS,CF 边缘终结 TLS)

---

## 8. TG 掌上控制台(可选)

```bash
mkdir -p /opt/tgbot
cp /opt/src/extras/tgbot/bot.py /opt/tgbot/bot.py
cp /opt/src/extras/tgbot/tgbot.service /etc/systemd/system/tgbot.service
@BotFather 建机器人拿 token → echo "TOKEN" > /opt/tgbot/token(600)
echo "你的chat_id" > /opt/tgbot/owner(600)
# 可选环境变量(写进 /opt/tgbot/bot.env, tgbot.service 用 EnvironmentFile 引用):
#   VPS_IP=你的VPS公网IP   VPNGATE_SUB_BASE=https://你的订阅域名
systemctl daemon-reload
systemctl enable --now tgbot
```

功能:🩺 真实拨测体检 / 📌 置顶实时面板 / 服务重启 / 换节点 / 封禁解封 / 线路诊断 /
一键备份(配置打包发到对话)/ 告警(SSH 登录、封禁、资源、核心崩溃、出口**换国**、每日日报)。
出口变更通知做了降噪:同一新出口需连续两轮巡检(约 40s)一致才确认(双确认去抖,
避免"换出去又换回"的瞬时抖动误报);**只在实测出口国家变化时推送**,同一国家内
轮换候选 IP 只更新置顶面板不响铃;菜单/状态里同时显示"绑定国/实测国",
链式中继声明国≠真实出口国时一眼可见。白名单硬锁:只有 `owner` 文件里的 chat_id
能用。零新增端口(长轮询出站)。

---

## 9. 校验

```bash
python3 /opt/tgbot/dialtest.py          # 抽样拨测(默认3槽)
python3 /opt/tgbot/dialtest.py --full   # 全量 15 节点
# 输出行: 名称|HTTP码|出口IP   —— 出口IP 必须与预期一致(住宅=住宅,主节点=VPS IP)
```

客户端侧:更新订阅 → 逐节点连通测试 → 访问 `https://api.ipify.org` 核对出口。

---

## 10. 日常运维

| 场景 | 操作 |
|---|---|
| 出口欺诈分变高 | 自动(看门狗);手动:`bash /opt/vpngate/pick_strict.sh` |
| 某槽位卡 | TG 槽位轮换按钮,或 `multi_watchdog.sh` 一轮 |
| 订阅名字与真实出口对不上 | `python3 /opt/vpngate/sync_subscriptions.py`(看门狗每轮也会自动同步) |
| 分流库过期 | `multi_refresh.py`/`bestip_refresh` 定时器自动;手动重跑即可 |
| 配置备份 | TG 一键备份(tar 含面板库/密钥/防火墙) |
| 日志 | `/opt/vpngate/{health.log,watchdog-switch.log,multi.log,multi_refresh.log,sync_subs.log,bestip.log}` |

---

## 踩坑记录(务必读)

1. **候选序号八进制**:`current.idx` 写成 `%02d` 后,看门狗 `$((IDX+1))` 在 08/09 时按八进制解析直接报错。修复:写十进制 + 运算处 `10#$IDX`
2. **auth-user-pass 重复**:VPNGate 官方配置自带 `auth-user-pass`,再追加一份会导致凭据解析混乱 —— 追加前先 grep
3. **部分服务器拒绝 vpn/vpn 认证**:正常现象(AUTH_FAILED),自动轮换兜底,约 2-4 分钟自愈
4. **链式出口**:入口 IP 评分高 ≠ 出口干净,必须按**实测出口**验收
5. **s-ui links 列类型**:BLOB 列写 TEXT 会让订阅接口 400,`json_set` 后记得 `CAST(... AS BLOB)`
6. **无 TLS 对象入站**的订阅链接缺 `security=tls` —— 客户端连不上却毫无提示
7. **XHTTP 不支持 vision 流控**(vision 仅裸 TCP);sing-box 不支持 XHTTP,服务端需 Xray
8. **多隧道 down 脚本**:隧道未建立(${dev} 未定义)时执行清理,会按默认值误删**别的隧道**的路由 —— down 脚本必须先判断 `${dev}` 是否存在
9. **cloudflared 2026 移除** `--edge-ip-version/--protocol` 参数,旧命令行会直接打印 help 退出
10. **不要把真实 IP/UUID/密钥提交到公开仓库**(真实教训:推一次洗一次历史,还得轮换凭据)
11. **订阅节点名不要写死国家**:VPNGate 上游列表每几分钟就变,写死一次的名字很快与真实出口不符,
    客户端看到的国旗/IP 就会"乱跳"。正确做法是 `sync_subscriptions.py` 按**实测出口**动态命名、
    emoji 钉国旗、槽位端口固定,并在每次看门狗巡检后自动同步

---

## 卸载

```bash
systemctl disable --now vpngate-tunnel vpngate-sbexit xray tgbot cloudflared
systemctl disable --now vpngate-bestip.timer vpngate-watchdog.timer \
  vpngate-multi-bestip.timer vpngate-multi-watchdog.timer
rm -rf /opt/vpngate /opt/tgbot /etc/xray
# nftables/sshd/fail2ban 按第 1 步反向操作
```
