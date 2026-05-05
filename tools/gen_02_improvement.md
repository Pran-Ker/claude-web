feat(gen-2): fix selector reliability, dual load-event, AsyncWebTool, multi-tab, BrowserPool, get_table

- **T1a (go timed out for example.com, title empty)**: `go()` now accepts `Page.frameStoppedLoading` as a co-trigger alongside `Page.loadEventFired` via the new `_wait_for_any_event(set)` helper; after the deadline a `document.readyState` check is attempted as a last-resort before printing the warning — pages that never fire `loadEventFired` but do stop loading via `frameStoppedLoading` (or whose readyState is already `complete`) now return promptly instead of always hitting the full 30 s timeout

- **T2d (hover ".figure:first-child img" → WebToolError: no element)**: replaced the `DOM.getDocument` + `DOM.querySelector` round-trip in `_query()` with `Runtime.evaluate(document.querySelector(...), returnByValue=False)` + `DOM.requestNode`; this delegates selector matching to the full browser JS engine, which supports all CSS3 pseudo-classes (`:first-child`, `:nth-child`, `:not`, attribute selectors, etc.) that CDP's own DOM.querySelector can silently fail on

- **T3a (get_table not implemented)**: added `get_table(selector="table")` — single `Runtime.evaluate` JS call walks `<thead>` for header names then maps every `<tbody tr>` to a dict, returned as a Python `list[dict]`; no extra round-trips

- **T3c (open_tab not implemented)**: added `open_tab(url)` via `Target.createTarget`, `switch_tab(tab_id)` (closes current WS, opens new one, re-enables Page/DOM/Runtime), `close_tab(tab_id)` via `Target.closeTarget`, and `get_open_tabs()` via `/json/list`; `connect()` now stores the current tab id and seeds `_tabs` registry; `_refresh_tab_registry()` syncs on demand

- **T4b (ModuleNotFoundError: No module named 'tools.browser_pool')**: created `tools/browser_pool.py` with `BrowserPool(size, headless, port_start)` — spawns N `BrowserCDP` processes (auto-selects free ports), connects one `WebTool` per process, and exposes thread-safe `acquire()` / `release()` backed by a `threading.Condition`; also provides `borrow()` context manager for automatic release; `stop()` cleanly tears down all WebTools and Chrome processes

- **Gen-2 focus — AsyncWebTool**: `AsyncWebTool` class added to `web_tool.py`; uses `websockets` library with a background `_recv_loop` task that demuxes CDP responses to `asyncio.Future` objects and broadcasts events to registered listeners; exposes `connect`, `close`, `cmd_async`, `go_async`, `js_async`, `click_async`, `fill_async`, `wait_async`, `screenshot_async`; `go_async` attaches a one-shot sync listener that resolves a Future on any `_LOAD_SIGNALS` event

- **Gen-2 focus — wait_for_navigation()**: added sync `wait_for_navigation(timeout=15)` on `WebTool` — calls `_wait_for_any_event(_LOAD_SIGNALS, timeout)` so callers can block on the next navigation after triggering a click or form submit; async counterpart `wait_for_navigation_async` added to `AsyncWebTool`

- **Gen-2 focus — get_all_links()**: added `get_all_links()` — single `Runtime.evaluate` call returning every `a[href]` href, filtered to exclude `javascript:` URIs

- **Bonus — get_form_fields(selector)**: added form introspection method returning tag/type/name/id/value/placeholder/required for every input, select, and textarea inside the matched form element

- **tools.json**: added schema entries for `wait_for_navigation`, `get_all_links`, `get_table`, `get_form_fields`, `open_tab`, `switch_tab`, `close_tab`, `get_open_tabs`, `browser_pool`, `async_web_tool`; updated `navigate`, `click`, and `hover` descriptions to document the improved CSS3 selector support
