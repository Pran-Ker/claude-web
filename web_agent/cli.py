"""Command-line surface for the agent.

Every command prints a single JSON object to stdout. Errors print structured
JSON with ``ok: false`` and exit non-zero. Nothing is ever printed to stdout
that isn't valid JSON — agents can ``json.loads`` the output unconditionally.

Subcommands::

    inspect [--scope CSS] [--port N]
    query    <id> [--role R] [--name TEXT] [--text TEXT] [--tag T]
                  [--scope-selector S] [--all-visibility] [--limit N]
    read     <id> <handle>
    act      <id> <handle> <action> [--text TEXT]
    navigate <url>
    screenshot <path>
    js       <code>
    page-info
    snapshots                # list saved snapshot ids
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import InvalidArguments, WebAgentError
from .inspector import (
    SnapshotStore,
    act_on_handle,
    capture_snapshot,
    query_snapshot,
)
from .inspector.query import read_handle
from .inspector.snapshot import receipt
from .primitives import dom
from .transport import CDPClient


def _emit(payload: dict, status: int = 0) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.exit(status)


def _store(args) -> SnapshotStore:
    return SnapshotStore(args.snapshots_dir)


def _client(args) -> CDPClient:
    return CDPClient(port=args.port).connect()


# -- subcommand handlers ----------------------------------------------------


def cmd_inspect(args) -> None:
    with _client(args) as client:
        snap = capture_snapshot(client, scope=args.scope)
    sid = _store(args).save(snap)
    snap["id"] = sid
    _emit(receipt(snap))


def cmd_query(args) -> None:
    snap = _store(args).load(args.inspect_id)
    out = query_snapshot(
        snap,
        role=args.role,
        name_contains=args.name,
        tag=args.tag,
        text_contains=args.text,
        visible_only=not args.all_visibility,
        scope_selector=args.scope_selector,
        limit=args.limit,
    )
    _emit(out)


def cmd_read(args) -> None:
    snap = _store(args).load(args.inspect_id)
    _emit(read_handle(snap, args.handle))


def cmd_act(args) -> None:
    snap = _store(args).load(args.inspect_id)
    extra: dict = {}
    if args.text is not None:
        extra["text"] = args.text
    with _client(args) as client:
        out = act_on_handle(client, snap, args.handle, args.action, **extra)
    _emit(out)


def cmd_navigate(args) -> None:
    with _client(args) as client:
        _emit(dom.navigate(client, args.url, wait_seconds=args.wait))


def cmd_screenshot(args) -> None:
    with _client(args) as client:
        _emit(dom.screenshot(client, args.path, quality=args.quality))


def cmd_js(args) -> None:
    with _client(args) as client:
        _emit(dom.evaluate_js(client, args.code))


def cmd_key(args) -> None:
    with _client(args) as client:
        _emit(dom.press_key(client, args.key))


def cmd_page_info(args) -> None:
    with _client(args) as client:
        _emit({"ok": True, **client.page_info()})


def cmd_snapshots(args) -> None:
    root = Path(args.snapshots_dir)
    ids = sorted(p.stem for p in root.glob("s*.json"))
    _emit({"ok": True, "snapshots": ids, "latest": _store(args).latest()})


# -- argparse wiring --------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m web_agent",
        description="CDP browser agent with a queryable DOM inspector.",
    )
    p.add_argument("--port", type=int, default=9222, help="CDP port (default 9222)")
    p.add_argument(
        "--snapshots-dir",
        default=".snapshots",
        help="Directory for snapshot store (default .snapshots)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("inspect", help="Capture an AX snapshot of the page")
    sp.add_argument("--scope", default=None, help="(reserved) CSS scope for snapshot")
    sp.set_defaults(fn=cmd_inspect)

    sp = sub.add_parser("query", help="Filter elements within a snapshot")
    sp.add_argument("inspect_id")
    sp.add_argument("--role")
    sp.add_argument("--name", help="substring match against accessible name")
    sp.add_argument("--text", help="substring match across name/value/description")
    sp.add_argument("--tag")
    sp.add_argument("--scope-selector", help="substring match against the element's selector")
    sp.add_argument("--all-visibility", action="store_true", help="include hidden elements")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(fn=cmd_query)

    sp = sub.add_parser("read", help="Get full detail for one handle")
    sp.add_argument("inspect_id")
    sp.add_argument("handle")
    sp.set_defaults(fn=cmd_read)

    sp = sub.add_parser("act", help="Perform an action against a handle")
    sp.add_argument("inspect_id")
    sp.add_argument("handle")
    sp.add_argument("action", choices=["click", "fill", "focus", "scroll_into_view"])
    sp.add_argument("--text", help="text to type (for action=fill)")
    sp.set_defaults(fn=cmd_act)

    sp = sub.add_parser("navigate", help="Load a URL")
    sp.add_argument("url")
    sp.add_argument("--wait", type=float, default=2.0)
    sp.set_defaults(fn=cmd_navigate)

    sp = sub.add_parser("screenshot", help="Save a JPEG screenshot")
    sp.add_argument("path")
    sp.add_argument("--quality", type=int, default=80)
    sp.set_defaults(fn=cmd_screenshot)

    sp = sub.add_parser("js", help="Evaluate JavaScript and return the value")
    sp.add_argument("code")
    sp.set_defaults(fn=cmd_js)

    sp = sub.add_parser("key", help="Press a key (Tab, Enter, Escape, ...)")
    sp.add_argument("key")
    sp.set_defaults(fn=cmd_key)

    sp = sub.add_parser("page-info", help="URL, title, viewport")
    sp.set_defaults(fn=cmd_page_info)

    sp = sub.add_parser("snapshots", help="List saved snapshot ids")
    sp.set_defaults(fn=cmd_snapshots)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except WebAgentError as e:
        _emit(e.to_dict(), status=2)
    except InvalidArguments as e:
        _emit(e.to_dict(), status=2)
    except Exception as e:
        _emit(
            {
                "ok": False,
                "kind": "internal_error",
                "error": f"{type(e).__name__}: {e}",
                "hint": "This is a bug in web_agent; please report with the command used.",
            },
            status=3,
        )
