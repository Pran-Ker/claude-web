# Claude Code as a Web Agent

Connect Claude Code to a real browser and a clean scraper. Ask it to fill forms, click buttons, log in, crawl a docs site, or pull a page as markdown — it drives Chrome via CDP and ships a queryable DOM inspector so it doesn't fumble around blindly.

![Minesweeper Demo](assets/images/Minesweeper-demo.gif)

---

## Install (⏱ < 1 min)

```bash
curl -fsSL https://raw.githubusercontent.com/agi-inc/claude-web/main/install.sh | sh
```

That's it. `web-agent` is on your PATH and Claude Code's `/web` skill is live.

Verify:

```bash
web-agent fetch https://example.com --markdown-only
```

You should get a JSON object with the page title and markdown.

**What the installer does** (clones to `~/Tools/claude-web` by default, override with `CLAUDE_WEB_DIR=/your/path sh`):
1. Clones (or updates) the repo
2. Installs `web-agent` globally via `uv tool install --editable .`
3. Symlinks the `/web` skill into `~/.claude/skills/web`

> **Prerequisites**: [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and `git` must be on your PATH. If you don't have `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## How to use

Open Claude Code and just ask — "browse hacker news and summarise the top story", "log into example.com and click submit", "crawl docs.example.com and pull every page as markdown". The `/web` skill teaches Claude how to drive the `web-agent` CLI.

If you want to drive it yourself from a shell, the CLI is fully usable standalone:

```bash
# Scrape a page
web-agent fetch https://example.com

# Interactive flow (requires a Chrome instance — see below)
web-agent navigate https://example.com
web-agent inspect
web-agent query <snapshot_id> --role button
web-agent act <snapshot_id> <handle> click
```

For interactive (Inspector/Browser) commands you need a Chrome instance with the DevTools Protocol enabled:

```bash
CDP_HEADLESS=1 python3 tools/browser.py start    # first free port 9222–9400
python3 tools/browser.py list
python3 tools/browser.py stop-all
```

Pure scraper commands (`fetch --engine http`, `fetch --engine jina`, `crawl`) do **not** need a running browser.

---

## Project Structure

```
.
├── README.md
├── CLAUDE.md                    # Detailed automation guide (read this for the full surface)
├── pyproject.toml               # Installs the `web-agent` CLI
├── .claude/skills/web/          # The /web skill, version-controlled with the code
├── web_agent/                   # CLI source (inspector, scraper, browser, transport)
└── tools/
    ├── browser.py               # Chrome launcher (CDP)
    └── web_tool.py              # Legacy automation library (backward compat)
```

See `CLAUDE.md` for the full agent playbook and `web_agent/tools/AGENTS.md` for the contract.
