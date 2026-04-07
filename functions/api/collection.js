const ID_RE  = /^[a-z0-9]{8}$/;
const PIN_RE = /^\d{4}$/;
const TTL    = 60 * 60 * 24 * 365; // 1 year

function json(data, status = 200) {
  return Response.json(data, { status });
}
function err(msg, status = 400) {
  return json({ error: msg }, status);
}

function generateId() {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let id = '';
  for (let i = 0; i < 8; i++) id += chars[Math.floor(Math.random() * chars.length)];
  return id;
}

// GET /api/collection?id=abc12345
// Public read — anyone can view a collection by ID (for sharing / comparing).
// Returns { id, code, updated } — never exposes the pin.
export async function onRequestGet({ request, env }) {
  const id = new URL(request.url).searchParams.get('id');
  if (!id || !ID_RE.test(id)) return err('Invalid ID');

  const raw = await env.COLLECTIONS.get(id);
  if (!raw) return err('Not found', 404);

  const data = JSON.parse(raw);
  return json({ id, code: data.code, updated: data.updated });
}

// POST /api/collection
// Three operations based on body:
//
// 1) CREATE  { code, pin }           → generates new ID
// 2) UPDATE  { id, code, pin }       → overwrites if pin matches
// 3) LOGIN   { id, pin, login:true } → returns code if pin matches
export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return err('Invalid JSON'); }

  const { id, code, pin, login } = body;

  // ── LOGIN ──────────────────────────────────────────────
  if (login) {
    if (!id || !ID_RE.test(id))   return err('Invalid ID');
    if (!pin || !PIN_RE.test(pin)) return err('PIN은 숫자 4자리입니다');

    const raw = await env.COLLECTIONS.get(id);
    if (!raw) return err('존재하지 않는 ID입니다', 404);

    const data = JSON.parse(raw);
    if (data.pin !== pin) return err('PIN이 일치하지 않습니다', 403);

    return json({ id, code: data.code, updated: data.updated });
  }

  // ── CREATE or UPDATE ───────────────────────────────────
  if (!pin || !PIN_RE.test(pin))
    return err('PIN은 숫자 4자리입니다');
  if (!code || typeof code !== 'string' || code.length > 20000)
    return err('Invalid collection code');

  const now = Date.now();

  if (id) {
    // UPDATE existing
    if (!ID_RE.test(id)) return err('Invalid ID');

    const raw = await env.COLLECTIONS.get(id);
    if (!raw) return err('존재하지 않는 ID입니다', 404);

    const data = JSON.parse(raw);
    if (data.pin !== pin) return err('PIN이 일치하지 않습니다', 403);

    const updated = JSON.stringify({ code, pin, updated: now });
    await env.COLLECTIONS.put(id, updated, { expirationTtl: TTL });
    return json({ id, updated: now });
  }

  // CREATE new
  let newId;
  for (let i = 0; i < 10; i++) {
    const candidate = generateId();
    if (!(await env.COLLECTIONS.get(candidate))) { newId = candidate; break; }
  }
  if (!newId) return err('Could not generate ID, try again', 503);

  const data = JSON.stringify({ code, pin, updated: now });
  await env.COLLECTIONS.put(newId, data, { expirationTtl: TTL });
  return json({ id: newId, updated: now });
}
