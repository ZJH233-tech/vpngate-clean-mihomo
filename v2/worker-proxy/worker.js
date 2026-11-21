// VPNGate 私有列表拉取代理 Worker（v2）
// 设计原则：
//   1. 仅放行 GET /csv，返回原始 VPNGate CSV（边缘缓存 120s，单次请求最多 1 次子请求）
//   2. 强制 Bearer / X-API-Key 常量时间鉴权（口令存 Cloudflare Secret: API_TOKEN）
//   3. 未配置 API_TOKEN 时 fail-closed（返回 503），绝不裸奔
//   4. 其它任何路径、方法或错误口令一律静默返回 404，不暴露任何服务指纹

const VPNGATE_API_URL = 'https://www.vpngate.net/api/iphone/';
const CACHE_TTL = 120;

function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const ba = enc.encode(String(a || ''));
  const bb = enc.encode(String(b || ''));
  if (ba.length === 0 || ba.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ba.length; i++) diff |= ba[i] ^ bb[i];
  return diff === 0;
}

function extractToken(request) {
  const auth = request.headers.get('authorization') || '';
  if (auth.toLowerCase().startsWith('bearer ')) return auth.slice(7).trim();
  return (request.headers.get('x-api-key') || '').trim();
}

export default {
  async fetch(request, env, ctx) {
    if (!env || !env.API_TOKEN) {
      return new Response('Service Misconfigured', { status: 503 });
    }
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, '') || '/';

    // 仅允许 GET /csv 且口令完全匹配；其余全部静默 404
    if (request.method !== 'GET' || path !== '/csv') {
      return new Response('Not Found', { status: 404 });
    }
    if (!timingSafeEqual(extractToken(request), env.API_TOKEN)) {
      return new Response('Not Found', { status: 404 });
    }

    const force = url.searchParams.get('refresh') === '1';
    const hasCache = typeof caches !== 'undefined' && caches.default;
    const cacheKey = new Request('https://vpngate-v2-cache.internal/iphone.csv');
    let stale = null;

    if (hasCache) {
      const hit = await caches.default.match(cacheKey);
      if (hit) {
        if (!force) {
          return new Response(hit.body, {
            status: 200,
            headers: {
              'content-type': 'text/plain; charset=utf-8',
              'x-cache': 'HIT',
              'cache-control': 'private, no-store',
            },
          });
        }
        stale = hit;
      }
    }

    try {
      const upstream = await fetch(VPNGATE_API_URL, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; VPNGateRelayV2/1.0)',
          Accept: 'text/plain,*/*',
        },
      });
      if (!upstream.ok) throw new Error('upstream HTTP ' + upstream.status);
      const text = await upstream.text();
      if (!text.includes('*vpn_servers')) {
        throw new Error('upstream returned non-CSV payload');
      }
      if (hasCache) {
        ctx.waitUntil(
          caches.default.put(
            cacheKey,
            new Response(text, { headers: { 'cache-control': 'public, max-age=' + CACHE_TTL } })
          )
        );
      }
      return new Response(text, {
        status: 200,
        headers: {
          'content-type': 'text/plain; charset=utf-8',
          'x-cache': 'MISS',
          'cache-control': 'private, no-store',
        },
      });
    } catch (err) {
      if (stale) {
        return new Response(stale.body, {
          status: 200,
          headers: {
            'content-type': 'text/plain; charset=utf-8',
            'x-cache': 'STALE',
            'cache-control': 'private, no-store',
          },
        });
      }
      return new Response('Upstream Error', { status: 502 });
    }
  },
};
