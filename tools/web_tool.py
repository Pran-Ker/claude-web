"""
web_tool.py — Generation 4 WebTool built on Chrome DevTools Protocol (CDP).

Changes over Generation 3
--------------------------
- _get_coords() / _get_coords_async()
    Fallback selector chain for pseudo-class mismatches (fixes T2d):
      1. Try exact selector as before (3 retries, 0.1 s gaps).
      2. If selector contains ':first-child', retry with ':first-of-type'
         (handles the common case where the element is the first of its
         type but NOT the absolute first child — e.g. when an <h3> precedes
         the .figure divs on the-internet.herokuapp.com/hovers).
      3. Strip all structural pseudo-classes (:first-child, :last-child,
         :nth-child(n), :first-of-type, :last-of-type, :nth-of-type(n))
         and try querySelector on the bare selector — picks the first match
         in DOM order, which matches the caller's intent.

- go_async() race-condition fix (fixes T4b):
    The old implementation registered a load-event listener *before* sending
    Page.navigate, which meant any stale 'Page.loadEventFired' that arrived
    during the round-trip (e.g. the initial blank-page load event queued by
    Chrome right after connect()) would immediately satisfy load_fut and
    return before the real page was loaded.  New sequence:

      1. Set up listener.
      2. Await Page.navigate (blocks until Chrome acks the command).
      3. If load_fut is already done → stale event.  Discard and set up a
         *fresh* future that will only be satisfied by the *next* load event.
      4. Check document.readyState directly.  If 'complete' or 'interactive',
         return immediately.
      5. Otherwise await the (new) load_fut with the remaining timeout.
      6. After any load signal, confirm readyState is 'complete' with one
         extra JS check; if not, poll up to 2 s.

- get_page_structure() — new single-call helper (gen 4 focus):
    Returns {title, url, h1s, h2s, link_count, form_count, table_count}
    in a single Runtime.evaluate round-trip.  Useful for quick page
    inspection without multiple js() calls.

- Async counterparts updated:
    go_async uses the new race-safe sequence.
    _get_coords_async uses the same fallback chain as _get_coords.
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

# Regex to strip structural pseudo-classes from CSS selectors so we can use
# querySelector on the "loosened" selector as a last resort.
_STRUCTURAL_PSEUDO_RE = re.compile(
    r":(?:first|last)-(?:child|of-type)"
    r"|:nth-(?:child|of-type)\([^)]*\)"
    r"|:nth-last-(?:child|of-type)\([^)]*\)"
    r"|:only-child|:only-of-type"
)


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class WebToolError(Exception):
    """Raised when a WebTool operation cannot complete."""


# ──────────────────────────────────────────────────────────────────────────────
# Selector escaping helpers
# ──────────────────────────────────────────────────────────────────────────────

def _esc_dq(selector: str) -> str:
    """Escape *selector* for embedding in a JS double-quoted string."""
    return selector.replace("\\", "\\\\").replace('"', '\\"')


def _esc_sq(selector: str) -> str:
    """Escape *selector* for embedding in a JS single-quoted string."""
    return selector.replace("\\", "\\\\").replace("'", "\\'")


def _selector_fallbacks(selector: str) -> list[str]:
    """Return a list of progressively-relaxed selector variants to try.

    Order:
      1. Exact selector (unchanged).
      2. ':first-child' replaced with ':first-of-type' (most common fix).
      3. ':last-child'  replaced with ':last-of-type'.
      4. All structural pseudo-classes stripped entirely — matches first
         element in DOM order among those with the right tag/class/attr.

    Duplicates (e.g. selector had no pseudo-classes at all) are removed so we
    don't waste extra round-trips.
    """
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
# Coord-fetch JS template  (used for both sync and async paths)
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

    # ──────────────────────────── navigation ─────────────────────────────────

    def go(self, url: str, timeout: float = 30.0):
        """Navigate to *url* and wait for a page-load signal."""
        self._drain_events()

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
                return

        self.ws.settimeout(None)

        if not load_fired:
            try:
                state = self.js("document.readyState")
                if state in ("complete", "interactive"):
                    return
            except Exception:
                pass
            print(
                f"[WebTool] Warning: page-load event not received within "
                f"{timeout}s for {url!r}"
            )

    def wait_for_navigation(self, timeout: float = 15.0) -> bool:
        """Block until the next page-load event fires (e.g. after a click)."""
        result = self._wait_for_any_event(_LOAD_SIGNALS, timeout)
        return result is not None

    # ──────────────────────────── element helpers ─────────────────────────────

    def _query(self, selector: str) -> int:
        """Return 1 if *selector* matches any element, 0 otherwise."""
        escaped = _esc_dq(selector)
        result = self.js(
            f'document.querySelector("{escaped}") !== null ? 1 : 0'
        )
        return 1 if result == 1 else 0

    def _get_coords(self, selector: str) -> Optional[tuple[float, float]]:
        """Return viewport-relative (x, y) centre of the element.

        Tries a chain of progressively-relaxed selector variants so that
        pseudo-class mismatches (e.g. ':first-child' when the element is not
        the absolute first child) are handled gracefully:

          1. Exact selector — 3 attempts with 0.1 s gaps.
          2. ':first-child' → ':first-of-type' variant.
          3. All structural pseudo-classes stripped (uses first DOM match).

        Each variant gets up to 3 attempts (0.1 s apart) to absorb
        post-navigation rendering delays.
        """
        for sel in _selector_fallbacks(selector):
            code = _coords_js(_esc_dq(sel))
            for attempt in range(3):
                raw = self.js(code)
                if raw:
                    try:
                        data = json.loads(raw)
                        return data["x"], data["y"]
                    except (json.JSONDecodeError, KeyError):
                        pass
                if attempt < 2:
                    time.sleep(0.1)
        return None

    def _center_of(self, node_id: int) -> tuple[float, float]:
        """Legacy shim — prefer _get_coords(selector) for new code."""
        box = self.cmd("DOM.getBoxModel", {"nodeId": node_id})
        if "error" in box:
            raise WebToolError(
                f"Cannot get box model for nodeId={node_id}: {box['error']}"
            )
        content = box["result"]["model"]["content"]
        x = (content[0] + content[4]) / 2
        y = (content[1] + content[5]) / 2
        return x, y

    # ──────────────────────────── public actions ──────────────────────────────

    def click(self, selector: str) -> bool:
        """Click the first element matching *selector*."""
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

    def type(self, text: str):
        """Type *text* character by character via Input.dispatchKeyEvent."""
        for char in text:
            self.cmd("Input.dispatchKeyEvent", {"type": "char", "text": char})

    def fill(self, selector: str, text: str):
        """Focus *selector*, clear existing content, and type *text*.

        Primary path: click to focus → Ctrl+A → type.
        Fallback: direct JS value assignment + input/change events.
        """
        coords = self._get_coords(selector)
        if coords is not None:
            x, y = coords
            self.cmd("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })
            self.cmd("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })
            self.cmd("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "modifiers": 2,
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

    def key(self, key: str):
        """Press a named key (Enter, Tab, Escape, ArrowDown …)."""
        self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
        self.cmd("Input.dispatchKeyEvent", {"type": "keyUp",   "key": key})

    def wait(self, selector: str, timeout: float = 10.0) -> bool:
        """Poll for *selector* at 0.1 s intervals; return True when found."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._query(selector):
                return True
            time.sleep(0.1)
        return False

    # ─── scrolling / hover / select ──────────────────────────────────────────

    def scroll(self, direction: str = "down", pixels: int = 500):
        """Scroll the page.

        Parameters
        ----------
        direction : "up" | "down" | "left" | "right"
        pixels    : number of pixels to scroll
        """
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
        """Move the mouse over *selector* to trigger :hover / reveal dropdowns.

        Uses the fallback-chain _get_coords() so that selectors with
        ':first-child' that don't literally match (element preceded by a
        sibling of a different tag) still succeed via ':first-of-type' or
        the stripped variant.
        """
        coords = self._get_coords(selector)
        if coords is None:
            raise WebToolError(f"hover(): no element for selector {selector!r}")
        x, y = coords
        self.cmd("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })

    def select_option(self, selector: str, value: str):
        """Set a <select> element to *value* and fire a change event."""
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

    # ─── text / data extraction ───────────────────────────────────────────────

    def find_by_text(self, text: str, tag: str = "*") -> Optional[dict]:
        """Return ``{tagName, selector}`` for the first element with exact *text*."""
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
        """Find the first element with visible text *text* and click it."""
        info = self.find_by_text(text, tag)
        if not info:
            raise WebToolError(
                f"click_by_text(): no {tag!r} element with text {text!r}"
            )
        return self.click(info["selector"])

    def get_all_links(self) -> list[str]:
        """Return every non-javascript: href on the page (single JS call)."""
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
        """Extract a <table> as a list of dicts keyed by header text."""
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
        """Introspect form structure; returns list of field dicts."""
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
        """Return a structural summary of the current page in one round-trip.

        Returns a dict with keys:
          title       : document.title
          url         : window.location.href
          h1s         : list of h1 text strings
          h2s         : list of h2 text strings
          link_count  : number of <a href> elements
          form_count  : number of <form> elements
          table_count : number of <table> elements
        """
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
        """Open a new browser tab and return its tab id."""
        result = self.cmd("Target.createTarget", {"url": url})
        tab_id: str = result["result"]["targetId"]
        self._refresh_tab_registry()
        return tab_id

    def switch_tab(self, tab_id: str):
        """Reconnect to the tab identified by *tab_id*."""
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
        self.msg_id = 0
        self.cmd("Page.enable")
        self.cmd("DOM.enable")
        self.cmd("Runtime.enable")

    def close_tab(self, tab_id: str):
        """Close the tab identified by *tab_id*."""
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
        """Return a list of dicts (id, url, title) for every open page tab."""
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
        """Evaluate *code* in the page context and return a Python value."""
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


# ──────────────────────────────────────────────────────────────────────────────
# Async tool
# ──────────────────────────────────────────────────────────────────────────────

class AsyncWebTool:
    """Fully async CDP browser automation tool using the ``websockets`` library.

    Usage
    -----
    async def main():
        web = AsyncWebTool(port=9222)
        await web.connect()
        await web.go_async("https://example.com")
        title = await web.js_async("document.title")
        await web.close()

    asyncio.run(main())
    """

    def __init__(self, port: int = 9222):
        self.port = port
        self._ws = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_listeners: list = []
        self._recv_task: Optional[asyncio.Task] = None

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

    # ──────────── navigation ─────────────────────────────────────────────────

    async def go_async(self, url: str, timeout: float = 30.0):
        """Navigate to *url* and await a page-load signal.

        Race-safe sequence
        ------------------
        The gen-3 implementation registered the load-event listener before
        sending Page.navigate, which meant a stale loadEventFired (e.g. the
        blank-page event buffered by Chrome right after connect()) would
        immediately resolve the future and return before the real page loaded.

        New sequence:
          1. Register listener.
          2. Send Page.navigate and *await the response* (blocks until Chrome
             acks the command — no events can be processed during this await in
             a single-threaded event loop because _recv_loop is a separate
             task, but the point of awaiting nav first is that we establish a
             clear "before navigate" vs "after navigate" boundary).
          3. If load_fut is already done → the event was stale (fired before
             we started navigating).  Create a fresh future and wait again.
          4. Immediately check document.readyState.  If the page loaded fast
             enough that the event arrived and was missed, we return early.
          5. Otherwise await the (possibly replaced) load_fut.
          6. After any load signal, poll readyState up to 2 s to confirm
             the DOM is actually settled ('complete').
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        # Step 1: set up listener with a resettable future
        load_fut: asyncio.Future = loop.create_future()

        def _on_event(msg: dict):
            if msg.get("method") in _LOAD_SIGNALS and not load_fut.done():
                load_fut.set_result(True)

        self._event_listeners.append(_on_event)
        try:
            # Step 2: send navigate and await the CDP response
            await self.cmd_async("Page.navigate", {"url": url})

            # Step 3: if load_fut already resolved, the event was stale —
            #         replace it with a fresh future for the real load event.
            if load_fut.done():
                new_fut: asyncio.Future = loop.create_future()

                def _on_event2(msg: dict):
                    if msg.get("method") in _LOAD_SIGNALS and not new_fut.done():
                        new_fut.set_result(True)

                self._event_listeners.append(_on_event2)
                try:
                    # Step 4: fast-path if readyState is already complete
                    state = await self.js_async("document.readyState")
                    if state in ("complete", "interactive"):
                        return

                    # Step 5: wait for the real load event
                    remaining = deadline - loop.time()
                    if remaining > 0:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(new_fut), timeout=remaining
                            )
                        except asyncio.TimeoutError:
                            pass
                finally:
                    try:
                        self._event_listeners.remove(_on_event2)
                    except ValueError:
                        pass
            else:
                # Step 4: fast-path readyState check
                state = await self.js_async("document.readyState")
                if state in ("complete", "interactive"):
                    return

                # Step 5: wait for load event
                remaining = deadline - loop.time()
                if remaining > 0:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(load_fut), timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        pass

            # Step 6: confirm readyState is settled
            poll_deadline = loop.time() + 2.0
            while loop.time() < poll_deadline:
                state = await self.js_async("document.readyState")
                if state == "complete":
                    return
                await asyncio.sleep(0.1)

            # Last resort: check readyState and warn if still not ready
            state = await self.js_async("document.readyState")
            if state not in ("complete", "interactive"):
                print(
                    f"[AsyncWebTool] Warning: page-load event not received "
                    f"within {timeout}s for {url!r} (readyState={state!r})"
                )

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
        """Return 1 if *selector* matches any element, 0 otherwise."""
        escaped = _esc_dq(selector)
        result = await self.js_async(
            f'document.querySelector("{escaped}") !== null ? 1 : 0'
        )
        return 1 if result == 1 else 0

    async def _get_coords_async(
        self, selector: str
    ) -> Optional[tuple[float, float]]:
        """Return viewport-relative (x, y) centre of element (async).

        Uses the same fallback-chain as the sync _get_coords():
          1. Exact selector (3 retries).
          2. ':first-child' → ':first-of-type'.
          3. All structural pseudo-classes stripped.
        """
        for sel in _selector_fallbacks(selector):
            code = _coords_js(_esc_dq(sel))
            for attempt in range(3):
                raw = await self.js_async(code)
                if raw:
                    try:
                        data = json.loads(raw)
                        return data["x"], data["y"]
                    except (json.JSONDecodeError, KeyError):
                        pass
                if attempt < 2:
                    await asyncio.sleep(0.1)
        return None

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

    async def fill_async(self, selector: str, text: str):
        """Focus *selector*, clear, then type *text* asynchronously."""
        coords = await self._get_coords_async(selector)
        if coords is not None:
            x, y = coords
            await self.cmd_async("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })
            await self.cmd_async("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })
            await self.cmd_async("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "modifiers": 2,
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
        """Poll for *selector* at 0.1 s intervals; return True when found."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self._query_async(selector):
                return True
            await asyncio.sleep(0.1)
        return False

    async def js_async(self, code: str):
        """Evaluate *code* in the page context and return a Python value."""
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

    Supports the async context manager protocol and parallel task dispatch
    via ``map()``.  Each pool slot owns a dedicated ``AsyncWebTool`` backed by
    its own Chrome process — no shared state between concurrent tasks.

    Parameters
    ----------
    size        : number of Chrome processes to spawn
    headless    : launch Chrome headless (default: True)
    port_start  : first port to allocate; subsequent slots use ports
                  port_start, port_start+10, port_start+20, …

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

    # ──────────── async context manager ──────────────────────────────────────

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
            # Allow Chrome's initial blank tab to register in /json
            await asyncio.sleep(1.5)
            tool = AsyncWebTool(port=port)
            await tool.connect()
            # Drain any stale load events from Chrome's initial page load
            # so go_async doesn't see them as real navigation completions.
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

    # ──────────── parallel dispatch ───────────────────────────────────────────

    async def map(
        self,
        fn: "Callable[..., Coroutine[Any, Any, Any]]",
        args_list: "list[Any]",
    ) -> "list[Any]":
        """Run ``fn(async_web_tool, arg)`` for every item in *args_list*.

        Tasks run in parallel, capped at ``size`` concurrent executions.
        Tools are distributed via an asyncio.Queue for fair exclusive access.
        """
        if not self._tools:
            raise WebToolError("BrowserPool.map(): pool not started — use 'async with'")

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

    # ──────────── introspection ───────────────────────────────────────────────

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

    web.go("https://google.com")
    web.fill("input[name='q']", "hello world")
    web.key("Enter")
    web.wait("h3", timeout=10)
    web.screenshot("result.jpg")

    web.close()
