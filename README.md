# Content Strategy App

Decides what content to create next for managed sites — turns query-level
signals (from Google Search Console, SEO Audit Engine, or manual input) into
scored, clustered opportunities, then into structured article briefs, and
tracks each through an editorial queue from `idea` to `published`.

## Boundaries (by design)

This app does **not**:
- publish to WordPress — that's `wp-site-connector`
- generate or edit images — that's the planned Image/Media app
- crawl or diagnose technical SEO — that's `SEO Audit Engine`

It orchestrates decisions and hands off clean, structured briefs to those
other layers.

## Capabilities (MVP)

| Function | Type | What it does |
|---|---|---|
| `create_site_profile` | write | Register a managed site (domain, brand, languages, categories, CTA) |
| `list_site_profiles` | read | List registered sites |
| `discover_opportunities` | write | Turn query signals into scored, clustered opportunities + queue items |
| `list_opportunities` | read | List opportunities, filter by site/status |
| `create_brief` | write | Generate a structured article brief from an opportunity |
| `list_briefs` | read | List article briefs, filter by site |
| `list_queue` | read | List the editorial queue, filter by site/status |
| `update_queue_status` | write | Move a queue item through its lifecycle |

`discover_opportunities` does not call other extensions itself — Webbee
fetches query data from the Google Search Console connector (or SEO Audit
Engine findings) first, then passes it into this tool as `queries`.

## Panels

- **Left** (`queue`) — editorial queue list, filterable by site, click to open detail
- **Center** (`brief`) — overlay detail view for one opportunity/brief with lifecycle actions
- **Right** (`sources`) — registered site profiles

## Architecture notes

See the Notes app entries:
- "System architecture — Content Strategy app MVP"
- "System architecture — Image / Media app MVP"
- "Product spec — AI Article Pipeline with Images (G4S / Climtec as first use case)"

for the full cross-app design this was derived from.
