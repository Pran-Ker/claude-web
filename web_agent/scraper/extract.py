"""HTML → clean markdown + structured metadata.

Wraps trafilatura, which is the open-source standard for boilerplate
removal and main-content extraction. We pre-absolutise links against the
fetched URL because trafilatura's ``url`` parameter is metadata-only and
won't resolve relative hrefs in the output.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import trafilatura
from lxml import html as lxhtml
from selectolax.parser import HTMLParser


def _absolutize(html: str, base_url: str | None) -> str:
    if not base_url or not html:
        return html
    try:
        tree = lxhtml.fromstring(html)
        tree.make_links_absolute(base_url, resolve_base_href=True)
        return lxhtml.tostring(tree, encoding="unicode")
    except (ValueError, lxhtml.etree.ParserError):  # type: ignore[attr-defined]
        return html


def extract_markdown(html: str, url: str | None = None) -> dict[str, Any]:
    """Run trafilatura. Returns markdown + extracted metadata."""
    if not html or not html.strip():
        return {"markdown": "", "title": None, "author": None, "date": None,
                "description": None}

    html_abs = _absolutize(html, url)

    md = trafilatura.extract(
        html_abs,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=True,
        include_tables=True,
    ) or ""

    meta_json = trafilatura.extract(
        html_abs,
        url=url,
        output_format="json",
        with_metadata=True,
    )
    meta: dict = {}
    if meta_json:
        try:
            meta = json.loads(meta_json)
        except (ValueError, TypeError):
            meta = {}

    return {
        "markdown": md,
        "title": meta.get("title"),
        "author": meta.get("author"),
        "date": meta.get("date"),
        "description": meta.get("description"),
    }


def extract_links(html: str, base_url: str) -> list[str]:
    """Pull all ``<a href>`` links, resolved against the base URL."""
    from urllib.parse import urldefrag

    if not html:
        return []
    tree = HTMLParser(html)
    seen: set[str] = set()
    out: list[str] = []
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = urljoin(base_url, href)
        absolute, _ = urldefrag(absolute)
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out
