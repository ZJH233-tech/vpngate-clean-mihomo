#!/bin/bash
# 生成三组 Reality 密钥(443/8388/8444) + Hy2 自签证书(10 年)
# 用法: HY2_DOMAIN=hy2.example.com bash gen_keys.sh
set -e
SB=/usr/local/bin/sing-box
HY2_DOMAIN="${HY2_DOMAIN:-hy2.example.com}"   # 改成你自己的 Hy2 SNI 域名

write_kp() {  # $1 = 后缀
  local OUT P S
  OUT=$($SB generate reality-keypair)
  P=$(echo "$OUT" | grep -oP 'PrivateKey:\s*\K\S+')
  S=$(echo "$OUT" | grep -oP 'PublicKey:\s*\K\S+')
  cat > /root/kp-$1.txt <<EOF
priv$1=$P
pub$1=$S
sid$1=$(openssl rand -hex 8)
EOF
}

write_kp 443
write_kp 8388
write_kp 8444
chmod 600 /root/kp-443.txt /root/kp-8388.txt /root/kp-8444.txt

# Hy2 自签 ECDSA P-256 证书
mkdir -p /etc/sing-box
if [ ! -f /etc/sing-box/hy2.crt ]; then
  openssl ecparam -genkey -name prime256v1 -out /etc/sing-box/hy2.key 2>/dev/null
  openssl req -new -x509 -days 3650 -key /etc/sing-box/hy2.key -out /etc/sing-box/hy2.crt \
    -subj "/CN=$HY2_DOMAIN" \
    -addext "subjectAltName=DNS:$HY2_DOMAIN" 2>/dev/null
fi
chmod 600 /etc/sing-box/hy2.key

PIN=$(openssl x509 -in /etc/sing-box/hy2.crt -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')
echo "=== kp-443 ===";  cat /root/kp-443.txt
echo "=== kp-8388 ==="; cat /root/kp-8388.txt
echo "=== kp-8444 ==="; cat /root/kp-8444.txt
echo "hy2_pin=$PIN"
echo "hy2_cert_expiry=$(openssl x509 -in /etc/sing-box/hy2.crt -noout -enddate)"
