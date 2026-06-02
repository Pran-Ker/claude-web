"""Resolve a snapshot handle and dispatch an action against the live page.

The handle's ``backend_node_id`` is the primary key. If the DOM has mutated
and that id is gone, we raise ``StaleHandle`` with a steering hint rather
than silently failing.
"""

from __future__ import annotations

from ..errors import ElementNotActionable, StaleHandle, TransportError
from ..transport import CDPClient
from .query import find_by_handle
from .snapshot import capture_snapshot


def _bbox_center(model: dict) -> tuple[float, float]:
    quad = model["content"]
    xs, ys = quad[0::2], quad[1::2]
    return (sum(xs) / 4, sum(ys) / 4)


def _resolve_box(client: CDPClient, el: dict, snapshot: dict | None = None) -> dict:
    model = client.get_box_for_backend_id(el["backend_node_id"])
    if model:
        return model
    # Auto-recover: refresh the snapshot and look for the same handle. If
    # found with a live backend node, use it instead of failing the agent turn.
    if snapshot is not None:
        refreshed = _refresh_snapshot_for_handle(client, snapshot, el["handle"])
        if refreshed:
            new_model = client.get_box_for_backend_id(refreshed["backend_node_id"])
            if new_model:
                el["backend_node_id"] = refreshed["backend_node_id"]
                el["bbox"] = refreshed.get("bbox")
                return new_model
    raise StaleHandle(
        f"Handle {el['handle']!r} no longer resolves to a DOM node "
        f"(backend_node_id={el['backend_node_id']}).",
        hint="Element appears gone even after refresh. Re-`find` for a new handle.",
    )


def _scroll_into_view(client: CDPClient, backend_id: int) -> None:
    try:
        client.cmd("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_id})
    except Exception:
        # Older Chromes may not have this; ignore — click will still try.
        pass


def _refresh_snapshot_for_handle(client: CDPClient, snapshot: dict, handle: str) -> dict | None:
    """Refresh the snapshot in-place and return the element with the same handle, if present.

    Returns None if no element with that handle exists in the fresh snapshot.
    The snapshot dict is mutated so callers retain the same object identity.
    """
    try:
        fresh = capture_snapshot(client)
    except Exception:
        return None
    # Mutate snapshot in place so callers see the refreshed elements.
    snapshot["elements"] = fresh["elements"]
    snapshot["summary"] = fresh["summary"]
    snapshot["url"] = fresh.get("url")
    snapshot["title"] = fresh.get("title")
    snapshot["captured_at"] = fresh.get("captured_at")
    snapshot["refreshed"] = True
    return find_by_handle(snapshot, handle)


def act_on_handle(client: CDPClient, snapshot: dict, handle: str, action: str, **kwargs) -> dict:
    el = find_by_handle(snapshot, handle)
    if not el:
        # Same-name handle may exist in a fresh snapshot — try once before
        # surfacing stale_handle to the agent.
        el = _refresh_snapshot_for_handle(client, snapshot, handle)
        if not el:
            raise StaleHandle(
                f"Handle {handle!r} not in snapshot {snapshot['id']}.",
                hint="Element no longer present after refresh. Re-`find` for a new handle.",
            )

    backend_id = el["backend_node_id"]
    try:
        _scroll_into_view(client, backend_id)
    except TransportError:
        pass

    sid = snapshot.get("id")

    if action == "click":
        model = _resolve_box(client, el, snapshot)
        x, y = _bbox_center(model)
        client.dispatch_click(x, y)
        return {"ok": True, "action": "click", "handle": handle, "at": [x, y],
                "snapshot_id": sid,
                "hint": "Snapshot still valid for elements you didn't touch. Re-inspect only on `stale_handle`."}

    if action == "fill":
        text = kwargs.get("text", "")
        if not client.focus_backend_id(backend_id):
            # Fall back to clicking center
            model = _resolve_box(client, el, snapshot)
            x, y = _bbox_center(model)
            client.dispatch_click(x, y)
        # Select-all then replace — Input.insertText sends the whole string
        # in a single CDP round-trip.
        client.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 4})  # cmd-a on mac
        client.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 4})
        client.dispatch_key("Delete")
        client.type_text(text)
        return {"ok": True, "action": "fill", "handle": handle, "text": text,
                "snapshot_id": sid,
                "hint": "Snapshot still valid. Re-use it for the next `act` instead of re-inspecting."}

    if action == "focus":
        ok = client.focus_backend_id(backend_id)
        if not ok:
            raise ElementNotActionable(
                f"Could not focus {handle!r}.",
                hint="The element may be disabled or not focusable. Try `act ... click` instead.",
            )
        return {"ok": True, "action": "focus", "handle": handle, "snapshot_id": sid}

    if action == "scroll_into_view":
        return {"ok": True, "action": "scroll_into_view", "handle": handle, "snapshot_id": sid}

    raise ElementNotActionable(
        f"Unknown action {action!r}.",
        hint="Supported actions: click, fill, focus, scroll_into_view.",
    )
