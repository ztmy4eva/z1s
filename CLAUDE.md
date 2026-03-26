# CLAUDE.md — Project Overview

## What This Project Is

A single-page web application serving as a card database and viewer for **ZUTOMAYO CARD** (ズトマヨカード), a trading card game. The UI is fully in Korean (한글 번역 = Korean translation).

## Tech Stack

- **Pure HTML/CSS/Vanilla JavaScript** — no frameworks, no build tools, no npm
- All card data is embedded directly in `index.html` as `data-*` attributes on `<img>` elements
- `style.css` handles layout (CSS Grid + Flexbox)

## File Structure

```
z1s/
├── index.html        # Entire app: markup + embedded JS + all card data (8,200+ lines)
├── style.css         # Styles (170 lines)
├── cards/            # Season 1 images (~106 PNGs)
├── cards2/           # Season 2 images (~106 PNGs)
├── cards3/           # Season 3 images (~106 PNGs)
└── cards4/           # Season 4 images (~107 JPGs)
```

## Card Data Model

Each card is an `<img>` tag with these `data-*` attributes:

| Attribute | Values | Notes |
|---|---|---|
| `data-name` | Korean string | Card name |
| `data-season` | `1`–`4` | Which season |
| `data-rarity` | `UR`, `SR`, `R`, `N`, `SE` | Rarity tier |
| `data-type` | `character`, `enchant`, `area-enchant` | Card type |
| `data-attribute` | `어둠`, `화염`, `전기`, `바람` | Element (Darkness/Fire/Electricity/Wind) |
| `data-chronos` | `0`–`6` | Chronos timer cost |
| `data-power` | `밤 : X   ｜   낮 : Y` | Day/Night power values |
| `data-cost` | string | Power cost (uses `♦︎` symbols) |
| `data-send` | number | Send to Power value |
| `data-effect` | multi-line string | Card ability text |

## Application Logic (in `index.html`)

### Global State
```js
currentSeason  // 'all' | '1' | '2' | '3' | '4'
currentRarity  // 'all' | 'UR' | 'SR' | 'R' | 'N' | 'SE'
currentType    // 'all' | 'character' | 'enchant' | 'area-enchant'
```

### Key Functions
- `applyFilter()` — AND-combines all three filters, shows/hides cards
- `filterSeason(season, btn)` — sets season, enables sidebar
- `filterRarity(rarity, btn)` — toggles rarity (exclusive)
- `filterType(type, btn)` — toggles card type (exclusive)
- `showOverview(btn)` — shows overview image, disables sidebar/filters
- `openModal(card)` — populates and shows detail modal from card's data attributes
- `closeModal(event)` — closes modal on background click
- `setInfo(id, value)` — hides table rows when value is empty

## UI Structure

- **Top bar**: Season selector buttons (Overview, S1, S2, S3, S4)
- **Sidebar** (left, 200px): Rarity and type filter buttons — disabled in overview mode
- **Card grid**: `repeat(auto-fill, 160px)` responsive CSS grid
- **Modal**: Click a card → overlay with card image + stats table

## How to Run

Open `index.html` directly in a browser — no server needed.

## Notes for Editing

- Adding new cards: copy an existing `<img class="card" ...>` block in `index.html` and update all `data-*` attributes and `src`
- All cards for a given season live in one contiguous block within `index.html`
- Season 4 images use `.jpg`; earlier seasons use `.png`
- The overview image for season 1 is `cards/zc123.png`
