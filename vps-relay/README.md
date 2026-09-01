# VPS 转换层：固定入口 + VPNGate 干净出口自动优选

这套脚本运行在**你自己的 Linux VPS** 上，实现：

- 客户端始终连你 VPS 的固定入口（端口/密码永不变）；
- VPS 在后台用 OpenVPN 连到 VPNGate，且**自动优选“纯净度高的住宅 IP”作为最终出口**；
- 定时刷新候选池、看门狗健康检查并平滑故障切换；只让指定流量走 VPN，SSH / 面板 / 其它服务不受影响。

```
客户端 ──固定入口(任意代理协议)──▶ VPS ──fwmark 策略路由──▶ OpenVPN tun0 ──▶ 优选干净住宅 IP ──▶ 互联网
```

## 0. 前提
- 一台 Linux VPS（Debian/Ubuntu 即可，1 核 1G 足够），root 权限；
- 已按[主 README](../README.md)部署 Worker，并拿到 `https://你的域名/api/servers`；
- 安装 OpenVPN 与 Python3：
  ```bash
  apt update && apt install -y openvpn python3 curl iproute2
  ```

## 1. 部署脚本
```bash
mkdir -p /opt/vpngate/nodes
# 把本目录下的脚本上传到 /opt/vpngate/
cp bestip_refresh.py bestip_refresh.sh build_running.sh ovpn-up.sh ovpn-down.sh watchdog.sh /opt/vpngate/
chmod +x /opt/vpngate/*.sh

# VPNGate 公共中继使用统一的公共账号（官方公开值 vpn / vpn）
printf 'vpn\nvpn\n' > /opt/vpngate/auth.txt
chmod 600 /opt/vpngate/auth.txt
```

## 2. 写运行配置
```bash
cp vpngate.env.example /opt/vpngate/vpngate.env
vi /opt/vpngate/vpngate.env
```
关键项：
```ini
VPNGATE_WORKER=https://你的域名/api/servers   # 不带 Worker 可留空（退回官方 CSV，但无纯净评分）
VPNGATE_CC=JP                                 # 固定日本出口；留空=全球
VPNGATE_CLEAN=1                               # 1 纯净优先 / 0 速度优先
POOL=12                                       # 候选池大小
```

## 3. 内核参数（策略路由必需）
fwmark 策略路由经 tun 转发时需要“松散”反向路径过滤，否则回包会被 strict rp_filter 丢弃：
```bash
cp sysctl/99-vpngate.conf.example /etc/sysctl.d/99-vpngate.conf
sysctl --system
```
建议同时开启 BBR（可选，提速）：
```bash
cat >/etc/sysctl.d/99-bbr.conf <<'EOF'
net.ipv4.tcp_congestion_control=bbr
net.core.default_qdisc=fq
EOF
sysctl --system
```

## 4. 首次生成候选池并验证
```bash
cd /opt/vpngate
python3 bestip_refresh.py            # 拉取并生成 nodes/*.ovpn 与 candidates.tsv（不重连）
cat candidates.tsv                   # 应看到前若干个干净节点 + 末尾官方集群兜底
./build_running.sh                   # 由 current.idx 拼出 running.ovpn
openvpn --config running.ovpn &      # 前台试连，看到 "Initialization Sequence Completed" 即成功
# 另开终端验证出口（应是日本干净 IP，而非 VPS 自身 IP）
curl -4 --interface tun0 https://api.ipify.org
kill %1
```

## 5. 安装 systemd 单元（开机自启 + 定时刷新 + 看门狗）
```bash
cp systemd/vpngate-tunnel.service   /etc/systemd/system/
cp systemd/vpngate-bestip.service   /etc/systemd/system/
cp systemd/vpngate-bestip.timer     /etc/systemd/system/
cp systemd/vpngate-watchdog.service /etc/systemd/system/
cp systemd/vpngate-watchdog.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vpngate-tunnel vpngate-bestip.timer vpngate-watchdog.timer
```
- `vpngate-tunnel`：OpenVPN 隧道，断线自动重启（`Restart=always`），重启时 `ExecStartPre` 会自动重建配置；
- `vpngate-bestip.timer`：每 10 分钟刷新候选池（**只刷新、不主动重连**，避免榜单抖动断流）；
- `vpngate-watchdog.timer`：每 2 分钟健康探测，**连续 2 次失败**才切到实时榜首（flock 与刷新互斥）。

## 6. 把你的代理入站“只让它走 VPN 出口”（关键：fwmark 策略路由）
隧道建好后，系统里存在一张独立路由表 `table 100`，默认走 `tun0`；只有打上 `fwmark 0x162`（十进制 **354**）的
连接才会查这张表。以 Xray 为例，新增一个**打标记的 outbound**，并让需要走 VPN 的入站路由到它：

```jsonc
// Xray 配置片段（示例，请与你现有的 inbounds/outbounds/routing 合并）
{
  "outbounds": [
    { /* 你原有的默认出站，保持不动 */ },
    {
      "tag": "vpngate-out",
      "protocol": "freedom",
      "settings": { "domainStrategy": "UseIP" },
      "streamSettings": {
        "sockopt": { "mark": 354 }          // 0x162 = 354，命中 table 100 → 走 tun0
      }
    }
  ],
  "routing": {
    "rules": [
      { "inboundTag": ["你的VPN入口inboundTag"], "outboundTag": "vpngate-out" }
      // 其它入站不写这条规则，因此依旧走 VPS 主网卡，互不影响
    ]
  }
}
```
> 要点：`ovpn-up.sh` 已自动创建 `ip rule fwmark 0x162 lookup 100` 与 `table 100 default dev tun0`，
> 并对被标记的 IPv6 做了 blackhole，避免 IPv6 从主网卡泄漏。其它代理内核（sing-box 等）同理，给出站包打 mark 354 即可。

## 7. 运维命令
```bash
curl -4 --interface tun0 https://api.ipify.org   # 当前 VPN 出口 IP
tail -f /opt/vpngate/health.log                  # 看门狗健康日志
tail -f /opt/vpngate/bestip.log                  # 优选/纯净排序日志
cat  /opt/vpngate/current-best.txt               # 当前榜首 + clean/risk
python3 /opt/vpngate/bestip_refresh.py --switch-top   # 立即手动切到实时最优
systemctl status vpngate-tunnel vpngate-bestip.timer vpngate-watchdog.timer
```

### 运行时开关（改 `/opt/vpngate/vpngate.env` 后等下次刷新自动生效）
| 变量 | 作用 |
|---|---|
| `VPNGATE_CLEAN=0` | 回到速度优先（官方集群排前） |
| `VPNGATE_CC=KR` | 改选其它国家，留空为全球 |
| `POOL=16` | 调整候选池大小 |
| `VPNGATE_DIR` | 工作目录（默认 `/opt/vpngate`） |
| `VPNGATE_MARK` / `VPNGATE_TABLE` | 自定义 fwmark / 路由表（默认 0x162 / 100，需与 Xray mark 一致） |

## 8. 文件说明
| 文件 | 职责 |
|---|---|
| `bestip_refresh.py` | 拉取节点 + 纯净评分排序 + 写候选池；`--switch-top` 时切换 |
| `build_running.sh` | 按 `current.idx` 拼 `running.ovpn`；候选缺失会先自举生成 |
| `ovpn-up.sh` | OpenVPN route-up：建 fwmark/oif 策略路由、IPv6 黑洞、rp_filter |
| `ovpn-down.sh` | OpenVPN down：清理 v4/v6 规则 |
| `watchdog.sh` | 健康探测（出口≠主网卡 IP 且 Google 204）、故障切换、顺序兜底 |
| `bestip_refresh.sh` | flock 互斥包装，供 timer 调用 |

## 9. 常见问题
- **出口等于 VPS 自身 IP？** 说明标记流量没走 tun：检查 Xray `sockopt.mark=354`、`ip rule`、`rp_filter=2`。
- **干净节点连不上？** 属正常波动，看门狗会自动换；候选池末尾保留了官方集群做最终兜底。
- **想全部走主网卡、临时停用？** `systemctl stop vpngate-tunnel` 即可，恢复用 `start`。
