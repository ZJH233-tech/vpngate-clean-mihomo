#!/bin/bash
set -eu
TOKEN_FILE="/etc/vpngate-relay/cf-tunnel.token"
if [ -s "$TOKEN_FILE" ]; then
  exec /usr/local/bin/cloudflared tunnel --no-autoupdate run --token-file "$TOKEN_FILE"
else
  exec /usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:18000
fi
