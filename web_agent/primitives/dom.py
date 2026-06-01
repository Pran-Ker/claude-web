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


def paste_rich(
    client: CDPClient,
    html: str,
    text: str | None = None,
    trigger: bool = True,
) -> dict:
    """Put rich HTML on the clipboard and (optionally) trigger a trusted paste.

    The caret must already be in the target editor — focus it first with
    ``act <id> <handle> click`` (or ``focus``). Re-inspect afterwards: the paste
    mutates the DOM. Works for any canvas/contenteditable app (Docs, Sheets,
    Notion, Figma) — nothing here is application-specific.
    """
    client.grant_clipboard()
    client.set_focus_emulation(True)

    # Poka-yoke: a trusted paste lands in whatever is focused. If nothing
    # editable is focused, refuse — and don't clobber the OS clipboard for a
    # no-op. (iframes are treated as editable: canvas editors delegate focus.)
    if trigger:
        focus = client.focused_editable()
        if not focus["editable"]:
            return {
                "ok": True,
                "action": "paste",
                "html_len": len(html),
                "clipboard_loaded": False,
                "dispatched": False,
                "editable_target": False,
                "active_element": focus["descriptor"],
                "hint": (
                    f"No editable element is focused (active: {focus['descriptor']}). "
                    "Nothing was pasted and the OS clipboard was left untouched. "
                    "Focus the editor first — `act <id> <handle> click` — then re-run."
                ),
            }

    client.set_clipboard_rich(html, text)
    if trigger:
        client.trusted_paste()
        return {
            "ok": True,
            "action": "paste",
            "html_len": len(html),
            "clipboard_loaded": True,
            # `dispatched` means the trusted Paste keystroke was SENT — not proof
            # the content landed. A shared OS clipboard can still be clobbered.
            "dispatched": True,
            "editable_target": True,
            "active_element": focus["descriptor"],
            "hint": (
                f"Paste dispatched into {focus['descriptor']}. NOT idempotent — "
                "re-running appends the content again. Re-inspect or screenshot "
                "to confirm it landed."
            ),
        }
    return {
        "ok": True,
        "action": "paste",
        "html_len": len(html),
        "clipboard_loaded": True,
        "dispatched": False,
        "hint": (
            "Clipboard loaded only (--no-trigger); no paste sent. Focus the "
            "editor and re-run without --no-trigger to paste."
        ),
    }
