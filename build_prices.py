#!/usr/bin/env python3
"""
Build per-card price data from Mercari listings.

Matching strategy (tried in order):
  1. Extract (season, card_number) from listing title
  2. Match by Japanese card name from card_names_ja.json  ← NEW
  3. Cards with no match fall back to rarity-level prices

Also outputs:
  - TOP_VALUE_SOLD   — top 5 sold listings with best value (cheapest vs rarity avg)
  - TOP_VALUE_LISTED — top 5 currently listed with best value

Output: prices.js  — RARITY_PRICES + CARD_PRICES + TOP_VALUE_SOLD + TOP_VALUE_LISTED

Run after:
  python scrape_card_names.py   (produces card_names_ja.json)
  python scrape_mercari.py      (produces mercari_listings.csv + mercari_analysis.json)
"""

import csv, json, os, re, io, sys
from collections import defaultdict
from statistics import median, mode as stat_mode

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ── Load listings ─────────────────────────────────────────────────────────────

with open('mercari_listings.csv', encoding='utf-8-sig', newline='') as f:
    listings = list(csv.DictReader(f))

singles = [r for r in listings if r['type'] == 'single']
print(f'Total single listings: {len(singles)}')

# ── Season detection ──────────────────────────────────────────────────────────

SEASON_PATTERNS = [
    (re.compile(r'第[一1]弾|1st|1弾'), '1'),
    (re.compile(r'第[二2]弾|2nd|2弾'), '2'),
    (re.compile(r'第[三3]弾|3rd|3弾'), '3'),
    (re.compile(r'第[四4]弾|4th|4弾'), '4'),
]

SEASON_FILE = {'1': '1st', '2': '2nd', '3': '3rd', '4': '4th'}

def detect_season(title: str) -> str | None:
    for pat, s in SEASON_PATTERNS:
        if pat.search(title):
            return s
    return None

# ── Card number detection ─────────────────────────────────────────────────────

NUM_PATTERNS = [
    re.compile(r'No[.\s]?(\d{3})'),                                  # No.019
    re.compile(r'(\d{3})/\d+'),                                      # 019/104
    re.compile(r'(?:SR|UR|SE|SEC|PR|R|N)\s+(\d{3})\b'),             # SR 031
    re.compile(r'第\d+弾(\d{3})'),                                    # 第4弾018
    re.compile(r'[^\d](\d{3})[^\d]'),                                # any 3-digit
]

def detect_number(title: str) -> str | None:
    for pat in NUM_PATTERNS:
        m = pat.search(title)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 200:
                return str(n)
    return None

# ── Japanese name matching ─────────────────────────────────────────────────────

def load_card_names_ja(path: str = 'card_names_ja.json') -> dict:
    """Load {src: {name_ja, season, number}} from card_names_ja.json."""
    if not os.path.exists(path):
        print(f'  ⚠ {path} not found — run scrape_card_names.py first')
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def build_name_lookup(card_names_ja: dict) -> list[tuple[str, str]]:
    """
    Build a list of (japanese_name, src) sorted longest-first.
    Longest-first ensures e.g. 'にらちゃん（正義）' matches before 'にらちゃん'.
    """
    pairs = []
    for src, info in card_names_ja.items():
        name = info.get('name_ja', '').strip()
        if name:
            pairs.append((name, src))
            # Also add version without full-width parentheses content
            # e.g. "にらちゃん（正義）" → also try "にらちゃん 正義" style
            inner = re.search(r'[（(]([^）)]+)[）)]', name)
            if inner:
                # "song name" alone is often used in Mercari titles
                song = inner.group(1).strip()
                char = name[:inner.start()].strip()
                if char and song:
                    # character + song as space-separated (lower priority)
                    pairs.append((f'{char} {song}', src))
                    pairs.append((f'{char}({song})', src))
                    pairs.append((f'{char}（{song}）', src))
    # Sort longest-first for greedy left-to-right matching
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs

def detect_name(title: str, name_lookup: list[tuple[str, str]]) -> str | None:
    """Return the card src if a Japanese card name is found in the title."""
    for name, src in name_lookup:
        if name in title:
            return src
    return None

# ── Load Japanese names ───────────────────────────────────────────────────────

print('\nLoading Japanese card names...')
card_names_ja = load_card_names_ja()
name_lookup   = build_name_lookup(card_names_ja)
print(f'  {len(card_names_ja)} cards with Japanese names loaded')
print(f'  {len(name_lookup)} name variants in lookup')

# ── Build per-card price lists ────────────────────────────────────────────────

# card_src → {sold: [prices], sale: [prices]}
card_prices: dict[str, dict] = defaultdict(lambda: {'sold': [], 'sale': []})

# Track individual listings per card for value analysis
# card_src → [{price, status, title}]
individual_listings: dict[str, list] = defaultdict(list)

matched_by_number = matched_by_name = unmatched = 0

for row in singles:
    title  = row['title']
    price  = int(row['price'])
    status = row['status']   # sold_out | on_sale

    season = detect_season(title)
    number = detect_number(title)
    src = None

    if season and number:
        src = f"cards/zutomayocard_{SEASON_FILE[season]}_{number}.png"
        matched_by_number += 1
    elif name_lookup:
        src = detect_name(title, name_lookup)
        if src:
            matched_by_name += 1

    if src:
        bucket = 'sold' if status == 'sold_out' else 'sale'
        card_prices[src][bucket].append(price)
        individual_listings[src].append({
            'price':  price,
            'status': status,
            'title':  title,
        })
    else:
        unmatched += 1

print(f'\nMatched by number: {matched_by_number}')
print(f'Matched by name:   {matched_by_name}')
print(f'Unmatched:         {unmatched}')
print(f'Unique cards with price data: {len(card_prices)}')

# ── Compute per-card estimates ────────────────────────────────────────────────

def safe_mode(prices: list[int]) -> int:
    if not prices:
        return 0
    try:
        return stat_mode(prices)
    except Exception:
        return round(median(prices))

def adaptive_sell(buy: int) -> int:
    if buy <= 300:    markup = 1.15
    elif buy <= 800:  markup = 1.20
    elif buy <= 3000: markup = 1.18
    else:             markup = 1.15
    raw = buy * markup
    return int(round(raw / 50) * 50)

def estimate(sold: list[int], sale: list[int]) -> dict:
    buy  = safe_mode(sold) if sold else 0
    sell = safe_mode(sale) if sale else 0
    if not sell and buy:
        sell = adaptive_sell(buy)
    if not buy:
        buy = sell
    sell = max(sell, buy)
    return {'buy': buy, 'sell': sell,
            'sold_count': len(sold), 'sale_count': len(sale)}

card_price_map = {}
for src, data in card_prices.items():
    card_price_map[src] = estimate(data['sold'], data['sale'])

# ── Load rarity fallback prices ───────────────────────────────────────────────

analysis = json.load(open('mercari_analysis.json', encoding='utf-8'))
rarity_prices = analysis['rarity_prices']
for r, p in rarity_prices.items():
    p['sell'] = max(p.get('sell', 0), p.get('buy', 0))

# ── Load cards.js for rarity per src ─────────────────────────────────────────

content = open('cards.js', encoding='utf-8').read()
m = re.search(r'const CARDS\s*=\s*(\[[\s\S]*?\]);', content)
js = re.sub(r',\s*}', '}', m.group(1))
js = re.sub(r',\s*]', ']', js)
cards = json.loads(js)

# Build rarity map and Korean name map from cards.js
rarity_for_src: dict[str, str] = {}
kr_name_for_src: dict[str, str] = {}
for c in cards:
    src = c.get('src', '')
    rarity_for_src[src]  = c.get('rarity', 'N')
    kr_name_for_src[src] = c.get('name', '')

covered   = sum(1 for c in cards if c['src'] in card_price_map)
uncovered = len(cards) - covered
print(f'\nCard coverage: {covered}/{len(cards)} cards have individual prices')
print(f'  ({uncovered} will use rarity fallback)')

# ── Print sample of matched cards ────────────────────────────────────────────

print('\nSample per-card prices:')
for c in cards[:60]:
    if c['src'] in card_price_map:
        p = card_price_map[c['src']]
        name_ja = card_names_ja.get(c['src'], {}).get('name_ja', '')
        print(f"  {c['src']:<45}  {c['rarity']:<4}  "
              f"{name_ja or c['name']:<30}  "
              f"buy=¥{p['buy']:,}  sell=¥{p['sell']:,}  "
              f"(n={p['sold_count']}sold/{p['sale_count']}sale)")

# ── Top-5 Best Value ──────────────────────────────────────────────────────────

def compute_top_value(status_filter: str, n: int = 5) -> list[dict]:
    """
    Return the n individual listings with the best value ratio:
      value_ratio = listing_price / rarity_buy_price   (lower = better deal)
    Only considers matched listings (src known) for the given status.
    Filters out extreme outliers (< 10% or > 200% of rarity price).
    """
    entries = []
    for src, listings in individual_listings.items():
        rarity = rarity_for_src.get(src, 'N')
        rarity_buy = rarity_prices.get(rarity, {}).get('buy', 0)
        if not rarity_buy:
            continue
        name_ja = card_names_ja.get(src, {}).get('name_ja', '')
        name_kr = kr_name_for_src.get(src, '')

        for listing in listings:
            if listing['status'] != status_filter:
                continue
            price = listing['price']
            ratio = price / rarity_buy
            # Skip nonsensical outliers
            if ratio < 0.05 or ratio > 3.0:
                continue
            entries.append({
                'src':        src,
                'name_ja':    name_ja,
                'name_kr':    name_kr,
                'rarity':     rarity,
                'price':      price,
                'rarity_avg': rarity_buy,
                'value_ratio': round(ratio, 3),
                'title':      listing['title'],
            })

    # Sort by ratio ascending (lowest price relative to rarity = best value)
    entries.sort(key=lambda x: x['value_ratio'])
    # De-duplicate: keep only the best listing per card src
    seen_srcs: set = set()
    deduped = []
    for e in entries:
        if e['src'] not in seen_srcs:
            seen_srcs.add(e['src'])
            deduped.append(e)
        if len(deduped) >= n:
            break
    return deduped

top5_sold   = compute_top_value('sold_out', n=5)
top5_listed = compute_top_value('on_sale',  n=5)

def print_top5(label: str, items: list[dict]):
    print(f'\n  ── {label} ───────────────────────────────────────────')
    for i, e in enumerate(items, 1):
        pct = int(e['value_ratio'] * 100)
        name = e['name_ja'] or e['name_kr'] or e['src']
        print(f'  {i}. {name:<30}  {e["rarity"]:<4}  '
              f'¥{e["price"]:,} vs avg ¥{e["rarity_avg"]:,}  ({pct}%)')
        print(f'     {e["title"][:70]}')

print_top5('TOP 5 BEST VALUE — Sold (completed sales)',   top5_sold)
print_top5('TOP 5 BEST VALUE — Listed (current listings)', top5_listed)

# ── Write prices.js ───────────────────────────────────────────────────────────

lean_card_prices = {
    src: {'buy': p['buy'], 'sell': p['sell']}
    for src, p in card_price_map.items()
}

# Slim down top-value output for JS (drop internal fields)
def slim_value(e: dict) -> dict:
    return {
        'src':        e['src'],
        'name_ja':    e['name_ja'],
        'name_kr':    e['name_kr'],
        'rarity':     e['rarity'],
        'price':      e['price'],
        'rarity_avg': e['rarity_avg'],
        'value_ratio': e['value_ratio'],
        'title':      e['title'],
    }

with open('prices.js', 'w', encoding='utf-8') as f:
    f.write('// Auto-generated by build_prices.py from Mercari Japan data\n')
    f.write('// RARITY_PRICES    = fallback when no per-card data\n')
    f.write('// CARD_PRICES      = specific prices where available\n')
    f.write('// TOP_VALUE_SOLD   = top 5 sold listings with best value (price vs rarity avg)\n')
    f.write('// TOP_VALUE_LISTED = top 5 current listings with best value\n\n')

    f.write('const RARITY_PRICES = ')
    json.dump({r: {'buy': v['buy'], 'sell': v['sell']}
               for r, v in rarity_prices.items()}, f, ensure_ascii=False, indent=2)
    f.write(';\n\n')

    f.write('const CARD_PRICES = ')
    json.dump(lean_card_prices, f, ensure_ascii=False, indent=2)
    f.write(';\n\n')

    f.write('const TOP_VALUE_SOLD = ')
    json.dump([slim_value(e) for e in top5_sold], f, ensure_ascii=False, indent=2)
    f.write(';\n\n')

    f.write('const TOP_VALUE_LISTED = ')
    json.dump([slim_value(e) for e in top5_listed], f, ensure_ascii=False, indent=2)
    f.write(';\n')

print(f'\nprices.js written')
print(f'  {len(lean_card_prices)} per-card prices')
print(f'  {len(top5_sold)} top-value sold entries')
print(f'  {len(top5_listed)} top-value listed entries')
