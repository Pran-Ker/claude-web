feat(gen-1): event-driven navigation, faster polling, WebToolError, scroll/hover/select/find-by-text

- **go()**: replaced `time.sleep(2)` with a proper `Page.loadEventFired` event-driven wait; loops over socket frames, collecting the nav ack and load event independently so neither can be missed regardless of ordering
- **wait()**: polling interval reduced from 0.5 s → 0.1 s; returns immediately when the element is found instead of continuing to the next sleep cycle
- **WebToolError**: new exception class raised on hard failures (element not found, box-model unavailable) instead of silently returning `False`/`None`, making error propagation explicit
- **_wait_for_event()**: internal helper that sets per-recv socket timeouts so the tool never blocks indefinitely on a stale load event
- **_drain_events() / _pending_events**: thread-safe side-channel that buffers unsolicited CDP events (method frames) encountered during `cmd()` calls, preventing them from being silently discarded
- **scroll(direction, pixels)**: sends a `Input.dispatchMouseEvent` `mouseWheel` event to scroll the page; supports up/down/left/right — unblocks lazy-loaded content tasks (Tier 2)
- **hover(selector)**: sends a `mouseMoved` event to the element's centre coordinate, triggering CSS `:hover` transitions and revealing dropdown sub-menus (Tier 2)
- **select_option(selector, value)**: sets `<select>.value` via JS and dispatches a bubbling `change` event so framework listeners (React, Vue, etc.) are notified (Tier 2)
- **find_by_text(text, tag)**: single batched `Runtime.evaluate` call that scans matching elements for exact `textContent` match and returns a computed CSS selector, avoiding multiple round-trips (Tier 2)
- **click_by_text(text, tag)**: convenience wrapper — `find_by_text` + `click`; raises `WebToolError` if no matching element is found (Tier 2)
- **js()**: switched to `returnByValue: True` in `Runtime.evaluate` so primitives and JSON-stringified objects are returned without needing a separate `Runtime.getProperties` round-trip
- **key()**: now sends both `keyDown` **and** `keyUp` events for correctness (baseline only sent `keyDown`)
- **tools.json**: added schema entries for `scroll`, `hover`, `select_option`, `find_by_text`, `click_by_text` with descriptions, parameter types, and bash usage examples; updated `navigate` and `wait` descriptions to reflect event-driven / faster-polling behaviour
- **browser.py**: copied verbatim from baseline — no changes needed for Generation 1
