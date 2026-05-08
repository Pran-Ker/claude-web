# CLAUDE.md — Web Agent Automation Guide

> **The agent surface is `python3 -m web_agent ...`.** It is a single CLI that
> exposes two logical tools (Inspector + Browser) via subcommands, and prints
> one JSON object per invocation. See `web_agent/tools/AGENTS.md` for the full
> playbook and `web_agent/tools/manifest.json` for the contract.
>
> The legacy `tools/web_tool.py` + `tools/tools.json` surface still exists for
> backward compatibility, but new work goes through `web_agent`.

---

## The standard loop

```bash
# 1. Land on the page
python3 -m web_agent navigate "https://example.com/checkout"

# 2. Snapshot — receipt comes back; full data goes to .snapshots/
python3 -m web_agent inspect
# → {"inspect_id": "s7", "total_elements": 142, "top_roles": {...}, "hint": "..."}

# 3. Query against the snapshot id (NOT the live page)
python3 -m web_agent query s7 --role button --name submit
# → {"matches": [{"handle": "button:submit-order", ...}], "total": 1}

# 4. Act using the readable handle
python3 -m web_agent act s7 button:submit-order click
```

## Subcommand reference

### Inspector (use these by default)

| Command                                                         | Purpose                                        |
|-----------------------------------------------------------------|------------------------------------------------|
| `inspect [--scope CSS]`                                         | Capture an accessibility-tree snapshot         |
| `query <id> [--role R] [--name X] [--text X] [--tag T] [--scope-selector S] [--all-visibility] [--limit N]` | Filter elements; default limit 20 |
| `read <id> <handle>`                                            | Full element record (attrs, selector, etc.)    |
| `act <id> <handle> <click\|fill\|focus\|scroll_into_view> [--text X]` | Dispatch action against a handle         |

### Browser (escape hatches — use only when the inspector can't reach it)

| Command                              | Purpose                              |
|--------------------------------------|--------------------------------------|
| `navigate <url> [--wait SECONDS]`    | Load a URL                           |
| `screenshot <path> [--quality N]`    | Save a JPEG                          |
| `js <code>`                          | Evaluate JS, return the value        |
| `key <Tab\|Enter\|Escape\|...>`      | Send a global keystroke              |
| `page-info`                          | URL / title / viewport               |
| `snapshots`                          | List saved snapshot ids              |

### Global flags

- `--port N` — CDP port (default 9222)
- `--snapshots-dir PATH` — snapshot store location (default `.snapshots`)

## Rules

1. **Re-inspect after every DOM mutation.** Navigation, click that opens a panel, fill that triggers re-render — all invalidate the snapshot. Stale handles raise `stale_handle` with a hint to re-inspect.
2. **Narrow before you list.** Don't `query` with no filters. Start with `--role`.
3. **Handles are readable.** `button:submit-order`, `textbox:email`, `link:about-2`. You can often guess them, but verify with `query` before `act`.
4. **Errors are instructions.** Every error JSON has `kind`, `error`, `hint`. Read the hint.
5. **One action per command.** Don't chain shell calls; check the JSON result, then decide the next call.

## Browser launch (CDP)

Start Chrome with the DevTools protocol enabled:

```bash
CDP_HEADLESS=1 python3 tools/browser.py start          # first free port in 9222–9400
CDP_PORT=9222   python3 tools/browser.py start          # specific port (auto-redirect if busy)
python3 tools/browser.py list                           # ports started by this tool
CDP_PORTS=9222,9223 python3 tools/browser.py stop       # stop specific ports
python3 tools/browser.py stop-all                       # stop everything
```

## Verification

After an action that should produce a result:
- Check `page-info` for URL change
- Re-`inspect` and `query` for success-state elements (toast, confirmation heading, etc.)
- Use `js` for content that isn't in the AX tree (canvas, third-party iframes)

### **MANDATORY: Official task evaluation**

After completing ANY task, run the evaluator:

```bash
python3 RealEval/evaluate_task.py TASK_ID "YOUR_ANSWER"
```

## Common patterns

```bash
# Find a task by id
echo "task-id" | python3 RealEval/tasks.py
```

## Package management

Top-level `pyproject.toml` defines the `web_agent` package. The legacy
`tools/pyproject.toml` is preserved for callers of the old `web_tool.py`.

```bash
uv sync                    # installs web_agent at repo root
cd tools && uv sync        # legacy
```

## Website cloning

**Quick clone:**

```bash
python3 clone/website_cloner.py
```

For pixel-perfect replicas, see `clone/CLONING_PROTOCOL.md`.

## Emergency debugging

When the inspector misses something:
1. `screenshot screenshots/debug.jpg` and look at it
2. `js "document.documentElement.outerHTML"` to dump raw HTML
3. `js "document.elementFromPoint(X, Y)?.outerHTML"` to identify a region
4. Re-`navigate` if the page is in a weird state

If the inspector misses non-semantic clickables (raw `<div onclick>` with no
role), use `js` to inspect them directly. A future enhancement will pick those
up via a DOM walk.

## Always do this

- Use `inspect` → `query` → `act` for any element interaction
- Verify each step's JSON before the next
- Re-inspect after navigation
- Read error `hint` fields and follow them

## Never do this

- Batch multiple interactions in one shell command
- Skip the inspect step and try CSS selectors directly (use `act` with handles)
- Continue past an error without reading its `hint`
- Mix `tools/web_tool.py` calls with `web_agent` calls — pick one
