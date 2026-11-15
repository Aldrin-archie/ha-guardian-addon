from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class Audit:
    """Append-only JSONL log of every action the agents take or are refused."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def log(self, kind: str, **fields) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, **fields}
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
