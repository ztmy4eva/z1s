// GET  /api/likes?top=N    → [{src, count}] top N by likes
// GET  /api/likes?card=KEY → {count}
// POST /api/likes {card: KEY} → increments count, returns {count}

const CARD_RE   = /^[a-zA-Z0-9/_\-.]{3,120}$/;
const INDEX_KEY = '_likes_index';

function json(data, status = 200) {
  return Response.json(data, { status });
}
function err(msg, status = 400) {
  return json({ error: msg }, status);
}

async function updateIndex(env, card, count) {
  try {
    const raw = await env.COLLECTIONS.get(INDEX_KEY);
    let index = raw ? JSON.parse(raw) : [];
    const i = index.findIndex(x => x.src === card);
    if (i >= 0) { index[i].count = count; }
    else        { index.push({ src: card, count }); }
    index.sort((a, b) => b.count - a.count);
    if (index.length > 50) index = index.slice(0, 50);
    await env.COLLECTIONS.put(INDEX_KEY, JSON.stringify(index));
  } catch {}
}

export async function onRequestGet({ request, env }) {
  const params = new URL(request.url).searchParams;
  const top    = params.get('top');
  const card   = params.get('card');

  if (top !== null) {
    const n   = Math.min(Math.max(parseInt(top, 10) || 10, 1), 50);
    const raw = await env.COLLECTIONS.get(INDEX_KEY);
    return json(raw ? JSON.parse(raw).slice(0, n) : []);
  }

  if (card !== null) {
    if (!CARD_RE.test(card)) return err('Invalid card key');
    const raw = await env.COLLECTIONS.get('like:' + card);
    return json({ count: raw ? parseInt(raw, 10) : 0 });
  }

  return err('Missing parameter: top or card');
}

export async function onRequestPost({ request, env, ctx }) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { card } = body;
  if (!card || !CARD_RE.test(card)) return err('Invalid card key');

  // Increment count (2 KV ops — fast path)
  const likeKey = 'like:' + card;
  const prev  = parseInt(await env.COLLECTIONS.get(likeKey) || '0', 10);
  const count = prev + 1;
  await env.COLLECTIONS.put(likeKey, String(count));

  // Update sorted index in the background — doesn't block the response
  ctx.waitUntil(updateIndex(env, card, count));

  return json({ count });
}
