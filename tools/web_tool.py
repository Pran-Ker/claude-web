"""
web_tool.py — Generation 8 WebTool built on Chrome DevTools Protocol (CDP).

Changes over Generation 7
--------------------------
Error-resilience and trajectory bug-fixes:

  Trajectory fix — T2c (select_option by text):
    select_option() now tries to match options by value attribute first, and
    falls back to matching by visible option text when no value matches.
    Previously, passing the display text "Option 2" to a <select> whose
    options have value="2" silently left the dropdown on its default.

  Trajectory fix — T4a / T1c (click navigation not awaited):
    New click_and_wait(selector, timeout) clicks an element and then blocks
    until the next page-load event, re-injecting the querySelector shim.
    This replaces the manual click() + wait_for_navigation() pattern and
    prevents the URL-unchanged failures seen when logout/form-submit clicks
    were not followed by an explicit navigation wait.

  Trajectory fix — T4b (go_async empty title in BrowserPool):
    go_async() now polls for a non-empty document.title for up to 3 s after
    readyState reaches "complete", guarding against fast Heroku cold-start
    responses that send an empty page before JS sets the title.

  Trajectory fix — T4c / T5c (js() returning None on JS-rendered pages):
    New js_wait(code, timeout, interval) retries a JS expression until it
    returns a non-None value or the timeout expires.  Useful when the DOM
    contains the required selector but a child text node is not yet populated.

  Gen-8 focus — safe_click(selector, fallback_text=None) → bool:
    Attempts to click by selector; if WebToolError is raised and
    fallback_text is given, retries via click_by_text(fallback_text).

  Gen-8 focus — safe_fill(selector, text) → bool:
    Attempts click-triple-select + type; if the element cannot be reached
    by coordinates, falls back to direct JS property assignment with
    input/change event dispatch.  Never raises on "element not visible"
    layout issues — only raises on "element not found".

  Gen-8 focus — auto-reconnect in cmd():
    cmd() is split into _cmd_impl() (raw send/recv, no reconnect) and
    cmd() (wraps _cmd_impl with one automatic reconnect attempt on any
    WebSocket transport error).  _reconnect() closes the stale socket,
    opens a fresh WebSocket to the same tab URL, and re-enables
    Page / DOM / Runtime (+ Network if logging was active).

  Gen-8 focus — health_check() → bool:
    Sends Browser.getVersion; returns True if a non-error response is
    received, False on any exception.  Can be called at any time to
    probe the connection before a long sequence of commands.

  New helpers:
    scroll_to(selector) — scrollIntoView with smooth behaviour.
    wait_for_count(selector, count, timeout) — wait until at least N
      elements matching selector are in the DOM.
    retry_click(selector, attempts, delay) — retries click() up to N times.

  All generation-7 behaviour is preserved unchanged.
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

# Network domain events we care about for logging
_NETWORK_EVENT_METHODS: frozenset[str] = frozenset({
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.loadingFinished",
    "Network.loadingFailed",
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
# Reconnect-safe transport errors
# ──────────────────────────────────────────────────────────────────────────────

_TRANSPORT_ERRORS = (
    websocket.WebSocketConnectionClosedException,
    websocket.WebSocketException,
    ConnectionResetError,
    BrokenPipeError,
    OSError,
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

        # Network logging state
        self._network_logging: bool = False
        self._network_lock = threading.Lock()
        self._network_log: list[dict] = []

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

    # ──────────────────────────── low-level cmd (with auto-reconnect) ────────

    def _cmd_impl(self, method: str, params: Optional[dict] = None) -> dict:
        """Raw CDP command — send and wait for matching response id.
        Does NOT reconnect on failure; called by cmd() which handles that.
        """
        self.msg_id += 1
        msg = {"id": self.msg_id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))

        target_id = self.msg_id
        while True:
            raw = self.ws.recv()
            response = json.loads(raw)
            if "method" in response:
                self._dispatch_event(response)
                with self._event_lock:
                    self._pending_events.append(response)
                continue
            if response.get("id") == target_id:
                return response

    def _reconnect(self):
        """Close the stale WebSocket and open a fresh one to the current tab."""
        if self._current_tab_id is None:
            raise WebToolError("Cannot reconnect: no current tab ID stored")

        ws_url = self._tabs.get(self._current_tab_id)
        if not ws_url:
            self._refresh_tab_registry()
            ws_url = self._tabs.get(self._current_tab_id)
        if not ws_url:
            raise WebToolError(
                f"Cannot reconnect: tab {self._current_tab_id!r} not in registry"
            )

        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

        self.ws = websocket.create_connection(ws_url)
        self.msg_id = 0
        self._css_enabled = False

        # Re-enable required CDP domains
        for domain_method in ("Page.enable", "DOM.enable", "Runtime.enable"):
            try:
                self._cmd_impl(domain_method, {})
            except Exception:
                pass
        if self._network_logging:
            try:
                self._cmd_impl("Network.enable", {})
            except Exception:
                pass
        try:
            self._apply_selector_shim()
        except Exception:
            pass

    def cmd(self, method: str, params: Optional[dict] = None) -> dict:
        """Send a CDP command, reconnecting once on transport error."""
        if self.ws is None:
            self._reconnect()
        try:
            return self._cmd_impl(method, params)
        except _TRANSPORT_ERRORS as exc:
            try:
                self._reconnect()
                return self._cmd_impl(method, params)
            except Exception as reconnect_err:
                raise WebToolError(
                    f"WebSocket connection lost and reconnect failed: {reconnect_err}"
                ) from exc

    def health_check(self) -> bool:
        """Return True if the CDP connection is alive and Chrome responds.

        Sends a lightweight Browser.getVersion command and returns True when a
        valid response is received.  Returns False on any exception.

        Example
        -------
        if not web.health_check():
            web.connect()   # re-establish from scratch
        """
        try:
            result = self.cmd("Browser.getVersion")
            return "result" in result
        except Exception:
            return False

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
                self._dispatch_event(response)
                if response["method"] in event_methods:
                    return response["method"]
                with self._event_lock:
                    self._pending_events.append(response)

        self.ws.settimeout(None)
        return None

    # ──────────────────────────── network event dispatch ─────────────────────

    def _dispatch_event(self, event: dict) -> None:
        """Route CDP events to registered handlers (currently: network logger)."""
        if not self._network_logging:
            return
        method = event.get("method", "")
        if method not in _NETWORK_EVENT_METHODS:
            return
        self._handle_network_event(event)

    def _handle_network_event(self, event: dict) -> None:
        """Parse a Network.* CDP event and append an entry to _network_log."""
        method = event.get("method", "")
        params = event.get("params", {})

        entry: dict = {
            "type": method,
            "timestamp": params.get("timestamp", 0),
            "requestId": params.get("requestId", ""),
        }

        if method == "Network.requestWillBeSent":
            req = params.get("request", {})
            entry["url"] = req.get("url", "")
            entry["method"] = req.get("method", "GET")
            entry["resourceType"] = params.get("type", "")
        elif method == "Network.responseReceived":
            resp = params.get("response", {})
            entry["url"] = resp.get("url", "")
            entry["status"] = resp.get("status", 0)
            entry["mimeType"] = resp.get("mimeType", "")
            entry["resourceType"] = params.get("type", "")
        elif method == "Network.loadingFinished":
            entry["url"] = ""
            entry["encodedDataLength"] = params.get("encodedDataLength", 0)
        elif method == "Network.loadingFailed":
            entry["url"] = ""
            entry["errorText"] = params.get("errorText", "")
            entry["resourceType"] = params.get("type", "")

        with self._network_lock:
            self._network_log.append(entry)

    # ──────────────────────────── network public API ──────────────────────────

    def enable_network_logging(self) -> None:
        """Enable CDP Network domain and start collecting network events."""
        self.cmd("Network.enable")
        with self._network_lock:
            self._network_log = []
        self._network_logging = True

    def get_network_log(self) -> list[dict]:
        """Return all collected network-event entries since enable_network_logging()."""
        for event in self._drain_events():
            self._dispatch_event(event)
        with self._network_lock:
            return list(self._network_log)

    def _try_fetch_body(self, entry: dict) -> None:
        """Best-effort: populate entry['body_preview'] via Network.getResponseBody."""
        request_id = entry.get("requestId", "")
        if not request_id:
            return
        try:
            result = self.cmd("Network.getResponseBody", {"requestId": request_id})
            body = result.get("result", {}).get("body", "")
            if body:
                entry["body_preview"] = body[:500]
        except Exception:
            pass

    def wait_for_request(
        self, pattern: str, timeout: float = 15.0
    ) -> Optional[dict]:
        """Block until a network request whose URL contains *pattern* fires."""
        if not self._network_logging:
            self.enable_network_logging()

        with self._network_lock:
            for entry in self._network_log:
                if (entry.get("type") == "Network.requestWillBeSent"
                        and pattern in entry.get("url", "")):
                    return dict(entry)

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

            msg = json.loads(raw)
            if "method" not in msg:
                continue

            self._dispatch_event(msg)
            with self._event_lock:
                self._pending_events.append(msg)

            if msg.get("method") == "Network.requestWillBeSent":
                params = msg.get("params", {})
                url = params.get("request", {}).get("url", "")
                if pattern in url:
                    self.ws.settimeout(None)
                    return {
                        "type": "Network.requestWillBeSent",
                        "url": url,
                        "method": params.get("request", {}).get("method", "GET"),
                        "resourceType": params.get("type", ""),
                        "requestId": params.get("requestId", ""),
                        "timestamp": params.get("timestamp", 0),
                    }

        self.ws.settimeout(None)
        return None

    def wait_for_response(
        self, pattern: str, timeout: float = 15.0
    ) -> Optional[dict]:
        """Block until a complete response whose URL contains *pattern* is available."""
        if not self._network_logging:
            self.enable_network_logging()

        matched_entry: Optional[dict] = None
        matched_request_id: Optional[str] = None
        loading_finished: bool = False

        with self._network_lock:
            request_ids_finished: set[str] = {
                e["requestId"]
                for e in self._network_log
                if e.get("type") == "Network.loadingFinished"
            }
            for entry in self._network_log:
                if (entry.get("type") == "Network.responseReceived"
                        and pattern in entry.get("url", "")):
                    matched_entry = {
                        "url": entry.get("url", ""),
                        "status": entry.get("status", 0),
                        "mimeType": entry.get("mimeType", ""),
                        "resourceType": entry.get("resourceType", ""),
                        "requestId": entry.get("requestId", ""),
                        "body_preview": "",
                    }
                    matched_request_id = entry.get("requestId", "")
                    if matched_request_id in request_ids_finished:
                        loading_finished = True
                    break

        if matched_entry and loading_finished:
            self._try_fetch_body(matched_entry)
            return matched_entry

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
                if matched_entry:
                    self._try_fetch_body(matched_entry)
                    if matched_entry.get("body_preview"):
                        return matched_entry
                continue
            except Exception:
                self.ws.settimeout(None)
                break

            msg = json.loads(raw)
            if "method" not in msg:
                continue

            self._dispatch_event(msg)
            with self._event_lock:
                self._pending_events.append(msg)

            method = msg.get("method", "")
            params = msg.get("params", {})

            if method == "Network.responseReceived" and matched_entry is None:
                resp = params.get("response", {})
                url = resp.get("url", "")
                if pattern in url:
                    matched_entry = {
                        "url": url,
                        "status": resp.get("status", 0),
                        "mimeType": resp.get("mimeType", ""),
                        "resourceType": params.get("type", ""),
                        "requestId": params.get("requestId", ""),
                        "body_preview": "",
                    }
                    matched_request_id = params.get("requestId", "")

            elif method == "Network.loadingFinished" and matched_entry is not None:
                if params.get("requestId") == matched_request_id:
                    self.ws.settimeout(None)
                    self._try_fetch_body(matched_entry)
                    return matched_entry

            elif method == "Network.loadingFailed" and matched_entry is not None:
                if params.get("requestId") == matched_request_id:
                    self.ws.settimeout(None)
                    matched_entry["errorText"] = params.get("errorText", "")
                    return matched_entry

        self.ws.settimeout(None)
        if matched_entry:
            self._try_fetch_body(matched_entry)
            return matched_entry
        return None

    # ──────────────────────────── selector shim ──────────────────────────────

    def _install_selector_shim(self):
        try:
            result = self.cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": _SELECTOR_SHIM_JS,
            })
            self._shim_script_id = (
                result.get("result", {}).get("identifier")
            )
        except Exception:
            pass
        self.js(_SELECTOR_SHIM_JS)

    def _apply_selector_shim(self):
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
                self._dispatch_event(msg)
                if msg["method"] in _LOAD_SIGNALS:
                    load_fired = True
                else:
                    with self._event_lock:
                        self._pending_events.append(msg)

            if nav_acked and load_fired:
                self.ws.settimeout(None)
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

    def click_and_wait(self, selector: str, timeout: float = 15.0) -> bool:
        """Click *selector* then block until the next page-load event fires.

        Combines click() + wait_for_navigation() in a single call.  Use this
        when clicking a link or submit button that causes a full-page
        navigation, to avoid reading stale DOM immediately after the click.

        Parameters
        ----------
        selector : str
            CSS selector for the element to click.
        timeout : float
            Maximum seconds to wait for the resulting page load.

        Returns
        -------
        True if a page-load event was observed, False on timeout (but the
        click was still dispatched).

        Example
        -------
        web.click_and_wait('a[href="/logout"]')
        print(web.js('window.location.href'))  # reliably shows new URL
        """
        self.click(selector)
        return self.wait_for_navigation(timeout=timeout)

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
        """Force CSS :hover on the node at (x, y) AND its ancestors."""
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
                    pass
        except Exception:
            pass

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

    def safe_click(
        self,
        selector: str,
        fallback_text: Optional[str] = None,
    ) -> bool:
        """Click *selector*, falling back to click_by_text(*fallback_text*) on failure.

        Tries to click the element matched by *selector*.  If no element is
        found (WebToolError) and *fallback_text* is provided, retries using
        :meth:`click_by_text` which searches visible text content instead of
        a CSS path.

        This is useful when a site's DOM structure changes between page loads
        (dynamic class names, shadow roots, etc.) but the button text is
        stable.

        Parameters
        ----------
        selector : str
            Primary CSS selector to try.
        fallback_text : str, optional
            Visible text to search for if *selector* fails.  Passed verbatim
            to click_by_text(fallback_text, tag='*').

        Returns
        -------
        True on success.  Raises WebToolError if both strategies fail.

        Example
        -------
        web.safe_click('button#submit', fallback_text='Submit')
        web.safe_click('a[href="/logout"]', fallback_text='Logout')
        """
        try:
            return self.click(selector)
        except WebToolError:
            if fallback_text is not None:
                return self.click_by_text(fallback_text)
            raise

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

    def retry_click(
        self,
        selector: str,
        attempts: int = 3,
        delay: float = 0.5,
    ) -> bool:
        """Attempt click() up to *attempts* times, waiting *delay* seconds between tries.

        Useful for elements that are in the DOM but not yet interactable
        (e.g. behind a loading spinner that disappears after animation).

        Parameters
        ----------
        selector : str
            CSS selector for the target element.
        attempts : int
            Maximum number of click attempts (default 3).
        delay : float
            Seconds to wait between failed attempts (default 0.5).

        Returns
        -------
        True on success.  Raises the last WebToolError if all attempts fail.

        Example
        -------
        web.retry_click('button#confirm', attempts=5, delay=0.3)
        """
        last_error: Optional[WebToolError] = None
        for _ in range(max(1, attempts)):
            try:
                return self.click(selector)
            except WebToolError as exc:
                last_error = exc
                time.sleep(delay)
        raise last_error  # type: ignore[misc]

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

    def safe_fill(self, selector: str, text: str) -> bool:
        """Fill *selector* with *text*, falling back to JS injection on layout failure.

        Strategy:
        1. Attempt the standard fill() (triple-click to select-all + type).
        2. If fill() raises WebToolError (element not found) — re-raise.
        3. If fill() succeeds but the element couldn't be reached by
           coordinates (off-screen, zero-size), the fallback JS path inside
           fill() handles it automatically.

        The method always fires ``input`` and ``change`` events so framework
        listeners (React, Vue, Angular) register the new value.

        Parameters
        ----------
        selector : str
            CSS selector for the input/textarea element.
        text : str
            Value to set.

        Returns
        -------
        True on success.  Raises WebToolError only when the element is absent.

        Example
        -------
        web.safe_fill('#search', 'python asyncio')
        web.safe_fill('textarea.notes', 'Hello\\nWorld')
        """
        esc_sel  = _esc_dq(selector)
        esc_text = _esc_dq(text)

        # Try normal fill first (handles both coordinate and JS-fallback paths)
        try:
            self.fill(selector, text)
            return True
        except WebToolError:
            pass  # element not reachable via coords, try pure JS injection

        # Pure JS injection fallback
        result = self.js(
            f'(function(){{'
            f'  var el = document.querySelector("{esc_sel}");'
            f'  if (!el) return "NOT_FOUND";'
            f'  el.focus();'
            f'  var nativeInput = Object.getOwnPropertyDescriptor('
            f'    Object.getPrototypeOf(el), "value");'
            f'  if (nativeInput && nativeInput.set) {{'
            f'    nativeInput.set.call(el, "{esc_text}");'
            f'  }} else {{'
            f'    el.value = "{esc_text}";'
            f'  }}'
            f'  el.dispatchEvent(new Event("input",  {{bubbles:true}}));'
            f'  el.dispatchEvent(new Event("change", {{bubbles:true}}));'
            f'  return "ok";'
            f'}})()'
        )
        if result == "NOT_FOUND":
            raise WebToolError(
                f"safe_fill(): no element for selector {selector!r}"
            )
        return True

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

    def wait_for_count(
        self,
        selector: str,
        count: int = 1,
        timeout: float = 10.0,
    ) -> bool:
        """Poll until at least *count* elements matching *selector* are in the DOM.

        Useful when a page renders items incrementally (infinite scroll, lazy
        load, streaming) and you need a minimum number before processing.

        Parameters
        ----------
        selector : str
            CSS selector to count.
        count : int
            Minimum number of matching elements to wait for (default 1).
        timeout : float
            Maximum seconds to wait (default 10).

        Returns
        -------
        True when the threshold is met, False on timeout.

        Example
        -------
        web.scroll('down', 1000)
        ok = web.wait_for_count('.product-card', count=20, timeout=8)
        """
        esc = _esc_dq(selector)
        code = f'document.querySelectorAll("{esc}").length'
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            n = self.js(code)
            if n is not None and int(n) >= count:
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
        """Wait until window.location.href differs from its current value."""
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

    def scroll_to(self, selector: str):
        """Scroll *selector* into the visible viewport (smooth behaviour).

        Uses ``element.scrollIntoView({behavior:'smooth', block:'center'})``
        which works even when the page has custom scroll containers.  Does
        nothing (no error) if the element is not found.

        Example
        -------
        web.scroll_to('#footer-newsletter')
        web.wait_for_visible('#footer-newsletter')
        """
        esc = _esc_dq(selector)
        self.js(
            f'(function(){{'
            f'  var el = document.querySelector("{esc}");'
            f'  if (el) el.scrollIntoView({{behavior:"smooth",block:"center"}});'
            f'}})()'
        )

    def hover(self, selector: str):
        """Move the mouse over *selector* and force CSS :hover on ancestors."""
        result = self._get_coords_full(selector)
        if result is None:
            raise WebToolError(f"hover(): no element for selector {selector!r}")
        x, y, matched_sel = result

        self.cmd("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })

        self._force_hover_at(x, y, matched_sel)

    def select_option(self, selector: str, value: str):
        """Set a <select> dropdown by option *value* attribute OR visible text.

        Matching priority
        -----------------
        1. Exact match on ``option.value``.
        2. Exact match on ``option.text.trim()`` (visible label).

        This two-pass approach handles the common discrepancy where a test
        passes the human-readable label ("Option 2") while the underlying
        ``<option>`` element carries a numeric value attribute ("2").

        After setting the selected option, a ``change`` event is dispatched
        with ``bubbles:true`` so framework listeners fire.

        Raises WebToolError if the element is not found, or if no option
        matches by either value or text.

        Example
        -------
        web.select_option('#country', 'US')          # by value
        web.select_option('#size',    'Extra Large')  # by text
        """
        esc_sel = _esc_sq(selector)
        esc_val = _esc_sq(value)
        code = (
            f"(function(){{"
            f"  var el = document.querySelector('{esc_sel}');"
            f"  if (!el) return 'NOT_FOUND';"
            f"  var found = false;"
            f"  for (var i = 0; i < el.options.length; i++) {{"
            f"    if (el.options[i].value === '{esc_val}') {{"
            f"      el.selectedIndex = i; found = true; break;"
            f"    }}"
            f"  }}"
            f"  if (!found) {{"
            f"    for (var j = 0; j < el.options.length; j++) {{"
            f"      if (el.options[j].text.trim() === '{esc_val}') {{"
            f"        el.selectedIndex = j; found = true; break;"
            f"      }}"
            f"    }}"
            f"  }}"
            f"  if (!found) return 'VALUE_NOT_FOUND';"
            f"  el.dispatchEvent(new Event('change', {{bubbles: true}}));"
            f"  return el.options[el.selectedIndex].text.trim();"
            f"}})()"
        )
        result = self.js(code)
        if result == "NOT_FOUND":
            raise WebToolError(
                f"select_option(): no element for selector {selector!r}"
            )
        if result == "VALUE_NOT_FOUND":
            raise WebToolError(
                f"select_option(): no option with value or text {value!r} "
                f"in {selector!r}"
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

    # ─── composite high-level flows ──────────────────────────────────────────

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
        """All-in-one login helper."""
        self.go(url)
        self.fill(username_selector, username)
        self.fill(password_selector, password)
        initial_url: str = self.js("window.location.href") or url
        self.click(submit_selector)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current: str = self.js("window.location.href") or ""
            if current and current != initial_url:
                self._apply_selector_shim()
                return True
            time.sleep(0.1)
        self._apply_selector_shim()
        final_url: str = self.js("window.location.href") or ""
        return bool(final_url and final_url != initial_url)

    def fill_form(self, field_map: "dict[str, str]") -> None:
        """Fill multiple form fields in one call."""
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
        """Collect data across multiple pages."""
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
        if self._network_logging:
            self.cmd("Network.enable")
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

    # ──────────────────────────── JS helpers ─────────────────────────────────

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

    def js_wait(
        self,
        code: str,
        timeout: float = 5.0,
        interval: float = 0.2,
    ):
        """Retry *code* via js() until it returns a non-None value or *timeout* expires.

        Handles the common race condition where the selector exists in the DOM
        but the element's text / inner content is not yet populated, causing a
        plain js() call to return null.  Also useful after scroll-triggered
        lazy loading when the exact moment of rendering is unknown.

        Parameters
        ----------
        code : str
            JavaScript expression to evaluate.  Should evaluate to a truthy
            value when the expected content is ready.
        timeout : float
            Maximum seconds to keep retrying (default 5).
        interval : float
            Sleep between retries in seconds (default 0.2).

        Returns
        -------
        The first non-None result, or None if the timeout expires.

        Example
        -------
        data = web.js_wait(
            "JSON.stringify(Array.from(document.querySelectorAll('.quote'))"
            ".map(q => q.querySelector('.text').textContent.trim()))",
            timeout=8,
        )
        quotes = json.loads(data)
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.js(code)
            if result is not None:
                return result
            time.sleep(interval)
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

    # Alias
    get_text = text

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

        # Network logging state
        self._network_logging: bool = False
        self._network_log: list[dict] = []
        self._network_log_lock = asyncio.Lock()

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
                    await self._dispatch_event_async(msg)
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

    # ──────────── async network event dispatch ────────────────────────────────

    async def _dispatch_event_async(self, event: dict) -> None:
        if not self._network_logging:
            return
        method = event.get("method", "")
        if method not in _NETWORK_EVENT_METHODS:
            return
        await self._handle_network_event_async(event)

    async def _handle_network_event_async(self, event: dict) -> None:
        method = event.get("method", "")
        params = event.get("params", {})

        entry: dict = {
            "type": method,
            "timestamp": params.get("timestamp", 0),
            "requestId": params.get("requestId", ""),
        }

        if method == "Network.requestWillBeSent":
            req = params.get("request", {})
            entry["url"] = req.get("url", "")
            entry["method"] = req.get("method", "GET")
            entry["resourceType"] = params.get("type", "")
        elif method == "Network.responseReceived":
            resp = params.get("response", {})
            entry["url"] = resp.get("url", "")
            entry["status"] = resp.get("status", 0)
            entry["mimeType"] = resp.get("mimeType", "")
            entry["resourceType"] = params.get("type", "")
        elif method == "Network.loadingFinished":
            entry["url"] = ""
            entry["encodedDataLength"] = params.get("encodedDataLength", 0)
        elif method == "Network.loadingFailed":
            entry["url"] = ""
            entry["errorText"] = params.get("errorText", "")
            entry["resourceType"] = params.get("type", "")

        async with self._network_log_lock:
            self._network_log.append(entry)

    # ──────────── async network public API ───────────────────────────────────

    async def enable_network_logging_async(self) -> None:
        """Enable CDP Network domain and start collecting network events (async)."""
        await self.cmd_async("Network.enable")
        async with self._network_log_lock:
            self._network_log = []
        self._network_logging = True

    async def get_network_log_async(self) -> list[dict]:
        """Return a snapshot of all collected network events (async)."""
        async with self._network_log_lock:
            return list(self._network_log)

    async def _try_fetch_body_async(self, entry: dict) -> None:
        request_id = entry.get("requestId", "")
        if not request_id:
            return
        try:
            result = await self.cmd_async(
                "Network.getResponseBody", {"requestId": request_id}
            )
            body = result.get("result", {}).get("body", "")
            if body:
                entry["body_preview"] = body[:500]
        except Exception:
            pass

    async def wait_for_request_async(
        self, pattern: str, timeout: float = 15.0
    ) -> Optional[dict]:
        """Async: block until a request URL matching pattern fires."""
        if not self._network_logging:
            await self.enable_network_logging_async()

        async with self._network_log_lock:
            for entry in self._network_log:
                if (entry.get("type") == "Network.requestWillBeSent"
                        and pattern in entry.get("url", "")):
                    return dict(entry)

        loop = asyncio.get_running_loop()
        result_fut: asyncio.Future = loop.create_future()

        async def _on_event(msg: dict):
            if result_fut.done():
                return
            if msg.get("method") == "Network.requestWillBeSent":
                params = msg.get("params", {})
                url = params.get("request", {}).get("url", "")
                if pattern in url:
                    result_fut.set_result({
                        "type": "Network.requestWillBeSent",
                        "url": url,
                        "method": params.get("request", {}).get("method", "GET"),
                        "resourceType": params.get("type", ""),
                        "requestId": params.get("requestId", ""),
                        "timestamp": params.get("timestamp", 0),
                    })

        self._event_listeners.append(_on_event)
        try:
            return await asyncio.wait_for(
                asyncio.shield(result_fut), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None
        finally:
            try:
                self._event_listeners.remove(_on_event)
            except ValueError:
                pass

    async def wait_for_response_async(
        self, pattern: str, timeout: float = 15.0
    ) -> Optional[dict]:
        """Async: block until a full response URL matching pattern is available."""
        if not self._network_logging:
            await self.enable_network_logging_async()

        async with self._network_log_lock:
            finished_ids: set[str] = {
                e["requestId"]
                for e in self._network_log
                if e.get("type") == "Network.loadingFinished"
            }
            for entry in self._network_log:
                if (entry.get("type") == "Network.responseReceived"
                        and pattern in entry.get("url", "")
                        and entry.get("requestId") in finished_ids):
                    result = {
                        "url": entry.get("url", ""),
                        "status": entry.get("status", 0),
                        "mimeType": entry.get("mimeType", ""),
                        "resourceType": entry.get("resourceType", ""),
                        "requestId": entry.get("requestId", ""),
                        "body_preview": "",
                    }
                    await self._try_fetch_body_async(result)
                    return result

        loop = asyncio.get_running_loop()
        response_fut: asyncio.Future = loop.create_future()
        matched_entry: dict = {}
        matched_request_id: list[str] = [""]

        async def _on_event(msg: dict):
            if response_fut.done():
                return
            method = msg.get("method", "")
            params = msg.get("params", {})

            if method == "Network.responseReceived" and not matched_request_id[0]:
                resp = params.get("response", {})
                url = resp.get("url", "")
                if pattern in url:
                    matched_entry.update({
                        "url": url,
                        "status": resp.get("status", 0),
                        "mimeType": resp.get("mimeType", ""),
                        "resourceType": params.get("type", ""),
                        "requestId": params.get("requestId", ""),
                        "body_preview": "",
                    })
                    matched_request_id[0] = params.get("requestId", "")

            elif method == "Network.loadingFinished":
                if (matched_request_id[0]
                        and params.get("requestId") == matched_request_id[0]):
                    await self._try_fetch_body_async(matched_entry)
                    if not response_fut.done():
                        response_fut.set_result(dict(matched_entry))

            elif method == "Network.loadingFailed":
                if (matched_request_id[0]
                        and params.get("requestId") == matched_request_id[0]):
                    matched_entry["errorText"] = params.get("errorText", "")
                    if not response_fut.done():
                        response_fut.set_result(dict(matched_entry))

        self._event_listeners.append(_on_event)
        try:
            return await asyncio.wait_for(
                asyncio.shield(response_fut), timeout=timeout
            )
        except asyncio.TimeoutError:
            if matched_entry:
                await self._try_fetch_body_async(matched_entry)
                return dict(matched_entry) if matched_entry else None
            return None
        finally:
            try:
                self._event_listeners.remove(_on_event)
            except ValueError:
                pass

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
        """Navigate to *url* and wait for a reliable page-load signal.

        Gen-8 improvement: after readyState reaches 'complete', polls for
        a non-empty ``document.title`` for up to 3 extra seconds.  This
        catches Heroku cold-start responses and other servers that send an
        almost-empty initial payload before JavaScript sets the page title.
        """
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

            # Wait for readyState complete
            poll_deadline = loop.time() + 2.0
            while loop.time() < poll_deadline:
                state = await self.js_async("document.readyState")
                if state == "complete":
                    break
                await asyncio.sleep(0.1)

            # Gen-8: wait for non-empty title (guards against cold-start empty pages)
            title_deadline = loop.time() + 3.0
            while loop.time() < title_deadline:
                title = await self.js_async("document.title")
                if title:
                    break
                await asyncio.sleep(0.2)

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
    """Async pool of N independent headless Chrome processes."""

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

    print("health_check:", web.health_check())

    web.go("https://quotes.toscrape.com")
    title = web.js("document.title")
    print("title:", title)

    web.close()
