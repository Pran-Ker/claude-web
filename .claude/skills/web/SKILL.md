---
name: web
description: Open a browser, browse the web, and take actions on the user's behalf. Use when the user asks to browse, open a website, search, click, fill forms, scrape content, or do anything on a website.
argument-hint: [task or URL]
---

The agent surface is the `web-agent` CLI. One binary exposes three logical tools — **Inspector**, **Browser**, **Scraper** — and every invocation prints one JSON object.

If `web-agent` is not on PATH, install it from this repo: `uv tool install --editable .` (run from the repo root).

## Standard interactive loop

```bash
web-agent navigate "https://example.com/checkout"
web-agent inspect                       # → {"inspect_id": "s7", ...}
web-agent query s7 --role button --name submit
web-agent act s7 button:submit-order click
```

Re-`inspect` after every DOM mutation. Stale handles raise `stale_handle`.

## Inspector (default for interaction)

| Command | Purpose |
|---|---|
| `inspect [--scope CSS]` | Capture an AX-tree snapshot |
| `query <id> [--role R] [--name X] [--text X] [--tag T] [--scope-selector S] [--all-visibility] [--limit N]` | Filter elements (default limit 20) |
| `read <id> <handle>` | Full element record |
| `act <id> <handle> <click\|fill\|focus\|scroll_into_view> [--text X]` | Dispatch action |

Handles are readable: `button:submit-order`, `textbox:email`, `link:about-2`.

## Browser (escape hatches)

| Command | Purpose |
|---|---|
| `navigate <url> [--wait SECONDS]` | Load a URL |
| `screenshot <path> [--quality N]` | Save a JPEG |
| `js <code>` | Eval JS, return value |
| `key <Tab\|Enter\|Escape\|...>` | Global keystroke |
| `page-info` | URL / title / viewport |
| `snapshots` | List saved snapshot ids |

## Scraper (read-only ingestion)

For "give me this page as markdown" or "crawl this site" — don't drive the inspector.

```bash
web-agent fetch <url>                          # auto: jina → http → cdp
web-agent fetch <url> --engine http            # fast, no JS
web-agent fetch <url> --engine cdp             # full JS render
web-agent fetch <url> --markdown-only
web-agent crawl <url> --limit 50 --depth 2     # returns job_id
web-agent crawl-status <job_id>                # queued|running|done|cancelled|failed
web-agent crawl-results <job_id> --limit 100
web-agent crawl-cancel <job_id>
```

## When to use which

- Read-only content extraction → `fetch` / `crawl`
- Form fill, login, click-to-load → Inspector
- One-off DOM check → `js`

## Browser launch (CDP)

Required for Inspector, Browser subcommands, and `fetch --engine cdp`. NOT needed for `http`/`jina`.

```bash
CDP_HEADLESS=1 python3 tools/browser.py start    # run from the repo root; first free port 9222–9400
python3 tools/browser.py list
python3 tools/browser.py stop-all
```

(A future revision will fold this into `web-agent browser start|list|stop` so no path is needed.)

## Rules

1. Re-`inspect` after every DOM mutation.
2. Narrow before you list — start `query` with `--role`.
3. One action per shell command; check JSON, then decide next.
4. Errors carry `kind` + `error` + `hint`. Read the hint.

## Global flags

- `--port N` — CDP port (default 9222)
- `--snapshots-dir PATH` — snapshot store (default `$PWD/.snapshots`)
- `--crawls-dir PATH` — crawl store (default `$PWD/.crawls`)

Outputs land in the caller's `$PWD`, not the repo. Override with the flags above if you want a shared location.

See `web_agent/tools/AGENTS.md` and `web_agent/tools/manifest.json` for the full contract.
