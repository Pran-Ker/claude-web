"""BFS crawler. Run inline by the worker process.

Constraints:
- Same-origin by default; ``--external`` to follow off-site links
- Respect robots.txt (best-effort; failure to fetch robots = allow)
- Hard caps on ``max_pages`` and ``max_depth``
- Cooperative cancellation: checks for the cancel sentinel each iteration
"""

from __future__ import annotations

import time
from collections import deque
from urllib import robotparser
from urllib.parse import urlparse

from .fetch import fetch, FetchFailed
from .jobs import JobStore


def _strip_www(host: str) -> str:
    h = host.lower()
    if h.startswith("www."):
        h = h[4:]
    # Drop port for comparison (a site rarely splits content across :80/:443).
    if ":" in h:
        h = h.split(":", 1)[0]
    return h


def _same_origin(a: str, b: str) -> bool:
    """Host-equal-modulo-www so http↔https and www↔apex don't break crawls."""
    pa, pb = urlparse(a), urlparse(b)
    return _strip_www(pa.netloc) == _strip_www(pb.netloc)


def _robots_for(seed: str) -> robotparser.RobotFileParser | None:
    try:
        u = urlparse(seed)
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{u.scheme}://{u.netloc}/robots.txt")
        rp.read()
        return rp
    except Exception:
        return None


def _sitemap_urls(seed: str, timeout: float = 5.0, max_urls: int = 1000,
                  max_sitemaps: int = 25) -> list[str]:
    """Best-effort sitemap discovery.

    Tries ``robots.txt`` for ``Sitemap:`` directives, then ``/sitemap.xml``
    fallback. Recursively follows ``<sitemapindex>`` entries up to a hard cap
    on number of fetched sitemaps. Returns at most ``max_urls`` URLs. Silent
    on failure — a missing sitemap is normal.
    """
    import re as _re
    import httpx
    from xml.etree import ElementTree as ET

    u = urlparse(seed)
    base = f"{u.scheme}://{u.netloc}"
    candidates: list[str] = []

    # 1. Sitemap: directives in robots.txt
    try:
        r = httpx.get(f"{base}/robots.txt", timeout=timeout, follow_redirects=True)
        if r.status_code == 200:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    candidates.append(line.split(":", 1)[1].strip())
    except httpx.HTTPError:
        pass

    # 2. /sitemap.xml fallback
    if not candidates:
        candidates.append(f"{base}/sitemap.xml")

    found: list[str] = []
    seen_sitemaps: set[str] = set()
    fetched = 0
    i = 0
    while i < len(candidates) and len(found) < max_urls and fetched < max_sitemaps:
        sm_url = candidates[i]
        i += 1
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)
        try:
            r = httpx.get(sm_url, timeout=timeout, follow_redirects=True)
            if r.status_code != 200:
                continue
            text = r.text
            fetched += 1
        except httpx.HTTPError:
            continue
        # Strip XML namespaces for easier parsing.
        text = _re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        # If a sitemap index, queue child sitemaps and continue.
        if root.tag.endswith("sitemapindex"):
            for sm in root.findall(".//sitemap/loc"):
                if sm.text:
                    candidates.append(sm.text.strip())
            continue
        for loc in root.findall(".//url/loc"):
            if loc.text:
                found.append(loc.text.strip())
                if len(found) >= max_urls:
                    break
    return found


def run_crawl(jid: str, store: JobStore) -> None:
    from .fetch import DEFAULT_USER_AGENT

    spec = store.read_spec(jid)
    seed: str = spec["url"]
    max_pages: int = spec.get("limit", 25)
    max_depth: int = spec.get("depth", 2)
    external: bool = spec.get("external", False)
    engine: str = spec.get("engine", "auto")
    port: int = spec.get("port", 9222)
    delay: float = max(0.0, float(spec.get("delay", 0.0)))
    timeout: float = float(spec.get("timeout", 20.0))
    user_agent = spec.get("user_agent") or DEFAULT_USER_AGENT
    use_sitemap: bool = bool(spec.get("use_sitemap", True))

    rp = _robots_for(seed) if spec.get("respect_robots", True) else None

    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    seen: set[str] = {seed}
    pages_done = 0
    pages_failed = 0
    errors: list[dict] = []
    warning: str | None = None

    if rp is not None and not rp.can_fetch(user_agent, seed):
        warning = (
            "Seed URL is disallowed by robots.txt. The crawl will likely "
            "produce 0 pages. Pass --no-robots to override."
        )

    # Sitemap seeding — pre-populate the queue with URLs the site has advertised.
    sitemap_count = 0
    if use_sitemap:
        for sm_url in _sitemap_urls(seed, timeout=min(timeout, 10.0)):
            if sm_url in seen:
                continue
            if not external and not _same_origin(seed, sm_url):
                continue
            seen.add(sm_url)
            queue.append((sm_url, 1))  # treat as depth-1 from seed
            sitemap_count += 1
            if sitemap_count >= max_pages * 2:  # don't blow up the queue
                break

    # Clear any stale cancel sentinel from a re-used directory.
    store.clear_cancel(jid)
    store.update_status(
        jid, state="running", started_at=time.time(),
        queue_size=len(queue), current_url=seed, warning=warning,
        sitemap_seeded=sitemap_count,
    )

    seed_canonical = seed
    try:
        while queue and pages_done < max_pages:
            store.tick(jid)  # liveness signal independent of state changes
            if store.is_cancelled(jid):
                store.update_status(jid, state="cancelled", finished_at=time.time())
                return

            url, depth = queue.popleft()
            store.update_status(jid, current_url=url, queue_size=len(queue))

            if rp is not None and not rp.can_fetch(user_agent, url):
                errors.append({"url": url, "reason": "robots_disallow"})
                pages_failed += 1
                store.update_status(jid, pages_failed=pages_failed, errors=errors[-50:])
                if delay > 0:
                    time.sleep(delay)
                continue

            try:
                result = fetch(url, engine=engine, port=port,  # type: ignore[arg-type]
                               timeout=timeout, user_agent=user_agent)
            except FetchFailed as e:
                errors.append({"url": url, "reason": e.message})
                pages_failed += 1
                store.update_status(jid, pages_failed=pages_failed, errors=errors[-50:])
                if delay > 0:
                    time.sleep(delay)
                continue

            # After the seed fetch, canonicalise to the post-redirect URL so
            # same-origin checks match the host the server actually serves.
            if pages_done == 0:
                final_url = result.get("url") or url
                if final_url and final_url != seed:
                    seed_canonical = final_url

            store.save_page(jid, url, result)
            pages_done += 1

            if depth < max_depth:
                for link in result.get("links", []):
                    if link in seen:
                        continue
                    if not external and not _same_origin(seed_canonical, link):
                        continue
                    seen.add(link)
                    queue.append((link, depth + 1))

            store.update_status(
                jid,
                pages_done=pages_done,
                queue_size=len(queue),
            )
            if delay > 0:
                time.sleep(delay)
    except Exception as e:  # noqa: BLE001 — last-line defense, recorded into status
        store.update_status(
            jid,
            state="failed",
            finished_at=time.time(),
            errors=errors + [{"url": None, "reason": f"{type(e).__name__}: {e}"}],
        )
        return

    store.update_status(
        jid,
        state="done",
        finished_at=time.time(),
        current_url=None,
        queue_size=len(queue),
        pages_done=pages_done,
        pages_failed=pages_failed,
        errors=errors[-50:],
    )
