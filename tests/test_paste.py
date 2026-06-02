"""Smoke + unit tests for the `paste` capability.

No browser required — the CDP client is faked. These guard the three things the
multi-agent sweep stressed: the focus poka-yoke, json.dumps escaping of the
clipboard payload, and the trusted Paste command construction.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from web_agent.primitives import dom
from web_agent.transport.cdp import CDPClient


# -- dom.paste_rich: the focus poka-yoke + result contract ------------------


def _fake_client(editable: bool, descriptor: str = "div#ed") -> MagicMock:
    client = MagicMock()
    client.focused_editable.return_value = {"editable": editable, "descriptor": descriptor}
    return client


def test_paste_refuses_when_no_editable_focus():
    client = _fake_client(editable=False, descriptor="body")
    out = dom.paste_rich(client, html="<b>x</b>", trigger=True)

    assert out["ok"] is True
    assert out["dispatched"] is False
    assert out["editable_target"] is False
    assert out["clipboard_loaded"] is False
    assert out["active_element"] == "body"
    # Must not touch the OS clipboard or dispatch a paste for a no-op.
    client.set_clipboard_rich.assert_not_called()
    client.trusted_paste.assert_not_called()


def test_paste_dispatches_when_editable():
    client = _fake_client(editable=True, descriptor="div#ed")
    out = dom.paste_rich(client, html="<p><b>hi</b></p>", trigger=True)

    assert out["dispatched"] is True
    assert out["editable_target"] is True
    assert out["clipboard_loaded"] is True
    client.grant_clipboard.assert_called_once()
    client.set_focus_emulation.assert_called_once()
    client.set_clipboard_rich.assert_called_once()
    client.trusted_paste.assert_called_once()


def test_no_trigger_loads_clipboard_without_pasting():
    client = _fake_client(editable=True)
    out = dom.paste_rich(client, html="<b>x</b>", trigger=False)

    assert out["clipboard_loaded"] is True
    assert out["dispatched"] is False
    client.set_clipboard_rich.assert_called_once()
    client.trusted_paste.assert_not_called()
    # With --no-trigger we never need to inspect focus.
    client.focused_editable.assert_not_called()


# -- CDPClient.set_clipboard_rich: payload escaping -------------------------


def test_set_clipboard_rich_escapes_payload():
    client = CDPClient()  # __init__ does not connect
    captured: list[str] = []
    client.evaluate = lambda expr: captured.append(expr)  # type: ignore[assignment]

    html = '<b>q"q</b> back\\slash'
    client.set_clipboard_rich(html)

    assert len(captured) == 1
    expr = captured[0]
    # The html must appear json-escaped (quotes + backslash), never raw.
    assert json.dumps(html) in expr
    assert "navigator.clipboard.write" in expr
    assert "text/html" in expr and "text/plain" in expr


def test_set_clipboard_rich_derives_plaintext_when_omitted():
    client = CDPClient()
    captured: list[str] = []
    client.evaluate = lambda expr: captured.append(expr)  # type: ignore[assignment]

    client.set_clipboard_rich("<p>Hello <b>world</b></p>")
    # Tag-stripped plaintext flavor is present.
    assert json.dumps("Hello world") in captured[0]


# -- CDPClient.trusted_paste: trusted Paste command construction ------------


def test_trusted_paste_emits_paste_command():
    client = CDPClient()
    calls: list[tuple] = []
    client.cmd = lambda method, params=None: calls.append((method, params))  # type: ignore[assignment]

    client.trusted_paste()

    assert len(calls) == 2
    (m1, p1), (m2, p2) = calls
    assert m1 == "Input.dispatchKeyEvent" and m2 == "Input.dispatchKeyEvent"
    assert p1["commands"] == ["Paste"]      # the trusted editing command
    assert p1["type"] == "rawKeyDown" and p2["type"] == "keyUp"
    assert p1["windowsVirtualKeyCode"] == 86  # V
    assert p1["modifiers"] in (2, 4)          # Ctrl (2) or Meta (4)


# -- CDPClient.focused_editable: the editability classifier -----------------


def test_focused_editable_allows_iframe_and_contenteditable():
    client = CDPClient()
    js_seen: list[str] = []

    def fake_eval(js):
        js_seen.append(js)
        return {"editable": True, "descriptor": "iframe (focus delegated)"}

    client.evaluate = fake_eval  # type: ignore[assignment]
    out = client.focused_editable()

    assert out["editable"] is True
    js = js_seen[0]
    # Google Docs delegates focus into a contenteditable iframe — must be allowed.
    assert "iframe" in js
    assert "isContentEditable" in js
    assert "textarea" in js


def test_focused_editable_handles_non_dict_result():
    client = CDPClient()
    client.evaluate = lambda js: None  # type: ignore[assignment]
    out = client.focused_editable()
    assert out == {"editable": False, "descriptor": "unknown"}
