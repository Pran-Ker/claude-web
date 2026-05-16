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

# Scraper imports are deferred — they pull in trafilatura/lxml/httpx, and we
# want the inspector/browser subcommands to keep working even when those
# heavy deps aren't installed yet (e.g. before `uv sync`).


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
        info = dom.navigate(client, args.url, wait_seconds=args.wait)
        payload = dict(info)
        if args.snap:
            # Verify the page actually loaded the URL we asked for. Concurrent
            # navigations on the same tab can race the snapshot capture.
            live = client.page_info()
            payload["url"] = live.get("url")
            payload["title"] = live.get("title")
            snap = capture_snapshot(client)
            sid = _store(args).save(snap)
            snap["id"] = sid
            payload["snapshot"] = receipt(snap)
            if args.url and live.get("url") and not live["url"].startswith(args.url.split("#")[0]):
                payload["warning"] = (
                    f"Loaded URL {live.get('url')!r} differs from requested {args.url!r} — "
                    "another navigation may have raced on this tab."
                )
    _emit(payload)


def cmd_batch(args) -> None:
    """Run a JSON list of ops in one CLI call.

    Accepts ops either inline (``web-agent batch '<json>'``) or via stdin
    (``... | web-agent batch -``). One CDP connection serves all ops.
    """
    from .batch import run_batch
    if args.ops_json == "-":
        payload = sys.stdin.read()
    else:
        payload = args.ops_json
    try:
        ops = json.loads(payload)
    except json.JSONDecodeError as e:
        raise InvalidArguments(
            f"Could not parse batch JSON: {e}",
            hint='Pass a JSON array, e.g. \'[{"op":"navigate","url":"..."}]\'.',
        )
    if not isinstance(ops, list):
        raise InvalidArguments(
            "Batch payload must be a JSON array.",
            hint='Wrap a single op in brackets: \'[{"op":"page_info"}]\'.',
        )
    with _client(args) as client:
        result = run_batch(client, _store(args), ops)
    _emit(result, status=0 if result.get("ok") else 2)


def cmd_find(args) -> None:
    """Compound: inspect + query in a single CLI call.

    Saves one agent round-trip vs. running `inspect` and then `query`. The
    returned snapshot id is reusable for subsequent `act` calls.
    """
    with _client(args) as client:
        snap = capture_snapshot(client)
    sid = _store(args).save(snap)
    snap["id"] = sid
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
    out["snapshot_id"] = sid
    _emit(out)


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


# -- scraper subcommands ----------------------------------------------------


def _require_scraper():
    """Lazy-import the scraper, with a friendly error if deps are missing."""
    try:
        from .scraper.fetch import fetch as scraper_fetch, DEFAULT_USER_AGENT
        from .scraper.jobs import JobStore
    except ImportError as e:
        raise WebAgentError(
            f"Scraper dependencies not installed: {e.name or e}.",
            hint="Run `uv sync` from the repo root, then use `.venv/bin/python -m web_agent ...`.",
        )
    return scraper_fetch, DEFAULT_USER_AGENT, JobStore


def cmd_fetch(args) -> None:
    scraper_fetch, DEFAULT_USER_AGENT, _ = _require_scraper()
    ua = args.user_agent if args.user_agent is not None else DEFAULT_USER_AGENT
    result = scraper_fetch(
        args.url,
        engine=args.engine,
        port=args.port,
        timeout=args.timeout,
        user_agent=ua,
        include_html=args.include_html,
        screenshot_path=args.screenshot,
    )
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        from .scraper.jobs import _slugify  # noqa: PLC0415 — already inside scraper guard
        path = out / f"{_slugify(result.get('url') or args.url)}.md"
        path.write_text(result.get("markdown") or "")
        result["markdown_path"] = str(path)
    if args.no_links:
        result["links"] = []
    if args.markdown_only:
        _emit({
            "ok": True,
            "url": result.get("url"),
            "engine": result.get("engine"),
            "title": result.get("title"),
            "attempts": result.get("attempts", []),
            "markdown": result.get("markdown", ""),
            "warning": result.get("warning"),
        })
    else:
        _emit({"ok": True, **result})


def _job_store(args):
    _, _, JobStore = _require_scraper()
    return JobStore(args.crawls_dir)


def cmd_crawl(args) -> None:
    import subprocess
    import sys as _sys

    if args.delay < 0:
        raise InvalidArguments(
            f"--delay must be non-negative (got {args.delay}).",
            hint="Use 0 to disable, or a positive number of seconds.",
        )
    if args.timeout <= 0:
        raise InvalidArguments(
            f"--timeout must be positive (got {args.timeout}).",
            hint="Pass a positive number of seconds.",
        )
    spec = {
        "url": args.url,
        "limit": args.limit,
        "depth": args.depth,
        "external": args.external,
        "engine": args.engine,
        "respect_robots": not args.no_robots,
        "port": args.port,
        "delay": args.delay,
        "timeout": args.timeout,
        "user_agent": args.user_agent,
        "use_sitemap": not args.no_sitemap,
    }
    store = _job_store(args)
    jid = store.create(spec)

    # Spawn detached worker. stdout/stderr go to a log inside the job dir
    # so they don't pollute the parent's JSON output.
    log = open(store.job_dir(jid) / "worker.log", "wb")
    proc = subprocess.Popen(
        [_sys.executable, "-m", "web_agent.scraper.worker", jid,
         "--crawls-dir", str(args.crawls_dir)],
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    store.update_status(jid, worker_pid=proc.pid)
    dir_flag = f" --crawls-dir {args.crawls_dir}" if args.crawls_dir != ".crawls" else ""
    _emit({
        "ok": True,
        "job_id": jid,
        "spec": spec,
        "status_cmd": f"python3 -m web_agent{dir_flag} crawl-status {jid}",
        "results_cmd": f"python3 -m web_agent{dir_flag} crawl-results {jid}",
        "cancel_cmd": f"python3 -m web_agent{dir_flag} crawl-cancel {jid}",
    })


def cmd_crawl_status(args) -> None:
    store = _job_store(args)
    spec = store.read_spec(args.job_id)
    status = store.reconcile(args.job_id)
    _emit({"ok": True, "job_id": args.job_id, "spec": spec, "status": status})


def cmd_crawl_cancel(args) -> None:
    store = _job_store(args)
    store.request_cancel(args.job_id)  # raises JobNotRunning if already terminal
    _emit({"ok": True, "job_id": args.job_id, "cancel_requested": True,
           "hint": "The worker checks for cancellation between pages; allow a few seconds."})


def cmd_crawl_results(args) -> None:
    store = _job_store(args)
    pages = store.list_pages(args.job_id)
    status = store.read_status(args.job_id)
    _emit({
        "ok": True,
        "job_id": args.job_id,
        "state": status.get("state"),
        "page_count": len(pages),
        "pages": pages[: args.limit],
        "shown": min(len(pages), args.limit),
        "total": len(pages),
        "hint": (f"{len(pages) - args.limit} more pages — raise --limit to see them."
                 if len(pages) > args.limit else None),
    })


def cmd_crawl_list(args) -> None:
    store = _job_store(args)
    jobs = store.list_jobs()
    summaries = []
    for jid in jobs:
        try:
            s = store.read_status(jid)
            spec = store.read_spec(jid)
            summaries.append({
                "job_id": jid,
                "state": s.get("state"),
                "url": spec.get("url"),
                "pages_done": s.get("pages_done"),
            })
        except Exception:
            continue
    _emit({"ok": True, "jobs": summaries, "total": len(summaries)})


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
    p.add_argument(
        "--crawls-dir",
        default=".crawls",
        help="Directory for crawl-job store (default .crawls)",
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
    sp.add_argument("--snap", action="store_true",
                    help="Capture a snapshot after load and include it in the response (saves an LLM turn)")
    sp.set_defaults(fn=cmd_navigate)

    sp = sub.add_parser("batch", help="Run a JSON list of ops in one CLI call (saves N agent turns)")
    sp.add_argument("ops_json", help="JSON array of ops, or '-' to read from stdin")
    sp.set_defaults(fn=cmd_batch)

    sp = sub.add_parser("find", help="inspect + query in one call (returns snapshot_id)")
    sp.add_argument("--role")
    sp.add_argument("--name", help="substring match against accessible name")
    sp.add_argument("--text", help="substring match across name/value/description")
    sp.add_argument("--tag")
    sp.add_argument("--scope-selector", help="substring match against the element's selector")
    sp.add_argument("--all-visibility", action="store_true", help="include hidden elements")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(fn=cmd_find)

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

    # ---- scraper subcommands ----
    sp = sub.add_parser("fetch", help="URL → clean markdown (Jina/HTTP/CDP ladder)")
    sp.add_argument("url")
    sp.add_argument("--engine", choices=["auto", "jina", "http", "cdp"], default="auto")
    sp.add_argument("--markdown-only", action="store_true",
                    help="Return only {url, engine, title, markdown, attempts}")
    sp.add_argument("--no-links", action="store_true", help="Empty the links list in output")
    sp.add_argument("--output-dir", help="If set, write markdown to <dir>/<slug>.md and add markdown_path to result")
    sp.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds")
    sp.add_argument("--user-agent", default=None,
                    help="Override the User-Agent header (http engine only)")
    sp.add_argument("--include-html", action="store_true",
                    help="Co-output the raw HTML alongside markdown")
    sp.add_argument("--screenshot", metavar="PATH",
                    help="Save a JPEG screenshot to PATH (requires --engine cdp or auto)")
    sp.set_defaults(fn=cmd_fetch)

    sp = sub.add_parser("crawl", help="BFS crawl a site (returns job_id immediately)")
    sp.add_argument("url")
    sp.add_argument("--limit", type=int, default=25, help="Max pages (default 25)")
    sp.add_argument("--depth", type=int, default=2, help="Max link depth (default 2)")
    sp.add_argument("--engine", choices=["auto", "jina", "http", "cdp"], default="auto")
    sp.add_argument("--external", action="store_true",
                    help="Follow off-origin links (default same-origin only)")
    sp.add_argument("--no-robots", action="store_true",
                    help="Ignore robots.txt (default: respect)")
    sp.add_argument("--no-sitemap", action="store_true",
                    help="Skip sitemap.xml seeding (default: use it)")
    sp.add_argument("--delay", type=float, default=0.5,
                    help="Seconds between page fetches (default 0.5; 0 to disable)")
    sp.add_argument("--timeout", type=float, default=20.0,
                    help="Per-page fetch timeout in seconds (default 20)")
    sp.add_argument("--user-agent", default=None,
                    help="Override the User-Agent (used by both fetch and robots check)")
    sp.set_defaults(fn=cmd_crawl)

    sp = sub.add_parser("crawl-status", help="Poll the status of a crawl job")
    sp.add_argument("job_id")
    sp.set_defaults(fn=cmd_crawl_status)

    sp = sub.add_parser("crawl-cancel", help="Request cancellation of a running crawl")
    sp.add_argument("job_id")
    sp.set_defaults(fn=cmd_crawl_cancel)

    sp = sub.add_parser("crawl-results", help="List the pages saved by a crawl job")
    sp.add_argument("job_id")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(fn=cmd_crawl_results)

    sp = sub.add_parser("crawl-list", help="List all crawl jobs")
    sp.set_defaults(fn=cmd_crawl_list)

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
