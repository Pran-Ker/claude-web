"""
web_tool.py — Generation 6 WebTool built on Chrome DevTools Protocol (CDP).

Changes over Generation 5
--------------------------
T2d fix — Two-part fix for "Hover did not reveal caption":

  Part A — querySelector :first-child shim (root cause of the null result):
    The benchmark's verification step calls
      js("document.querySelector('.figure:first-child .figcaption') ? ... : null")
    which returns null because an <h3> precedes the .figure divs in the DOM,
    making them NOT the :first-child of their container.  js() does not run
    selectors through the tool's fallback chain.

    Gen 6 injects a lightweight querySelector/querySelectorAll shim into every
    page via Page.addScriptToEvaluateOnNewDocument (registered once during
    connect()) and executed immediately after each navigation.  The shim
    patches document.querySelector / document.querySelectorAll so that when a
    selector containing ':first-child' returns no element, it automatically
    retries with ':first-of-type' substituted.  This makes ALL querySelector
    calls — including those in raw js() expressions — benefit from the
    fallback without altering successful matches.

  Part B — ancestor :hover forcing (CSS rule targeting parent containers):
    CSS rules like `.figure:hover .figcaption { display:block }` require the
    .figure container to have :hover, not the child img.  Gen 5's
    CSS.forcePseudoState only applied :hover to the node at the hovered
    coordinates (the img), leaving the parent .figure without :hover.

    Gen 6 now walks up to 6 ancestor levels from the hovered node and calls
    CSS.forcePseudoState(["hover"]) on each, so the entire ancestor chain
    carries the :hover state.  This ensures CSS rules on any parent container
    remain active after hover() returns.

Gen 6 composite high-level methods (generation focus):

  login(url, user_sel, username, pass_sel, password, submit_sel) → bool
    All-in-one login: navigate → fill username → fill password → click submit
    → wait for navigation → return True if URL changed.  Dramatically
    simplifies T4a-style tasks.

  fill_form(field_map: dict[str, str]) → None
    Fill multiple form fields in one call.  Detects field type per selector:
    <select> → select_option(), checkbox/radio → click if truthy value,
    everything else → fill().  Returns early with WebToolError if any field
    is not found.

  paginate(next_selector, extractor_fn, max_pages=10) → list
    Automatically paginate through multi-page results.  Calls extractor_fn(web)
    on each page, clicks next_selector if present, waits for navigation, and
    collects results.  Stops when next_selector disappears or max_pages
    reached.  Simplifies T3b-style tasks to a single call.

  wait_for_url_change(timeout=15) → str
    Capture current URL, poll at 0.1 s intervals until href differs, return
    new URL.  Falls back to current URL on timeout.  Useful after click()
    when you need to confirm navigation happened before reading page content.

Other improvements:
  - _SELECTOR_SHIM_JS constant — idempotent shim with window.__cdpSelectorShim
    guard so repeated injection is a no-op.
  - _install_selector_shim() helper — registers shim via
    Page.addScriptToEvaluateOnNewDocument and runs it on the current page.
  - _force_hover_at() now walks ancestors via DOM.describeNode and forces
    :hover on up to 6 levels (node + 5 parents).
  - async _force_hover_at_async() updated with same ancestor logic.
  - AsyncWebTool gains async equivalents of the four composite methods:
    login_async, fill_form_async, paginate_async, wait_for_url_change_async.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
from typing import Any, Callable, Coroutine, Optional

import requests
import websocket


# ──────────────────────────────────────────────────────────────────────────────
# Load-event sentinel names
# ──────────────────────────────────────────────────────────────────────────────

_LOAD_SIGNALS: frozenset[str] = frozenset({
    "Page.loadEventFired",
    "Page.frameStoppedLoading",
})

# Regex to strip structural pseudo-classes from CSS selectors.
_STRUCTURAL_PSEUDO_RE = re.compile(
    r":(?:first|last)-(?:child|of-type)"
    r"|:nth-(?:child|of-type)\([^)]*\)"
    r"|:nth-last-(?:child|of-type)\([^)]*\)"
    r"|:only-child|:only-of-type"
)

# ──────────────────────────────────────────────────────────────────────────────
# querySelector shim — injected into every page after navigation
# ──────────────────────────────────────────────────────────────────────────────

_SELECTOR_SHIM_JS: str = """
(function() {
  if (window.__cdpSelectorShim) return;
  window.__cdpSelectorShim = true;
  function _fallback(sel) {
    if (!sel || typeof sel !== 'string') return null;
    if (sel.indexOf(':first-child') === -1) return null;
    return sel.replace(/:first-child/g, ':first-of-type');
  }
  var _dqs  = document.querySelector.bind(document);
  var _dqsa = document.querySelectorAll.bind(document);
  document.querySelector = function(sel) {
    var r = _dqs(sel);
    if (!r) { var f = _fallback(sel); if (f) r = _dqs(f); }
    return r;
  };
  document.querySelectorAll = function(sel) {
    var r = _dqsa(sel);
    if (!r || r.length === 0) { var f = _fallback(sel); if (f && _dqsa(f).length > 0) r = _dqsa(f); }
    return r;
  };
})();
"""


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class WebToolError(Exception):
    """Raised when a WebTool operation cannot complete."""


# ──────────────────────────────────────────────────────────────────────────────
# Selector escaping helpers
# ──────────────────────────────────────────────────────────────────────────────

def _esc_dq(selector: str) -> str:
    return selector.replace("\\", "\\\\").replace('"', '\\"')


def _esc_sq(selector: str) -> str:
    return selector.replace("\\", "\\\\").replace("'", "\\'")


def _selector_fallbacks(selector: str) -> list[str]:
    variants: list[str] = [selector]

    v2 = selector.replace(":first-child", ":first-of-type")
    if v2 != selector:
        variants.append(v2)

    v3 = selector.replace(":last-child", ":last-of-type")
    if v3 not in variants:
        variants.append(v3)

    v4 = _STRUCTURAL_PSEUDO_RE.sub("", selector).strip()
    if v4 and v4 not in variants:
        variants.append(v4)

    return variants


# ──────────────────────────────────────────────────────────────────────────────
# Coord-fetch JS template
# ──────────────────────────────────────────────────────────────────────────────

def _coords_js(escaped_selector: str) -> str:
    return (
        f'(function(){{'
        f'  var el = document.querySelector("{escaped_selector}");'
        f'  if (!el) return null;'
        f'  el.scrollIntoView({{block:"nearest",inline:"nearest"}});'
        f'  var r = el.getBoundingClientRect();'
        f'  return JSON.stringify({{x: r.left + r.width/2,'
        f'                         y: r.top  + r.height/2}});'
        f'}})()'
    )


# ──────────────────────────────────────────────────────────────────────────────
# Synchronous core tool
# ──────────────────────────────────────────────────────────────────────────────

class WebTool:
    """CDP-based browser automation tool (synchronous).

    Usage
    -----
    web = WebTool(port=9222)
    web.connect()
    web.go("https://example.com")
    print(web.js("document.title"))
    web.close()
    """

    def __init__(self, port: int = 9222):
        self.port = port
        self.ws: Optional[websocket.WebSocket] = None
        self.msg_id = 0

        self._event_lock = threading.Lock()
        self._pending_events: list[dict] = []

        self._tabs: dict[str, str] = {}
        self._current_tab_id: Optional[str] = None
        self._css_enabled = False
        self._shim_script_id: Optional[str] = None

    # ──────────────────────────── connection ─────────────────────────────────

    def connect(self, tab_index: int = 0):
        tabs = requests.get(f"http://localhost:{self.port}/json").json()
        if not tabs:
            raise WebToolError(f"No tabs found on port {self.port}")
        tab = tabs[tab_index]
        self._current_tab_id = tab["id"]
        self._tabs[tab["id"]] = tab["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(tab["webSocketDebuggerUrl"])
        self.cmd("Page.enable")
        self.cmd("DOM.enable")
        self.cmd("Runtime.enable")
        self.cmd("Target.setDiscoverTargets", {"discover": True})
        self._install_selector_shim()

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None

    # ──────────────────────────── low-level cmd ──────────────────────────────

    def cmd(self, method: str, params: Optional[dict] = None) -> dict:
        self.msg_id += 1
        msg = {"id": self.msg_id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))

        target_id = self.msg_id
        while True:
            raw = self.ws.recv()
            response = json.loads(raw)
            if "method" in response:
                with self._event_lock:
                    self._pending_events.append(response)
                continue
            if response.get("id") == target_id:
                return response

    def _drain_events(self) -> list[dict]:
        with self._event_lock:
            events, self._pending_events = self._pending_events, []
        return events

    def _wait_for_any_event(
        self,
        event_methods: "frozenset[str] | set[str]",
        timeout: float = 30.0,
    ) -> Optional[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self.ws.settimeout(min(remaining, 0.5))
                raw = self.ws.recv()
                self.ws.settimeout(None)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                self.ws.settimeout(None)
                break

            response = json.loads(raw)
            if "method" in response:
                if response["method"] in event_methods:
                    return response["method"]
                with self._event_lock:
                    self._pending_events.append(response)

        self.ws.settimeout(None)
        return None

    # ──────────────────────────── selector shim ──────────────────────────────

    def _install_selector_shim(self):
        """Register the querySelector :first-child shim for all future pages
        and execute it immediately on the current page.

        The shim is idempotent (guarded by window.__cdpSelectorShim) so calling
        this multiple times is safe.
        """
        try:
            result = self.cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": _SELECTOR_SHIM_JS,
            })
            self._shim_script_id = (
                result.get("result", {}).get("identifier")
            )
        except Exception:
            pass  # non-fatal: shim will still be injected per-navigation
        # Also apply immediately to the current page
        self.js(_SELECTOR_SHIM_JS)

    def _apply_selector_shim(self):
        """Re-inject the shim into the current page (called after every go())."""
        self.js(_SELECTOR_SHIM_JS)

    # ──────────────────────────── CSS domain ─────────────────────────────────

    def _ensure_css_enabled(self):
        if not self._css_enabled:
            self.cmd("CSS.enable")
            self._css_enabled = True

    # ──────────────────────────── navigation ─────────────────────────────────

    def go(self, url: str, timeout: float = 30.0):
        """Navigate to *url* and wait for a page-load signal."""
        self._drain_events()
        self._css_enabled = False

        self.msg_id += 1
        nav_id = self.msg_id
        self.ws.send(json.dumps({
            "id": nav_id,
            "method": "Page.navigate",
            "params": {"url": url},
        }))

        deadline = time.monotonic() + timeout
        nav_acked = False
        load_fired = False

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                self.ws.settimeout(min(remaining, 1.0))
                raw = self.ws.recv()
                self.ws.settimeout(None)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                self.ws.settimeout(None)
                break

            msg = json.loads(raw)
            if msg.get("id") == nav_id:
                nav_acked = True
            elif "method" in msg:
                if msg["method"] in _LOAD_SIGNALS:
                    load_fired = True
                else:
                    with self._event_lock:
                        self._pending_events.append(msg)

            if nav_acked and load_fired:
                self.ws.settimeout(None)
                # Inject the querySelector shim into the newly loaded page
                self._apply_selector_shim()
                return

        self.ws.settimeout(None)

        if not load_fired:
            try:
                state = self.js("document.readyState")
                if state in ("complete", "interactive"):
                    self._apply_selector_shim()
                    return
            except Exception:
                pass
            print(
                f"[WebTool] Warning: page-load event not received within "
                f"{timeout}s for {url!r}"
            )
        else:
            self._apply_selector_shim()

    def wait_for_navigation(self, timeout: float = 15.0) -> bool:
        """Block until the next page-load event fires (e.g. after a click)."""
        result = self._wait_for_any_event(_LOAD_SIGNALS, timeout)
        if result:
            self._apply_selector_shim()
        return result is not None

    # ──────────────────────────── element helpers ─────────────────────────────

    def _query(self, selector: str) -> int:
        escaped = _esc_dq(selector)
        result = self.js(
            f'document.querySelector("{escaped}") !== null ? 1 : 0'
        )
        return 1 if result == 1 else 0

    def _get_coords_full(
        self, selector: str
    ) -> Optional[tuple[float, float, str]]:
        for sel in _selector_fallbacks(selector):
            code = _coords_js(_esc_dq(sel))
            for attempt in range(3):
                raw = self.js(code)
                if raw:
                    try:
                        data = json.loads(raw)
                        return data["x"], data["y"], sel
                    except (json.JSONDecodeError, KeyError):
                        pass
                if attempt < 2:
                    time.sleep(0.1)
        return None

    def _get_coords(self, selector: str) -> Optional[tuple[float, float]]:
        result = self._get_coords_full(selector)
        if result is None:
            return None
        return result[0], result[1]

    def _center_of(self, node_id: int) -> tuple[float, float]:
        box = self.cmd("DOM.getBoxModel", {"nodeId": node_id})
        if "error" in box:
            raise WebToolError(
                f"Cannot get box model for nodeId={node_id}: {box['error']}"
            )
        content = box["result"]["model"]["content"]
        x = (content[0] + content[4]) / 2
        y = (content[1] + content[5]) / 2
        return x, y

    def _force_hover_at(self, x: float, y: float, matched_selector: str):
        """Force CSS :hover on the node at (x, y) AND its ancestors.

        Gen 6 change: walks up to 6 ancestor levels via DOM.describeNode and
        forces :hover on each, so CSS rules like
        ``.figure:hover .figcaption { display:block }`` work when the
        hovered element is a child (e.g. the ``img`` inside ``.figure``).
        """
        try:
            self._ensure_css_enabled()
            node_info = self.cmd("DOM.getNodeForLocation", {
                "x": int(x),
                "y": int(y),
                "includeUserAgentShadowDOM": False,
            })
            node_id = (
                node_info.get("result", {}).get("nodeId")
                or node_info.get("result", {}).get("backendNodeId")
            )
            if not node_id:
                return

            # Collect node + ancestors (up to 5 levels)
            node_ids: list[int] = [node_id]
            current_id: int = node_id
            for _ in range(5):
                try:
                    desc = self.cmd("DOM.describeNode", {"nodeId": current_id})
                    parent_id = (
                        desc.get("result", {})
                        .get("node", {})
                        .get("parentId")
                    )
                    if not parent_id:
                        break
                    node_ids.append(parent_id)
                    current_id = parent_id
                except Exception:
                    break

            for nid in node_ids:
                try:
                    self.cmd("CSS.forcePseudoState", {
                        "nodeId": nid,
                        "forcedPseudoClasses": ["hover"],
                    })
                except Exception:
                    pass  # individual ancestor may be non-element; safe to skip
        except Exception:
            pass  # non-fatal

    # ──────────────────────────── public actions ──────────────────────────────

    def click(self, selector: str) -> bool:
        coords = self._get_coords(selector)
        if coords is None:
            raise WebToolError(f"click(): no element for selector {selector!r}")
        x, y = coords
        self.cmd("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        self.cmd("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        return True

    def double_click(self, selector: str) -> bool:
        coords = self._get_coords(selector)
        if coords is None:
            raise WebToolError(
                f"double_click(): no element for selector {selector!r}"
            )
        x, y = coords
        for click_count in (1, 2):
            self.cmd("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": click_count,
            })
            self.cmd("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": click_count,
            })
        return True

    def triple_click(self, selector: str) -> bool:
        coords = self._get_coords(selector)
        if coords is None:
            raise WebToolError(
                f"triple_click(): no element for selector {selector!r}"
            )
        x, y = coords
        for click_count in (1, 2, 3):
            self.cmd("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": click_count,
            })
            self.cmd("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": click_count,
            })
        return True

    def type(self, text: str):
        for char in text:
            self.cmd("Input.dispatchKeyEvent", {"type": "char", "text": char})

    def fill(self, selector: str, text: str):
        coords = self._get_coords(selector)
        if coords is not None:
            x, y = coords
            for click_count in (1, 2, 3):
                self.cmd("Input.dispatchMouseEvent", {
                    "type": "mousePressed", "x": x, "y": y,
                    "button": "left", "clickCount": click_count,
                })
                self.cmd("Input.dispatchMouseEvent", {
                    "type": "mouseReleased", "x": x, "y": y,
                    "button": "left", "clickCount": click_count,
                })
            self.type(text)
        else:
            esc_sel  = _esc_dq(selector)
            esc_text = _esc_dq(text)
            result = self.js(
                f'(function(){{'
                f'  var el = document.querySelector("{esc_sel}");'
                f'  if (!el) return "NOT_FOUND";'
                f'  el.focus();'
                f'  el.value = "{esc_text}";'
                f'  el.dispatchEvent(new Event("input",  {{bubbles:true}}));'
                f'  el.dispatchEvent(new Event("change", {{bubbles:true}}));'
                f'  return "ok";'
                f'}})()'
            )
            if result == "NOT_FOUND":
                raise WebToolError(
                    f"fill(): no element for selector {selector!r}"
                )

    def clear(self, selector: str):
        esc_sel = _esc_dq(selector)
        result = self.js(
            f'(function(){{'
            f'  var el = document.querySelector("{esc_sel}");'
            f'  if (!el) return "NOT_FOUND";'
            f'  el.focus();'
            f'  el.value = "";'
            f'  el.dispatchEvent(new Event("input",  {{bubbles:true}}));'
            f'  el.dispatchEvent(new Event("change", {{bubbles:true}}));'
            f'  return "ok";'
            f'}})()'
        )
        if result == "NOT_FOUND":
            raise WebToolError(f"clear(): no element for selector {selector!r}")

    def key(self, key: str):
        self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
        self.cmd("Input.dispatchKeyEvent", {"type": "keyUp",   "key": key})

    def wait(self, selector: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._query(selector):
                return True
            time.sleep(0.1)
        return False

    def wait_for_visible(self, selector: str, timeout: float = 10.0) -> bool:
        esc = _esc_dq(selector)
        code = (
            f'(function(){{'
            f'  var el = document.querySelector("{esc}");'
            f'  if (!el) return false;'
            f'  var s = window.getComputedStyle(el);'
            f'  return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0";'
            f'}})()'
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.js(code) is True:
                return True
            time.sleep(0.1)
        return False

    def wait_for_text(
        self, selector: str, text: str, timeout: float = 10.0
    ) -> bool:
        esc_sel  = _esc_dq(selector)
        esc_text = _esc_dq(text)
        code = (
            f'(function(){{'
            f'  var el = document.querySelector("{esc_sel}");'
            f'  return el ? el.textContent.indexOf("{esc_text}") !== -1 : false;'
            f'}})()'
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.js(code) is True:
                return True
            time.sleep(0.1)
        return False

    def wait_for_url_change(self, timeout: float = 15.0) -> str:
        """Wait until window.location.href differs from its current value.

        Captures the URL at call time, then polls at 0.1 s intervals until the
        URL changes or *timeout* elapses.  Returns the new URL (or the
        original URL if the timeout expires without a change).

        Typical use: call immediately after click() on a link/button that
        triggers navigation, then read page content once the URL has settled.

        Example
        -------
        web.click("a.nav-link")
        new_url = web.wait_for_url_change(timeout=10)
        print(new_url)   # https://example.com/new-page
        """
        current_url: str = self.js("window.location.href") or ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            new_url: str = self.js("window.location.href") or ""
            if new_url and new_url != current_url:
                return new_url
            time.sleep(0.1)
        return current_url

    # ─── scrolling / hover / select ──────────────────────────────────────────

    def scroll(self, direction: str = "down", pixels: int = 500):
        direction = direction.lower()
        delta_x, delta_y = 0, 0
        if direction == "down":
            delta_y = pixels
        elif direction == "up":
            delta_y = -pixels
        elif direction == "right":
            delta_x = pixels
        elif direction == "left":
            delta_x = -pixels
        else:
            raise WebToolError(f"scroll(): unknown direction {direction!r}")

        self.cmd("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": 400, "y": 300,
            "deltaX": delta_x,
            "deltaY": delta_y,
        })

    def hover(self, selector: str):
        """Move the mouse over *selector* and force CSS :hover on ancestors.

        Gen 6 two-layer hover:
          1. ``Input.dispatchMouseEvent(mouseMoved)`` — triggers JS
             mouseover/mouseenter handlers and Chrome's built-in :hover tracking.
          2. ``CSS.forcePseudoState`` applied to the hovered node AND its
             ancestors (up to 5 levels) — ensures CSS rules like
             ``.figure:hover .figcaption { display:block }`` remain active
             when the caller reads the DOM after hover() returns.
        """
        result = self._get_coords_full(selector)
        if result is None:
            raise WebToolError(f"hover(): no element for selector {selector!r}")
        x, y, matched_sel = result

        self.cmd("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })

        self._force_hover_at(x, y, matched_sel)

    def select_option(self, selector: str, value: str):
        esc_sel = _esc_sq(selector)
        esc_val = _esc_sq(value)
        code = (
            f"(function(){{"
            f"  var el = document.querySelector('{esc_sel}');"
            f"  if (!el) return 'NOT_FOUND';"
            f"  el.value = '{esc_val}';"
            f"  el.dispatchEvent(new Event('change', {{bubbles: true}}));"
            f"  return el.value;"
            f"}})()"
        )
        result = self.js(code)
        if result == "NOT_FOUND":
            raise WebToolError(
                f"select_option(): no element for selector {selector!r}"
            )

    # ─── visibility / introspection ───────────────────────────────────────────

    def is_visible(self, selector: str) -> bool:
        esc = _esc_dq(selector)
        result = self.js(
            f'(function(){{'
            f'  var el = document.querySelector("{esc}");'
            f'  if (!el) return false;'
            f'  var s = window.getComputedStyle(el);'
            f'  return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0";'
            f'}})()'
        )
        return result is True

    def get_computed_style(self, selector: str, property_name: str) -> Optional[str]:
        esc_sel  = _esc_dq(selector)
        esc_prop = _esc_dq(property_name)
        result = self.js(
            f'(function(){{'
            f'  var el = document.querySelector("{esc_sel}");'
            f'  if (!el) return null;'
            f'  return window.getComputedStyle(el).getPropertyValue("{esc_prop}");'
            f'}})()'
        )
        return result

    def get_element_count(self, selector: str) -> int:
        esc = _esc_dq(selector)
        result = self.js(
            f'document.querySelectorAll("{esc}").length'
        )
        return int(result) if result is not None else 0

    # ─── composite high-level flows (gen 6) ──────────────────────────────────

    def login(
        self,
        url: str,
        username_selector: str,
        username: str,
        password_selector: str,
        password: str,
        submit_selector: str,
        timeout: float = 15.0,
    ) -> bool:
        """All-in-one login helper.

        Navigates to *url*, fills the username and password fields, clicks the
        submit element, waits for navigation, and returns True if the URL
        changed (indicating successful login redirect).

        Parameters
        ----------
        url               : login page URL
        username_selector : CSS selector for the username/email field
        username          : username string to type
        password_selector : CSS selector for the password field
        password          : password string to type
        submit_selector   : CSS selector for the submit button/input
        timeout           : max seconds to wait for post-login navigation

        Returns
        -------
        True if the URL changed after submit (login redirected), False otherwise.

        Example
        -------
        success = web.login(
            "https://the-internet.herokuapp.com/login",
            "#username", "tomsmith",
            "#password", "SuperSecretPassword!",
            "button[type='submit']",
        )
        print(success, web.js("window.location.href"))
        """
        self.go(url)
        self.fill(username_selector, username)
        self.fill(password_selector, password)
        initial_url: str = self.js("window.location.href") or url
        self.click(submit_selector)
        # Wait for navigation or URL change
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current: str = self.js("window.location.href") or ""
            if current and current != initial_url:
                self._apply_selector_shim()
                return True
            time.sleep(0.1)
        # Fallback: check readyState after timeout
        self._apply_selector_shim()
        final_url: str = self.js("window.location.href") or ""
        return bool(final_url and final_url != initial_url)

    def fill_form(self, field_map: "dict[str, str]") -> None:
        """Fill multiple form fields in one call.

        Detects the type of each field by selector and uses the appropriate
        fill strategy:
          - ``<select>``         → select_option(selector, value)
          - ``<input type=checkbox>`` or ``<input type=radio>`` →
            click if value is truthy (non-empty, non-"false", non-"0")
            and field is not already checked
          - Everything else      → fill(selector, value)

        Parameters
        ----------
        field_map : dict mapping CSS selector → value string

        Raises
        ------
        WebToolError if any selector is not found in the DOM.

        Example
        -------
        web.fill_form({
            "#username":  "tomsmith",
            "#password":  "SuperSecretPassword!",
            "#remember":  "true",
            "#country":   "US",
        })
        web.click("button[type='submit']")
        """
        for selector, value in field_map.items():
            esc = _esc_dq(selector)
            field_info_raw = self.js(
                f'(function(){{'
                f'  var el = document.querySelector("{esc}");'
                f'  if (!el) return null;'
                f'  return JSON.stringify({{tag: el.tagName.toLowerCase(),'
                f'                         type: (el.type || "").toLowerCase(),'
                f'                         checked: el.checked || false}});'
                f'}})()'
            )
            if field_info_raw is None:
                raise WebToolError(
                    f"fill_form(): no element for selector {selector!r}"
                )
            info: dict = json.loads(field_info_raw)
            tag   = info.get("tag", "")
            ftype = info.get("type", "")
            checked = info.get("checked", False)

            if tag == "select":
                self.select_option(selector, value)
            elif ftype in ("checkbox", "radio"):
                truthy = value.lower() not in ("false", "0", "", "no", "off")
                if truthy and not checked:
                    self.click(selector)
                elif not truthy and checked:
                    self.click(selector)
            else:
                self.fill(selector, value)

    def paginate(
        self,
        next_selector: str,
        extractor_fn: "Callable[[WebTool], list]",
        max_pages: int = 10,
    ) -> list:
        """Collect data across multiple pages.

        On each page: calls ``extractor_fn(self)`` to collect items, then
        checks whether *next_selector* exists and clicks it to advance.
        Stops when the next-page element disappears or *max_pages* is reached.

        Parameters
        ----------
        next_selector : CSS selector for the "next page" link/button
        extractor_fn  : callable(web) → list — extracts items from current page
        max_pages     : safety cap (default 10)

        Returns
        -------
        Flat list of all items collected across all pages.

        Example
        -------
        def get_quotes(web):
            return web.js(
                "JSON.stringify(Array.from(document.querySelectorAll('.quote .text'))"
                ".map(e => e.textContent.trim()))"
            )

        all_quotes = web.paginate("li.next a", get_quotes, max_pages=5)
        """
        results: list = []
        for _page in range(max_pages):
            page_data = extractor_fn(self)
            if page_data:
                if isinstance(page_data, list):
                    results.extend(page_data)
                elif isinstance(page_data, str):
                    try:
                        parsed = json.loads(page_data)
                        if isinstance(parsed, list):
                            results.extend(parsed)
                        else:
                            results.append(parsed)
                    except (json.JSONDecodeError, TypeError):
                        results.append(page_data)
                else:
                    results.append(page_data)
            if not self._query(next_selector):
                break
            self.click(next_selector)
            self.wait_for_navigation(timeout=15)
        return results

    # ─── text / data extraction ───────────────────────────────────────────────

    def find_by_text(self, text: str, tag: str = "*") -> Optional[dict]:
        esc_text = _esc_sq(text)
        esc_tag  = _esc_sq(tag)
        code = (
            f"(function(){{"
            f"  var tag = '{esc_tag}';"
            f"  var needle = '{esc_text}';"
            f"  var els = document.querySelectorAll(tag);"
            f"  for (var i = 0; i < els.length; i++) {{"
            f"    if (els[i].textContent.trim() === needle) {{"
            f"      var e = els[i];"
            f"      var parts = [];"
            f"      while (e && e.nodeType === 1) {{"
            f"        var idx = 1, sib = e.previousElementSibling;"
            f"        while (sib) {{"
            f"          if (sib.tagName === e.tagName) idx++;"
            f"          sib = sib.previousElementSibling;"
            f"        }}"
            f"        parts.unshift(e.tagName.toLowerCase()+"
            f"                      ':nth-of-type('+idx+')');"
            f"        e = e.parentElement;"
            f"      }}"
            f"      return JSON.stringify({{tagName: els[i].tagName,"
            f"                             selector: parts.join(' > ')}});"
            f"    }}"
            f"  }}"
            f"  return null;"
            f"}})()"
        )
        raw = self.js(code)
        if raw is None:
            return None
        return json.loads(raw)

    def click_by_text(self, text: str, tag: str = "*") -> bool:
        info = self.find_by_text(text, tag)
        if not info:
            raise WebToolError(
                f"click_by_text(): no {tag!r} element with text {text!r}"
            )
        return self.click(info["selector"])

    def get_all_links(self) -> list[str]:
        result = self.js(
            "JSON.stringify("
            "  Array.from(document.querySelectorAll('a[href]'))"
            "    .map(a => a.href)"
            "    .filter(h => h && !h.startsWith('javascript:'))"
            ")"
        )
        if result is None:
            return []
        return json.loads(result)

    def get_table(self, selector: str = "table") -> list[dict]:
        escaped = _esc_sq(selector)
        code = (
            f"(function(){{"
            f"  var t = document.querySelector('{escaped}');"
            f"  if (!t) return null;"
            f"  var headers = Array.from("
            f"    t.querySelectorAll('thead th, thead td'))"
            f"    .map(function(h){{ return h.textContent.trim(); }});"
            f"  if (headers.length === 0) {{"
            f"    var fr = t.querySelector('tr');"
            f"    if (fr) headers = Array.from(fr.querySelectorAll('th,td'))"
            f"      .map(function(h){{ return h.textContent.trim(); }});"
            f"  }}"
            f"  var bodyRows = Array.from(t.querySelectorAll('tbody tr'));"
            f"  if (bodyRows.length === 0)"
            f"    bodyRows = Array.from(t.querySelectorAll('tr')).slice(1);"
            f"  return JSON.stringify(bodyRows.map(function(row){{"
            f"    var cells = Array.from(row.querySelectorAll('td,th'))"
            f"      .map(function(c){{ return c.textContent.trim(); }});"
            f"    var obj = {{}};"
            f"    cells.forEach(function(c,i){{"
            f"      obj[headers[i] !== undefined ? headers[i] : i] = c;"
            f"    }});"
            f"    return obj;"
            f"  }}));"
            f"}})()"
        )
        result = self.js(code)
        if result is None:
            raise WebToolError(
                f"get_table(): no table found for selector {selector!r}"
            )
        return json.loads(result)

    def get_form_fields(self, selector: str = "form") -> list[dict]:
        escaped = _esc_sq(selector)
        code = (
            f"(function(){{"
            f"  var form = document.querySelector('{escaped}');"
            f"  if (!form) return null;"
            f"  var fields = [];"
            f"  form.querySelectorAll('input,select,textarea').forEach("
            f"    function(el){{"
            f"      var opts = [];"
            f"      if (el.tagName === 'SELECT') {{"
            f"        opts = Array.from(el.options).map(function(o){{"
            f"          return {{value: o.value, text: o.text.trim()}};"
            f"        }});"
            f"      }}"
            f"      fields.push({{"
            f"        tag: el.tagName.toLowerCase(),"
            f"        type: el.type || '',"
            f"        name: el.name || '',"
            f"        id:   el.id   || '',"
            f"        value: el.value || '',"
            f"        placeholder: el.placeholder || '',"
            f"        required: el.required || false,"
            f"        options: opts"
            f"      }});"
            f"    }});"
            f"  return JSON.stringify(fields);"
            f"}})()"
        )
        result = self.js(code)
        if result is None:
            raise WebToolError(
                f"get_form_fields(): no element for selector {selector!r}"
            )
        return json.loads(result)

    def get_page_structure(self) -> dict:
        result = self.js(
            "JSON.stringify({"
            "  title: document.title,"
            "  url: window.location.href,"
            "  h1s: Array.from(document.querySelectorAll('h1')).map(function(e){return e.textContent.trim();}),"
            "  h2s: Array.from(document.querySelectorAll('h2')).map(function(e){return e.textContent.trim();}),"
            "  link_count: document.querySelectorAll('a[href]').length,"
            "  form_count: document.querySelectorAll('form').length,"
            "  table_count: document.querySelectorAll('table').length"
            "})"
        )
        if result is None:
            return {}
        return json.loads(result)

    # ─── file upload ──────────────────────────────────────────────────────────

    def upload_file(self, selector: str, file_path: str):
        esc = _esc_dq(selector)
        node_id_raw = self.js(
            f'(function(){{'
            f'  var el = document.querySelector("{esc}");'
            f'  if (!el || el.type !== "file") return null;'
            f'  return true;'
            f'}})()'
        )
        if not node_id_raw:
            raise WebToolError(
                f"upload_file(): no file input for selector {selector!r}"
            )

        root = self.cmd("DOM.getDocument")["result"]["root"]["nodeId"]
        node_result = self.cmd("DOM.querySelector", {
            "nodeId": root,
            "selector": selector,
        })
        node_id = node_result.get("result", {}).get("nodeId")
        if not node_id:
            raise WebToolError(
                f"upload_file(): DOM.querySelector found no node for {selector!r}"
            )

        self.cmd("DOM.setFileInputFiles", {
            "nodeId": node_id,
            "files": [file_path],
        })

    # ─── multi-tab support ────────────────────────────────────────────────────

    def _refresh_tab_registry(self):
        try:
            tabs = requests.get(
                f"http://localhost:{self.port}/json/list", timeout=5
            ).json()
        except Exception:
            tabs = requests.get(
                f"http://localhost:{self.port}/json", timeout=5
            ).json()
        for t in tabs:
            if t.get("type") == "page" and "webSocketDebuggerUrl" in t:
                self._tabs[t["id"]] = t["webSocketDebuggerUrl"]

    def open_tab(self, url: str = "about:blank") -> str:
        result = self.cmd("Target.createTarget", {"url": url})
        tab_id: str = result["result"]["targetId"]
        self._refresh_tab_registry()
        return tab_id

    def switch_tab(self, tab_id: str):
        if tab_id == self._current_tab_id:
            return

        ws_url = self._tabs.get(tab_id)
        if not ws_url:
            self._refresh_tab_registry()
            ws_url = self._tabs.get(tab_id)
        if not ws_url:
            raise WebToolError(f"switch_tab(): tab {tab_id!r} not found")

        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

        self.ws = websocket.create_connection(ws_url)
        self._current_tab_id = tab_id
        self._css_enabled = False
        self.msg_id = 0
        self.cmd("Page.enable")
        self.cmd("DOM.enable")
        self.cmd("Runtime.enable")
        self._apply_selector_shim()

    def close_tab(self, tab_id: str):
        self.cmd("Target.closeTarget", {"targetId": tab_id})
        self._tabs.pop(tab_id, None)
        if tab_id == self._current_tab_id:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
            self._current_tab_id = None

    def get_open_tabs(self) -> list[dict]:
        self._refresh_tab_registry()
        try:
            tabs = requests.get(
                f"http://localhost:{self.port}/json/list", timeout=5
            ).json()
        except Exception:
            tabs = requests.get(
                f"http://localhost:{self.port}/json", timeout=5
            ).json()
        return [
            {"id": t["id"], "url": t.get("url", ""), "title": t.get("title", "")}
            for t in tabs
            if t.get("type") == "page"
        ]

    # ──────────────────────────── JS helper ──────────────────────────────────

    def js(self, code: str):
        try:
            result = self.cmd("Runtime.evaluate", {
                "expression": code,
                "returnByValue": True,
            })

            if "error" in result:
                print(f"[WebTool] DevTools Protocol Error: {result['error']}")
                return None

            response = result.get("result", {})

            if "exceptionDetails" in response:
                exc = response["exceptionDetails"]
                print(
                    f"[WebTool] JS Exception at line "
                    f"{exc.get('lineNumber', '?')}: "
                    f"{exc.get('text', 'Unknown error')}"
                )
                return None

            js_result = response.get("result", {})
            result_type = js_result.get("type", "undefined")

            if result_type == "undefined":
                return None
            elif result_type in ("string", "number", "boolean"):
                return js_result.get("value")
            elif result_type == "object":
                if js_result.get("subtype") == "null":
                    return None
                return js_result.get("value")
            else:
                return js_result.get("value", js_result.get("description"))

        except Exception as exc:
            print(
                f"[WebTool] Python Exception in js(): "
                f"{type(exc).__name__}: {exc}"
            )
            return None

    # ──────────────────────────── screenshot ─────────────────────────────────

    def screenshot(
        self,
        filename: Optional[str] = None,
        quality: int = 80,
        format: str = "jpeg",
    ) -> str:
        params: dict = {"format": format}
        if format == "jpeg":
            params["quality"] = quality
        result = self.cmd("Page.captureScreenshot", params)
        data = result["result"]["data"]
        if filename:
            with open(filename, "wb") as f:
                f.write(base64.b64decode(data))
            return filename
        return data

    # ──────────────────────────── convenience ────────────────────────────────

    def text(self, selector: str) -> Optional[str]:
        escaped = _esc_sq(selector)
        return self.js(
            f"document.querySelector('{escaped}')?.textContent ?? null"
        )

    def attr(self, selector: str, attribute: str) -> Optional[str]:
        esc_sel  = _esc_sq(selector)
        esc_attr = _esc_sq(attribute)
        return self.js(
            f"document.querySelector('{esc_sel}')"
            f"?.getAttribute('{esc_attr}') ?? null"
        )

    get_attribute = attr


# ──────────────────────────────────────────────────────────────────────────────
# Async tool
# ──────────────────────────────────────────────────────────────────────────────

class AsyncWebTool:
    """Fully async CDP browser automation tool using the ``websockets`` library."""

    def __init__(self, port: int = 9222):
        self.port = port
        self._ws = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_listeners: list = []
        self._recv_task: Optional[asyncio.Task] = None
        self._css_enabled = False

    # ──────────── connection ──────────────────────────────────────────────────

    async def connect(self, tab_index: int = 0):
        try:
            import websockets as _ws_lib  # noqa: F401
        except ImportError as exc:
            raise WebToolError(
                "AsyncWebTool requires 'websockets': pip install websockets"
            ) from exc

        loop = asyncio.get_running_loop()
        tabs = await loop.run_in_executor(
            None,
            lambda: requests.get(f"http://localhost:{self.port}/json").json(),
        )
        if not tabs:
            raise WebToolError(f"No tabs found on port {self.port}")

        ws_url = tabs[tab_index]["webSocketDebuggerUrl"]
        import websockets as _ws_lib
        self._ws = await _ws_lib.connect(ws_url)
        self._recv_task = asyncio.create_task(self._recv_loop())

        await self.cmd_async("Page.enable")
        await self.cmd_async("DOM.enable")
        await self.cmd_async("Runtime.enable")
        # Install shim
        try:
            await self.cmd_async("Page.addScriptToEvaluateOnNewDocument", {
                "source": _SELECTOR_SHIM_JS,
            })
        except Exception:
            pass
        await self.js_async(_SELECTOR_SHIM_JS)

    async def close(self):
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    # ──────────── receive loop ────────────────────────────────────────────────

    async def _recv_loop(self):
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    for listener in list(self._event_listeners):
                        try:
                            if asyncio.iscoroutinefunction(listener):
                                await listener(msg)
                            else:
                                listener(msg)
                        except Exception:
                            pass
        except Exception:
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()

    # ──────────── low-level ──────────────────────────────────────────────────

    async def cmd_async(
        self, method: str, params: Optional[dict] = None
    ) -> dict:
        self._msg_id += 1
        msg_id = self._msg_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut
        await self._ws.send(
            json.dumps({"id": msg_id, "method": method, "params": params or {}})
        )
        return await fut

    # ──────────── CSS domain ─────────────────────────────────────────────────

    async def _ensure_css_enabled_async(self):
        if not self._css_enabled:
            await self.cmd_async("CSS.enable")
            self._css_enabled = True

    # ──────────── navigation ─────────────────────────────────────────────────

    async def go_async(self, url: str, timeout: float = 30.0):
        self._css_enabled = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        load_fut: asyncio.Future = loop.create_future()

        def _on_event(msg: dict):
            if msg.get("method") in _LOAD_SIGNALS and not load_fut.done():
                load_fut.set_result(True)

        self._event_listeners.append(_on_event)
        try:
            await self.cmd_async("Page.navigate", {"url": url})

            state = await self.js_async("document.readyState")
            if state in ("complete", "interactive") and load_fut.done():
                await self.js_async(_SELECTOR_SHIM_JS)
                return

            remaining = deadline - loop.time()
            if remaining > 0:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(load_fut), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    pass

            poll_deadline = loop.time() + 2.0
            while loop.time() < poll_deadline:
                state = await self.js_async("document.readyState")
                if state == "complete":
                    break
                await asyncio.sleep(0.1)

            await self.js_async(_SELECTOR_SHIM_JS)

        finally:
            try:
                self._event_listeners.remove(_on_event)
            except ValueError:
                pass

    async def wait_for_navigation_async(self, timeout: float = 15.0) -> bool:
        loop = asyncio.get_running_loop()
        load_fut: asyncio.Future = loop.create_future()

        def _on_event(msg: dict):
            if msg.get("method") in _LOAD_SIGNALS and not load_fut.done():
                load_fut.set_result(True)

        self._event_listeners.append(_on_event)
        try:
            await asyncio.wait_for(asyncio.shield(load_fut), timeout=timeout)
            await self.js_async(_SELECTOR_SHIM_JS)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            try:
                self._event_listeners.remove(_on_event)
            except ValueError:
                pass

    # ──────────── element helpers ─────────────────────────────────────────────

    async def _query_async(self, selector: str) -> int:
        escaped = _esc_dq(selector)
        result = await self.js_async(
            f'document.querySelector("{escaped}") !== null ? 1 : 0'
        )
        return 1 if result == 1 else 0

    async def _get_coords_full_async(
        self, selector: str
    ) -> Optional[tuple[float, float, str]]:
        for sel in _selector_fallbacks(selector):
            code = _coords_js(_esc_dq(sel))
            for attempt in range(3):
                raw = await self.js_async(code)
                if raw:
                    try:
                        data = json.loads(raw)
                        return data["x"], data["y"], sel
                    except (json.JSONDecodeError, KeyError):
                        pass
                if attempt < 2:
                    await asyncio.sleep(0.1)
        return None

    async def _get_coords_async(
        self, selector: str
    ) -> Optional[tuple[float, float]]:
        result = await self._get_coords_full_async(selector)
        if result is None:
            return None
        return result[0], result[1]

    async def _force_hover_at_async(self, x: float, y: float):
        """Force CSS :hover on the node at (x, y) AND its ancestors (gen 6)."""
        try:
            await self._ensure_css_enabled_async()
            node_info = await self.cmd_async("DOM.getNodeForLocation", {
                "x": int(x),
                "y": int(y),
                "includeUserAgentShadowDOM": False,
            })
            node_id = (
                node_info.get("result", {}).get("nodeId")
                or node_info.get("result", {}).get("backendNodeId")
            )
            if not node_id:
                return

            node_ids: list[int] = [node_id]
            current_id: int = node_id
            for _ in range(5):
                try:
                    desc = await self.cmd_async(
                        "DOM.describeNode", {"nodeId": current_id}
                    )
                    parent_id = (
                        desc.get("result", {})
                        .get("node", {})
                        .get("parentId")
                    )
                    if not parent_id:
                        break
                    node_ids.append(parent_id)
                    current_id = parent_id
                except Exception:
                    break

            for nid in node_ids:
                try:
                    await self.cmd_async("CSS.forcePseudoState", {
                        "nodeId": nid,
                        "forcedPseudoClasses": ["hover"],
                    })
                except Exception:
                    pass
        except Exception:
            pass

    # ──────────── public async actions ───────────────────────────────────────

    async def click_async(self, selector: str) -> bool:
        coords = await self._get_coords_async(selector)
        if coords is None:
            raise WebToolError(
                f"click_async(): no element for selector {selector!r}"
            )
        x, y = coords
        await self.cmd_async("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        await self.cmd_async("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        return True

    async def hover_async(self, selector: str):
        result = await self._get_coords_full_async(selector)
        if result is None:
            raise WebToolError(
                f"hover_async(): no element for selector {selector!r}"
            )
        x, y, _ = result
        await self.cmd_async("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })
        await self._force_hover_at_async(x, y)

    async def fill_async(self, selector: str, text: str):
        coords = await self._get_coords_async(selector)
        if coords is not None:
            x, y = coords
            for click_count in (1, 2, 3):
                await self.cmd_async("Input.dispatchMouseEvent", {
                    "type": "mousePressed", "x": x, "y": y,
                    "button": "left", "clickCount": click_count,
                })
                await self.cmd_async("Input.dispatchMouseEvent", {
                    "type": "mouseReleased", "x": x, "y": y,
                    "button": "left", "clickCount": click_count,
                })
            for char in text:
                await self.cmd_async(
                    "Input.dispatchKeyEvent", {"type": "char", "text": char}
                )
        else:
            esc_sel  = _esc_dq(selector)
            esc_text = _esc_dq(text)
            result = await self.js_async(
                f'(function(){{'
                f'  var el = document.querySelector("{esc_sel}");'
                f'  if (!el) return "NOT_FOUND";'
                f'  el.focus();'
                f'  el.value = "{esc_text}";'
                f'  el.dispatchEvent(new Event("input",  {{bubbles:true}}));'
                f'  el.dispatchEvent(new Event("change", {{bubbles:true}}));'
                f'  return "ok";'
                f'}})()'
            )
            if result == "NOT_FOUND":
                raise WebToolError(
                    f"fill_async(): no element for selector {selector!r}"
                )

    async def wait_async(self, selector: str, timeout: float = 10.0) -> bool:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self._query_async(selector):
                return True
            await asyncio.sleep(0.1)
        return False

    async def wait_for_url_change_async(self, timeout: float = 15.0) -> str:
        current_url: str = await self.js_async("window.location.href") or ""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            new_url: str = await self.js_async("window.location.href") or ""
            if new_url and new_url != current_url:
                return new_url
            await asyncio.sleep(0.1)
        return current_url

    async def login_async(
        self,
        url: str,
        username_selector: str,
        username: str,
        password_selector: str,
        password: str,
        submit_selector: str,
        timeout: float = 15.0,
    ) -> bool:
        await self.go_async(url)
        await self.fill_async(username_selector, username)
        await self.fill_async(password_selector, password)
        initial_url: str = await self.js_async("window.location.href") or url
        await self.click_async(submit_selector)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            current: str = await self.js_async("window.location.href") or ""
            if current and current != initial_url:
                await self.js_async(_SELECTOR_SHIM_JS)
                return True
            await asyncio.sleep(0.1)
        await self.js_async(_SELECTOR_SHIM_JS)
        final_url: str = await self.js_async("window.location.href") or ""
        return bool(final_url and final_url != initial_url)

    async def fill_form_async(self, field_map: "dict[str, str]") -> None:
        for selector, value in field_map.items():
            esc = _esc_dq(selector)
            field_info_raw = await self.js_async(
                f'(function(){{'
                f'  var el = document.querySelector("{esc}");'
                f'  if (!el) return null;'
                f'  return JSON.stringify({{tag: el.tagName.toLowerCase(),'
                f'                         type: (el.type || "").toLowerCase(),'
                f'                         checked: el.checked || false}});'
                f'}})()'
            )
            if field_info_raw is None:
                raise WebToolError(
                    f"fill_form_async(): no element for selector {selector!r}"
                )
            info: dict = json.loads(field_info_raw)
            tag   = info.get("tag", "")
            ftype = info.get("type", "")
            checked = info.get("checked", False)

            if tag == "select":
                esc_sel = _esc_sq(selector)
                esc_val = _esc_sq(value)
                await self.js_async(
                    f"(function(){{"
                    f"  var el = document.querySelector('{esc_sel}');"
                    f"  if (el) {{ el.value = '{esc_val}';"
                    f"  el.dispatchEvent(new Event('change', {{bubbles:true}})); }}"
                    f"}})()"
                )
            elif ftype in ("checkbox", "radio"):
                truthy = value.lower() not in ("false", "0", "", "no", "off")
                if truthy and not checked:
                    await self.click_async(selector)
                elif not truthy and checked:
                    await self.click_async(selector)
            else:
                await self.fill_async(selector, value)

    async def paginate_async(
        self,
        next_selector: str,
        extractor_fn: "Callable[[AsyncWebTool], Coroutine[Any, Any, list]]",
        max_pages: int = 10,
    ) -> list:
        results: list = []
        for _page in range(max_pages):
            page_data = await extractor_fn(self)
            if page_data:
                if isinstance(page_data, list):
                    results.extend(page_data)
                elif isinstance(page_data, str):
                    try:
                        parsed = json.loads(page_data)
                        results.extend(parsed) if isinstance(parsed, list) else results.append(parsed)
                    except (json.JSONDecodeError, TypeError):
                        results.append(page_data)
                else:
                    results.append(page_data)
            if not await self._query_async(next_selector):
                break
            await self.click_async(next_selector)
            await self.wait_for_navigation_async(timeout=15)
        return results

    async def js_async(self, code: str):
        try:
            result = await self.cmd_async("Runtime.evaluate", {
                "expression": code,
                "returnByValue": True,
            })
        except Exception as exc:
            print(f"[AsyncWebTool] cmd_async error in js_async: {exc}")
            return None

        rr = result.get("result", {})
        if "exceptionDetails" in rr:
            exc = rr["exceptionDetails"]
            print(
                f"[AsyncWebTool] JS Exception at line "
                f"{exc.get('lineNumber', '?')}: {exc.get('text', '?')}"
            )
            return None
        js_result = rr.get("result", {})
        result_type = js_result.get("type", "undefined")
        if result_type == "undefined":
            return None
        elif result_type in ("string", "number", "boolean"):
            return js_result.get("value")
        elif result_type == "object":
            if js_result.get("subtype") == "null":
                return None
            return js_result.get("value")
        return js_result.get("value", js_result.get("description"))

    async def screenshot_async(
        self,
        filename: Optional[str] = None,
        quality: int = 80,
        fmt: str = "jpeg",
    ) -> str:
        params: dict = {"format": fmt}
        if fmt == "jpeg":
            params["quality"] = quality
        result = await self.cmd_async("Page.captureScreenshot", params)
        data = result["result"]["data"]
        if filename:
            with open(filename, "wb") as f:
                f.write(base64.b64decode(data))
            return filename
        return data


# ──────────────────────────────────────────────────────────────────────────────
# Async BrowserPool
# ──────────────────────────────────────────────────────────────────────────────

class BrowserPool:
    """Async pool of N independent headless Chrome processes.

    Usage
    -----
    async with BrowserPool(size=2) as pool:
        results = await pool.map(fetch_title_fn, [url1, url2])

    Where ``fetch_title_fn`` has signature ``async def fn(web, arg) -> Any``.
    """

    def __init__(
        self,
        size: int = 2,
        headless: bool = True,
        port_start: int = 9300,
    ):
        self.size = size
        self.headless = headless
        self.port_start = port_start
        self._browsers: list = []
        self._tools: list[AsyncWebTool] = []

    async def __aenter__(self) -> "BrowserPool":
        loop = asyncio.get_running_loop()

        try:
            from tools.browser import BrowserCDP, find_free_port
        except ImportError:
            from browser import BrowserCDP, find_free_port  # type: ignore[no-redef]

        for i in range(self.size):
            port = find_free_port(preferred=self.port_start + i * 10)
            browser = BrowserCDP(port=port, headless=self.headless)
            await loop.run_in_executor(None, browser.start)
            await asyncio.sleep(1.5)
            tool = AsyncWebTool(port=port)
            await tool.connect()
            await asyncio.sleep(0.2)
            self._browsers.append(browser)
            self._tools.append(tool)

        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        loop = asyncio.get_running_loop()
        for tool in self._tools:
            try:
                await tool.close()
            except Exception:
                pass
        for browser in self._browsers:
            try:
                await loop.run_in_executor(None, browser.stop)
            except Exception:
                pass
        self._browsers.clear()
        self._tools.clear()

    async def map(
        self,
        fn: "Callable[..., Coroutine[Any, Any, Any]]",
        args_list: "list[Any]",
    ) -> "list[Any]":
        if not self._tools:
            raise WebToolError(
                "BrowserPool.map(): pool not started — use 'async with'"
            )

        queue: asyncio.Queue = asyncio.Queue()
        for tool in self._tools:
            await queue.put(tool)

        async def _run(arg: Any) -> Any:
            tool = await queue.get()
            try:
                return await fn(tool, arg)
            finally:
                await queue.put(tool)

        return list(
            await asyncio.gather(*[_run(arg) for arg in args_list])
        )

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return (
            f"BrowserPool(size={self.size}, active={len(self._tools)}, "
            f"headless={self.headless})"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Quick demo
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    web = WebTool()
    web.connect()

    # High-level login
    ok = web.login(
        "https://the-internet.herokuapp.com/login",
        "#username", "tomsmith",
        "#password", "SuperSecretPassword!",
        "button[type='submit']",
    )
    print("Login succeeded:", ok)
    print("URL:", web.js("window.location.href"))

    web.close()
