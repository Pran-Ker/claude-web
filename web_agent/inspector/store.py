"""On-disk snapshot store. Snapshots are immutable; ids autoincrement.

Layout::

    .snapshots/
        index.json        # {"next_id": 8, "latest": "s7"}
        s1.json
        s2.json
        ...
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import SnapshotNotFound

DEFAULT_DIR = Path(".snapshots")


class SnapshotStore:
    def __init__(self, root: Path | str = DEFAULT_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"

    def _read_index(self) -> dict:
        if not self._index_path.exists():
            return {"next_id": 1, "latest": None}
        return json.loads(self._index_path.read_text())

    def _write_index(self, idx: dict) -> None:
        self._index_path.write_text(json.dumps(idx, indent=2))

    def save(self, snapshot: dict) -> str:
        idx = self._read_index()
        sid = f"s{idx['next_id']}"
        idx["next_id"] += 1
        idx["latest"] = sid
        snapshot["id"] = sid
        (self.root / f"{sid}.json").write_text(json.dumps(snapshot, indent=2))
        self._write_index(idx)
        return sid

    def load(self, sid: str) -> dict:
        path = self.root / f"{sid}.json"
        if not path.exists():
            raise SnapshotNotFound(
                f"No snapshot named {sid!r}.",
                hint="Run `inspect` to create a fresh snapshot, then use the returned id.",
            )
        return json.loads(path.read_text())

    def latest(self) -> str | None:
        return self._read_index().get("latest")
