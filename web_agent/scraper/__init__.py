"""Read-only ingestion: URL → clean markdown, with site crawling and async jobs.

Designed to complement the interactive CDP layer:
- ``fetch``: single URL → markdown, with smart engine selection
- ``crawl``: BFS site crawl, runs in a detached background worker
- ``crawl_status`` / ``crawl_cancel`` / ``crawl_results``

Engine ladder (auto):
1. Jina Reader (free, hosted, returns clean markdown)
2. httpx + trafilatura (fast, free, no JS)
3. CDP browser + trafilatura (slow, free, full JS — uses your existing browser)
"""
