"""SQLite persistence with atomic operations and no external database server."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


def encode(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, mode TEXT NOT NULL,
                created_at TEXT NOT NULL, payload TEXT NOT NULL, analysis TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS snapshots_mode ON snapshots(mode,created_at);
            CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,
                mode TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,
                PRIMARY KEY(kind,id));
            CREATE INDEX IF NOT EXISTS records_mode ON records(kind,mode,created_at);
            """)
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value, db=None):
        if db is not None:
            db.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))
            return
        with self.connect() as conn:
            self.put(key, value, conn)

    def record(self, kind, value, mode, db=None):
        if db is not None:
            db.execute("INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
                       (kind, value["id"], mode, value["created_at"], encode(value)))
            return
        with self.connect() as conn:
            self.record(kind, value, mode, conn)

    def records(self, kind, mode, limit=10000):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM records WHERE kind=? AND mode=? ORDER BY created_at DESC,id DESC LIMIT ?", (kind, mode, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def snapshot(self, bundle, analysis, id_, created_at):
        with self.connect() as db:
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?)", (id_, bundle["mode"], created_at, encode(bundle), encode(analysis)))
            # Full snapshots are bounded. Frozen plans and signal events are retained separately.
            db.execute("DELETE FROM snapshots WHERE mode=? AND id NOT IN (SELECT id FROM snapshots WHERE mode=? ORDER BY created_at DESC,id DESC LIMIT 200)", (bundle["mode"], bundle["mode"]))

    def latest_snapshot(self, mode):
        with self.connect() as db:
            row = db.execute("SELECT * FROM snapshots WHERE mode=? ORDER BY created_at DESC,id DESC LIMIT 1", (mode,)).fetchone()
        return {"id": row["id"], "bundle": json.loads(row["payload"]), "analysis": json.loads(row["analysis"])} if row else None

    def snapshot_history(self, mode, limit=100):
        with self.connect() as db:
            rows = db.execute("SELECT id,created_at,analysis FROM snapshots WHERE mode=? ORDER BY created_at DESC,id DESC LIMIT ?", (mode, limit)).fetchall()
        return [{"id": r["id"], "created_at": r["created_at"], "analysis": json.loads(r["analysis"])} for r in rows]
