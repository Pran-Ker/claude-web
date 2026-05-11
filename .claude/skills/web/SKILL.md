---
name: web
description: Drive a real browser via `web-agent` CLI. Use for browsing, clicking, form-filling, login, scraping pages to markdown, or crawling sites. Inspector for interaction, Scraper for read-only content.
argument-hint: [task or URL]
---

`web-agent <subcommand>` — every call prints one JSON object. If not on PATH: `uv tool install --editable .` from repo root.

## Decide which surface

- Read content (page → markdown, site crawl) → **Scraper** (no browser needed for `http`/`jina`)
- Click, fill, login, click-to-load → **Inspector** (needs CDP browser)
- Raw DOM check → `js`

## Inspector loop (the default for interaction)

```bash
web-agent navigate <url>
web-agent inspect                          # → {"inspect_id": "s7", ...}
web-agent query s7 --role button --name submit
web-agent act s7 button:submit-order click
```

Re-`inspect` after every DOM mutation; stale handles raise `stale_handle`.

| Command | Purpose |
|---|---|
| `inspect [--scope CSS]` | AX-tree snapshot |
| `query <id> [--role R] [--name X] [--text X] [--tag T] [--scope-selector S] [--all-visibility] [--limit N]` | Filter (default limit 20) |
| `read <id> <handle>` | Full element record |
| `act <id> <handle> <click\|fill\|focus\|scroll_into_view> [--text X]` | Dispatch action |

Handles are readable: `button:submit-order`, `textbox:email`, `link:about-2`.

## Browser (escape hatches)

`navigate <url>`, `screenshot <path>`, `js <code>`, `key <Tab|Enter|...>`, `page-info`, `snapshots`.

## Scraper

```bash
web-agent fetch <url>                          # auto: jina → http → cdp
web-agent fetch <url> --engine http            # fast, no JS
web-agent fetch <url> --engine cdp             # full JS render (needs browser)
web-agent fetch <url> --markdown-only
web-agent crawl <url> --limit 50 --depth 2     # → job_id (async)
web-agent crawl-status <job_id>                # queued|running|done|cancelled|failed
web-agent crawl-results <job_id> --limit 100
web-agent crawl-cancel <job_id>
```

## CDP browser (required for Inspector + `fetch --engine cdp`)

```bash
CDP_HEADLESS=1 python3 tools/browser.py start    # repo root; auto-port 9222–9400
python3 tools/browser.py list
python3 tools/browser.py stop-all
```

## Rules

1. Re-`inspect` after navigation or any DOM mutation.
2. Always start `query` with `--role`; never query unfiltered.
3. One action per command — read the JSON, then decide.
4. Errors carry `kind` + `error` + `hint`. Follow the hint.

## Flags & outputs

`--port N` (default 9222), `--snapshots-dir PATH` (default `$PWD/.snapshots`), `--crawls-dir PATH` (default `$PWD/.crawls`). Outputs land in caller's `$PWD`.

Full contract: `web_agent/tools/AGENTS.md`, `manifest.json`.
