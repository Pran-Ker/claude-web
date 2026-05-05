fix(gen-5): CSS.forcePseudoState hover, triple-click fill, new capability methods

- **T2d root-cause analysis (Hover did not reveal caption)**: Trajectory shows `hover(".figure:first-child img")` succeeds (gen-4 fallback chain resolves element via `:first-of-type`), but the subsequent `js("document.querySelector('.figure:first-child .figcaption')…")` returns `null`. The `:first-child` pseudo-class never matches on the-internet.herokuapp.com/hovers because an `<h3>` precedes the `.figure` divs, making them NOT the first child of their container. The benchmark's own verification query embeds the broken selector in a raw `js()` call — outside the tool's fallback chain. This is a benchmark selector issue; rewriting `js()` to silently fix callers' selectors would corrupt its semantics. Instead, gen-5 adds `CSS.forcePseudoState` to `hover()` so the CSS `:hover` state is reliably pinned (benefits other hover-dependent tasks), and documents the unfixable root cause here.

- **hover() two-layer reliability via CSS.forcePseudoState (gen-5 focus)**: After dispatching `Input.dispatchMouseEvent(mouseMoved)`, `hover()` now calls `DOM.getNodeForLocation` to resolve the exact node under the cursor, then calls `CSS.forcePseudoState({nodeId, forcedPseudoClasses:["hover"]})` on it. This pins the CSS `:hover` pseudo-class on the node even after the mouse event completes, so CSS rules like `.parent:hover .child { display:block }` remain active when the caller inspects computed styles or waits for revealed content — eliminating any race between the mouse event settling and the next DOM query. The same two-layer approach is applied to `hover_async()` in `AsyncWebTool`.

- **_get_coords_full() — matched-selector tracking**: Refactored `_get_coords()` into `_get_coords_full()` which returns `(x, y, matched_selector)`. `hover()` and `hover_async()` now know which selector variant actually resolved the element (exact, `:first-of-type`, or stripped), enabling precise `DOM.querySelector` lookup for the CSS.forcePseudoState call without re-probing the fallback chain.

- **fill() triple-click select-all (replaces Ctrl+A)**: `fill()` now sends `clickCount=1,2,3` (triple-click) to select all existing text before typing, rather than `Ctrl+A` (`keyDown key="a" modifiers=2`). Triple-click is natively handled by Chrome's input layer and selects all text in any input/textarea regardless of whether the page's JavaScript intercepts keyboard events. The same change is applied to `fill_async()`.

- **New method: double_click(selector)**: Dispatches two mouse-pressed/released pairs with `clickCount=1` then `clickCount=2`, satisfying Chrome's dblclick detection. Useful for inline editors, sortable column headers, and other dblclick-triggered interactions.

- **New method: triple_click(selector)**: Public API for triple-clicking — selects all text in an input without typing. Useful when the caller wants to select-all and then `type()` separately (e.g. to verify the field is focused first).

- **New method: clear(selector)**: Sets element value to `""` via JS and dispatches `input`/`change` events. More targeted than triple-click + type for clearing fields programmatically.

- **New method: is_visible(selector)**: Single-call computed-style check — returns `True` if the element is present, `display != 'none'`, `visibility != 'hidden'`, and `opacity != '0'`. Useful for verifying that hover() revealed an element without polling.

- **New method: wait_for_visible(selector, timeout)**: Polls at 0.1 s intervals until the element is both present AND computed-visible. Replaces the pattern of `wait()` (DOM presence) + separate computed-style check — particularly useful after `hover()` reveals dynamically shown content.

- **New method: wait_for_text(selector, text, timeout)**: Polls at 0.1 s intervals until the element's `textContent` contains the given string. Eliminates busy-wait patterns like `time.sleep(N)` followed by a content check.

- **New method: get_computed_style(selector, property_name)**: Returns `window.getComputedStyle(el).getPropertyValue(prop)` for the matched element. Lets callers confirm CSS-driven state (e.g. `'display'` → `'block'` after hover) without taking a screenshot.

- **New method: get_element_count(selector)**: Returns `document.querySelectorAll(selector).length` in one round-trip — cleaner than `js("document.querySelectorAll(…).length")` for counting items.

- **New method: upload_file(selector, file_path)**: Uses `DOM.setFileInputFiles` to set a file-input's value without triggering the OS file picker — essential for file-upload automation that gen 4 had no support for.

- **_css_enabled flag**: `WebTool` and `AsyncWebTool` track whether `CSS.enable` has been called so `_ensure_css_enabled()` is only sent once per connection/navigation, keeping the per-hover overhead to a single round-trip (DOM.getNodeForLocation + CSS.forcePseudoState) rather than three.

- **go() / switch_tab() — reset _css_enabled**: `_css_enabled` is reset to `False` on navigation and tab switch so the CSS domain is re-enabled fresh on the new page context, preventing stale domain state from gen-4 tab-switching code.

- **get_attribute alias**: `WebTool.get_attribute` is added as an alias for `attr()` for better discoverability — both names work identically.

- **browser.py** — unchanged from gen-4; BrowserPool T4b benchmark passes; no modifications needed.
