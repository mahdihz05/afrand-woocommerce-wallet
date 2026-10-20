"""A minimal SQLite idempotency ledger for order-write requests."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Ledger:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS operations (
                    operation_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            db.execute("CREATE TABLE IF NOT EXISTS import_drafts (draft_id TEXT PRIMARY KEY, rows_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")

    def save_draft(self, draft_id: str, rows: list[dict]) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM import_drafts WHERE created_at < datetime('now', '-1 day')")
            db.execute("INSERT INTO import_drafts(draft_id, rows_json) VALUES(?, ?)", (draft_id, json.dumps(rows, ensure_ascii=False)))

    def get_draft(self, draft_id: str) -> list[dict] | None:
        with self._connect() as db:
            row = db.execute("SELECT rows_json FROM import_drafts WHERE draft_id=? AND created_at >= datetime('now', '-1 day')", (draft_id,)).fetchone()
        return json.loads(row["rows_json"]) if row else None

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def phone_mutation_lock(self):
        """OS lock shared by panel threads/processes using the same ledger.

        Process death releases the lock, not uncertain operations/reservations.
        """
        with open(self.path + ".phone-lock", "a+b") as lock:
            if os.name == "nt":
                import msvcrt

                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"0")
                    lock.flush()
                while True:
                    lock.seek(0)
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def reserve_phone_update(self, phone: str):
        """Called under phone_mutation_lock before a potentially uncertain PUT."""
        with self._connect() as db:
            db.execute(
                "INSERT INTO operations(operation_key,kind,state) VALUES(?,?,?) "
                "ON CONFLICT(operation_key) DO UPDATE SET state='processing', result_json=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE operations.state='succeeded'",
                ("phone-update-" + phone, "phone_update", "processing"),
            )

    def claim(self, operation_key: str, kind: str, legacy_key: str | None = None,
              order_ref: str | None = None) -> tuple[str, dict | None, str | None]:
        """Return claimed, replay, or blocked. Never reclaims an uncertain write."""
        with self._connect() as db:
            # Atomic legacy check/new claim, without migration or reset.
            db.execute("BEGIN IMMEDIATE")
            if legacy_key:
                legacy = db.execute(
                    "SELECT kind,state,result_json FROM operations WHERE operation_key=?",
                    (legacy_key,),
                ).fetchone()
                if legacy:
                    saved = json.loads(legacy["result_json"] or "{}")
                    if (legacy["kind"] == kind and legacy["state"] == "succeeded"
                            and saved.get("order_ref") == order_ref and saved.get("order_id")):
                        return "replay", saved, "succeeded"
                    return "blocked", None, legacy["state"]
            cursor = db.execute(
                "INSERT OR IGNORE INTO operations(operation_key,kind,state) VALUES(?,?,?)",
                (operation_key, kind, "processing"),
            )
            row = db.execute(
                "SELECT kind,state,result_json FROM operations WHERE operation_key=?",
                (operation_key,),
            ).fetchone()
            if cursor.rowcount == 1:
                return "claimed", None, None
            if row["kind"] != kind:
                return "blocked", None, row["state"]
            if row["state"] == "rejected":
                db.execute("UPDATE operations SET state='processing', updated_at=CURRENT_TIMESTAMP WHERE operation_key=?", (operation_key,))
                return "claimed", None, None
            if row["state"] == "succeeded":
                return "replay", json.loads(row["result_json"] or "{}"), row["state"]
            return "blocked", None, row["state"]

    def lookup(self, operation_key: str) -> tuple[str | None, dict | None]:
        """Read a prior operation without claiming a new key."""
        with self._connect() as db:
            row = db.execute(
                "SELECT state,result_json FROM operations WHERE operation_key=?",
                (operation_key,),
            ).fetchone()
        if row is None:
            return None, None
        result = json.loads(row["result_json"] or "{}") if row["result_json"] else None
        return row["state"], result

    def finish(self, operation_key: str, state: str, result: dict | None = None) -> None:
        if state not in {"succeeded", "unknown", "rejected"}:
            raise ValueError("unsupported operation state")
        serialized = json.dumps(result, ensure_ascii=False) if result is not None else None
        with self._connect() as db:
            db.execute(
                "UPDATE operations SET state=?, result_json=?, updated_at=CURRENT_TIMESTAMP WHERE operation_key=?",
                (state, serialized, operation_key),
            )
