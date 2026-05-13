"""Chrome DevTools Protocol client.

Replaces the legacy ``tools/web_tool.py``. Differences:
- Methods raise typed exceptions on failure rather than printing to stdout.
- ``evaluate()`` returns structured results instead of mixing values with error prints.
- ``cmd()`` raises ``TransportError`` on protocol-level errors.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests
import websocket

from ..errors import JSExecutionError, TransportError


class CDPClient:
    def __init__(self, port: int = 9222, tab_index: int = 0):
        self.port = port
        self.tab_index = tab_index
        self.ws: websocket.WebSocket | None = None
        self.msg_id = 0

    # -- lifecycle -----------------------------------------------------------

    def connect(self) -> "CDPClient":
        try:
            all_tabs = requests.get(f"http://localhost:{self.port}/json", timeout=5).json()
        except Exception as e:
            raise TransportError(
                f"Could not reach Chrome on port {self.port}: {e}",
                hint="Start Chrome with `python tools/browser.py start` "
                "or check `python tools/browser.py list`.",
            ) from e

        # Skip background pages, service workers, devtools — pick actual page tabs.
        tabs = [
            t for t in all_tabs
            if t.get("type") == "page" and not t.get("url", "").startswith("chrome-extension://")
        ]
        if not tabs:
            tabs = [t for t in all_tabs if t.get("type") == "page"] or all_tabs

        if not tabs:
            raise TransportError(
                f"No tabs available on port {self.port}.",
                hint="Open a tab in the running Chrome instance.",
            )

        self.ws = websocket.create_connection(tabs[self.tab_index]["webSocketDebuggerUrl"])
        self.cmd("Page.enable")
        self.cmd("DOM.enable")
        self.cmd("Runtime.enable")
        self.cmd("Accessibility.enable")
        return self

    def close(self) -> None:
        if self.ws:
            try:
                self.ws.close()
            finally:
                self.ws = None

    def __enter__(self) -> "CDPClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- core RPC ------------------------------------------------------------

    def cmd(self, method: str, params: dict | None = None) -> dict:
        if not self.ws:
            raise TransportError("CDPClient not connected.", hint="Call .connect() first.")
        self.msg_id += 1
        self.ws.send(json.dumps({"id": self.msg_id, "method": method, "params": params or {}}))
        while True:
            response = json.loads(self.ws.recv())
            if response.get("id") == self.msg_id:
                if "error" in response:
                    err = response["error"]
                    raise TransportError(
                        f"{method}: {err.get('message', err)}",
                        hint="Verify the page is loaded and the target node still exists.",
                    )
                return response.get("result", {})

    # -- JavaScript evaluation ----------------------------------------------

    def evaluate(self, code: str, return_by_value: bool = True) -> Any:
        """Run JS and return the value. Raises JSExecutionError on JS exceptions."""
        has_await = "await" in code
        expression = f"(async () => {{ return ({code}); }})()" if has_await else code
        result = self.cmd(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": return_by_value, "awaitPromise": has_await},
        )
        if "exceptionDetails" in result:
            ex = result["exceptionDetails"]
            text = ex.get("exception", {}).get("description") or ex.get("text") or "JS error"
            line = ex.get("lineNumber")
            where = f" at line {line + 1}" if isinstance(line, int) else ""
            raise JSExecutionError(
                f"JS exception{where}: {text}",
                hint="Check the expression syntax; ensure referenced selectors exist.",
            )
        obj = result.get("result", {})
        kind = obj.get("type", "undefined")
        if kind == "undefined":
            return None
        if kind == "object" and obj.get("subtype") == "null":
            return None
        return obj.get("value")

    # -- helpers used by primitives -----------------------------------------

    def navigate(self, url: str, wait_seconds: float = 2.0) -> None:
        self.cmd("Page.navigate", {"url": url})
        time.sleep(wait_seconds)

    def screenshot_bytes(self, quality: int = 80, fmt: str = "jpeg") -> bytes:
        import base64

        params: dict = {"format": fmt}
        if fmt == "jpeg":
            params["quality"] = quality
        result = self.cmd("Page.captureScreenshot", params)
        return base64.b64decode(result["data"])

    def get_box_for_backend_id(self, backend_node_id: int) -> dict | None:
        try:
            box = self.cmd("DOM.getBoxModel", {"backendNodeId": backend_node_id})
        except TransportError:
            return None
        return box.get("model")

    def dispatch_click(self, x: float, y: float) -> None:
        for ev in ("mousePressed", "mouseReleased"):
            self.cmd(
                "Input.dispatchMouseEvent",
                {"type": ev, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    def dispatch_key(self, key: str) -> None:
        self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
        self.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})

    def type_text(self, text: str) -> None:
        for ch in text:
            self.cmd("Input.dispatchKeyEvent", {"type": "char", "text": ch})

    def focus_backend_id(self, backend_node_id: int) -> bool:
        try:
            self.cmd("DOM.focus", {"backendNodeId": backend_node_id})
            return True
        except TransportError:
            return False

    def page_info(self) -> dict:
        return {
            "url": self.evaluate("window.location.href"),
            "title": self.evaluate("document.title"),
            "viewport": self.evaluate(
                "[window.innerWidth, window.innerHeight]"
            ),
        }
