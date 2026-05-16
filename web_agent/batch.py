"""Batch executor — run a list of ops in one CLI invocation.

Collapses what would be N agent turns into a single subprocess + CDP session.
The agent submits a JSON array of ops; we execute serially against a
self-managed snapshot, auto-recovering from stale_handle, and return one
JSON payload describing every step.

Supported ops::

    {"op": "navigate", "url": "...", "wait": 2.0}
    {"op": "snapshot"}                              # capture/refresh
    {"op": "find", "role": "button", "name": "Submit"}   # returns handle
    {"op": "click", "handle": "button:submit"}
    {"op": "click", "find": {"role": "button", "name": "Submit"}}
    {"op": "fill",  "handle": "textbox:email", "text": "x@y"}
    {"op": "fill",  "find":   {"role": "textbox", "name": "Email"}, "text": "x@y"}
    {"op": "focus", "handle": "..."}
    {"op": "key",   "key": "Enter"}
    {"op": "js",    "code": "..."}
    {"op": "page_info"}
    {"op": "sleep", "seconds": 0.2}                 # rarely needed; explicit waits beat polls

The result is::

    {"ok": True, "steps": [<per-op result>, ...], "snapshot_id": "<final>"}

If a step fails, the executor stops and the failed step carries `"ok": false`
plus `kind`/`error`/`hint`. The caller sees exactly which step broke.
"""

from __future__ import annotations

import time

from .errors import StaleHandle, WebAgentError
from .inspector import capture_snapshot, query_snapshot
from .inspector.act import act_on_handle
from .inspector.query import find_by_handle, read_handle
from .inspector.store import SnapshotStore
from .primitives import dom
from .transport import CDPClient


def _resolve_handle(snap: dict, find_spec: dict, client: CDPClient) -> tuple[str, dict]:
    """Resolve a {role, name, text, tag} spec to a single handle, refreshing if needed."""
    def _run_query(s):
        return query_snapshot(
            s,
            role=find_spec.get("role"),
            name_contains=find_spec.get("name"),
            tag=find_spec.get("tag"),
            text_contains=find_spec.get("text"),
            visible_only=not find_spec.get("all_visibility", False),
            scope_selector=find_spec.get("scope_selector"),
            limit=find_spec.get("limit", 5),
        )

    out = _run_query(snap)
    if out["total"] == 0:
        # Try once with a refresh — the DOM may have mutated since the
        # last snapshot.
        refreshed = capture_snapshot(client)
        snap["elements"] = refreshed["elements"]
        snap["summary"] = refreshed["summary"]
        snap["url"] = refreshed.get("url")
        snap["title"] = refreshed.get("title")
        out = _run_query(snap)

    if out["total"] == 0:
        raise WebAgentError(
            f"No element matches {find_spec!r}.",
            hint="Loosen the filters (drop --name, try --text), or pass --all-visibility.",
        )
    handle = out["matches"][0]["handle"]
    el = find_by_handle(snap, handle)
    assert el is not None  # query just returned it
    return handle, out


def run_batch(client: CDPClient, store: SnapshotStore, ops: list[dict]) -> dict:
    snap: dict | None = None
    sid: str | None = None
    steps: list[dict] = []

    def _ensure_snap() -> dict:
        nonlocal snap, sid
        if snap is None:
            snap = capture_snapshot(client)
            sid = store.save(snap)
            snap["id"] = sid
        return snap

    for i, op in enumerate(ops):
        if not isinstance(op, dict) or "op" not in op:
            steps.append({"ok": False, "kind": "invalid_arguments",
                          "error": f"Step {i}: each op must be a dict with an 'op' field.",
                          "step": i})
            return {"ok": False, "steps": steps, "snapshot_id": sid}

        name = op["op"]
        try:
            if name == "navigate":
                info = dom.navigate(client, op["url"], wait_seconds=op.get("wait", 2.0))
                # Navigation invalidates any held snapshot.
                snap = None
                sid = None
                if op.get("snap", True):
                    s = _ensure_snap()
                    steps.append({"ok": True, "op": name, "step": i,
                                  "url": info.get("url"), "title": info.get("title"),
                                  "snapshot_id": s["id"], "total_elements": s["summary"]["total"]})
                else:
                    steps.append({"ok": True, "op": name, "step": i, **info})

            elif name == "snapshot":
                snap = capture_snapshot(client)
                sid = store.save(snap)
                snap["id"] = sid
                steps.append({"ok": True, "op": name, "step": i,
                              "snapshot_id": sid, "total_elements": snap["summary"]["total"]})

            elif name == "find":
                s = _ensure_snap()
                _, out = _resolve_handle(s, op, client)
                steps.append({"ok": True, "op": name, "step": i,
                              "snapshot_id": s["id"], **{k: out[k] for k in ("shown", "total", "matches")}})

            elif name in ("click", "fill", "focus", "scroll_into_view"):
                s = _ensure_snap()
                handle = op.get("handle")
                if handle is None and "find" in op:
                    handle, _ = _resolve_handle(s, op["find"], client)
                if not handle:
                    raise WebAgentError(
                        f"Step {i} '{name}' needs either 'handle' or 'find'.",
                        hint="Pass {'handle': '...'} or {'find': {'role': '...', 'name': '...'}}.",
                    )
                extra = {}
                if name == "fill":
                    extra["text"] = op.get("text", "")
                result = act_on_handle(client, s, handle, name, **extra)
                steps.append({"ok": True, "op": name, "step": i, "handle": handle, **result})

            elif name == "read":
                s = _ensure_snap()
                handle = op["handle"]
                steps.append({"ok": True, "op": name, "step": i, **read_handle(s, handle)})

            elif name == "js":
                value = client.evaluate(op["code"])
                steps.append({"ok": True, "op": name, "step": i, "value": value})

            elif name == "key":
                client.dispatch_key(op["key"])
                steps.append({"ok": True, "op": name, "step": i, "key": op["key"]})

            elif name == "page_info":
                steps.append({"ok": True, "op": name, "step": i, **client.page_info()})

            elif name == "sleep":
                time.sleep(float(op.get("seconds", 0)))
                steps.append({"ok": True, "op": name, "step": i, "slept": op.get("seconds", 0)})

            else:
                steps.append({"ok": False, "kind": "invalid_arguments",
                              "error": f"Unknown op {name!r}.", "step": i,
                              "hint": "Supported: navigate, snapshot, find, click, fill, focus, "
                                      "scroll_into_view, read, js, key, page_info, sleep."})
                return {"ok": False, "steps": steps, "snapshot_id": sid}

        except StaleHandle as e:
            # act_on_handle already retried once; if we get here the element
            # is really gone.
            steps.append({"ok": False, "kind": "stale_handle", "error": str(e),
                          "step": i, "op": name, "hint": e.hint})
            return {"ok": False, "steps": steps, "snapshot_id": sid}
        except WebAgentError as e:
            steps.append({"ok": False, "kind": e.kind, "error": str(e),
                          "step": i, "op": name, "hint": e.hint})
            return {"ok": False, "steps": steps, "snapshot_id": sid}

    return {"ok": True, "steps": steps, "snapshot_id": sid, "step_count": len(steps)}
