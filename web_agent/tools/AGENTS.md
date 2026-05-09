# How to drive web_agent

You have two logical tools, both invoked via the same CLI:

- **Inspector** — `inspect`, `query`, `read`, `act`
- **Browser** — `navigate`, `screenshot`, `js`, `key`, `page-info`, `snapshots`

Every command prints one JSON object to stdout. Read it; don't grep it.

## Standard loop

```bash
# 1. Land on the page
python -m web_agent navigate "https://example.com/checkout"

# 2. Snapshot — receipt comes back, full data goes to disk
python -m web_agent inspect
# → {"ok": true, "inspect_id": "s7", "total_elements": 142, "top_roles": {...}, "hint": "..."}

# 3. Query against the snapshot id (NOT against the live page)
python -m web_agent query s7 --role button --name submit
# → {"matches": [{"handle": "button:submit-order", ...}], "total": 1}

# 4. Act using the handle
python -m web_agent act s7 button:submit-order click
```

## Rules of the road

1. **Re-inspect after anything that mutates the DOM.** Navigation, click on a control that opens a panel, fill that triggers a re-render. Snapshots are immutable; stale handles raise `stale_handle` with a hint telling you to re-inspect.
2. **Narrow before you list.** Don't `query` with no filters. Start with `--role` (e.g. `button`, `textbox`, `link`) and add `--name` text.
3. **Handles are readable.** `button:submit-order`, `textbox:email`, `button:close-2`. You can often guess what a handle should be — but always verify with `query` before `act`.
4. **Errors are instructions.** Every error has a `hint` field. Follow it.
5. **Use `js` and `screenshot` only when the inspector can't see what you need** (e.g. canvas content, third-party iframe). They are escape hatches, not the default.

## Common roles

`button`, `link`, `textbox`, `searchbox`, `combobox`, `checkbox`, `radio`, `tab`, `menuitem`, `option`, `heading`, `image`, `dialog`.

## Actions

| action            | what it does                                     |
|-------------------|--------------------------------------------------|
| `click`           | scrolls into view, then dispatches a real click  |
| `fill --text X`   | focuses the field, selects all, types X          |
| `focus`           | focus only (no click)                            |
| `scroll_into_view`| ensures the element is on screen                 |

## When something fails

- `stale_handle` → re-run `inspect`, re-`query`, retry `act`.
- `element_not_actionable` → element is disabled or off-screen; check `read <id> <handle>` for `enabled`/`visible`.
- `js_exception` → your JS expression broke; fix syntax.
- `transport_error` → Chrome isn't reachable on the port. Check `python tools/browser.py list`.
