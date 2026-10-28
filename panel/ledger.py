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
            db.execute("""CREATE TABLE IF NOT EXISTS wallet_imports (
                draft_id TEXT PRIMARY KEY, preview_json TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("""CREATE TABLE IF NOT EXISTS wallet_import_results (
                draft_id TEXT NOT NULL, row_index INTEGER NOT NULL, result_json TEXT NOT NULL,
                PRIMARY KEY(draft_id,row_index))""")
            db.execute("""CREATE TABLE IF NOT EXISTS wallet_documents (
                operation_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                payload_json TEXT, result_json TEXT)""")

    def save_wallet_import(self, preview):
        with self._connect() as db:
            db.execute("INSERT INTO wallet_imports(draft_id,preview_json) VALUES(?,?)",
                       (preview['draft_id'], json.dumps(preview, ensure_ascii=False)))

    def wallet_import(self, draft_id):
        with self._connect() as db:
            saved = db.execute("SELECT * FROM wallet_imports WHERE draft_id=?", (draft_id,)).fetchone()
            rows = db.execute("SELECT row_index,result_json FROM wallet_import_results WHERE draft_id=? ORDER BY row_index", (draft_id,)).fetchall()
        if not saved:
            return None
        preview = json.loads(saved['preview_json'])
        results = {row['row_index']: json.loads(row['result_json']) for row in rows}
        remaining = [i for i in range(preview['valid_count']) if not results.get(i, {}).get('transaction_id')]
        return dict(preview=preview, confirmed=bool(saved['confirmed']), results=list(results.values()),
                    next_offset=remaining[0] if remaining else preview['valid_count'], done=not remaining)

    def wallet_imports(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT draft_id,confirmed,created_at FROM wallet_imports ORDER BY created_at DESC, rowid DESC LIMIT 30")]

    def confirm_wallet_import(self, draft_id):
        with self._connect() as db:
            db.execute("UPDATE wallet_imports SET confirmed=1 WHERE draft_id=?", (draft_id,))

    def save_wallet_result(self, draft_id, index, result):
        with self._connect() as db:
            db.execute("INSERT INTO wallet_import_results VALUES(?,?,?) ON CONFLICT(draft_id,row_index) DO UPDATE SET result_json=excluded.result_json",
                       (draft_id, index, json.dumps(result, ensure_ascii=False)))

    def bind_wallet_document(self, key, fingerprint):
        """Bind invoice meaning before any remote mutation; never replace it."""
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO wallet_documents(operation_key,fingerprint) VALUES(?,?)", (key, fingerprint))
            row = db.execute("SELECT * FROM wallet_documents WHERE operation_key=?", (key,)).fetchone()
        if row['fingerprint'] != fingerprint:
            raise ValueError('شماره سند قبلاً با اطلاعات متفاوت استفاده شده است.')
        return {name: json.loads(row[name]) if row[name] else None for name in ('payload_json', 'result_json')}

    def save_wallet_document(self, key, payload=None, result=None):
        with self._connect() as db:
            if payload is not None:
                db.execute("UPDATE wallet_documents SET payload_json=? WHERE operation_key=?", (json.dumps(payload, ensure_ascii=False), key))
            if result is not None:
                db.execute("UPDATE wallet_documents SET result_json=? WHERE operation_key=?", (json.dumps(result, ensure_ascii=False), key))

    @contextmanager
    def wallet_document_lock(self, key):
        """Cross-process per-invoice lock; crash releases only the OS lock."""
        folder = Path(self.path + '.wallet-locks')
        folder.mkdir(exist_ok=True)
        import hashlib
        lock_path = folder / hashlib.sha256(key.encode()).hexdigest()
        with open(lock_path, 'a+b') as lock:
            if os.name == 'nt':
                import msvcrt
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b'0'); lock.flush()
                deadline = time.monotonic() + 10
                while True:
                    lock.seek(0)
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('این سند در حال پردازش است؛ نتیجه را دوباره بررسی کنید.')
                        time.sleep(.05)
                try:
                    yield
                finally:
                    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                deadline = time.monotonic() + 10
                while True:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('این سند در حال پردازش است؛ نتیجه را دوباره بررسی کنید.')
                        time.sleep(.05)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

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
