"""Generate stable, human-readable handles for elements.

Format: ``{role}:{slug}`` or ``{role}:{slug}-{n}`` for duplicates.
A readable handle (``button:submit-order``) lets the agent reason about an
element by name and often re-resolve after a re-inspect even if internal
ids change. This follows the Anthropic guidance that tool outputs should
favour human-readable identifiers over opaque ones.
"""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 30) -> str:
    if not text:
        return "unnamed"
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    if not slug:
        return "unnamed"
    return slug[:max_len].rstrip("-") or "unnamed"


def make_handle(role: str, name: str | None, used: dict[str, int]) -> str:
    base = f"{role or 'node'}:{slugify(name or '')}"
    n = used.get(base, 0) + 1
    used[base] = n
    return base if n == 1 else f"{base}-{n}"
