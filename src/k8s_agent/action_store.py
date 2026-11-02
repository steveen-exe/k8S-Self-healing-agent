"""Durable action state for approval and remediation workflows."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ActionStore:
    """SQLite-backed store with atomic action state transitions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    actor TEXT,
                    error TEXT
                )
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, action_id: str, payload: dict[str, Any]) -> None:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO actions
                    (action_id, status, payload_json, created_at, updated_at)
                VALUES (?, 'pending', ?, ?, ?)
                """,
                (action_id, json.dumps(payload, default=str), now, now),
            )

    def list_by_status(self, status: str) -> dict[str, dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT action_id, payload_json FROM actions WHERE status = ?",
                (status,),
            ).fetchall()
        return {
            row["action_id"]: json.loads(row["payload_json"])
            for row in rows
        }

    def transition(
        self,
        action_id: str,
        expected_status: str,
        new_status: str,
        *,
        actor: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically transition an action and return its payload."""
        now = self._now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM actions WHERE action_id = ? AND status = ?",
                (action_id, expected_status),
            ).fetchone()
            if row is None:
                return None

            connection.execute(
                """
                UPDATE actions
                   SET status = ?, updated_at = ?, actor = ?, error = ?
                 WHERE action_id = ? AND status = ?
                """,
                (new_status, now, actor, error, action_id, expected_status),
            )
            return json.loads(row["payload_json"])

    def status(self, action_id: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        return row["status"] if row else None
