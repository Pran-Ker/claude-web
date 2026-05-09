"""Jina Reader fast path: GET https://r.jina.ai/<url> → markdown.

Free, no auth required. Hits the public hosted Reader. Used as the first-try
engine in the ``auto`` ladder. Returns ``None`` on failure so the caller can
fall back to a heavier engine.

The raw Jina response prepends a metadata header (``Title:``, ``URL Source:``,
``Published Time:``, ``Markdown Content:``) and frequently double-emits the
top heading. We strip both so downstream consumers get clean markdown.
"""

from __future__ import annotations

import re

import httpx

JINA_BASE = "https://r.jina.ai/"
TIMEOUT = 20.0

_HEADER_RE = re.compile(r"^[A-Z][A-Za-z ]{1,30}:\s.*$")
_BODY_MARKER = "Markdown Content:"


_JINA_PREAMBLE_KEYS = {
    "title", "url_source", "published_time", "description", "warning",
    "content_length", "language",
}


def _strip_jina_preamble(md: str) -> tuple[str, dict]:
    """Pull metadata out of the leading header lines, return (clean_md, meta).

    Only strip a header line if it is one of Jina's known preamble keys.
    Without this whitelist, real content lines like ``Note:``, ``Author:``,
    ``Warning:`` get eaten whenever the ``Markdown Content:`` marker is absent.
    """
    meta: dict = {}
    if _BODY_MARKER in md:
        head, _, body = md.partition(_BODY_MARKER)
        for line in head.splitlines():
            s = line.strip()
            if not s or ":" not in s:
                continue
            k, _, v = s.partition(":")
            meta[k.strip().lower().replace(" ", "_")] = v.strip()
        md = body.lstrip("\n")
    else:
        # No marker — only peel known preamble keys off the top.
        kept = []
        in_head = True
        for line in md.splitlines():
            if in_head:
                if line.strip() == "":
                    continue
                m = _HEADER_RE.match(line)
                if m:
                    k, _, v = line.partition(":")
                    key = k.strip().lower().replace(" ", "_")
                    if key in _JINA_PREAMBLE_KEYS:
                        meta[key] = v.strip()
                        continue
                in_head = False
            kept.append(line)
        md = "\n".join(kept)

    # Jina sometimes prints the H1 twice in a row.
    lines = md.splitlines()
    if len(lines) >= 3 and lines[0].startswith("# ") and lines[1].strip() == "" \
            and lines[2].strip() == lines[0].strip():
        lines = [lines[0]] + lines[3:]
        md = "\n".join(lines)

    return md.lstrip("\n"), meta


# Allow one level of balanced parens inside the URL so that Wikipedia/MDN
# style ``Mercury_(planet)`` URLs parse correctly. Without this, the regex
# stops at the first ``)`` and produces broken hrefs.
_MD_LINK_RE = re.compile(
    r"\[(?:[^\]]*)\]"
    r"\("
    r"(https?://(?:[^()\s]+|\([^()\s]*\))+)"
    r"\)"
)


def _extract_links_from_markdown(md: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _MD_LINK_RE.finditer(md or ""):
        url = m.group(1)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def fetch_via_jina(url: str, timeout: float = TIMEOUT) -> dict | None:
    try:
        resp = httpx.get(
            JINA_BASE + url,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "text/markdown", "X-Return-Format": "markdown"},
        )
    except httpx.HTTPError:
        return None

    if resp.status_code != 200 or not resp.text.strip():
        return None

    md, meta = _strip_jina_preamble(resp.text)
    title = meta.get("title")
    if not title:
        for line in md.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

    return {
        "engine": "jina",
        "url": meta.get("url_source") or url,
        "title": title,
        "markdown": md,
        "status": 200,
        "links": _extract_links_from_markdown(md),
        "description": meta.get("description"),
        "author": None,
        "date": meta.get("published_time"),
    }
