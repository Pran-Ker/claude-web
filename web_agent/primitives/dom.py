"""Low-level page-wide operations that don't need a snapshot handle.

The inspector covers element-targeted actions; these are page/global escape
hatches: navigate to a URL, send a raw key, take a screenshot, evaluate JS.
"""

from __future__ import annotations

from pathlib import Path

from ..transport import CDPClient


def navigate(client: CDPClient, url: str, wait_seconds: float = 2.0) -> dict:
    client.navigate(url, wait_seconds=wait_seconds)
    info = client.page_info()
    return {"ok": True, "action": "navigate", **info}


def screenshot(client: CDPClient, path: str | Path, quality: int = 80) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = client.screenshot_bytes(quality=quality)
    path.write_bytes(data)
    return {"ok": True, "action": "screenshot", "path": str(path), "bytes": len(data)}


def press_key(client: CDPClient, key: str) -> dict:
    client.dispatch_key(key)
    return {"ok": True, "action": "key", "key": key}


def evaluate_js(client: CDPClient, code: str) -> dict:
    value = client.evaluate(code)
    return {"ok": True, "action": "js", "value": value}
