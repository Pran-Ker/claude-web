"""Latency harness for web-agent.

Measures wall-clock time per subcommand against a fixed local page.
Prints a markdown table of median + p90 over N trials.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

PORT = os.environ.get("CDP_PORT", "9223")
N = int(os.environ.get("BENCH_N", "5"))
PAGE = "file://" + str(Path(__file__).parent.resolve() / "page.html")
SNAP_DIR = Path(__file__).parent / ".snapshots"
SNAP_DIR.mkdir(exist_ok=True)


def run(*args: str, capture: bool = True) -> tuple[float, dict]:
    cmd = ["web-agent", "--port", PORT, "--snapshots-dir", str(SNAP_DIR), *args]
    t0 = time.perf_counter()
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    dt = time.perf_counter() - t0
    payload = json.loads(out.stdout) if capture and out.stdout.strip() else {}
    return dt, payload


def bench(label: str, fn, n: int = N) -> dict:
    times = []
    for _ in range(n):
        t, _ = fn()
        times.append(t)
    return {
        "label": label,
        "n": n,
        "median": statistics.median(times),
        "p90": sorted(times)[max(0, int(0.9 * n) - 1)] if n > 1 else times[0],
        "min": min(times),
        "max": max(times),
    }


def main() -> None:
    # Warm-up: navigate once so the page is loaded.
    run("navigate", PAGE, "--wait", "0.5")

    results = []

    # 1. page-info — cheap baseline; measures CDP connect + a few JS evals
    results.append(bench("page-info", lambda: run("page-info")))

    # 2. js — single Runtime.evaluate
    results.append(bench("js (1+1)", lambda: run("js", "1+1")))

    # 3. navigate — includes default 2s wait
    results.append(bench("navigate (wait=0.5)", lambda: run("navigate", PAGE, "--wait", "0.5")))

    # 4. inspect — the big one
    results.append(bench("inspect", lambda: run("inspect")))

    # We need a snapshot id for the next ones.
    _, snap = run("inspect")
    sid = snap["inspect_id"]

    # 5. query
    results.append(bench("query --role link", lambda: run("query", sid, "--role", "link", "--limit", "5")))

    # 6. act click (re-snapshot each round to avoid stale handles after layout)
    def act_click():
        return run("act", sid, "link:link-0", "click")
    results.append(bench("act click", act_click, n=max(3, N)))

    # 7. act fill — types into #email
    def act_fill():
        return run("act", sid, "textbox:email", "fill", "--text", "hello@world.com")
    results.append(bench("act fill (15 chars)", act_fill, n=max(3, N)))

    # Print results
    print(f"\n## Latency results (n={N}, port={PORT})\n")
    print("| command | median (ms) | p90 (ms) | min | max |")
    print("|---|---:|---:|---:|---:|")
    for r in results:
        print(f"| {r['label']} | {r['median']*1000:.0f} | {r['p90']*1000:.0f} | "
              f"{r['min']*1000:.0f} | {r['max']*1000:.0f} |")


if __name__ == "__main__":
    main()
