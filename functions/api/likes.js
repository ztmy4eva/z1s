// GET  /api/likes?top=N    → [{src, name, count}] top N by likes
// GET  /api/likes?card=KEY → {count}
// POST /api/likes {card: KEY} → increments count, returns {count}
// Rate limit: KV key `rl:IP:KEY` with 10s TTL

const CARD_RE = /^[a-zA-Z0-9/_\-.]{3,120}$/;
const INDEX_KEY = '_likes_index';

function json(data, status = 200) {
  return Response.json(data, {
    status,
    headers: { 'Access-Control-Allow-Origin': '*' },
  });
}
function err(msg, status = 400) {
  return json({ error: msg }, status);
}

export async function onRequestGet({ request, env }) {
  const url    = new URL(request.url);
  const top    = url.searchParams.get('top');
  const card   = url.searchParams.get('card');

  if (top !== null) {
    // Return top N liked cards from the index
    const n = Math.min(Math.max(parseInt(top, 10) || 10, 1), 50);
    const raw = await env.COLLECTIONS.get(INDEX_KEY);
    if (!raw) return json([]);
    const index = JSON.parse(raw); // [{src, count}]
    return json(index.slice(0, n));
  }

  if (card !== null) {
    if (!CARD_RE.test(card)) return err('Invalid card key');
    const raw = await env.COLLECTIONS.get('like:' + card);
    return json({ count: raw ? parseInt(raw, 10) : 0 });
  }

  return err('Missing parameter: top or card');
}

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { card } = body;
  if (!card || !CARD_RE.test(card)) return err('Invalid card key');

  // Server-side rate limit per IP
  const ip = request.headers.get('CF-Connecting-IP') ||
             request.headers.get('X-Forwarded-For') ||
             'unknown';
  const rlKey = 'rl:' + ip + ':' + card;
  const rl = await env.COLLECTIONS.get(rlKey);
  if (rl) return json({ count: parseInt(await env.COLLECTIONS.get('like:' + card) || '0', 10), limited: true });

  // Set rate limit with 60s TTL (KV minimum is 60s)
  await env.COLLECTIONS.put(rlKey, '1', { expirationTtl: 60 });

  // Increment like count
  const likeKey = 'like:' + card;
  const prev = parseInt(await env.COLLECTIONS.get(likeKey) || '0', 10);
  const count = prev + 1;
  await env.COLLECTIONS.put(likeKey, String(count));

  // Update the likes index (top 50, sorted by count desc)
  const rawIndex = await env.COLLECTIONS.get(INDEX_KEY);
  let index = rawIndex ? JSON.parse(rawIndex) : [];

  const existing = index.findIndex(item => item.src === card);
  if (existing >= 0) {
    index[existing].count = count;
  } else {
    index.push({ src: card, count });
  }

  // Sort descending, keep top 50
  index.sort((a, b) => b.count - a.count);
  if (index.length > 50) index = index.slice(0, 50);

  await env.COLLECTIONS.put(INDEX_KEY, JSON.stringify(index));

  return json({ count });
}
