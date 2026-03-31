#!/usr/bin/env python3
"""
Scrape Japanese card names from zutomayocard.net using Playwright.
Also tries official zutomayo.net gallery pages as fallback.

Output: card_names_ja.json
  {"cards/zutomayocard_1st_1.png": {"name_ja": "...", "season": "1", "number": 1}, ...}

Run:
    python scrape_card_names.py
"""

import asyncio, json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

# ── Constants ─────────────────────────────────────────────────────────────────

SEASON_FILE = {'1': '1st', '2': '2nd', '3': '3rd', '4': '4th'}

SEASON_CHAR_MAP = {
    '一': '1', '二': '2', '三': '3', '四': '4',
    '1': '1', '2': '2', '3': '3', '4': '4',
}

OUTPUT_FILE = 'card_names_ja.json'

# Pattern: 第一弾【9 / 104】 Card Name  (wiki-style, from page text or title)
WIKI_RE = re.compile(
    r'第([一二三四1-4])弾\s*[【\[]\s*(\d{1,3})\s*/\s*\d+\s*[】\]]\s*'
    r'([^\n\r<|【\[]{2,60})',
    re.UNICODE,
)

# Pattern: No.009/104  (sometimes card number/total appears in HTML)
# Used to detect pages likely containing card data
CARD_NUM_RE = re.compile(r'(\d{1,3})\s*/\s*104', re.UNICODE)

# ── Site targets (priority order) ─────────────────────────────────────────────

# Each entry: (label, list_of_urls_to_try)
TARGETS = [
    ('zutomayocard.net', [
        'https://zutomayocard.net/',
        'https://zutomayocard.net/cards',
        'https://zutomayocard.net/card-list',
        'https://zutomayocard.net/cardlist',
        'https://zutomayocard.net/gallery',
        'https://zutomayocard.net/list',
        'https://zutomayocard.net/vol1',
        'https://zutomayocard.net/vol2',
        'https://zutomayocard.net/vol3',
        'https://zutomayocard.net/vol4',
        'https://zutomayocard.net/1st',
        'https://zutomayocard.net/2nd',
        'https://zutomayocard.net/3rd',
        'https://zutomayocard.net/4th',
        'https://zutomayocard.net/season/1',
        'https://zutomayocard.net/season/2',
        'https://zutomayocard.net/season/3',
        'https://zutomayocard.net/season/4',
    ]),
    ('zutomayo.net official gallery', [
        'https://zutomayo.net/thebattlebegins_cardsearch/',
        'https://zutomayo.net/thebattlebegins_cardsearch_3rd/',
        'https://zutomayo.net/thebattlebegins_cardsearch_4th/',
    ]),
    ('zutomayo-card.com wiki', [
        'https://zutomayo-card.com/',
        'https://zutomayo-card.com/cards',
        'https://zutomayo-card.com/cardlist',
    ]),
]

# ── Extraction helpers ─────────────────────────────────────────────────────────

def clean_name(raw: str) -> str:
    """Strip trailing junk from an extracted card name."""
    # Remove " | Site Name" suffix
    raw = re.sub(r'\s*[|｜].*$', '', raw)
    # Remove " - Site Name" suffix
    raw = re.sub(r'\s*[-‐]\s*Zutomayo.*$', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*[-‐]\s*ずとまよ.*$', '', raw)
    # Remove trailing HTML artifacts
    raw = re.sub(r'<.*$', '', raw)
    return raw.strip()


def extract_from_text(text: str, results: dict) -> int:
    """
    Parse text for wiki-style card name patterns.
    Returns number of new entries added.
    """
    before = len(results)
    for m in WIKI_RE.finditer(text):
        season_char = m.group(1)
        season = SEASON_CHAR_MAP.get(season_char)
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
    return len(results) - before


def extract_from_json(data, results: dict, _depth: int = 0) -> int:
    """
    Recursively scan a JSON object for card-like entries {name, number, season}.
    Returns number of new entries added.
    """
    if _depth > 12:
        return 0
    before = len(results)
    if isinstance(data, dict):
        name = None
        for k in ('name', 'card_name', 'cardName', 'jp_name', 'name_ja',
                  'title', 'カード名', 'cardTitle'):
            v = data.get(k)
            if isinstance(v, str) and len(v) >= 2:
                name = v
                break

        num = None
        for k in ('number', 'no', 'card_no', 'cardNumber', 'num', 'card_number'):
            v = data.get(k)
            if v is not None:
                try:
                    n = int(v)
                    if 1 <= n <= 200:
                        num = n
                        break
                except (ValueError, TypeError):
                    pass

        season = None
        for k in ('season', 'vol', 'volume', 'set', 'dan', 'edition'):
            v = data.get(k)
            if v is not None:
                s = str(v).strip()
                mapped = SEASON_CHAR_MAP.get(s) or SEASON_CHAR_MAP.get(s[0] if s else '')
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
                extract_from_json(v, results, _depth + 1)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                extract_from_json(item, results, _depth + 1)

    return len(results) - before


# ── Per-URL scraper ────────────────────────────────────────────────────────────

async def scrape_url(page, url: str, results: dict) -> int:
    """
    Navigate to one URL, wait for dynamic content, extract card names.
    Returns number of new entries added.
    """
    before = len(results)
    try:
        resp = await page.goto(url, wait_until='domcontentloaded', timeout=22_000)
        if not resp or resp.status not in (200, 301, 302):
            return 0
        # Wait for JS rendering
        await page.wait_for_timeout(3_000)

        # Inner text (rendered)
        try:
            text = await page.evaluate('() => document.body.innerText')
            extract_from_text(text, results)
        except Exception:
            pass

        # Raw HTML (catches data in attributes / hidden elements)
        try:
            html = await page.content()
            extract_from_text(html, results)
        except Exception:
            pass

        # Discover internal card-list links and follow them (one level deep)
        try:
            links = await page.evaluate(r"""() => {
                const seen = new Set();
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => {
                        if (seen.has(h)) return false;
                        seen.add(h);
                        return /card|vol|season|弾|list|gallery/i.test(h);
                    })
                    .slice(0, 40);
            }""")
        except Exception:
            links = []

        for link in links:
            if link == url or not link.startswith('http'):
                continue
            # Only follow same-host links
            from urllib.parse import urlparse
            if urlparse(link).netloc != urlparse(url).netloc:
                continue
            try:
                r2 = await page.goto(link, wait_until='domcontentloaded', timeout=15_000)
                if r2 and r2.status == 200:
                    await page.wait_for_timeout(2_000)
                    t2 = await page.evaluate('() => document.body.innerText')
                    extract_from_text(t2, results)
                    h2 = await page.content()
                    extract_from_text(h2, results)
            except Exception:
                pass

    except Exception as e:
        print(f'    ⚠ {url}: {e}')
        return 0

    added = len(results) - before
    return added


# ── Main ───────────────────────────────────────────────────────────────────────

async def run():
    print('╔══════════════════════════════════════════════════════╗')
    print('║  Scrape Japanese Card Names — zutomayocard.net       ║')
    print('╚══════════════════════════════════════════════════════╝\n')

    results: dict = {}   # (season_str, number_int) → Japanese name

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale='ja-JP',
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        )

        # Intercept API JSON and card-data JS files ──────────────────────────
        intercepted_json = []
        intercepted_js   = []

        async def on_response(resp):
            if resp.status != 200:
                return
            ct = resp.headers.get('content-type', '')
            url = resp.url
            try:
                if 'json' in ct:
                    data = await resp.json()
                    intercepted_json.append(data)
                elif 'javascript' in ct or url.endswith('.js'):
                    text = await resp.text()
                    # Only keep JS that looks like card data
                    if any(kw in text for kw in
                           ['にら', 'グレイ', 'ニラ', '第一弾', '第二弾',
                            'card_name', 'cardName', 'name_ja']):
                        intercepted_js.append(text)
            except Exception:
                pass

        # ── Scrape each target ───────────────────────────────────────────────
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
            if site_found:
                print(f'  ✓ {site_found} names from {label}')
            else:
                print(f'  — nothing found at {label}')

        await page.close()

        # ── Process intercepted responses ────────────────────────────────────
        print('\n【Processing intercepted API/JS responses】')
        json_added = sum(extract_from_json(d, results) for d in intercepted_json)
        js_added   = sum(extract_from_text(t, results) for t in intercepted_js)
        if json_added or js_added:
            print(f'  +{json_added} from JSON API,  +{js_added} from JS files')

        await browser.close()

    print(f'\n  Total unique card entries found: {len(results)}')

    # ── Build output: src → {name_ja, season, number} ────────────────────────
    output = {}
    for (season, number), name in sorted(results.items()):
        sf = SEASON_FILE.get(season)
        if not sf:
            continue
        src = f'cards/zutomayocard_{sf}_{number}.png'
        output[src] = {
            'name_ja': name,
            'season':  season,
            'number':  number,
        }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_expected = 107 + 106 + 106 + 107  # 4 seasons
    pct = 100 * len(output) // total_expected if total_expected else 0
    print(f'  → {OUTPUT_FILE} written')
    print(f'  → Coverage: {len(output)}/{total_expected} ({pct}%)')

    if len(output) < 50:
        print('\n  ⚠  Low coverage — the site may use a structure not yet handled.')
        print('     Inspect intercepted responses or try --verbose for details.')
    else:
        print('\n  Sample card names:')
        for src, info in list(output.items())[:10]:
            print(f'    {src:<45}  {info["name_ja"]}')


if __name__ == '__main__':
    asyncio.run(run())
