"""SQLite persistence so a paused run survives a process restart."""
from __future__ import annotations

import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, workflow TEXT NOT NULL, status TEXT NOT NULL,
  state TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (
  run_id TEXT NOT NULL, step TEXT NOT NULL, status TEXT NOT NULL,
  reason TEXT, approver TEXT, decided REAL,
  PRIMARY KEY (run_id, step));
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def save_run(self, run_id: str, workflow: str, status: str, state: dict) -> None:
        self.db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
            "status=excluded.status, state=excluded.state, updated=excluded.updated",
            (run_id, workflow, status, json.dumps(state), time.time()))
        self.db.commit()

    def load_run(self, run_id: str) -> tuple[str, str, dict] | None:
        row = self.db.execute(
            "SELECT workflow, status, state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return (row[0], row[1], json.loads(row[2])) if row else None

    def request_approval(self, run_id: str, step: str, reason: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO approvals(run_id, step, status, reason) VALUES (?,?,?,?)",
            (run_id, step, "pending", reason))
        self.db.commit()

    def decide(self, run_id: str, step: str, approved: bool, approver: str) -> None:
        cur = self.db.execute(
            "UPDATE approvals SET status=?, approver=?, decided=? "
            "WHERE run_id=? AND step=? AND status='pending'",
            ("approved" if approved else "rejected", approver, time.time(), run_id, step))
        if cur.rowcount == 0:
            raise LookupError(f"no pending approval for {run_id}/{step}")
        self.db.commit()

    def approval_status(self, run_id: str, step: str) -> str | None:
        row = self.db.execute(
            "SELECT status FROM approvals WHERE run_id=? AND step=?", (run_id, step)).fetchone()
        return row[0] if row else None

    def pending(self) -> list[tuple[str, str, str]]:
        return self.db.execute(
            "SELECT run_id, step, reason FROM approvals WHERE status='pending' "
            "ORDER BY run_id, step").fetchall()
