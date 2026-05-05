"""
web_tool.py — Generation 2 WebTool built on Chrome DevTools Protocol (CDP).

Changes over Generation 1
--------------------------
- _query()            : switched from DOM.querySelector to Runtime.evaluate +
                        document.querySelector + DOM.requestNode — full CSS3
                        pseudo-selector support (:first-child, :nth-child, etc.)
- go()                : also accepts Page.frameStoppedLoading as a load signal
                        (in addition to Page.loadEventFired); document.readyState
                        check as last-resort fallback before warning
- _wait_for_any_event : generalised helper that accepts a *set* of event names
- wait_for_navigation : block until next page load event fires after click/submit
- get_all_links       : extract all hrefs from the current page in one JS call
- get_table           : extract a <table> as a list of dicts keyed by header text
- get_form_fields     : introspect form structure (inputs, selects, textareas)
- open_tab            : open a new Chrome tab via Target.createTarget
- switch_tab          : reconnect the WebSocket to a different tab
- close_tab           : close a tab by id
- get_open_tabs       : list all open page-type tabs
- AsyncWebTool        : fully async implementation using the `websockets` library
                        with go_async, cmd_async, js_async, click_async, fill_async
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from typing import Optional

import requests
import websocket


# ──────────────────────────────────────────────────────────────────────────────
# Sentinel load-event names that signal "page is done loading"
# ──────────────────────────────────────────────────────────────────────────────

_LOAD_SIGNALS: frozenset[str] = frozenset({
    "Page.loadEventFired",
    "Page.frameStoppedLoading",
})


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class WebToolError(Exception):
    """Raised when a WebTool operation cannot complete."""


# ──────────────────────────────────────────────────────────────────────────────
# Synchronous core tool
# ──────────────────────────────────────────────────────────────────────────────

class WebTool:
    """CDP-based browser automation tool.

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

        # Thread-safe buffer for CDP events received while waiting for a
        # command response.
        self._event_lock = threading.Lock()
        self._pending_events: list[dict] = []

        # Multi-tab support
        self._tabs: dict[str, str] = {}          # tab_id  → ws_debugger_url
        self._current_tab_id: Optional[str] = None

    # ──────────────────────────── connection ─────────────────────────────────

    def connect(self, tab_index: int = 0):
        """Connect to the tab at *tab_index* (default: first tab)."""
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
        """Close the WebSocket connection."""
        if self.ws:
            self.ws.close()
            self.ws = None

    # ──────────────────────────── low-level cmd ──────────────────────────────

    def cmd(self, method: str, params: Optional[dict] = None) -> dict:
        """Send a CDP command and return the matching response."""
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
        """Block until *any* of the CDP events in *event_methods* arrives.

        Returns the matched method name, or None on timeout.
        """
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
        """Navigate to *url* and wait for a page-load signal.

        Accepts either ``Page.loadEventFired`` *or* ``Page.frameStoppedLoading``
        as the termination condition so that pages that skip loadEventFired
        (e.g. via HTTP redirects or custom resource handling) are handled
        correctly.  Falls back to a ``document.readyState`` check if neither
        event arrives within *timeout*.
        """
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
            # Last-resort: if readyState is already complete/interactive, accept it.
            try:
                state = self.js("document.readyState")
                if state in ("complete", "interactive"):
                    return
            except Exception:
                pass
            print(
                f"[WebTool] Warning: page-load event not received within {timeout}s "
                f"for {url!r}"
            )

    def wait_for_navigation(self, timeout: float = 15.0) -> bool:
        """Block until the next page-load event fires (e.g. after a click).

        Returns True if a load event arrived, False on timeout.
        Useful after ``click()`` on a link or form submit button.
        """
        result = self._wait_for_any_event(_LOAD_SIGNALS, timeout)
        return result is not None

    # ──────────────────────────── element helpers ─────────────────────────────

    def _query(self, selector: str) -> int:
        """Return nodeId for *selector*, or 0 if not found.

        Uses ``Runtime.evaluate`` + ``document.querySelector`` for full CSS3
        support (including ``:first-child``, ``:nth-child``, attribute selectors,
        etc.) then converts the remote object to a nodeId via ``DOM.requestNode``.
        """
        escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        result = self.cmd("Runtime.evaluate", {
            "expression": f"document.querySelector('{escaped}')",
            "returnByValue": False,
        })

        rr = result.get("result", {})
        # JS exception inside evaluate
        if "exceptionDetails" in rr:
            return 0

        obj = rr.get("result", {})
        obj_id = obj.get("objectId")
        # querySelector returned null
        if not obj_id or obj.get("subtype") == "null":
            return 0

        node_result = self.cmd("DOM.requestNode", {"objectId": obj_id})
        return node_result.get("result", {}).get("nodeId", 0)

    def _center_of(self, node_id: int) -> tuple[float, float]:
        """Return (x, y) centre of the element bounding box."""
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
        """Click the first element matching *selector*.

        Raises WebToolError if the element is not found or has no box model.
        """
        node_id = self._query(selector)
        if not node_id:
            raise WebToolError(f"click(): no element for selector {selector!r}")

        x, y = self._center_of(node_id)
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
        """Click *selector*, select-all existing content, then type *text*."""
        self.click(selector)
        self.cmd("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "a", "modifiers": 2,
        })
        self.type(text)

    def key(self, key: str):
        """Press a named key (Enter, Tab, Escape, ArrowDown …)."""
        self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
        self.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})

    def wait(self, selector: str, timeout: float = 10.0) -> bool:
        """Poll for *selector* at 0.1 s intervals; return True when found.

        Returns False if *timeout* elapses without finding the element.
        """
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

        Raises WebToolError if the element is not found.
        """
        node_id = self._query(selector)
        if not node_id:
            raise WebToolError(f"hover(): no element for selector {selector!r}")

        x, y = self._center_of(node_id)
        self.cmd("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })

    def select_option(self, selector: str, value: str):
        """Set a <select> element to *value* and fire a change event.

        Parameters
        ----------
        selector : CSS selector targeting the <select> element
        value    : the *value* attribute of the <option> to select
        """
        escaped_sel = selector.replace("'", "\\'")
        escaped_val = value.replace("'", "\\'")
        code = (
            f"(function(){{"
            f"  var el = document.querySelector('{escaped_sel}');"
            f"  if (!el) return 'NOT_FOUND';"
            f"  el.value = '{escaped_val}';"
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
        """Return info about the first element with *text* as visible text content.

        Returns a dict with ``{tagName, selector}`` or None if not found.
        Uses a single batched JS call for efficiency.
        """
        escaped_text = text.replace("\\", "\\\\").replace("'", "\\'")
        escaped_tag  = tag.replace("'", "\\'")
        code = (
            f"(function(){{"
            f"  var tag = '{escaped_tag}';"
            f"  var needle = '{escaped_text}';"
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
            f"        parts.unshift(e.tagName.toLowerCase() + ':nth-of-type(' + idx + ')');"
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
        """Find the first element with visible text *text* and click it.

        Raises WebToolError if no matching element is found.
        """
        info = self.find_by_text(text, tag)
        if not info:
            raise WebToolError(
                f"click_by_text(): no {tag!r} element with text {text!r}"
            )
        return self.click(info["selector"])

    def get_all_links(self) -> list[str]:
        """Return every non-javascript href found on the page.

        Performs a single batched ``Runtime.evaluate`` call.
        """
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
        """Extract the first ``<table>`` matching *selector* as a list of dicts.

        Header cells (``<th>`` in ``<thead>``, or the first ``<tr>``) become
        dict keys; each body row becomes one dict.

        Raises WebToolError if no matching table is found.
        """
        escaped = selector.replace("'", "\\'")
        code = (
            f"(function(){{"
            f"  var t = document.querySelector('{escaped}');"
            f"  if (!t) return null;"
            f"  var headers = Array.from(t.querySelectorAll('thead th, thead td'))"
            f"                     .map(function(h){{ return h.textContent.trim(); }});"
            f"  if (headers.length === 0) {{"
            f"    var fr = t.querySelector('tr');"
            f"    if (fr) headers = Array.from(fr.querySelectorAll('th,td'))"
            f"                           .map(function(h){{ return h.textContent.trim(); }});"
            f"  }}"
            f"  var bodyRows = Array.from(t.querySelectorAll('tbody tr'));"
            f"  if (bodyRows.length === 0)"
            f"    bodyRows = Array.from(t.querySelectorAll('tr')).slice(1);"
            f"  return JSON.stringify(bodyRows.map(function(row){{"
            f"    var cells = Array.from(row.querySelectorAll('td,th'))"
            f"                     .map(function(c){{ return c.textContent.trim(); }});"
            f"    var obj = {{}};"
            f"    cells.forEach(function(c,i){{ obj[headers[i] !== undefined ? headers[i] : i] = c; }});"
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
        """Introspect form structure.

        Returns a list of dicts, one per ``<input>``, ``<select>``, or
        ``<textarea>`` inside the first element matching *selector*.

        Each dict has keys: ``tag``, ``type``, ``name``, ``id``, ``value``,
        ``placeholder``, ``required``.

        Raises WebToolError if no matching element is found.
        """
        escaped = selector.replace("'", "\\'")
        code = (
            f"(function(){{"
            f"  var form = document.querySelector('{escaped}');"
            f"  if (!form) return null;"
            f"  var fields = [];"
            f"  form.querySelectorAll('input,select,textarea').forEach(function(el){{"
            f"    fields.push({{"
            f"      tag: el.tagName.toLowerCase(),"
            f"      type: el.type || '',"
            f"      name: el.name || '',"
            f"      id: el.id || '',"
            f"      value: el.value || '',"
            f"      placeholder: el.placeholder || '',"
            f"      required: el.required || false"
            f"    }});"
            f"  }});"
            f"  return JSON.stringify(fields);"
            f"}})()"
        )
        result = self.js(code)
        if result is None:
            raise WebToolError(
                f"get_form_fields(): no element for selector {selector!r}"
            )
        return json.loads(result)

    # ─── multi-tab support ────────────────────────────────────────────────────

    def _refresh_tab_registry(self):
        """Sync self._tabs with the Chrome tab list."""
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
        """Open a new browser tab and return its tab id.

        The new tab is created but the *current* connection is **not** changed.
        Call ``switch_tab(tab_id)`` to interact with it.

        Parameters
        ----------
        url : initial URL to navigate the new tab to
        """
        result = self.cmd("Target.createTarget", {"url": url})
        tab_id: str = result["result"]["targetId"]
        # Register the debugger URL for future switch_tab calls
        self._refresh_tab_registry()
        return tab_id

    def switch_tab(self, tab_id: str):
        """Reconnect the WebSocket to the tab identified by *tab_id*.

        After this call, all subsequent commands operate on that tab.
        Raises WebToolError if the tab is not found.
        """
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
        # Re-enable CDP domains for the new session
        self.cmd("Page.enable")
        self.cmd("DOM.enable")
        self.cmd("Runtime.enable")

    def close_tab(self, tab_id: str):
        """Close the tab identified by *tab_id*.

        If this is the currently active tab, the WebSocket connection is also
        closed.  You must call ``switch_tab()`` to another tab afterwards.
        """
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
        """Return a list of dicts for every open page-type tab.

        Each dict has keys: ``id``, ``url``, ``title``.
        """
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
            {
                "id": t["id"],
                "url": t.get("url", ""),
                "title": t.get("title", ""),
            }
            for t in tabs
            if t.get("type") == "page"
        ]

    # ──────────────────────────── JS helper ──────────────────────────────────

    def js(self, code: str):
        """Evaluate *code* in the page context and return a Python value.

        Primitive types (string, number, boolean, null) are returned directly.
        Objects/arrays must be wrapped in JSON.stringify() by the caller.
        """
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
                    f"[WebTool] JS Exception at line {exc.get('lineNumber', '?')}: "
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
            print(f"[WebTool] Python Exception in js(): {type(exc).__name__}: {exc}")
            return None

    # ──────────────────────────── screenshot ─────────────────────────────────

    def screenshot(
        self,
        filename: Optional[str] = None,
        quality: int = 80,
        format: str = "jpeg",
    ) -> str:
        """Capture a screenshot.

        Returns the filename if provided, otherwise the raw base-64 string.
        """
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
        """Return the textContent of the first element matching *selector*."""
        escaped = selector.replace("'", "\\'")
        return self.js(
            f"document.querySelector('{escaped}')?.textContent ?? null"
        )

    def attr(self, selector: str, attribute: str) -> Optional[str]:
        """Return an attribute value from the first element matching *selector*."""
        escaped_sel  = selector.replace("'", "\\'")
        escaped_attr = attribute.replace("'", "\\'")
        return self.js(
            f"document.querySelector('{escaped_sel}')"
            f"?.getAttribute('{escaped_attr}') ?? null"
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

    Requires
    --------
    ``pip install websockets``
    """

    def __init__(self, port: int = 9222):
        self.port = port
        self._ws = None                            # websockets connection
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_listeners: list = []           # list of sync/async callables
        self._recv_task: Optional[asyncio.Task] = None

    # ──────────── connection ──────────────────────────────────────────────────

    async def connect(self, tab_index: int = 0):
        """Connect to the tab at *tab_index*."""
        try:
            import websockets as _ws_lib  # noqa: F401
        except ImportError as exc:
            raise WebToolError(
                "AsyncWebTool requires the 'websockets' package: "
                "pip install websockets"
            ) from exc

        loop = asyncio.get_running_loop()
        tabs = await loop.run_in_executor(
            None,
            lambda: requests.get(
                f"http://localhost:{self.port}/json"
            ).json(),
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
        """Cancel the receive loop and close the WebSocket."""
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
        """Background task: demux CDP messages to waiting futures or listeners."""
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
            pass

    # ──────────── low-level ──────────────────────────────────────────────────

    async def cmd_async(
        self, method: str, params: Optional[dict] = None
    ) -> dict:
        """Send a CDP command and await its response."""
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
        """Navigate to *url* and await a page-load signal."""
        loop = asyncio.get_running_loop()
        load_fut: asyncio.Future = loop.create_future()

        def _on_event(msg: dict):
            if msg.get("method") in _LOAD_SIGNALS and not load_fut.done():
                load_fut.set_result(True)

        self._event_listeners.append(_on_event)
        try:
            await self.cmd_async("Page.navigate", {"url": url})
            await asyncio.wait_for(asyncio.shield(load_fut), timeout=timeout)
        except asyncio.TimeoutError:
            print(
                f"[AsyncWebTool] Warning: page-load event not received "
                f"within {timeout}s for {url!r}"
            )
        finally:
            try:
                self._event_listeners.remove(_on_event)
            except ValueError:
                pass

    async def wait_for_navigation_async(self, timeout: float = 15.0) -> bool:
        """Await the next page-load event. Returns True if it arrived."""
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
        """Return nodeId for *selector* (0 = not found)."""
        escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        result = await self.cmd_async("Runtime.evaluate", {
            "expression": f"document.querySelector('{escaped}')",
            "returnByValue": False,
        })
        rr = result.get("result", {})
        if "exceptionDetails" in rr:
            return 0
        obj = rr.get("result", {})
        obj_id = obj.get("objectId")
        if not obj_id or obj.get("subtype") == "null":
            return 0
        node_result = await self.cmd_async("DOM.requestNode", {"objectId": obj_id})
        return node_result.get("result", {}).get("nodeId", 0)

    async def _center_of_async(self, node_id: int) -> tuple[float, float]:
        box = await self.cmd_async("DOM.getBoxModel", {"nodeId": node_id})
        if "error" in box:
            raise WebToolError(
                f"Cannot get box model for nodeId={node_id}: {box['error']}"
            )
        content = box["result"]["model"]["content"]
        return (content[0] + content[4]) / 2, (content[1] + content[5]) / 2

    # ──────────── public async actions ───────────────────────────────────────

    async def click_async(self, selector: str) -> bool:
        """Click the first element matching *selector*."""
        node_id = await self._query_async(selector)
        if not node_id:
            raise WebToolError(
                f"click_async(): no element for selector {selector!r}"
            )
        x, y = await self._center_of_async(node_id)
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
        """Click *selector*, select-all, then type *text* asynchronously."""
        await self.click_async(selector)
        await self.cmd_async("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "a", "modifiers": 2,
        })
        for char in text:
            await self.cmd_async(
                "Input.dispatchKeyEvent", {"type": "char", "text": char}
            )

    async def wait_async(self, selector: str, timeout: float = 10.0) -> bool:
        """Poll for *selector* at 0.1 s intervals; return True when found."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self._query_async(selector):
                return True
            await asyncio.sleep(0.1)
        return False

    async def js_async(self, code: str):
        """Evaluate *code* in the page context and return a Python value."""
        result = await self.cmd_async("Runtime.evaluate", {
            "expression": code,
            "returnByValue": True,
        })
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
        """Capture a screenshot asynchronously."""
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
