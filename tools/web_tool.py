"""
web_tool.py — Improved WebTool built on Chrome DevTools Protocol (CDP).

Improvements over baseline
--------------------------
- go()           : event-driven wait on Page.loadEventFired (no fixed sleep)
- wait()         : 0.1 s polling intervals; returns as soon as element appears
- WebToolError   : raised on hard failures instead of returning False/None
- scroll()       : scroll the page by direction & pixel amount
- hover()        : trigger mousemove/:hover so dropdowns reveal
- select_option(): set <select> value and fire change event
- find_by_text() : locate an element by its visible text content
- click_by_text(): find + click by visible text
"""

import json
import time
import base64
import threading
from typing import Optional

import requests
import websocket


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class WebToolError(Exception):
    """Raised when a WebTool operation cannot complete."""


# ──────────────────────────────────────────────────────────────────────────────
# Core tool
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

        # Thread-safe storage for async CDP events received while waiting for a
        # command response (Page.loadEventFired arrives on the same socket).
        self._event_lock = threading.Lock()
        self._pending_events: list[dict] = []

    # ──────────────────────────── connection ─────────────────────────────────

    def connect(self):
        """Connect to the first available tab."""
        tabs = requests.get(f"http://localhost:{self.port}/json").json()
        if not tabs:
            raise WebToolError(f"No tabs found on port {self.port}")
        self.ws = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"])
        self.cmd("Page.enable")
        self.cmd("DOM.enable")
        self.cmd("Runtime.enable")

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

            # Store events for callers that drain them later
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

    def _wait_for_event(self, event_method: str, timeout: float = 30.0) -> bool:
        """Block until a CDP event with the given method arrives (or timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Short receive timeout so we don't block forever
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
                if response["method"] == event_method:
                    return True
                with self._event_lock:
                    self._pending_events.append(response)
            # Ignore command responses that arrive unexpectedly here

        self.ws.settimeout(None)
        return False

    # ──────────────────────────── navigation ─────────────────────────────────

    def go(self, url: str, timeout: float = 30.0):
        """Navigate to *url* and wait for Page.loadEventFired (event-driven)."""
        # Flush any stale events before navigating
        self._drain_events()

        # Issue the navigate command (don't wait for its response yet — the
        # load event may arrive before the command ack on a cached page).
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
                if msg["method"] == "Page.loadEventFired":
                    load_fired = True
                else:
                    with self._event_lock:
                        self._pending_events.append(msg)

            if nav_acked and load_fired:
                self.ws.settimeout(None)
                return

        self.ws.settimeout(None)
        if not load_fired:
            # Non-fatal: some pages (e.g. SPAs) may never fire loadEventFired;
            # carry on but log a warning.
            print(f"[WebTool] Warning: Page.loadEventFired not received within {timeout}s for {url!r}")

    # ──────────────────────────── element helpers ─────────────────────────────

    def _root_node_id(self) -> int:
        doc = self.cmd("DOM.getDocument")
        return doc["result"]["root"]["nodeId"]

    def _query(self, selector: str) -> int:
        """Return nodeId for *selector*, or 0 if not found."""
        root = self._root_node_id()
        result = self.cmd("DOM.querySelector", {"nodeId": root, "selector": selector})
        return result["result"].get("nodeId", 0)

    def _center_of(self, node_id: int) -> tuple[float, float]:
        """Return (x, y) centre of the element bounding box."""
        box = self.cmd("DOM.getBoxModel", {"nodeId": node_id})
        if "error" in box:
            raise WebToolError(f"Cannot get box model for nodeId={node_id}: {box['error']}")
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
        """Type *text* character by character."""
        for char in text:
            self.cmd("Input.dispatchKeyEvent", {"type": "char", "text": char})

    def fill(self, selector: str, text: str):
        """Click *selector*, select-all, then type *text*."""
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
            node_id = self._query(selector)
            if node_id:
                return True
            time.sleep(0.1)
        return False

    # ─── new Tier-2 methods ───────────────────────────────────────────────────

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
        # Use JS: set .value then dispatch change so frameworks pick it up
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
            raise WebToolError(f"select_option(): no element for selector {selector!r}")

    def find_by_text(self, text: str, tag: str = "*") -> Optional[dict]:
        """Return info about the first element with *text* as visible text content.

        Returns a dict with ``{nodeId, tagName, selector}`` or None if not found.
        Uses a single batched JS call for efficiency.
        """
        escaped_text = text.replace("'", "\\'").replace("\\", "\\\\")
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
            f"        while (sib) {{ if (sib.tagName === e.tagName) idx++; sib = sib.previousElementSibling; }}"
            f"        parts.unshift(e.tagName.toLowerCase() + ':nth-of-type(' + idx + ')');"
            f"        e = e.parentElement;"
            f"      }}"
            f"      return JSON.stringify({{tagName: els[i].tagName, selector: parts.join(' > ')}});"
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
            raise WebToolError(f"click_by_text(): no {tag!r} element with text {text!r}")
        return self.click(info["selector"])

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
                print(f"[WebTool] JS Exception at line {exc.get('lineNumber', '?')}: "
                      f"{exc.get('text', 'Unknown error')}")
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

    def screenshot(self, filename: Optional[str] = None, quality: int = 80,
                   format: str = "jpeg") -> str:
        """Capture a screenshot.

        Returns the filename if provided, otherwise the raw base-64 string.
        """
        params = {"format": format}
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
        return self.js(f"document.querySelector('{escaped}')?.textContent ?? null")

    def attr(self, selector: str, attribute: str) -> Optional[str]:
        """Return an attribute value from the first element matching *selector*."""
        escaped_sel  = selector.replace("'", "\\'")
        escaped_attr = attribute.replace("'", "\\'")
        return self.js(
            f"document.querySelector('{escaped_sel}')?.getAttribute('{escaped_attr}') ?? null"
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
