fix(gen-3): replace DOM.requestNode with pure-JS getBoundingClientRect, add async BrowserPool with map()

- **T1c / T4a (fill "input#username" / "#username" → click(): no element for selector)**: root cause was `_query()` calling `DOM.requestNode` with an objectId obtained via `Runtime.evaluate`; after a navigation the V8 execution context is recreated and the objectId is valid but `DOM.requestNode` maps it through the DOM tree which can still be mid-construction, returning nodeId=0; fixed by replacing `_query()` entirely with a pure-JS check (`document.querySelector("…") !== null ? 1 : 0`) that never touches the CDP DOM domain

- **T2a (click "a[href='/login']" → no element)**: same `DOM.requestNode` race; additionally the gen-2 `_query()` used single-quote JS string delimiters, so selectors containing `'` (like attribute selectors with `href='/…'`) relied on backslash-escaping inside a single-quoted string, which worked syntactically but the underlying `DOM.requestNode` race meant the result was still 0; new `_get_coords()` uses double-quote delimiters (`_esc_dq()`) so `a[href='/login']` is embedded verbatim without any conflict

- **T2d (hover ".figure:first-child img" → no element)**: same DOM.requestNode issue; `_get_coords()` calls a single `Runtime.evaluate` that runs `scrollIntoView()` + `getBoundingClientRect()` and returns `{x, y}` directly — no CDP DOM round-trip, no stale-context failure; up to 3 retries with 0.1 s gaps absorb any remaining post-navigation delay

- **T4b (BrowserPool does not support async context manager protocol)**: gen-2 `BrowserPool` in `browser_pool.py` was synchronous only; new async `BrowserPool` added to `web_tool.py` (first import path the benchmark tries) with `__aenter__` / `__aexit__` that spawn N Chrome processes via `loop.run_in_executor` + connect N `AsyncWebTool` instances, and `map(fn, args_list)` that distributes tasks via an `asyncio.Queue` for fair exclusive-access concurrency

- **_get_coords() reliability**: new helper is the single source of truth for element coordinates in both `click()` and `hover()`; calls `el.scrollIntoView({block:"nearest"})` before measuring so off-screen elements are brought into the viewport before `getBoundingClientRect()` reads viewport-relative coordinates correct for `Input.dispatchMouseEvent`

- **fill() JS fallback**: if `_get_coords()` returns None (element genuinely unreachable via mouse), `fill()` falls back to `el.value = "…"` + `input`/`change` `dispatchEvent` so the field value is set even in edge cases where the element has no layout box yet

- **AsyncWebTool._query_async / click_async**: updated to use the same double-quote `_esc_dq()` approach and `_get_coords_async()` helper, matching the synchronous improvements

- **_recv_loop robustness**: on websocket disconnect all pending futures are now explicitly cancelled to prevent coroutines from hanging indefinitely
