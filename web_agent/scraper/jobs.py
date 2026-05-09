"""File-based job store for async crawls.

Layout::

    .crawls/
        index.json                  # {"next": 4}
        j1/
            spec.json               # original crawl request
            status.json             # live status (state, counts, errors)
            cancel                  # presence = cancel signal (cleared after finish)
            pages/
                <slug>.json         # {url, markdown, title, links, ...}

State machine: ``queued → running → done | cancelled | failed | orphaned``.

Concurrency: ``create()`` allocates ids by attempting ``mkdir(exist_ok=False)``,
so two concurrent callers cannot land on the same id.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from ..errors import WebAgentError

DEFAULT_DIR = Path(".crawls")
TERMINAL_STATES = {"done", "cancelled", "failed", "orphaned"}
HEARTBEAT_STALE_SECONDS = 120.0


class JobNotFound(WebAgentError):
    kind = "job_not_found"


class JobNotRunning(WebAgentError):
    kind = "job_not_running"


def _slugify(s: str, n: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s or "page")[:n]


def _job_sort_key(name: str) -> tuple[int, str]:
    m = re.match(r"j(\d+)$", name)
    return (int(m.group(1)) if m else 10**9, name)


class JobStore:
    def __init__(self, root: Path | str = DEFAULT_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"

    def _read_index(self) -> dict:
        if not self._index_path.exists():
            return {"next": 1}
        try:
            return json.loads(self._index_path.read_text())
        except (ValueError, OSError):
            return {"next": 1}

    def _write_index(self, idx: dict) -> None:
        self._index_path.write_text(json.dumps(idx, indent=2))

    @contextmanager
    def _lock(self):
        """Cross-process advisory lock around index.json mutations."""
        lock_path = self.root / ".lock"
        with open(lock_path, "a+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def create(self, spec: dict) -> str:
        """Allocate a fresh job id atomically. flock-protected."""
        with self._lock():
            idx = self._read_index()
            # Reconcile counter against any directories created out-of-band.
            existing = []
            for p in self.root.glob("j*"):
                m = re.match(r"j(\d+)$", p.name)
                if m and p.is_dir():
                    existing.append(int(m.group(1)))
            next_n = max(idx.get("next", 1), (max(existing) + 1) if existing else 1)
            jid = f"j{next_n}"
            job_dir = self.root / jid
            job_dir.mkdir()
            (job_dir / "pages").mkdir()
            (job_dir / "spec.json").write_text(json.dumps(spec, indent=2))
            self.write_status(jid, {
                "state": "queued",
                "started_at": None,
                "finished_at": None,
                "pages_done": 0,
                "pages_failed": 0,
                "queue_size": 0,
                "current_url": None,
                "errors": [],
                "worker_pid": None,
                "heartbeat": time.time(),
            })
            self._write_index({"next": next_n + 1})
        return jid

    def job_dir(self, jid: str) -> Path:
        d = self.root / jid
        if not d.exists():
            raise JobNotFound(
                f"No job named {jid!r}.",
                hint="List crawls with `crawl-list` or start one with `crawl <url>`.",
            )
        return d

    def read_spec(self, jid: str) -> dict:
        return json.loads((self.job_dir(jid) / "spec.json").read_text())

    def read_status(self, jid: str) -> dict:
        return json.loads((self.job_dir(jid) / "status.json").read_text())

    def write_status(self, jid: str, status: dict) -> None:
        path = self.root / jid / "status.json"
        path.write_text(json.dumps(status, indent=2))

    def update_status(self, jid: str, **changes) -> dict:
        """Patch fields. Does NOT touch heartbeat — use ``tick`` for that.

        Decoupling is deliberate: a worker stuck in a retry storm that calls
        update_status on every error must still be detectable as hung.
        """
        status = self.read_status(jid)
        status.update(changes)
        self.write_status(jid, status)
        return status

    def tick(self, jid: str) -> None:
        """Worker liveness signal — call from the top of each loop iteration."""
        status = self.read_status(jid)
        status["heartbeat"] = time.time()
        self.write_status(jid, status)

    def request_cancel(self, jid: str) -> None:
        status = self.read_status(jid)
        if status.get("state") in TERMINAL_STATES:
            raise JobNotRunning(
                f"Job {jid} is already {status['state']!r}.",
                hint="Cancel only applies while the job is queued or running.",
            )
        (self.job_dir(jid) / "cancel").touch()

    def is_cancelled(self, jid: str) -> bool:
        return (self.job_dir(jid) / "cancel").exists()

    def clear_cancel(self, jid: str) -> None:
        sentinel = self.job_dir(jid) / "cancel"
        if sentinel.exists():
            sentinel.unlink()

    def reconcile(self, jid: str) -> dict:
        """Mark a 'running' job as orphaned if the worker is dead or its
        heartbeat is stale (worker hung)."""
        status = self.read_status(jid)
        if status.get("state") != "running":
            return status
        pid = status.get("worker_pid")
        now = time.time()
        # 1) Process gone?
        if pid:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return self.update_status(
                    jid,
                    state="orphaned",
                    finished_at=now,
                    errors=(status.get("errors") or []) + [
                        {"url": None, "reason": f"worker pid {pid} no longer running"},
                    ],
                )
        # 2) Process alive but heartbeat is ancient?
        hb = status.get("heartbeat")
        if isinstance(hb, (int, float)) and now - hb > HEARTBEAT_STALE_SECONDS:
            return self.update_status(
                jid,
                state="orphaned",
                finished_at=now,
                errors=(status.get("errors") or []) + [
                    {"url": None,
                     "reason": f"worker heartbeat stale ({now - hb:.0f}s) — likely hung"},
                ],
            )
        return status

    def save_page(self, jid: str, url: str, page: dict) -> Path:
        slug = _slugify(url)
        base = slug
        i = 2
        while (self.job_dir(jid) / "pages" / f"{slug}.json").exists():
            slug = f"{base}-{i}"
            i += 1
        page_path = self.job_dir(jid) / "pages" / f"{slug}.json"
        page_path.write_text(json.dumps({"saved_at": time.time(), **page}, indent=2))
        return page_path

    def list_pages(self, jid: str) -> list[dict]:
        out: list[dict] = []
        for p in sorted((self.job_dir(jid) / "pages").glob("*.json")):
            try:
                doc = json.loads(p.read_text())
            except (ValueError, OSError):
                continue
            out.append({
                "url": doc.get("url"),
                "title": doc.get("title"),
                "json_path": str(p),
                "engine": doc.get("engine"),
                "char_count": len(doc.get("markdown") or ""),
            })
        return out

    def list_jobs(self) -> list[str]:
        names = [p.name for p in self.root.glob("j*") if p.is_dir()]
        return sorted(names, key=_job_sort_key)
