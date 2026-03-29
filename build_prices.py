#!/usr/bin/env python3
"""
Build per-card price data from Mercari listings.

Matching strategy:
  1. Extract (season, card_number) from listing title
  2. Map to src: cards/zutomayocard_{season}th_{number}.png
  3. Aggregate sold prices (buy) and sale prices (sell) per card
  4. Cards with no direct match fall back to rarity-level prices

Output: prices.js  — RARITY_PRICES + CARD_PRICES objects for the website
"""

import csv, json, re, io, sys
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

# Patterns like: No.019, No 019, 019/104, 031/104, SR 031, N 099, R 065
NUM_PATTERNS = [
    re.compile(r'No[.\s]?(\d{3})'),          # No.019, No 019
    re.compile(r'(\d{3})/\d+'),              # 019/104
    re.compile(r'(?:SR|UR|SE|SEC|PR|R|N)\s+(\d{3})\b'),  # SR 031, N 099
    re.compile(r'第\d+弾(\d{3})'),           # 第4弾018
    re.compile(r'[^\d](\d{3})[^\d]'),        # any 3-digit surrounded by non-digits
]

def detect_number(title: str) -> str | None:
    for pat in NUM_PATTERNS:
        m = pat.search(title)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 200:   # sanity: card numbers are in this range
                return str(n)
    return None

# ── Build per-card price lists ────────────────────────────────────────────────

# card_src → {sold: [prices], sale: [prices]}
card_prices: dict[str, dict] = defaultdict(lambda: {'sold': [], 'sale': []})
matched = unmatched = 0

for row in singles:
    title   = row['title']
    price   = int(row['price'])
    status  = row['status']  # sold_out | on_sale

    season = detect_season(title)
    number = detect_number(title)

    if season and number:
        src = f"cards/zutomayocard_{SEASON_FILE[season]}_{number}.png"
        if status == 'sold_out':
            card_prices[src]['sold'].append(price)
        else:
            card_prices[src]['sale'].append(price)
        matched += 1
    else:
        unmatched += 1

print(f'Matched: {matched}  Unmatched: {unmatched}')
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
    """Estimate a sell price from a buy price using a tiered markup."""
    if buy <= 300:   markup = 1.15
    elif buy <= 800: markup = 1.20
    elif buy <= 3000: markup = 1.18
    else:            markup = 1.15
    raw = buy * markup
    # Round to nearest 50
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
# Enforce sell >= buy at rarity level too
for r, p in rarity_prices.items():
    p['sell'] = max(p.get('sell', 0), p.get('buy', 0))

# ── Load cards.js to verify matched srcs and get coverage stats ──────────────

content = open('cards.js', encoding='utf-8').read()
m = re.search(r'const CARDS\s*=\s*(\[[\s\S]*?\]);', content)
js = re.sub(r',\s*}', '}', m.group(1))
js = re.sub(r',\s*]', ']', js)
cards = json.loads(js)

covered   = sum(1 for c in cards if c['src'] in card_price_map)
uncovered = len(cards) - covered
print(f'\nCard coverage: {covered}/{len(cards)} cards have individual prices')
print(f'  ({uncovered} will use rarity fallback)')

# ── Print sample of matched cards ────────────────────────────────────────────

print('\nSample per-card prices:')
for c in cards[:60]:
    if c['src'] in card_price_map:
        p = card_price_map[c['src']]
        print(f"  {c['src']:<45}  {c['rarity']:<4}  {c['name']:<25}  "
              f"buy=¥{p['buy']:,}  sell=¥{p['sell']:,}  "
              f"(n={p['sold_count']}sold/{p['sale_count']}sale)")

# ── Write prices.js ───────────────────────────────────────────────────────────

# Build lean output — only buy/sell per card (drop count metadata)
lean_card_prices = {
    src: {'buy': p['buy'], 'sell': p['sell']}
    for src, p in card_price_map.items()
}

with open('prices.js', 'w', encoding='utf-8') as f:
    f.write('// Auto-generated by build_prices.py from Mercari Japan data\n')
    f.write('// RARITY_PRICES = fallback when no per-card data\n')
    f.write('// CARD_PRICES   = specific prices where available\n\n')
    f.write('const RARITY_PRICES = ')
    json.dump({r: {'buy': v['buy'], 'sell': v['sell']}
               for r, v in rarity_prices.items()}, f, ensure_ascii=False, indent=2)
    f.write(';\n\n')
    f.write('const CARD_PRICES = ')
    json.dump(lean_card_prices, f, ensure_ascii=False, indent=2)
    f.write(';\n')

print(f'\nprices.js written  ({len(lean_card_prices)} per-card prices + rarity fallback)')
