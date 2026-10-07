import json
import sqlite3
import time
import shutil
from uuid import UUID
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
                CREATE INDEX IF NOT EXISTS jobs_owner_draft ON jobs(owner,draft);
                CREATE TABLE IF NOT EXISTS deleted_documents(id TEXT PRIMARY KEY, owner TEXT, pending INTEGER NOT NULL);
            """)
        self.finish_deletions()
        # Upgrade existing completed downloads to retained History entries.
        with self.connect() as db:
            ids = db.execute("SELECT DISTINCT draft,owner FROM jobs WHERE status IN ('ready','delivered')").fetchall()
        for row in ids:
            self.mark_archived(row['draft'], row['owner'])

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
            if db.execute('SELECT 1 FROM deleted_documents WHERE id=?', (draft_id,)).fetchone():
                return None
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
        return {**json.loads(row["value"]), "id": row["id"], "draft_id": row['draft'], "status": row["status"]} if row else None

    def mark_archived(self, draft_id, owner):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM drafts WHERE id=? AND owner=?', (draft_id, owner)).fetchone()
            if row:
                value = json.loads(row['value'])
                value['archived'] = True
                db.execute('UPDATE drafts SET value=? WHERE id=? AND owner=?', (json.dumps(value), draft_id, owner))

    def history(self, owner):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE owner=? AND draft NOT IN (SELECT id FROM deleted_documents) ORDER BY rowid DESC", (owner,)).fetchall()
            drafts = {r['id']: json.loads(r['value']) for r in db.execute('SELECT * FROM drafts WHERE owner=?', (owner,))}
        documents = {}
        for row in rows:
            draft_id = row['draft']
            if draft_id not in documents:
                draft = drafts.get(draft_id)
                documents[draft_id] = {'id': draft_id, 'description': draft.get('note', '') if draft else '',
                                       'available': bool(draft), 'jobs': []}
            documents[draft_id]['jobs'].append({**json.loads(row['value']), 'id': row['id'], 'draft_id': draft_id, 'status': row['status']})
        return [d for d in documents.values() if any(j['status'] in ('ready', 'delivered') for j in d['jobs'])]

    def delete_documents(self, ids, owner):
        """Authorize the complete set before deleting anything; resume after crashes."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for draft_id in ids:
                UUID(draft_id)
                owned = db.execute('SELECT 1 FROM drafts WHERE id=? AND owner=? UNION SELECT 1 FROM jobs WHERE draft=? AND owner=? UNION SELECT 1 FROM deleted_documents WHERE id=? AND owner=?',
                                   (draft_id,owner,draft_id,owner,draft_id,owner)).fetchone()
                if not owned:
                    raise ValueError('Document not found')
                active = db.execute("SELECT 1 FROM jobs WHERE draft=? AND status IN ('queued','processing','waiting','uncertain','reconciling')", (draft_id,)).fetchone()
                if active:
                    raise ValueError('Finish or reconcile active deliveries before deleting history')
            for draft_id in ids:
                db.execute('INSERT INTO deleted_documents VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET pending=1', (draft_id,owner))
        self.finish_deletions(ids)

    def finish_deletions(self, ids=None):
        with self.connect() as db:
            pending = db.execute('SELECT * FROM deleted_documents WHERE pending=1').fetchall()
        for row in pending:
            if ids is not None and row['id'] not in ids:
                continue
            directory = self.settings.data_dir/'drafts'/str(UUID(row['id']))
            try:
                shutil.rmtree(directory)
            except FileNotFoundError:
                pass
            with self.connect() as db:
                db.execute('DELETE FROM jobs WHERE draft=? AND owner=?', (row['id'], row['owner']))
                db.execute('DELETE FROM drafts WHERE id=? AND owner=?', (row['id'], row['owner']))
                db.execute('UPDATE deleted_documents SET pending=0 WHERE id=?', (row['id'],))
