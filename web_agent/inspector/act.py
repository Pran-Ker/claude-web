"""Resolve a snapshot handle and dispatch an action against the live page.

The handle's ``backend_node_id`` is the primary key. If the DOM has mutated
and that id is gone, we raise ``StaleHandle`` with a steering hint rather
than silently failing.
"""

from __future__ import annotations

from ..errors import ElementNotActionable, StaleHandle
from ..transport import CDPClient
from .query import find_by_handle


def _bbox_center(model: dict) -> tuple[float, float]:
    quad = model["content"]
    xs, ys = quad[0::2], quad[1::2]
    return (sum(xs) / 4, sum(ys) / 4)


def _resolve_box(client: CDPClient, el: dict) -> dict:
    model = client.get_box_for_backend_id(el["backend_node_id"])
    if model:
        return model
    raise StaleHandle(
        f"Handle {el['handle']!r} no longer resolves to a DOM node "
        f"(backend_node_id={el['backend_node_id']}).",
        hint="The page changed since the snapshot. Re-run `inspect` and retry.",
    )


def _scroll_into_view(client: CDPClient, backend_id: int) -> None:
    try:
        client.cmd("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_id})
    except Exception:
        # Older Chromes may not have this; ignore — click will still try.
        pass


def act_on_handle(client: CDPClient, snapshot: dict, handle: str, action: str, **kwargs) -> dict:
    el = find_by_handle(snapshot, handle)
    if not el:
        raise StaleHandle(
            f"Handle {handle!r} not in snapshot {snapshot['id']}.",
            hint="Run `inspect` to refresh, then retry.",
        )

    backend_id = el["backend_node_id"]
    _scroll_into_view(client, backend_id)

    if action == "click":
        model = _resolve_box(client, el)
        x, y = _bbox_center(model)
        client.dispatch_click(x, y)
        return {"ok": True, "action": "click", "handle": handle, "at": [x, y]}

    if action == "fill":
        text = kwargs.get("text", "")
        if not client.focus_backend_id(backend_id):
            # Fall back to clicking center
            model = _resolve_box(client, el)
            x, y = _bbox_center(model)
            client.dispatch_click(x, y)
        # Select-all then type — works for most inputs.
        client.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 4})  # cmd-a on mac
        client.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 4})
        client.dispatch_key("Delete")
        client.type_text(text)
        return {"ok": True, "action": "fill", "handle": handle, "text": text}

    if action == "focus":
        ok = client.focus_backend_id(backend_id)
        if not ok:
            raise ElementNotActionable(
                f"Could not focus {handle!r}.",
                hint="The element may be disabled or not focusable. Try `act ... click` instead.",
            )
        return {"ok": True, "action": "focus", "handle": handle}

    if action == "scroll_into_view":
        return {"ok": True, "action": "scroll_into_view", "handle": handle}

    raise ElementNotActionable(
        f"Unknown action {action!r}.",
        hint="Supported actions: click, fill, focus, scroll_into_view.",
    )
