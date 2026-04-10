# ZUTOMAYO CARD 한글 번역

A fan-made Korean translation and collection tracker for ZUTOMAYO trading cards (즛카드).

## Features

- **Browse** — View all cards across Seasons 1–4 with Korean translations (name, effect text, attributes)
- **Collection tracker** — Mark owned cards, track duplicates, and manage counts per card
- **Summary** — Full collection overview with season/rarity breakdown, owned/missing cards at a glance, and stats image export
- **Account system** — Create an account with a custom ID and 4-digit PIN; collections sync automatically to the cloud and are accessible from any device
- **Compare & Trade** — Load another user's collection by ID to see what you need, what you can offer, and get same-rarity (UR/SR) trade suggestions
- **Wishlist** — Star cards in your collection to mark them as wanted
- **Likes** — Like any card from the detail popup; view the top 10 most liked cards on the overview page
- **PDF export** — Print your full collection as a PDF with a preview page
- **Card hover preview** — Hover over cards in collection mode to see a large preview

## Stack

- Vanilla HTML / CSS / JS — no frameworks
- [Cloudflare Pages](https://pages.cloudflare.com/) for hosting
- [Cloudflare Workers KV](https://developers.cloudflare.com/kv/) for cloud storage (user collections + likes)

## Deployment

The site deploys automatically from this repository via Cloudflare Pages.

**KV binding required:** create a KV namespace in the Cloudflare dashboard and add it to `wrangler.toml`:

```toml
[[kv_namespaces]]
binding = "COLLECTIONS"
id = "your-kv-namespace-id"
```

## Disclaimer

This is an unofficial fan project. All card artwork and game content belong to their respective owners.
