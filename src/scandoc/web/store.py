import json
import sqlite3
import time
from contextlib import contextmanager
from cryptography.fernet import Fernet


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir / "state.sqlite3"
        self.cipher = Fernet(settings.encryption_key.encode())
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, owner TEXT, value TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, owner TEXT, value TEXT);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner TEXT, draft TEXT, status TEXT, value TEXT);
                CREATE TABLE IF NOT EXISTS destinations(id TEXT PRIMARY KEY, value TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def encrypt(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))

    def session(self, session_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=? AND expires>?", (session_id, time.time())).fetchone()
        return self.decrypt(row["value"]) if row else None

    def draft(self, draft_id, owner):
        with self.connect() as db:
            row = db.execute("SELECT value FROM drafts WHERE id=? AND owner=?", (draft_id, owner)).fetchone()
        return json.loads(row["value"]) if row else None

    def save_draft(self, draft, owner):
        with self.connect() as db:
            db.execute("INSERT INTO drafts VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value WHERE drafts.owner=excluded.owner", (draft["id"], owner, json.dumps(draft)))

    def destinations(self):
        with self.connect() as db:
            result = {r["id"]: self.decrypt(r["value"]) for r in db.execute("SELECT * FROM destinations")}
        result.update(self.settings.configured_destinations())
        return result

    def job(self, job_id, owner):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        return {**json.loads(row["value"]), "id": row["id"], "status": row["status"]} if row else None
