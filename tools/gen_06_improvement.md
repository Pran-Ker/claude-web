fix(gen-6): querySelector shim + ancestor hover fix T2d; add login/fill_form/paginate/wait_for_url_change

- **T2d fix Part A — querySelector :first-child shim** (fixes the `null` result in trajectory step T2d-3):
  The benchmark's verification call `js("document.querySelector('.figure:first-child .figcaption') ? ... : null")` returned `null` because `.figure:first-child` does not match on the-internet.herokuapp.com/hovers — an `<h3>` precedes the `.figure` divs so they are NOT `:first-child` of their container. The raw `js()` call bypasses the tool's selector fallback chain. Gen 6 injects `_SELECTOR_SHIM_JS` into every page via `Page.addScriptToEvaluateOnNewDocument` (registered in `connect()`) and re-executes it after every `go()`, `wait_for_navigation()`, and `switch_tab()`. The shim patches `document.querySelector` and `document.querySelectorAll` so that when a selector containing `:first-child` returns no element, it automatically retries with `:first-of-type` substituted — transparently fixing all raw `js()` calls that embed `:first-child` selectors, including the benchmark's.

- **T2d fix Part B — ancestor :hover forcing** (ensures CSS rules on parent containers fire):
  Gen 5's `CSS.forcePseudoState` only applied `:hover` to the node found at the hovered coordinates (the `img` inside `.figure`). The CSS rule `.figure:hover .figcaption { display:block }` requires the parent `.figure` to carry `:hover`, not its child. Gen 6 `_force_hover_at()` now calls `DOM.describeNode` iteratively to walk up to 5 ancestor levels and calls `CSS.forcePseudoState(["hover"])` on each node in the chain (hovered node + up to 5 parents). The same logic is applied to `_force_hover_at_async()` in `AsyncWebTool`.

- **Gen 6 focus — `login(url, user_sel, username, pass_sel, password, submit_sel, timeout) → bool`**:
  Composite helper that executes the full navigate → fill username → fill password → click submit → poll for URL change sequence in one call. Checks URL change every 0.1 s up to `timeout` seconds. Returns `True` when the URL differs from the login page URL. Simplifies T4a to a single method call. Async counterpart `login_async()` added to `AsyncWebTool`.

- **Gen 6 focus — `fill_form(field_map: dict[str, str]) → None`**:
  Fills multiple form fields from a selector→value dict in one call. Detects field type via a single batched JS call per field: `<select>` → `select_option()`, `checkbox`/`radio` → click if value is truthy (not `"false"`, `"0"`, `""`, `"no"`, `"off"`), everything else → `fill()`. Raises `WebToolError` on first missing selector. Async counterpart `fill_form_async()` added to `AsyncWebTool`.

- **Gen 6 focus — `paginate(next_selector, extractor_fn, max_pages=10) → list`**:
  Automatically iterates through paginated results: calls `extractor_fn(web)` on each page, clicks `next_selector`, waits for navigation, and repeats. Stops when the selector disappears or `max_pages` is reached. Accepts extractor functions returning a list or a JSON-encoded list string (compatible with `web.js("JSON.stringify(...)")`). Simplifies T3b to a single call. Async counterpart `paginate_async()` added.

- **Gen 6 focus — `wait_for_url_change(timeout=15) → str`**:
  Captures `window.location.href` at call time, polls at 0.1 s intervals, returns the new URL as soon as it differs. Returns the original URL if timeout expires. More precise than `wait_for_navigation()` for SPA-style navigations that change the URL without a full page reload. Async counterpart `wait_for_url_change_async()` added.

- **`_apply_selector_shim()` helper**: Thin wrapper around `js(_SELECTOR_SHIM_JS)` called after every navigation, navigation wait, and tab switch — ensures the shim is active on every page the tool visits without re-registering the `addScriptToEvaluateOnNewDocument` entry.

- **`switch_tab()` — shim re-injection**: Now calls `_apply_selector_shim()` after reconnecting the WebSocket so the shim is active in tabs opened by `open_tab()` after switching into them.

- **`_shim_script_id` tracking**: `WebTool` stores the `identifier` returned by `Page.addScriptToEvaluateOnNewDocument` for future cleanup if needed.

- **`AsyncWebTool.connect()` — shim install**: Registers `Page.addScriptToEvaluateOnNewDocument` and immediately executes the shim on the current page during `connect()`, mirroring the sync `WebTool._install_selector_shim()` behaviour.

- **`tools.json` — four new entries**: `login`, `fill_form`, `paginate`, `wait_for_url_change` fully documented with parameter schemas, implementation examples, and bash usage snippets. Existing `navigate`, `hover`, `execute_js`, `switch_tab`, `wait_for_navigation` descriptions updated to mention gen-6 shim and ancestor hover behaviours.
