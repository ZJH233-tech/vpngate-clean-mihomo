# VPNGate 私有列表拉取代理 Worker（v2）

极简只读代理：仅暴露 `GET /csv` + 强制 `Bearer` 口令校验，其他路径与错口令全部静默 `404`。

## 部署步骤（CF 账号 `ouyangyouhe`）
```bash
cd v2/worker-proxy
cp wrangler.toml.example wrangler.toml
# 1) 部署 Worker
wrangler deploy
# 2) 生成随机强口令并写入 Cloudflare Secret（切勿写入代码或仓库）
openssl rand -hex 24   # 复制保存，后续填入目标机 /etc/vpngate-relay/secrets.env 的 VPNGATE_WORKER_TOKEN
wrangler secret put API_TOKEN
```

## 验收测试
```bash
# 无口令 / 错路径 -> 必须返回 404
curl -s -o /dev/null -w "no_auth=%{http_code}\n" "https://<你的worker>/csv"
curl -s -o /dev/null -w "root=%{http_code}\n" "https://<你的worker>/"
# 带正确口令 -> 必须返回 200 且首行 *vpn_servers
curl -sS -H "Authorization: Bearer <你的API_TOKEN>" "https://<你的worker>/csv" | head -n 2
```
