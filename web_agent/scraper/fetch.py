"""Single-URL fetch with engine selection.

Engine ladder when ``engine="auto"``:
1. Jina Reader (free, hosted, returns clean markdown)
2. httpx GET + trafilatura (fast, no JS)
3. CDP browser + trafilatura (full JS rendering; needs a running CDP browser)

The agent receives one JSON object indicating which engine handled it. If a
fetch returns suspiciously little content (e.g. a JS-only landing page), the
router escalates to the next engine. Every result has the same shape:

    {ok, engine, url, status, title, markdown, links, attempts,
     description, author, date}
"""

from __future__ import annotations

import time
from typing import Literal
from urllib.parse import urlparse

import httpx

from ..errors import InvalidArguments, WebAgentError
from .extract import extract_links, extract_markdown
from .jina import fetch_via_jina

Engine = Literal["auto", "jina", "http", "cdp"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 web_agent/0.1"
)
HTTP_TIMEOUT = 20.0
MIN_USEFUL_CHARS = 200  # below this we suspect a JS-only / blocked response

_TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml", "application/xml")
MAX_BODY_BYTES = 25 * 1024 * 1024  # 25 MB cap to bound memory per fetch


class FetchFailed(WebAgentError):
    kind = "fetch_failed"


class UnsupportedContentType(WebAgentError):
    kind = "unsupported_content_type"


def _validate_url(url: str) -> None:
    if not url or not url.strip():
        raise InvalidArguments("URL is empty.", hint="Pass a full URL like https://example.com.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidArguments(
            f"Unsupported URL scheme {parsed.scheme!r}.",
            hint="Only http:// and https:// are supported.",
        )
    if not parsed.netloc:
        raise InvalidArguments(f"URL has no host: {url!r}.", hint="Include a domain.")


_NON_TEXT_EXT = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".wav", ".ogg",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dmg", ".pkg", ".deb", ".rpm",
    ".woff", ".woff2", ".ttf", ".otf",
}


def _content_type_precheck(url: str, timeout: float) -> None:
    """HEAD-probe the URL for content-type. Cheap PDF/binary guard.

    Raises ``UnsupportedContentType`` for non-text resources. Silently
    returns if HEAD fails or is rejected — the engine path will catch it
    via its own GET. Also short-circuits via URL extension to avoid the
    network round-trip for obvious binary URLs.
    """
    parsed = urlparse(url)
    path = parsed.path.lower()
    for ext in _NON_TEXT_EXT:
        if path.endswith(ext):
            raise UnsupportedContentType(
                f"URL path ends with {ext!r}, which is not text/HTML.",
                hint="The URL points at a non-text resource.",
            )
    try:
        resp = httpx.head(
            url, timeout=min(timeout, 5.0), follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
    except httpx.HTTPError:
        return
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and not any(ctype.startswith(t) for t in _TEXT_TYPES):
        raise UnsupportedContentType(
            f"Content-Type {ctype!r} is not text/HTML.",
            hint="The URL points at a non-text resource (PDF, image, etc.).",
        )


def _empty_result(url: str, engine: str, status: int = 0,
                  warning: str | None = None) -> dict:
    out = {
        "engine": engine,
        "url": url,
        "title": None,
        "markdown": "",
        "status": status,
        "links": [],
        "description": None,
        "author": None,
        "date": None,
    }
    if warning:
        out["warning"] = warning
    return out


def _normalize(result: dict) -> dict:
    """Guarantee uniform shape no matter which engine produced it."""
    for k, default in (("status", 200), ("links", []), ("title", None),
                       ("description", None), ("author", None), ("date", None),
                       ("markdown", "")):
        result.setdefault(k, default)
    return result


def _fetch_http(url: str, *, timeout: float = HTTP_TIMEOUT,
                user_agent: str = DEFAULT_USER_AGENT,
                retries: int = 2,
                include_html: bool = False) -> dict:
    last_exc: Exception | None = None
    resp = None
    for attempt in range(retries + 1):
        try:
            with httpx.stream(
                "GET", url,
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": user_agent},
            ) as r:
                # Pre-flight Content-Length cap.
                cl = r.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
                    raise FetchFailed(
                        f"Response is {int(cl):,} bytes (cap {MAX_BODY_BYTES:,}).",
                        hint="The page is too large to safely process; raise the cap or skip.",
                    )
                # Stream up to the cap.
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BODY_BYTES:
                        raise FetchFailed(
                            f"Response exceeded {MAX_BODY_BYTES:,} bytes mid-stream.",
                            hint="The page is too large to safely process.",
                        )
                # Synthesise a response-like object with text + headers.
                resp = type("Resp", (), {
                    "status_code": r.status_code,
                    "headers": r.headers,
                    "url": r.url,
                    "text": buf.decode(r.encoding or "utf-8", errors="replace"),
                })()
        except httpx.HTTPError as e:
            last_exc = e
            if attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise FetchFailed(
                f"HTTP error fetching {url}: {e}",
                hint="Check the URL or try --engine cdp for JS-rendered pages.",
            )
        if 500 <= resp.status_code < 600 and attempt < retries:
            time.sleep(0.5 * (2 ** attempt))
            continue
        break

    if resp.status_code == 404:
        raise FetchFailed(
            f"HTTP 404 for {url}.",
            hint="The page does not exist. Verify the URL.",
        )
    if resp.status_code == 403:
        raise FetchFailed(
            f"HTTP 403 for {url}.",
            hint="Site blocked the request. Try --engine cdp or set --user-agent.",
        )
    if resp.status_code >= 400:
        raise FetchFailed(
            f"HTTP {resp.status_code} for {url}.",
            hint=("Server error. Retry, or try --engine cdp."
                  if resp.status_code >= 500
                  else "Site rejected the request. Try --engine cdp."),
        )

    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and not any(ctype.startswith(t) for t in _TEXT_TYPES):
        raise UnsupportedContentType(
            f"Content-Type {ctype!r} is not text/HTML.",
            hint="The URL points at a non-text resource (PDF, image, etc.).",
        )

    extracted = extract_markdown(resp.text, url=str(resp.url))
    extracted["engine"] = "http"
    extracted["url"] = str(resp.url)
    extracted["status"] = resp.status_code
    extracted["links"] = extract_links(resp.text, str(resp.url))
    if include_html:
        extracted["html"] = resp.text
    return _normalize(extracted)


def _fetch_cdp(url: str, port: int, wait_seconds: float = 2.5, *,
               include_html: bool = False,
               screenshot_path: str | None = None) -> dict:
    from pathlib import Path as _Path
    from ..transport import CDPClient

    with CDPClient(port=port).connect() as client:
        client.navigate(url, wait_seconds=wait_seconds)
        info = client.page_info()
        final_url_check = info.get("url") or ""
        if final_url_check.startswith("chrome-error://"):
            raise FetchFailed(
                f"Browser failed to load {url} (chrome-error page).",
                hint="DNS, certificate, or network failure. Verify the URL.",
            )
        html = client.evaluate("document.documentElement.outerHTML") or ""
        shot_meta: dict | None = None
        if screenshot_path:
            data = client.screenshot_bytes(quality=85)
            sp = _Path(screenshot_path)
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_bytes(data)
            shot_meta = {"path": str(sp), "bytes": len(data)}
    final_url = info.get("url") or url
    extracted = extract_markdown(html, url=final_url)
    extracted["engine"] = "cdp"
    extracted["url"] = final_url
    extracted["status"] = 200
    extracted["links"] = extract_links(html, final_url)
    if not extracted.get("title"):
        extracted["title"] = info.get("title")
    if include_html:
        extracted["html"] = html
    if shot_meta:
        extracted["screenshot"] = shot_meta
    return _normalize(extracted)


def _useful(result: dict | None) -> bool:
    if not result:
        return False
    md = result.get("markdown") or ""
    return len(md.strip()) >= MIN_USEFUL_CHARS


JINA_AUTO_TIMEOUT = 8.0  # cap Jina in `auto` mode so a hung Jina doesn't burn the budget


def fetch(url: str, engine: Engine = "auto", port: int = 9222, *,
          timeout: float = HTTP_TIMEOUT,
          user_agent: str = DEFAULT_USER_AGENT,
          include_html: bool = False,
          screenshot_path: str | None = None) -> dict:
    """Fetch a URL → clean markdown via the chosen engine."""
    _validate_url(url)
    if timeout <= 0:
        raise InvalidArguments(
            f"--timeout must be positive (got {timeout}).",
            hint="Pass a positive number of seconds, e.g. --timeout 20.",
        )
    if screenshot_path and engine not in ("cdp", "auto"):
        raise InvalidArguments(
            f"--screenshot requires engine cdp or auto (got {engine!r}).",
            hint="Use --engine cdp.",
        )
    # Content-type guard runs for every engine to keep PDFs/binaries from
    # leaking through the Jina or CDP paths (which don't sniff Content-Type).
    _content_type_precheck(url, timeout=timeout)

    if engine == "jina":
        result = fetch_via_jina(url, timeout=timeout)
        if not result:
            raise FetchFailed(
                f"Jina Reader returned no content for {url}.",
                hint="Try --engine http or --engine cdp.",
            )
        result["attempts"] = ["jina:ok"]
        return _normalize(result)

    if engine == "http":
        result = _fetch_http(url, timeout=timeout, user_agent=user_agent,
                             include_html=include_html)
        result["attempts"] = ["http:ok"]
        if not result.get("markdown", "").strip():
            result["warning"] = (
                "Fetched OK but extracted no main content. The page may be "
                "JS-rendered or have no readable body. Try --engine cdp."
            )
        return result

    if engine == "cdp":
        result = _fetch_cdp(url, port, include_html=include_html,
                            screenshot_path=screenshot_path)
        result["attempts"] = ["cdp:ok"]
        if not result.get("markdown", "").strip():
            result["warning"] = (
                "CDP rendered the page but trafilatura found no main content."
            )
        return result

    # auto: jina → http → cdp, escalating on emptiness.
    attempts: list[str] = []

    j = fetch_via_jina(url, timeout=min(timeout, JINA_AUTO_TIMEOUT))
    attempts.append("jina:ok" if _useful(j) else ("jina:thin" if j else "jina:fail"))
    if _useful(j):
        j["attempts"] = attempts  # type: ignore[index]
        return _normalize(j)  # type: ignore[arg-type]

    last: dict | None = None
    try:
        h = _fetch_http(url, timeout=timeout, user_agent=user_agent,
                        include_html=include_html)
        attempts.append("http:ok" if _useful(h) else "http:thin")
        if _useful(h):
            h["attempts"] = attempts
            return h
        last = h
    except UnsupportedContentType:
        # CDP can't help with PDFs either; surface the original error.
        raise
    except FetchFailed as e:
        attempts.append(f"http:fail({e.kind})")

    try:
        c = _fetch_cdp(url, port, include_html=include_html,
                       screenshot_path=screenshot_path)
        attempts.append("cdp:ok" if _useful(c) else "cdp:thin")
        c["attempts"] = attempts
        return c
    except WebAgentError as e:
        attempts.append(f"cdp:fail({e.kind})")
        if last is not None:
            last["attempts"] = attempts
            last["warning"] = (
                "All engines returned thin content; returning best-effort http result."
            )
            return last
        raise FetchFailed(
            f"All engines failed for {url}.",
            hint=(f"Attempts: {attempts}. Start a CDP browser via "
                  f"`python tools/browser.py start`, or try --engine jina."),
        )
