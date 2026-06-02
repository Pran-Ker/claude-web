"""Capture an accessibility-tree snapshot of the live page.

Strategy:
1. Pull the full AX tree (``Accessibility.getFullAXTree``) — this is the
   semantic, LLM-friendly view: roles + names + values, already filtered.
2. For each AX node bound to a DOM node, resolve tag, attrs, bbox.
3. Skip nodes whose role is ``none``/``presentation``/``InlineTextBox`` and
   nodes with no visible bounding box, unless they hold an interactive role.
4. Compute a readable handle per element.

Non-semantic clickables (a ``<div onclick=...>`` with no role) are picked up
in a second pass via JS, since AX may miss them. They get role ``clickable``.
"""

from __future__ import annotations

import time
from typing import Any

from ..transport import CDPClient
from .handle import make_handle


# Roles considered interactive even if not currently visible (e.g. menus).
_INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "switch", "menuitem", "menuitemcheckbox", "menuitemradio",
    "tab", "option", "slider", "spinbutton",
}

_SKIP_ROLES = {"none", "presentation", "InlineTextBox", "LineBreak", "StaticText"}


def _ax_value(prop: dict | None) -> Any:
    if not prop:
        return None
    v = prop.get("value")
    if isinstance(v, dict):
        return v.get("value")
    return v


def _quad_to_bbox(quad: list[float]) -> list[float]:
    xs = quad[0::2]
    ys = quad[1::2]
    x, y = min(xs), min(ys)
    return [round(x, 1), round(y, 1), round(max(xs) - x, 1), round(max(ys) - y, 1)]


def _bbox_visible(bbox: list[float] | None) -> bool:
    return bool(bbox and bbox[2] > 0 and bbox[3] > 0)


def _safe_attrs(attr_pairs: list[str]) -> dict[str, str]:
    """CDP returns DOM attributes as a flat [name, value, name, value, ...] list."""
    out: dict[str, str] = {}
    for i in range(0, len(attr_pairs) - 1, 2):
        out[attr_pairs[i]] = attr_pairs[i + 1]
    keep = {"id", "name", "type", "placeholder", "aria-label", "data-testid", "href", "value"}
    return {k: v for k, v in out.items() if k in keep}


def _selector_for(attrs: dict[str, str], tag: str) -> str | None:
    if "id" in attrs:
        return f"#{attrs['id']}"
    if "data-testid" in attrs:
        return f'{tag}[data-testid="{attrs["data-testid"]}"]'
    if "name" in attrs:
        return f'{tag}[name="{attrs["name"]}"]'
    return None


def _build_dom_index(client: CDPClient) -> dict[int, dict]:
    """One CDP call → all DOM nodes' tag/attrs/bbox keyed by backendNodeId.

    Replaces N×(DOM.describeNode + DOM.getBoxModel). DOMSnapshot.captureSnapshot
    returns the entire DOM tree plus layout in a single round-trip.
    """
    snap = client.cmd("DOMSnapshot.captureSnapshot", {
        "computedStyles": [],
        "includeDOMRects": True,
    })
    strings = snap.get("strings", [])

    def s(i: int | None) -> str:
        if i is None or i < 0 or i >= len(strings):
            return ""
        return strings[i]

    index: dict[int, dict] = {}
    keep = {"id", "name", "type", "placeholder", "aria-label", "data-testid", "href", "value"}

    for doc in snap.get("documents", []) or []:
        nodes = doc.get("nodes", {}) or {}
        layout = doc.get("layout", {}) or {}
        backend_ids: list[int] = nodes.get("backendNodeId") or []
        node_names: list[int] = nodes.get("nodeName") or []
        attrs_arr: list[list[int]] = nodes.get("attributes") or []

        layout_node_idx: list[int] = layout.get("nodeIndex") or []
        bounds: list[list[float]] = layout.get("bounds") or []
        bbox_by_node: dict[int, list[float]] = {}
        for li, ni in enumerate(layout_node_idx):
            if li < len(bounds):
                b = bounds[li]
                bbox_by_node[ni] = [round(b[0], 1), round(b[1], 1), round(b[2], 1), round(b[3], 1)]

        for idx_, bid in enumerate(backend_ids):
            if not bid:
                continue
            tag = s(node_names[idx_] if idx_ < len(node_names) else None).lower()
            attrs: dict[str, str] = {}
            pairs = attrs_arr[idx_] if idx_ < len(attrs_arr) else []
            for j in range(0, len(pairs) - 1, 2):
                k = s(pairs[j])
                if k in keep:
                    attrs[k] = s(pairs[j + 1])
            index[bid] = {
                "tag": tag,
                "attrs": attrs,
                "bbox": bbox_by_node.get(idx_),
            }
    return index


def capture_snapshot(client: CDPClient, scope: str | None = None) -> dict:
    """Build a snapshot dict. The store assigns the final ``id``."""
    client.enable("DOM")
    client.enable("Runtime")
    client.enable("DOMSnapshot")
    client.enable("Accessibility")

    page = client.page_info()

    # One CDP call for the full DOM + layout.
    dom_index = _build_dom_index(client)
    # One CDP call for the AX tree.
    ax_result = client.cmd("Accessibility.getFullAXTree")
    nodes = ax_result.get("nodes", [])

    elements: list[dict] = []
    used_handles: dict[str, int] = {}
    by_role: dict[str, int] = {}

    for node in nodes:
        role = _ax_value(node.get("role"))
        if not role or role in _SKIP_ROLES:
            continue
        if node.get("ignored"):
            continue
        backend_id = node.get("backendDOMNodeId")
        if not backend_id:
            continue

        info = dom_index.get(backend_id)
        if not info:
            continue

        name = _ax_value(node.get("name")) or ""
        value = _ax_value(node.get("value"))
        description = _ax_value(node.get("description"))

        tag = info["tag"]
        attrs = info["attrs"]
        bbox = info["bbox"]
        visible = _bbox_visible(bbox)
        if not visible and role not in _INTERACTIVE_ROLES:
            continue

        handle = make_handle(role, name, used_handles)
        by_role[role] = by_role.get(role, 0) + 1
        props = {p["name"]: p.get("value", {}).get("value") for p in (node.get("properties") or [])}
        enabled = not props.get("disabled", False)

        elements.append({
            "handle": handle,
            "role": role,
            "name": name,
            "value": value,
            "description": description,
            "tag": tag,
            "visible": visible,
            "enabled": enabled,
            "bbox": bbox,
            "attrs": attrs,
            "selector": _selector_for(attrs, tag),
            "backend_node_id": backend_id,
        })

    snapshot = {
        "url": page.get("url"),
        "title": page.get("title"),
        "viewport": page.get("viewport"),
        "captured_at": time.time(),
        "scope": scope,
        "summary": {"total": len(elements), "by_role": by_role},
        "elements": elements,
    }
    return snapshot


def receipt(snapshot: dict) -> dict:
    """Compact return for the agent — does NOT include the elements list."""
    s = snapshot["summary"]
    top = sorted(s["by_role"].items(), key=lambda kv: -kv[1])[:5]
    counts = ", ".join(f"{n} {r}" for r, n in top)
    return {
        "ok": True,
        "inspect_id": snapshot["id"],
        "url": snapshot["url"],
        "title": snapshot["title"],
        "total_elements": s["total"],
        "top_roles": dict(top),
        "hint": (
            f"{s['total']} elements indexed ({counts}). "
            f"Use `query {snapshot['id']} --role <role> --name <text>` to filter. "
            "Re-inspect after navigation or DOM-mutating actions."
        ),
    }
