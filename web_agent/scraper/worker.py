"""Detached worker process. Spawned by the CLI when a crawl is created.

Invoked as::

    python -m web_agent.scraper.worker <job_id> [--snapshots-dir BASE]

The worker runs the crawl synchronously, persisting status to disk so the
main CLI can poll ``crawl-status`` without IPC.
"""

from __future__ import annotations

import argparse
import sys
import time

from .crawl import run_crawl
from .jobs import JobStore


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m web_agent.scraper.worker")
    p.add_argument("job_id")
    p.add_argument("--crawls-dir", default=".crawls")
    args = p.parse_args(argv)

    store = JobStore(args.crawls_dir)
    try:
        run_crawl(args.job_id, store)
    except Exception as e:  # noqa: BLE001
        store.update_status(
            args.job_id,
            state="failed",
            finished_at=time.time(),
            errors=[{"url": None, "reason": f"{type(e).__name__}: {e}"}],
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
