const ID_RE = /^[a-z0-9]{8}$/;
const TTL   = 60 * 60 * 24 * 365; // 1 year

function err(msg, status = 400) {
  return Response.json({ error: msg }, { status });
}

function generateId() {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let id = '';
  for (let i = 0; i < 8; i++) id += chars[Math.floor(Math.random() * chars.length)];
  return id;
}

// GET /api/collection?id=abc12345  → { id, code }
export async function onRequestGet({ request, env }) {
  const id = new URL(request.url).searchParams.get('id');
  if (!id || !ID_RE.test(id)) return err('Invalid ID');

  const code = await env.COLLECTIONS.get(id);
  if (!code) return err('Not found', 404);

  return Response.json({ id, code });
}

// POST /api/collection         { code }              → creates new ID → { id, code }
// POST /api/collection?id=...  { code }              → updates existing → { id, code }
export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { code } = body;
  if (!code || typeof code !== 'string' || code.length > 20000) return err('Invalid code');

  const url = new URL(request.url);
  let id = url.searchParams.get('id');

  if (id) {
    if (!ID_RE.test(id)) return err('Invalid ID');
  } else {
    // generate a new unique ID
    for (let i = 0; i < 5; i++) {
      const candidate = generateId();
      if (!(await env.COLLECTIONS.get(candidate))) { id = candidate; break; }
    }
    if (!id) return err('Could not generate ID, try again', 503);
  }

  await env.COLLECTIONS.put(id, code, { expirationTtl: TTL });
  return Response.json({ id, code });
}
