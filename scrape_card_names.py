#!/usr/bin/env python3
"""
Scrape Japanese card names from zutomayocard.net/gallery/ using Playwright.
Falls back to official zutomayo.net gallery pages.

Credentials (if the gallery requires HTTP Basic Auth):
    export ZTMYCARD_USER=yourusername
    export ZTMYCARD_PASS=yourpassword

Output: card_names_ja.json
  {"cards/zutomayocard_1st_1.png": {"name_ja": "...", "season": "1", "number": 1}, ...}

Run:
    python scrape_card_names.py
"""

import asyncio, json, os, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

# ── Credentials (optional, for sites behind HTTP Basic Auth) ──────────────────
ZTMYCARD_USER = os.environ.get('ZTMYCARD_USER', '')
ZTMYCARD_PASS = os.environ.get('ZTMYCARD_PASS', '')

# ── Constants ─────────────────────────────────────────────────────────────────
SEASON_FILE = {'1': '1st', '2': '2nd', '3': '3rd', '4': '4th'}
SEASON_CHAR_MAP = {
    '一': '1', '二': '2', '三': '3', '四': '4',
    '1': '1', '2': '2', '3': '3', '4': '4',
}
OUTPUT_FILE = 'card_names_ja.json'

# ── Text patterns ─────────────────────────────────────────────────────────────

# "第一弾 001/104 カード名" or "第一弾【1 / 104】カード名"
WIKI_RE = re.compile(
    r'第([一二三四1-4])弾\s*[【\[]?\s*(\d{1,3})\s*/\s*\d+\s*[】\]]?\s*'
    r'([^\n\r<|【\[]{2,60})',
    re.UNICODE,
)

# "001 / 104\nカード名" — number and name on adjacent lines (common in gallery grids)
GRID_RE = re.compile(
    r'(\d{1,3})\s*/\s*(?:104|106)\s*[\n\r\s]*([^\n\r<|【\[0-9]{2,60})',
    re.UNICODE,
)

# JSON key patterns for card data
CARD_JSON_NAME_KEYS  = ('name', 'card_name', 'cardName', 'name_ja', 'jp_name', 'title', 'カード名')
CARD_JSON_NUM_KEYS   = ('number', 'no', 'card_no', 'cardNumber', 'num', 'card_number', 'order')
CARD_JSON_SEASON_KEYS = ('season', 'vol', 'volume', 'set', 'dan', 'edition', 'pack')

# Inline JS data variables (common in SSR / Next.js / Nuxt apps)
JS_DATA_RE = re.compile(
    r'(?:window\.__(?:INITIAL|NUXT|DATA|STATE)__\s*=\s*|'
    r'__NUXT__\s*=\s*|'
    r'window\.__props__\s*=\s*)'
    r'(\{[\s\S]{20,})',
    re.IGNORECASE,
)

# ── Site targets ──────────────────────────────────────────────────────────────
# Each: (label, [urls])
TARGETS = [
    ('zutomayocard.net gallery', [
        'https://zutomayocard.net/gallery/',
        'https://zutomayocard.net/gallery',
        # Season-specific pages (common patterns)
        'https://zutomayocard.net/gallery/?season=1',
        'https://zutomayocard.net/gallery/?season=2',
        'https://zutomayocard.net/gallery/?season=3',
        'https://zutomayocard.net/gallery/?season=4',
        'https://zutomayocard.net/gallery/1',
        'https://zutomayocard.net/gallery/2',
        'https://zutomayocard.net/gallery/3',
        'https://zutomayocard.net/gallery/4',
        'https://zutomayocard.net/gallery/?vol=1',
        'https://zutomayocard.net/gallery/?vol=2',
        'https://zutomayocard.net/gallery/?vol=3',
        'https://zutomayocard.net/gallery/?vol=4',
        # Also try root paths
        'https://zutomayocard.net/',
        'https://zutomayocard.net/cards',
        'https://zutomayocard.net/cardlist',
    ]),
    ('zutomayo.net official gallery', [
        'https://zutomayo.net/thebattlebegins_cardsearch/',
        'https://zutomayo.net/thebattlebegins_cardsearch_3rd/',
        'https://zutomayo.net/thebattlebegins_cardsearch_4th/',
    ]),
    ('zutomayo-card.com wiki', [
        'https://zutomayo-card.com/',
        'https://zutomayo-card.com/cards',
    ]),
]

# ── Extraction helpers ─────────────────────────────────────────────────────────

def clean_name(raw: str) -> str:
    raw = re.sub(r'\s*[|｜].*$', '', raw)
    raw = re.sub(r'\s*[-‐]\s*[Zずzｚ]utomayo.*$', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'<.*$', '', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()


def extract_from_text(text: str, results: dict) -> int:
    before = len(results)

    # Pattern 1: wiki-style "第一弾【N / 104】 名前"
    for m in WIKI_RE.finditer(text):
        season = SEASON_CHAR_MAP.get(m.group(1))
        if not season:
            continue
        try:
            num = int(m.group(2))
        except ValueError:
            continue
        if not (1 <= num <= 200):
            continue
        name = clean_name(m.group(3))
        if name and len(name) >= 2:
            results[(season, num)] = name

    # Pattern 2: grid-style "001 / 104\n名前"
    for m in GRID_RE.finditer(text):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        if not (1 <= num <= 200):
            continue
        name = clean_name(m.group(2))
        if name and len(name) >= 2:
            # Without season context we can't reliably assign — skip
            # (relies on wiki pattern above for season info)
            pass

    return len(results) - before


def extract_from_json(data, results: dict, season_hint: str = None, _depth: int = 0) -> int:
    if _depth > 12:
        return 0
    before = len(results)
    if isinstance(data, dict):
        name = None
        for k in CARD_JSON_NAME_KEYS:
            v = data.get(k)
            if isinstance(v, str) and 2 <= len(v) <= 60:
                name = v
                break

        num = None
        for k in CARD_JSON_NUM_KEYS:
            v = data.get(k)
            if v is not None:
                try:
                    n = int(str(v).lstrip('0') or '0')
                    if 1 <= n <= 200:
                        num = n
                        break
                except (ValueError, TypeError):
                    pass

        season = season_hint
        for k in CARD_JSON_SEASON_KEYS:
            v = data.get(k)
            if v is not None:
                s = str(v).strip()
                mapped = SEASON_CHAR_MAP.get(s) or SEASON_CHAR_MAP.get(s[:1])
                if mapped:
                    season = mapped
                    break
                if s in ('1', '2', '3', '4'):
                    season = s
                    break
                if s in ('1st', '2nd', '3rd', '4th'):
                    season = s[0]
                    break

        if name and num and season:
            results[(season, num)] = name

        for v in data.values():
            if isinstance(v, (dict, list)):
                extract_from_json(v, results, season, _depth + 1)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                extract_from_json(item, results, season_hint, _depth + 1)

    return len(results) - before


def extract_from_inline_js(html: str, results: dict) -> int:
    """Try to extract JSON data embedded in <script> tags."""
    before = len(results)
    # Find inline data variables (Next.js, Nuxt, etc.)
    for m in JS_DATA_RE.finditer(html):
        chunk = m.group(1)
        # Try to parse as JSON (might have trailing JS)
        for end in range(min(len(chunk), 500_000), 0, -1000):
            try:
                data = json.loads(chunk[:end])
                extract_from_json(data, results)
                break
            except (json.JSONDecodeError, ValueError):
                pass

    # Also look for all <script> tags with likely card data
    script_re = re.compile(r'<script[^>]*>([\s\S]*?)</script>', re.IGNORECASE)
    for m in script_re.finditer(html):
        content = m.group(1)
        if any(kw in content for kw in ['にら', 'グレイ', 'ニラ', '第一弾', '第二弾',
                                          'card_name', 'cardName', 'name_ja']):
            extract_from_text(content, results)
            # Try JSON arrays/objects
            for json_m in re.finditer(r'[\[{][\s\S]{50,}', content):
                try:
                    data = json.loads(json_m.group(0))
                    extract_from_json(data, results)
                    break
                except (json.JSONDecodeError, ValueError):
                    pass

    return len(results) - before


# ── Per-URL scraper ────────────────────────────────────────────────────────────

async def scrape_url(page, url: str, results: dict) -> int:
    before = len(results)
    try:
        resp = await page.goto(url, wait_until='domcontentloaded', timeout=22_000)
        if not resp or resp.status not in (200, 304):
            return 0

        await page.wait_for_timeout(4_000)   # allow JS to render

        # Inner text
        try:
            text = await page.evaluate('() => document.body.innerText')
            extract_from_text(text, results)
        except Exception:
            pass

        # Raw HTML (catches hidden data, SSR JSON, inline scripts)
        try:
            html = await page.content()
            extract_from_text(html, results)
            extract_from_inline_js(html, results)
        except Exception:
            pass

        # Follow same-host card/gallery links one level deep
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc
            links = await page.evaluate(r"""() => {
                const seen = new Set();
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => { if (seen.has(h)) return false; seen.add(h);
                        return /card|vol|season|弾|gallery|list/i.test(h); })
                    .slice(0, 50);
            }""")
            for link in links:
                if not link.startswith('http'):
                    continue
                if urlparse(link).netloc != host:
                    continue
                if link == url:
                    continue
                try:
                    r2 = await page.goto(link, wait_until='domcontentloaded', timeout=15_000)
                    if r2 and r2.status == 200:
                        await page.wait_for_timeout(3_000)
                        t2 = await page.evaluate('() => document.body.innerText')
                        extract_from_text(t2, results)
                        h2 = await page.content()
                        extract_from_text(h2, results)
                        extract_from_inline_js(h2, results)
                except Exception:
                    pass
        except Exception:
            pass

    except Exception as e:
        print(f'    ⚠ {url}: {e}')
        return 0

    return len(results) - before


# ── Main ───────────────────────────────────────────────────────────────────────

async def run():
    print('╔══════════════════════════════════════════════════════╗')
    print('║  Scrape Japanese Card Names — zutomayocard.net/gallery/║')
    print('╚══════════════════════════════════════════════════════╝\n')

    if ZTMYCARD_USER:
        print(f'  Using HTTP credentials for: {ZTMYCARD_USER}')
    else:
        print('  No credentials set. If the site needs auth, set:')
        print('    export ZTMYCARD_USER=yourusername')
        print('    export ZTMYCARD_PASS=yourpassword\n')

    results: dict = {}  # (season, number) → Japanese name

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Build context options
        ctx_kwargs = dict(
            locale='ja-JP',
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        )
        if ZTMYCARD_USER:
            ctx_kwargs['http_credentials'] = {
                'username': ZTMYCARD_USER,
                'password': ZTMYCARD_PASS,
            }

        ctx = await browser.new_context(**ctx_kwargs)

        # Intercept API JSON + card-data JS files
        intercepted_json: list = []
        intercepted_js:   list = []

        async def on_response(resp):
            if resp.status != 200:
                return
            ct  = resp.headers.get('content-type', '')
            url = resp.url
            try:
                if 'json' in ct:
                    data = await resp.json()
                    intercepted_json.append(data)
                elif 'javascript' in ct or url.endswith('.js'):
                    text = await resp.text()
                    if any(kw in text for kw in
                           ['にら', 'グレイ', 'ニラ', '第一弾', '第二弾',
                            'card_name', 'cardName', 'name_ja']):
                        intercepted_js.append(text)
            except Exception:
                pass

        page = await ctx.new_page()
        page.on('response', on_response)

        for label, urls in TARGETS:
            print(f'\n【{label}】')
            site_found = 0
            for url in urls:
                print(f'  → {url}')
                added = await scrape_url(page, url, results)
                if added:
                    print(f'    +{added} card names  (total: {len(results)})')
                    site_found += added
                if len(results) >= 400:
                    print('  ✓ Sufficient coverage reached, stopping early.')
                    break
            if site_found:
                print(f'  ✓ {site_found} names from {label}')
            else:
                print(f'  — nothing found at {label}')

            if len(results) >= 400:
                break

        await page.close()

        # Process intercepted responses
        print('\n【Processing intercepted API / JS responses】')
        json_added = sum(extract_from_json(d, results) for d in intercepted_json)
        js_added   = sum(extract_from_text(t, results) for t in intercepted_js)
        if json_added or js_added:
            print(f'  +{json_added} from JSON API,  +{js_added} from JS files')
        else:
            print('  (nothing new)')

        await browser.close()

    print(f'\n  Total unique entries: {len(results)}')

    # Build output
    output = {}
    for (season, number), name in sorted(results.items()):
        sf = SEASON_FILE.get(season)
        if not sf:
            continue
        src = f'cards/zutomayocard_{sf}_{number}.png'
        output[src] = {'name_ja': name, 'season': season, 'number': number}

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_expected = 107 + 106 + 106 + 107
    pct = 100 * len(output) // total_expected if total_expected else 0
    print(f'  → {OUTPUT_FILE} written')
    print(f'  → Coverage: {len(output)}/{total_expected} ({pct}%)')

    if output:
        print('\n  Sample:')
        for src, info in list(output.items())[:8]:
            print(f'    {src:<45}  {info["name_ja"]}')

    if pct < 30:
        print('\n  ⚠ Low coverage. Tips:')
        print('    1. Set ZTMYCARD_USER / ZTMYCARD_PASS if the site needs auth')
        print('    2. Run with --verbose to see full page content')
        print('    3. The site may use client-side JS rendering — try adding')
        print('       a longer wait or scrolling the page')


if __name__ == '__main__':
    asyncio.run(run())
