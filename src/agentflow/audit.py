"""Tamper-evident audit log.

Every record stores the SHA-256 of the previous record, so editing or deleting
any line breaks the chain and `verify()` reports exactly where.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

GENESIS = "0" * 64


def _digest(prev: str, body: dict) -> str:
    payload = prev + json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last = self._load_last()

    def _load_last(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = GENESIS
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def record(self, event: str, **fields) -> dict:
        body = {"ts": round(time.time(), 3), "event": event, **fields}
        entry = {**body, "prev": self._last, "hash": _digest(self._last, body)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        self._last = entry["hash"]
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def verify(self) -> tuple[bool, int | None]:
        """Return (ok, index_of_first_bad_record)."""
        prev = GENESIS
        for i, e in enumerate(self.entries()):
            body = {k: v for k, v in e.items() if k not in ("prev", "hash")}
            if e["prev"] != prev or e["hash"] != _digest(prev, body):
                return False, i
            prev = e["hash"]
        return True, None
