"""Filter elements within a snapshot. Truncated results carry a steering hint."""

from __future__ import annotations

from typing import Iterable


def _match(el: dict, *, role, name_contains, tag, text_contains, visible_only, scope_selector) -> bool:
    if visible_only and not el.get("visible"):
        return False
    if role and el.get("role") != role:
        return False
    if tag and el.get("tag") != tag:
        return False
    if name_contains:
        n = (el.get("name") or "").lower()
        if name_contains.lower() not in n:
            return False
    if text_contains:
        haystack = " ".join(
            str(x or "") for x in (el.get("name"), el.get("value"), el.get("description"))
        ).lower()
        if text_contains.lower() not in haystack:
            return False
    if scope_selector:
        sel = el.get("selector") or ""
        if scope_selector not in sel:
            return False
    return True


_PROJECTION = ("handle", "role", "name", "tag", "visible", "enabled", "bbox", "value")


def _project(el: dict) -> dict:
    return {k: el[k] for k in _PROJECTION if k in el}


def query_snapshot(
    snapshot: dict,
    *,
    role: str | None = None,
    name_contains: str | None = None,
    tag: str | None = None,
    text_contains: str | None = None,
    visible_only: bool = True,
    scope_selector: str | None = None,
    limit: int = 20,
) -> dict:
    elements: Iterable[dict] = snapshot["elements"]
    matched = [
        el for el in elements
        if _match(
            el,
            role=role,
            name_contains=name_contains,
            tag=tag,
            text_contains=text_contains,
            visible_only=visible_only,
            scope_selector=scope_selector,
        )
    ]

    total = len(matched)
    shown = matched[:limit]
    out: dict = {
        "ok": True,
        "inspect_id": snapshot["id"],
        "shown": len(shown),
        "total": total,
        "matches": [_project(e) for e in shown],
    }
    if total > limit:
        out["hint"] = (
            f"{total - limit} more matches not shown. Narrow with "
            "--name, --role, --tag, or --scope-selector, "
            "or raise --limit."
        )
    elif total == 0:
        out["hint"] = (
            "No matches. Try dropping a filter, or pass --all-visibility "
            "to include hidden elements."
        )
    return out


def find_by_handle(snapshot: dict, handle: str) -> dict | None:
    for el in snapshot["elements"]:
        if el.get("handle") == handle:
            return el
    return None


def read_handle(snapshot: dict, handle: str) -> dict:
    el = find_by_handle(snapshot, handle)
    if not el:
        from ..errors import StaleHandle
        raise StaleHandle(
            f"Handle {handle!r} not in snapshot {snapshot['id']}.",
            hint="Run `inspect` to refresh, then re-query.",
        )
    return {"ok": True, "inspect_id": snapshot["id"], "element": el}
