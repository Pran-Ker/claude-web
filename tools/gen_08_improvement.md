feat(gen-8): error resilience — safe_click, safe_fill, auto-reconnect, select_option text matching, click_and_wait

- **T2c fix — `select_option` by value OR text**: `select_option('#dropdown', 'Option 2')` was returning "Please select an option" because the `<option>` carries `value="2"` not `value="Option 2"`. New implementation does a two-pass scan: first match on `option.value`, then fallback to `option.text.trim()`. Both passes are done in a single JS round-trip.

- **T4a fix — `click_and_wait(selector, timeout=15)`**: After `web.click('a[href="/logout"]')` the URL remained at `/secure` because no navigation wait was issued. New `click_and_wait()` dispatches mouse events then immediately blocks on the next `Page.loadEventFired` / `Page.frameStoppedLoading` event, re-injecting the querySelector shim on completion. Replaces the brittle `click() + wait_for_navigation()` two-liner.

- **T1c root cause — `click_and_wait` also fixes form-submit navigation**: Same pattern as T4a — submitting a login form with bad credentials redirects back to the login page with an `.alert` element, but the DOM check ran before the redirect completed. `click_and_wait()` is the documented solution.

- **T4b fix — `go_async` title guard**: The-internet.herokuapp.com returned an almost-empty page (title `""`, fetch_s=0.04) because `go_async` returned immediately after `readyState=complete` without waiting for JavaScript to set the title. Added a post-load poll (up to 3 s) for `document.title !== ''` inside `go_async`. The other two BrowserPool fetches were unaffected; only the Heroku cold-start URL was empty.

- **T4c / T5c fix — `js_wait(code, timeout, interval)`**: Both failures trace to `json.loads(None)` after `web.js()` returned `None` on a JS-rendered page where the selector existed but child text nodes were unpopulated at the moment of evaluation. New `js_wait()` retries the expression at 200 ms intervals until non-None, up to a configurable timeout. Use in place of bare `js()` when `wait()` confirms elements exist but content may lag.

- **Gen-8 — `safe_click(selector, fallback_text=None)`**: Wraps `click()` and, on `WebToolError`, retries via `click_by_text(fallback_text)`. Zero overhead when selector resolves; no extra JS calls or sleeps. Raises `WebToolError` only when both strategies fail.

- **Gen-8 — `safe_fill(selector, text)`**: Calls the standard `fill()` first (triple-click + type). If that fails due to layout (element off-screen, zero-size), falls back to direct JS property assignment using the native input value setter (`Object.getOwnPropertyDescriptor`) to bypass React's controlled-component caching, then fires `input` + `change` events. Raises only on element-not-found.

- **Gen-8 — auto-reconnect in `cmd()`**: Split implementation into `_cmd_impl()` (raw send/recv, no reconnect) and `cmd()` (wraps with one reconnect attempt on `WebSocketConnectionClosedException`, `ConnectionResetError`, `BrokenPipeError`, `OSError`). `_reconnect()` opens a fresh WebSocket to the stored tab URL, resets `msg_id`, re-enables Page/DOM/Runtime/Network domains, and re-injects the selector shim. `switch_tab()` and `go()` continue to use `cmd()` unchanged.

- **Gen-8 — `health_check() → bool`**: Sends `Browser.getVersion` and returns `True` on a clean response, `False` on any exception. Lightweight probe for connection liveness before long command sequences or inside retry loops.

- **New — `scroll_to(selector)`**: Calls `element.scrollIntoView({behavior:'smooth', block:'center'})`. No-op when element absent. Complements `scroll(direction, pixels)` for element-targeted scrolling.

- **New — `wait_for_count(selector, count, timeout)`**: Polls `querySelectorAll(selector).length >= count` at 0.1 s intervals. Useful for paginated/lazy-loaded lists where a minimum item count must be present before extraction begins.

- **New — `retry_click(selector, attempts=3, delay=0.5)`**: Retries `click()` up to N times with configurable inter-attempt delay. Handles elements that are transiently unclickable (CSS transitions, loading spinners disappearing).

- **New — `get_text` alias**: `get_text = text` alias added so `web.get_text(selector)` works without breaking existing `web.text(selector)` calls.

- **`browser.py`**: Copied unchanged from generation 7 — no modifications required.
