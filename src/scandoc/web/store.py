import json
import sqlite3
import time
import shutil
from uuid import UUID
from contextlib import contextmanager
from cryptography.fernet import Fernet


class Store:
    def __init__(self, settings, webdav_secrets=None):
        self.settings = settings
        self.webdav_secrets = webdav_secrets if webdav_secrets is not None else {}
        self.path = settings.data_dir / "state.sqlite3"
        self.cipher = Fernet(settings.encryption_key.encode())
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, owner TEXT, value TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, owner TEXT, value TEXT);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner TEXT, draft TEXT, status TEXT, value TEXT);
                CREATE TABLE IF NOT EXISTS destinations(id TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS webdav_accounts(id TEXT PRIMARY KEY, owner TEXT NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS user_destinations(id TEXT PRIMARY KEY, owner TEXT NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS webdav_login_attempts(owner TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_owner_draft ON jobs(owner,draft);
                CREATE TABLE IF NOT EXISTS deleted_documents(id TEXT PRIMARY KEY, owner TEXT, pending INTEGER NOT NULL);
            """)
        self.path.chmod(0o600)
        # Shared WebDAV credentials are deliberately retired, never assigned to
        # an arbitrary user. Existing document sources and receipts stay intact.
        with self.connect() as db:
            for row in db.execute('SELECT * FROM destinations').fetchall():
                if self.decrypt(row['value']).get('kind') == 'webdav':
                    db.execute('DELETE FROM destinations WHERE id=?', (row['id'],))
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

    def webdav_accounts(self, owner):
        with self.connect() as db:
            return {r['id']: self.decrypt(r['value']) for r in db.execute('SELECT * FROM webdav_accounts WHERE owner=?', (owner,))}

    def personal_destinations(self, owner):
        with self.connect() as db:
            return {r['id']: self.decrypt(r['value']) for r in db.execute('SELECT * FROM user_destinations WHERE owner=?', (owner,))}

    def remember_webdav_session(self, owner, session_id, account_id, password):
        if owner == 'local':
            expires = time.time() + self.settings.session_hours * 3600
        else:
            with self.connect() as db:
                row = db.execute('SELECT expires FROM sessions WHERE id=? AND owner=? AND expires>?', (session_id, owner, time.time())).fetchone()
            if not row:
                raise ValueError('Scandoc session expired; sign in again')
            expires = row['expires']
        self.webdav_secrets[(owner, session_id, account_id)] = {'password': password, 'expires': expires}

    def webdav_password(self, owner, session_id, account_id):
        key = (owner, session_id, account_id)
        secret = self.webdav_secrets.get(key)
        if not secret:
            return None
        valid = secret['expires'] > time.time()
        if valid and owner != 'local':
            with self.connect() as db:
                valid = bool(db.execute('SELECT 1 FROM sessions WHERE id=? AND owner=? AND expires>?', (session_id, owner, time.time())).fetchone())
        if not valid:
            self.webdav_secrets.pop(key, None)
            return None
        return secret['password']

    def forget_webdav_session(self, owner, session_id=None, account_id=None):
        for key in list(self.webdav_secrets.keys()):
            if key[0] == owner and (session_id is None or key[1] == session_id) and (account_id is None or key[2] == account_id):
                self.webdav_secrets.pop(key, None)

    def prune_webdav_sessions(self):
        for owner, session_id, account_id in list(self.webdav_secrets.keys()):
            self.webdav_password(owner, session_id, account_id)

    def destinations(self, owner=None, session_id=None):
        with self.connect() as db:
            result = {r["id"]: self.decrypt(r["value"]) for r in db.execute("SELECT * FROM destinations")}
        result.update(self.settings.configured_destinations())
        result = {key: value for key, value in result.items() if value.get('kind') != 'webdav'}
        if owner is not None:
            from .webdav import collection_url
            accounts = self.webdav_accounts(owner)
            for key, destination in self.personal_destinations(owner).items():
                account = accounts.get(destination['account_id'])
                if account:
                    password = self.webdav_password(owner, session_id, destination['account_id']) if session_id else None
                    result[key] = {**destination, 'url': collection_url(account['url'], destination['path']),
                                   'username': account['username'], 'password': password, 'connected': bool(password)}
        return result

    def webdav_login_allowed(self, owner):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM webdav_login_attempts WHERE owner=?', (owner,)).fetchone()
            now = time.time()
            if row and now - row['started'] < 60:
                if row['attempts'] >= 10:
                    return False
                db.execute('UPDATE webdav_login_attempts SET attempts=attempts+1 WHERE owner=?', (owner,))
            else:
                db.execute('INSERT OR REPLACE INTO webdav_login_attempts VALUES(?,?,1)', (owner, now))
        return True

    def job(self, job_id, owner):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND draft NOT IN (SELECT id FROM deleted_documents)", (job_id, owner)).fetchone()
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
