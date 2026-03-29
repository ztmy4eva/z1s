#!/usr/bin/env python3
"""
Mercari Japan scraper + price analyzer for ZUTOMAYO CARD.

Strategy:
  - Scrape all pages via Playwright (intercepts API JSON)
  - Classify listings entirely in Python (titles have explicit rarity labels)
  - Send ONE compact summary to Ollama for price estimation
  - Output: per-listing table, per-rarity prices, all 425 cards with estimated prices

Install:
    pip install playwright requests
    playwright install chromium

Run (full):
    python scrape_mercari.py

Re-run analysis only (skips scraping):
    python scrape_mercari.py --analyze-only
"""

import asyncio, json, re, csv, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from urllib.parse import quote
from collections import defaultdict
from statistics import mode as stat_mode, median
from playwright.async_api import async_playwright
import requests

# ─── Config ───────────────────────────────────────────────────────────────────

SEARCH_KEYWORDS = ["ずとまよカード", "ズトマヨカード", "zutomayo card", "ZUTOMAYO CARD"]
STATUSES        = ["on_sale", "sold_out"]
MAX_PAGES       = 30

OLLAMA_URL     = "http://localhost:11434"
OLLAMA_TIMEOUT = 300   # one compact call — should be fast

OUTPUT_RAW      = "mercari_raw.json"
OUTPUT_LISTINGS = "mercari_listings.csv"
OUTPUT_PRICES   = "mercari_card_prices.csv"
OUTPUT_ANALYSIS = "mercari_analysis.json"

# ─── Phase 1: Scrape ─────────────────────────────────────────────────────────

def extract_items(data, status: str, seen: set, out: list):
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("title", "")
                price_raw = entry.get("price", 0)
                item_id = str(entry.get("id", ""))
                if name and price_raw:
                    if item_id and item_id in seen:
                        continue
                    try:
                        price = int(str(price_raw).replace(",","").replace("¥","").strip())
                    except (ValueError, TypeError):
                        price = 0
                    if price > 0:
                        if item_id:
                            seen.add(item_id)
                        out.append({"id": item_id, "title": name.strip(),
                                    "price": price, "status": status})
                for v in entry.values():
                    if isinstance(v, (dict, list)):
                        extract_items(v, status, seen, out)
        return
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, (dict, list)):
                extract_items(v, status, seen, out)


async def scrape_page(url: str, status: str, seen: set) -> list[dict]:
    items: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="ja-JP", user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        page = await ctx.new_page()

        async def on_response(resp):
            if resp.status != 200 or "mercari" not in resp.url:
                return
            if "json" not in resp.headers.get("content-type", ""):
                return
            try:
                extract_items(await resp.json(), status, seen, items)
            except Exception:
                pass

        page.on("response", on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            for _ in range(5):
                await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
        except Exception as e:
            print(f"      ⚠ {e}")
        await browser.close()
    return items


async def scrape_all() -> list[dict]:
    all_items: list[dict] = []
    seen: set = set()
    for kw in SEARCH_KEYWORDS:
        enc = quote(kw)
        for status in STATUSES:
            print(f"\n  '{kw}' / {status}")
            for pg in range(1, MAX_PAGES + 1):
                url = (f"https://jp.mercari.com/search?keyword={enc}"
                       f"&status={status}&page_token={quote(f'v1:{pg}')}"
                       f"&sort=created_time&order=desc")
                before = len(all_items)
                new = await scrape_page(url, status, seen)
                all_items.extend(new)
                added = len(all_items) - before
                print(f"    pg {pg:>2}: +{added:>3}  total={len(all_items)}")
                if added == 0:
                    break
    return all_items


# ─── Phase 2: Load cards.js ───────────────────────────────────────────────────

def load_cards(path: str = "cards.js") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"const CARDS\s*=\s*(\[[\s\S]*?\]);", content) or \
        re.search(r"const CARDS\s*=\s*(\[[\s\S]*\])", content)
    if not m:
        return []
    js = m.group(1)
    js = re.sub(r",\s*}", "}", js)
    js = re.sub(r",\s*]", "]", js)
    try:
        return json.loads(js)
    except json.JSONDecodeError:
        return []


# ─── Phase 3: Python classification ──────────────────────────────────────────

# Patterns that mark a listing as irrelevant (accessories, clothing, etc.)
IRRELEVANT_RE = re.compile(
    r"スリーブ|ローダー|デッキケース|バインダー|ウォールポケット|"
    r"ぬいぐるみ|フィギュア|ポスター|Tシャツ|ロンT|タオル|チケット|"
    r"DVD|Blu.ray|カレンダー|トートバッグ|ペンライト|写真集|"
    r"ねんどろいど|ステッカー|シール(?!付)",  # シール付 = sticker included → might be card
    re.IGNORECASE,
)

# Patterns that signal a bundle / multi-card listing
BUNDLE_RE = re.compile(
    r"まとめ|コンプ|全種|全カード|スターター|パック|BOX|ボックス|"
    r"セット(?!.*\b1枚\b)|"    # セット but not "1枚セット"
    r"(\d+)\s*枚(?!\s*セット入り)|"   # N枚 (not "N枚セット入り" which might be pack)
    r"(\d+)\s*点|"
    r"(\d+)\s*種",
    re.IGNORECASE,
)

# Rarity detection — order matters (check UR/SR before R)
RARITY_PATTERNS = [
    ("SEC", re.compile(r"\bSE\b|SE\s*\d|シークレット|ゴールド|金カード|\bSEC\b", re.IGNORECASE)),
    ("UR",  re.compile(r"\bUR\b", re.IGNORECASE)),
    ("SR",  re.compile(r"\bSR\b", re.IGNORECASE)),
    ("R",   re.compile(r"(?<![SsUuSs])\bR\b(?!\+)|[\s　]R[\s　\d]|^R[\s　]|[\s　]R$", re.IGNORECASE)),
    ("N",   re.compile(r"[\s　]N[\s　\d]|^N[\s　]|[\s　]N$|\bN\s+\d{3}\b")),
    ("PR",  re.compile(r"来場特典|ご当地|限定|プレミアム会員|PR\b", re.IGNORECASE)),
]

def detect_rarity(title: str) -> str:
    for rarity, pattern in RARITY_PATTERNS:
        if pattern.search(title):
            return rarity
    return "unknown"

def detect_bundle_size(title: str) -> int:
    """Return the number of cards if explicitly stated, else 0 (unknown)."""
    m = re.search(r"(\d+)\s*枚", title)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*点", title)
    if m:
        return int(m.group(1))
    return 0

def classify_listing(item: dict) -> dict:
    title = item["title"]

    # Irrelevant?
    if IRRELEVANT_RE.search(title):
        return {**item, "type": "irrelevant", "rarity": "—", "bundle_size": 0}

    # Not a ZUTOMAYO CARD listing?
    zutomayo_hints = ["ずとまよ", "ズトマヨ", "zutomayo", "ZUTOMAYO", "ずっと真夜中"]
    if not any(h.lower() in title.lower() for h in zutomayo_hints):
        return {**item, "type": "irrelevant", "rarity": "—", "bundle_size": 0}

    # Bundle?
    if BUNDLE_RE.search(title):
        size = detect_bundle_size(title)
        return {**item, "type": "bundle", "rarity": detect_rarity(title), "bundle_size": size}

    # Single card
    return {**item, "type": "single", "rarity": detect_rarity(title), "bundle_size": 1}


def classify_all(items: list[dict]) -> list[dict]:
    return [classify_listing(i) for i in items]


# ─── Phase 4: Ollama price estimation (ONE call) ─────────────────────────────

def get_ollama_model() -> str | None:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = r.json().get("models", [])
        if models:
            name = models[0]["name"]
            print(f"  Ollama model: {name}")
            return name
    except Exception as e:
        print(f"  ⚠ Ollama unreachable: {e}")
    return None


def safe_mode(prices: list[int]) -> int:
    if not prices:
        return 0
    try:
        return stat_mode(prices)
    except Exception:
        return round(median(prices))


def build_price_summary(classified: list[dict]) -> dict[str, dict]:
    """
    Compute median/mode prices per rarity from single card listings.
    Returns {rarity: {sold: [prices], sale: [prices], sold_mode, sale_mode}}
    """
    by_rarity: dict[str, dict] = defaultdict(lambda: {"sold": [], "sale": []})
    for item in classified:
        if item["type"] != "single":
            continue
        r = item["rarity"]
        if r == "—":
            continue
        price = item["price"]
        if item["status"] == "sold_out":
            by_rarity[r]["sold"].append(price)
        else:
            by_rarity[r]["sale"].append(price)

    summary = {}
    for r, data in by_rarity.items():
        sold_prices = sorted(data["sold"])
        sale_prices = sorted(data["sale"])
        summary[r] = {
            "sold_count": len(sold_prices),
            "sale_count": len(sale_prices),
            "sold_mode":  safe_mode(sold_prices),
            "sale_mode":  safe_mode(sale_prices),
            "sold_median": round(median(sold_prices)) if sold_prices else 0,
            "sale_median": round(median(sale_prices)) if sale_prices else 0,
            "sold_min": min(sold_prices) if sold_prices else 0,
            "sold_max": max(sold_prices) if sold_prices else 0,
            "sale_min": min(sale_prices) if sale_prices else 0,
            "sale_max": max(sale_prices) if sale_prices else 0,
        }
    return summary


def estimate_prices_ollama(summary: dict, model: str) -> dict:
    """Single Ollama call — given price summary stats, return clean rarity price table."""

    rows = []
    for r in ["N", "R", "SR", "UR", "SEC", "PR", "unknown"]:
        if r not in summary:
            rows.append(f"  {r}: no data")
            continue
        s = summary[r]
        rows.append(
            f"  {r}: sold={s['sold_count']}x  sold_mode=¥{s['sold_mode']}  "
            f"sold_range=¥{s['sold_min']}-¥{s['sold_max']}  "
            f"sale={s['sale_count']}x  sale_mode=¥{s['sale_mode']}  "
            f"sale_range=¥{s['sale_min']}-¥{s['sale_max']}"
        )
    stats_text = "\n".join(rows)

    prompt = f"""/no_think
You are a Japanese TCG market analyst. Based on Mercari Japan sales data for ZUTOMAYO CARD (ズトマヨカード) by the band ずっと真夜中でいいのに。, estimate fair market prices per rarity.

Rarities (ascending value): N < R < SR < UR < SEC, plus PR (promo).
- "sold" = actual transaction prices (most reliable signal)
- "sale" = current asking prices (upper bound)
- Use sold_mode as primary signal. Cross-check with sold_range for outliers.
- For rarities with no data, interpolate from neighboring rarities.
- Round to nearest ¥100.

RAW STATS:
{stats_text}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "rarity_prices": {{
    "N":   {{"buy": 0, "sell": 0, "confidence": "high|medium|low", "note": ""}},
    "R":   {{"buy": 0, "sell": 0, "confidence": "high|medium|low", "note": ""}},
    "SR":  {{"buy": 0, "sell": 0, "confidence": "high|medium|low", "note": ""}},
    "UR":  {{"buy": 0, "sell": 0, "confidence": "high|medium|low", "note": ""}},
    "SEC": {{"buy": 0, "sell": 0, "confidence": "high|medium|low", "note": ""}},
    "PR":  {{"buy": 0, "sell": 0, "confidence": "high|medium|low", "note": ""}}
  }},
  "summary": "brief methodology note"
}}"""

    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.1},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content.strip())
    return json.loads(content)


# ─── Phase 5: Assign prices to all cards ─────────────────────────────────────

def assign_card_prices(cards: list[dict], rarity_prices: dict) -> list[dict]:
    result = []
    for card in cards:
        r = card.get("rarity", "?")
        p = rarity_prices.get(r, rarity_prices.get("N", {"buy": 0, "sell": 0}))
        result.append({
            "name":          card.get("name", ""),
            "rarity":        r,
            "season":        card.get("season", ""),
            "type":          card.get("typelabel", ""),
            "buy_price_est": p.get("buy", 0),
            "sell_price_est":p.get("sell", 0),
            "src":           card.get("src", ""),
        })
    return result


# ─── Output helpers ───────────────────────────────────────────────────────────

def print_listings_table(classified: list[dict]):
    singles  = [i for i in classified if i["type"] == "single"]
    bundles  = [i for i in classified if i["type"] == "bundle"]
    irr      = [i for i in classified if i["type"] == "irrelevant"]
    print(f"\n  Single cards:  {len(singles)}")
    print(f"  Bundles:       {len(bundles)}")
    print(f"  Irrelevant:    {len(irr)}")

    print("\n  ── SINGLE CARDS ──────────────────────────────────────────────────────────")
    print(f"  {'STATUS':<10} {'PRICE':>8}  {'RARITY':<7}  TITLE")
    print("  " + "─"*75)
    for item in sorted(singles, key=lambda x: (x["rarity"], x["price"])):
        flag = "SOLD" if item["status"] == "sold_out" else "SALE"
        print(f"  {flag:<10} ¥{item['price']:>7,}  {item['rarity']:<7}  {item['title'][:60]}")


def print_rarity_table(rarity_prices: dict, summary_stats: dict):
    print("\n  ┌────────────────────────────────────────────────────────────────┐")
    print("  │  RARITY PRICE TABLE                                            │")
    print("  ├──────┬──────────────┬──────────────┬────────────┬─────────────┤")
    print("  │Rarity│  Buy (sold)  │  Sell (ask)  │Confidence  │ Note        │")
    print("  ├──────┼──────────────┼──────────────┼────────────┼─────────────┤")
    for r in ["N", "R", "SR", "UR", "SEC", "PR"]:
        p    = rarity_prices.get(r, {})
        buy  = f"¥{p.get('buy',0):,}"  if p.get("buy")  else "—"
        sell = f"¥{p.get('sell',0):,}" if p.get("sell") else "—"
        conf = p.get("confidence", "—")
        note = p.get("note", "")[:12]
        s    = summary_stats.get(r, {})
        cnt  = f"({s.get('sold_count',0)}sold/{s.get('sale_count',0)}sale)"
        print(f"  │{r:<6}│{buy:>14}│{sell:>14}│{conf:<12}│{note:<13}│  {cnt}")
    print("  └──────┴──────────────┴──────────────┴────────────┴─────────────┘")


# ─── Main ─────────────────────────────────────────────────────────────────────

async def run(skip_scrape: bool = False):
    print("╔══════════════════════════════════════════════════════╗")
    print("║   ZUTOMAYO CARD – Mercari Scraper + Price Analyzer   ║")
    print("╚══════════════════════════════════════════════════════╝\n")

    # ── 1: Scrape ──────────────────────────────────────────────────
    if skip_scrape:
        print("【Phase 1】 Loading existing mercari_raw.json...")
        with open(OUTPUT_RAW, encoding="utf-8") as f:
            all_items = json.load(f)
    else:
        print("【Phase 1】 Scraping Mercari Japan...")
        all_items = await scrape_all()
        with open(OUTPUT_RAW, "w", encoding="utf-8") as f:
            json.dump(all_items, f, ensure_ascii=False, indent=2)

    on_n  = sum(1 for i in all_items if i["status"] == "on_sale")
    sol_n = sum(1 for i in all_items if i["status"] == "sold_out")
    print(f"\n  {len(all_items)} total unique listings  ({on_n} on sale | {sol_n} sold)")

    # ── 2: Load cards ──────────────────────────────────────────────
    print("\n【Phase 2】 Loading cards.js...")
    cards = load_cards("cards.js")
    print(f"  {len(cards)} cards loaded")

    # ── 3: Classify in Python ─────────────────────────────────────
    print("\n【Phase 3】 Classifying listings (Python)...")
    classified = classify_all(all_items)

    singles = [i for i in classified if i["type"] == "single"]
    bundles = [i for i in classified if i["type"] == "bundle"]
    irr     = [i for i in classified if i["type"] == "irrelevant"]
    print(f"  → Single: {len(singles)}  Bundles: {len(bundles)}  Irrelevant: {len(irr)}")

    print_listings_table(classified)

    # Save listings CSV
    with open(OUTPUT_LISTINGS, "w", encoding="utf-8-sig", newline="") as f:
        fields = ["status", "price", "type", "rarity", "bundle_size", "title"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(classified)
    print(f"\n  → Listings saved to {OUTPUT_LISTINGS}")

    # ── 4: Price estimation ───────────────────────────────────────
    print("\n【Phase 4】 Computing price stats per rarity...")
    summary_stats = build_price_summary(classified)
    for r, s in sorted(summary_stats.items()):
        print(f"  {r:<6}: sold={s['sold_count']}x mode=¥{s['sold_mode']:,}  "
              f"sale={s['sale_count']}x mode=¥{s['sale_mode']:,}")

    print("\n  Asking Ollama to estimate final prices...")
    model = get_ollama_model()
    rarity_prices = {}

    if model:
        try:
            result = estimate_prices_ollama(summary_stats, model)
            rarity_prices = result.get("rarity_prices", {})
            summary_text  = result.get("summary", "")
            print_rarity_table(rarity_prices, summary_stats)
            if summary_text:
                print(f"\n  AI: {summary_text}")
        except Exception as e:
            print(f"  ⚠ Ollama failed: {e}")
            print("  Falling back to Python mode prices...")
            for r, s in summary_stats.items():
                rarity_prices[r] = {
                    "buy":  s["sold_mode"] or s["sale_mode"],
                    "sell": s["sale_mode"] or s["sold_mode"],
                    "confidence": "medium", "note": "python fallback"
                }
    else:
        print("  ⚠ Ollama not available. Using Python stats as fallback.")
        for r, s in summary_stats.items():
            rarity_prices[r] = {
                "buy":  s["sold_mode"] or s["sale_mode"],
                "sell": s["sale_mode"] or s["sold_mode"],
                "confidence": "medium", "note": "no ollama"
            }

    # ── 5: Save analysis + card prices ────────────────────────────
    analysis_out = {
        "total_listings": len(all_items),
        "singles": len(singles),
        "bundles": len(bundles),
        "irrelevant": len(irr),
        "summary_stats": summary_stats,
        "rarity_prices": rarity_prices,
    }
    with open(OUTPUT_ANALYSIS, "w", encoding="utf-8") as f:
        json.dump(analysis_out, f, ensure_ascii=False, indent=2)
    print(f"\n  → Analysis saved to {OUTPUT_ANALYSIS}")

    if cards and rarity_prices:
        print("\n【Phase 5】 Assigning prices to all cards...")
        card_prices = assign_card_prices(cards, rarity_prices)
        with open(OUTPUT_PRICES, "w", encoding="utf-8-sig", newline="") as f:
            fields = ["name", "rarity", "season", "type",
                      "buy_price_est", "sell_price_est", "src"]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(card_prices)
        print(f"  → {len(card_prices)} cards saved to {OUTPUT_PRICES}")

    print("\n✓  Done.\n")
    print(f"  {OUTPUT_RAW:<35} raw scraped listings")
    print(f"  {OUTPUT_ANALYSIS:<35} price stats + Ollama estimates")
    print(f"  {OUTPUT_LISTINGS:<35} per-listing classified table")
    if cards and rarity_prices:
        print(f"  {OUTPUT_PRICES:<35} all 425 cards with estimated prices")


if __name__ == "__main__":
    asyncio.run(run(skip_scrape="--analyze-only" in sys.argv))
