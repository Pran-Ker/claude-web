---
name: web
category: tools
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

`act` returns a fresh `snapshot_id` and auto-retries on `stale_handle`, so you rarely need a manual re-`inspect` between steps. For a one-shot lookup, skip `inspect`+`query` and use `find` (it snapshots internally); pair with `navigate --snap` to fold the first snapshot into the load:

```bash
web-agent navigate <url> --snap             # navigate + snapshot in one call
web-agent find --role button --name submit  # → handle directly, no inspect/query
```

| Command | Purpose |
|---|---|
| `inspect [--scope CSS]` | AX-tree snapshot |
| `query <id> [--role R] [--name X] [--text X] [--tag T] [--scope-selector S] [--all-visibility] [--limit N]` | Filter (default limit 20) |
| `find [--role R] [--name X] [--text X] [--tag T] [--limit N]` | One-shot: snapshot + query → first matching handle |
| `read <id> <handle>` | Full element record |
| `act <id> <handle> <click\|fill\|focus\|scroll_into_view> [--text X]` | Dispatch action; returns fresh `snapshot_id`, auto-retries `stale_handle` |

Handles are readable: `button:submit-order`, `textbox:email`, `link:about-2`.

## Batch (fast path for multi-step flows)

Run many ops in one CLI call + one CDP session instead of N turns; auto-recovers from `stale_handle`. Each op takes a `handle` or an inline `find:{...}`.

```bash
web-agent batch '[
  {"op":"navigate","url":"https://example.com/login"},
  {"op":"fill","find":{"role":"textbox","name":"Email"},"text":"x@y.com"},
  {"op":"click","find":{"role":"button","name":"Sign in"}}
]'
```

Ops: `navigate`, `snapshot`, `find`, `click`, `fill`, `focus`, `key`, `js`, `page_info`, `sleep`. Returns `{ok, steps:[...], snapshot_id}`; stops at the first failing step with `kind`/`error`/`hint`. Pass `-` to read the JSON from stdin.

## Browser (escape hatches)

`navigate <url>`, `screenshot <path>`, `js <code>`, `key <Tab|Enter|...>`, `paste`, `page-info`, `snapshots`.

`paste (--html H | --html-file P) [--text T] [--no-trigger]` — put rich HTML on the clipboard and fire a **trusted** paste into the focused editor (canvas apps: Google Docs/Sheets, Notion, Figma). Focus the editor first; headed browser only; not idempotent. See `web_agent/tools/AGENTS.md`.

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
CDP_HEADLESS=1 python3 "$(python3 -c 'import web_agent, pathlib; print(pathlib.Path(web_agent.__file__).parent.parent / "tools" / "browser.py")')" start    # auto-port 9222–9400
python3 "$(python3 -c 'import web_agent, pathlib; print(pathlib.Path(web_agent.__file__).parent.parent / "tools" / "browser.py")')" list
python3 "$(python3 -c 'import web_agent, pathlib; print(pathlib.Path(web_agent.__file__).parent.parent / "tools" / "browser.py")')" stop-all
```

## Rules

1. `act` auto-retries `stale_handle` and hands back a fresh `snapshot_id`; re-`inspect` only when the page changes under you (navigation, a new panel). Prefer `find`/`batch` over manual `inspect`+`query` loops.
2. Always filter `query`/`find` with `--role`; never query unfiltered.
3. For multi-step flows use `batch`; otherwise one action per command — read the JSON, then decide.
4. Errors carry `kind` + `error` + `hint`. Follow the hint.

## Flags & outputs

`--port N` (default 9222), `--snapshots-dir PATH` (default `$PWD/.snapshots`), `--crawls-dir PATH` (default `$PWD/.crawls`). Outputs land in caller's `$PWD`.

Full contract: `web_agent/tools/AGENTS.md`, `manifest.json`.
