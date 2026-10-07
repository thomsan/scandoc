import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4
import argparse
import asyncio
import hashlib
import io
import json
import multiprocessing
import re
import secrets
import shutil
import time

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Depends
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
import httpx

from ..scan import load_image, detect_corners, correct_image, validate_corners
from .config import Settings
from .store import Store
from .delivery import paperless, results, validate_destination, client
from . import webdav
from .worker import start
from .naming import export_filename


class Login(BaseModel):
    username: str
    password: str


class DraftInput(BaseModel):
    id: UUID
    title: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class PageEdit(BaseModel):
    corners: list[list[float]]
    rotation: int = 0
    mode: str = "color"


class Export(BaseModel):
    id: UUID
    destination: str = "download"
    filename: str = Field(default="document.pdf", max_length=160)
    page_size: str = "natural"
    metadata: dict = Field(default_factory=dict)


def identifier(value):
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(422, "Invalid identifier")


class WebDAVLogin(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=4096)
    name: str = Field(default='', max_length=64)


class WebDAVPassword(BaseModel):
    password: str = Field(min_length=1, max_length=4096)


class WebDAVFolder(BaseModel):
    path: str = Field(default='/', max_length=2048)
    name: str = Field(min_length=1, max_length=128)
    default: bool = False


def create_app(settings=None):
    settings = settings or Settings()
    settings.prepare()
    store = Store(settings)
    worker = None
    processing_slot = multiprocessing.get_context("spawn").BoundedSemaphore(1)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal worker
        manager = None
        cleaner = None
        if settings.worker_enabled:
            manager = multiprocessing.get_context('spawn').Manager()
            store.webdav_secrets = manager.dict()
            worker = start(settings, processing_slot, store.webdav_secrets)
            async def prune_secrets():
                while True:
                    await run_in_threadpool(store.prune_webdav_sessions)
                    await asyncio.sleep(5)
            cleaner = asyncio.create_task(prune_secrets())
        yield
        if cleaner:
            cleaner.cancel()
            try:
                await cleaner
            except asyncio.CancelledError:
                pass
        if worker:
            worker[1].set()
            await run_in_threadpool(worker[0].join, 30)
            if worker[0].is_alive():
                worker[0].terminate()
                worker[0].join(5)
        if manager:
            manager.shutdown()

    app = FastAPI(title="Scandoc", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    mutation_lock = asyncio.Lock()

    @app.middleware("http")
    async def security(request, call_next):
        expected = urlparse(settings.origin)
        if request.url.path != "/health" and request.url.hostname != expected.hostname:
            return JSONResponse({"detail": "Untrusted host"}, 400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != settings.origin:
                return JSONResponse({"detail": "Untrusted origin"}, 403)
            if settings.hosted and not origin:
                return JSONResponse({"detail": "Origin required"}, 403)
            async with mutation_lock:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path in ("/docs", "/redoc"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def user(request: Request):
        if not settings.hosted:
            return {"owner": "local", "username": "Local workspace", "admin": True, "csrf": "local", "session_id": "local"}
        session_id = request.cookies.get("scandoc_session", "")
        session = store.session(hashlib.sha256(session_id.encode()).hexdigest())
        if not session:
            raise HTTPException(401, "Please sign in")
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            raise HTTPException(403, "Invalid CSRF token")
        return {**session, "session_id": hashlib.sha256(session_id.encode()).hexdigest()}

    def admin(current=Depends(user)):
        if not current["admin"]:
            raise HTTPException(403, "Administrator permission required")
        if settings.hosted:
            profile = paperless(settings, current["token"], "GET", "ui_settings/")["user"]
            if not profile.get("is_superuser", False):
                raise HTTPException(403, "Paperless administrator permission required")
        return current

    def draft_for(draft_id, current, writable=False):
        draft_id = identifier(draft_id)
        draft = store.draft(draft_id, current["owner"])
        if not draft:
            raise HTTPException(404, "Draft not found")
        if writable:
            with store.connect() as db:
                active = db.execute("SELECT 1 FROM jobs WHERE draft=? AND status IN ('queued','processing','waiting','uncertain','reconciling')", (draft_id,)).fetchone()
            if active:
                raise HTTPException(409, "Finish or reconcile delivery before changing this draft")
        return draft

    def page_for(draft, page_id):
        page = next((p for p in draft["pages"] if p["id"] == identifier(page_id)), None)
        if not page:
            raise HTTPException(404, "Page not found")
        return page, settings.data_dir / "drafts" / draft["id"] / (page["id"] + ".png")

    def public_job(job):
        return {k: v for k, v in job.items() if k not in ("credential", "request_hash", "webdav_session")}

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request, exc):
        return JSONResponse({"detail": "Paperless or storage is unavailable or rejected the request"}, 502)

    @contextmanager
    def image_processing():
        # Share the memory budget with the durable worker, keeping status requests free.
        while not processing_slot.acquire(timeout=1):
            if worker and not worker[0].is_alive():
                raise HTTPException(503, "Processing worker stopped; restart the service to resume")
        try:
            yield
        finally:
            from .memory import release_image_memory
            release_image_memory()
            processing_slot.release()

    @app.get("/health")
    def health():
        with store.connect() as db:
            db.execute("SELECT 1")
        if worker and not worker[0].is_alive():
            raise HTTPException(503, "Processing worker stopped")
        return {"status": "ok"}

    @app.get("/api/v1/session")
    def session(current=Depends(user)):
        return {k: current[k] for k in ("owner", "username", "admin", "csrf")}

    @app.post("/api/v1/session")
    def login(body: Login, response: Response):
        if not settings.hosted:
            raise HTTPException(409, "Standalone mode does not require login")
        try:
            token = paperless(settings, None, "POST", "token/", json=body.model_dump())["token"]
            profile = paperless(settings, token, "GET", "ui_settings/")["user"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise HTTPException(429, "Too many sign-in attempts. Wait before trying again.", headers={"Retry-After": exc.response.headers.get("Retry-After", "60")})
            raise HTTPException(401, "Invalid credentials or missing Paperless access")
        current = {"owner": str(profile["id"]), "username": profile["username"], "admin": bool(profile.get("is_superuser", False)), "token": token, "csrf": secrets.token_urlsafe(32)}
        session_id = secrets.token_urlsafe(48)
        with store.connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(session_id.encode()).hexdigest(), current["owner"], store.encrypt(current), time.time()+settings.session_hours*3600))
        response.set_cookie("scandoc_session", session_id, httponly=True, secure=True, samesite="strict", max_age=settings.session_hours*3600)
        return {k: current[k] for k in ("owner", "username", "admin", "csrf")}

    @app.delete("/api/v1/session")
    def logout(request: Request, response: Response, current=Depends(user)):
        with store.connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (hashlib.sha256(request.cookies.get("scandoc_session", "").encode()).hexdigest(),))
        store.forget_webdav_session(current["owner"], current["session_id"])
        response.delete_cookie("scandoc_session", secure=True, httponly=True, samesite="strict")
        return {"status": "signed out"}

    @app.get("/api/v1/drafts")
    def list_drafts(current=Depends(user)):
        with store.connect() as db:
            return [json.loads(r["value"]) for r in db.execute("SELECT value FROM drafts WHERE owner=? AND id NOT IN (SELECT id FROM deleted_documents)", (current["owner"],))]

    @app.post("/api/v1/drafts")
    def create_draft(body: DraftInput, current=Depends(user)):
        draft_id = str(body.id)
        with store.connect() as db:
            existing = db.execute("SELECT owner FROM drafts WHERE id=?", (draft_id,)).fetchone()
            deleted = db.execute('SELECT 1 FROM deleted_documents WHERE id=?', (draft_id,)).fetchone()
        if existing and existing["owner"] != current["owner"]:
            raise HTTPException(409, "Identifier unavailable")
        if deleted:
            raise HTTPException(409, "This document was deleted")
        draft = store.draft(draft_id, current["owner"]) or {"id": draft_id, "title": body.title, "note": body.note or body.title, "pages": [], "created": time.time()}
        (settings.data_dir / "drafts" / draft_id).mkdir(parents=True, exist_ok=True, mode=0o700)
        store.save_draft(draft, current["owner"])
        return draft

    @app.get("/api/v1/drafts/{draft_id}")
    def get_draft(draft_id: str, current=Depends(user)):
        return draft_for(draft_id, current)

    @app.patch("/api/v1/drafts/{draft_id}")
    def edit_draft(draft_id: str, body: dict, current=Depends(user)):
        draft = draft_for(draft_id, current, True)
        allowed = {"note", "title", "filename", "type", "created", "tags", "correspondent", "destination", "pageSize"}
        if "note" in body and (not isinstance(body["note"], str) or len(body["note"]) > 2000):
            raise ValueError("Note must be text of at most 2000 characters")
        for key, value in body.items():
            if key in allowed:
                draft[key] = value
        store.save_draft(draft, current["owner"])
        return draft

    @app.delete("/api/v1/drafts/{draft_id}")
    def delete_draft(draft_id: str, current=Depends(user)):
        draft = draft_for(draft_id, current, True)
        store.delete_documents([draft['id']], current['owner'])
        return {"status": "deleted"}

    @app.get('/api/v1/history')
    def history(current=Depends(user)):
        return [{**entry, 'jobs':[public_job(job) for job in entry['jobs']]} for entry in store.history(current['owner'])]

    @app.delete('/api/v1/history')
    def delete_history(current=Depends(user)):
        ids = [d['id'] for d in store.history(current['owner'])]
        try:
            store.delete_documents(ids, current['owner'])
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {'status':'deleted', 'ids':ids}

    @app.delete('/api/v1/history/{document_id}')
    def delete_history_item(document_id: str, current=Depends(user)):
        document_id = identifier(document_id)
        if document_id not in {d['id'] for d in store.history(current['owner'])}:
            raise HTTPException(404, 'History item not found')
        try:
            store.delete_documents([document_id], current['owner'])
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {'status':'deleted', 'ids':[document_id]}

    @app.put("/api/v1/drafts/{draft_id}/pages/{page_id}")
    def upload_page(draft_id: str, page_id: str, file: UploadFile = File(), current=Depends(user)):
        draft = draft_for(draft_id, current, True)
        page_id = identifier(page_id)
        old = next((p for p in draft["pages"] if p["id"] == page_id), None)
        if old:
            return old
        if len(draft["pages"]) >= settings.max_pages:
            raise HTTPException(413, "Document page limit reached")
        with image_processing():
            # Leave waiting uploads in their spooled files, rather than retaining
            # one maximum-size byte buffer for every concurrent request.
            draft = draft_for(draft_id, current, True)
            old = next((p for p in draft["pages"] if p["id"] == page_id), None)
            if old:
                return old
            if len(draft["pages"]) >= settings.max_pages:
                raise HTTPException(413, "Document page limit reached")
            raw = file.file.read(settings.max_image_bytes+1)
            if len(raw) > settings.max_image_bytes or sum(p["bytes"] for p in draft["pages"]) + len(raw) > settings.max_document_bytes:
                raise HTTPException(413, "Image or document byte limit exceeded")
            with load_image(io.BytesIO(raw), settings.max_pixels, allowed_formats={"JPEG", "PNG", "WEBP", "TIFF"}) as image:
                page = {"id": page_id, "width": image.width, "height": image.height, "corners": detect_corners(image), "rotation": 0, "mode": "color", "bytes": len(raw)}
                path = settings.data_dir / "drafts" / draft["id"] / (page_id + ".png")
                image.save(path)
            draft["pages"].append(page)
            store.save_draft(draft, current["owner"])
        return page

    @app.get("/api/v1/drafts/{draft_id}/pages/{page_id}/source")
    def source(draft_id: str, page_id: str, current=Depends(user)):
        _, path = page_for(draft_for(draft_id, current), page_id)
        return retained_file(path, "image/png")

    @app.patch("/api/v1/drafts/{draft_id}/pages/{page_id}")
    def edit_page(draft_id: str, page_id: str, body: PageEdit, current=Depends(user)):
        draft = draft_for(draft_id, current, True)
        page, _ = page_for(draft, page_id)
        validate_corners(body.corners, page["width"], page["height"])
        if body.rotation not in (0, 90, 180, 270) or body.mode not in ("color", "grayscale", "document"):
            raise ValueError("Invalid rotation or mode")
        page.update(body.model_dump())
        store.save_draft(draft, current["owner"])
        return page

    @app.post("/api/v1/drafts/{draft_id}/pages/{page_id}/detect")
    def detect(draft_id: str, page_id: str, current=Depends(user)):
        draft = draft_for(draft_id, current, True)
        page, path = page_for(draft, page_id)
        with image_processing(), load_image(path, settings.max_pixels) as image:
            page["corners"] = detect_corners(image)
        store.save_draft(draft, current["owner"])
        return page

    @app.post("/api/v1/drafts/{draft_id}/pages/{page_id}/preview")
    def preview(draft_id: str, page_id: str, body: PageEdit, current=Depends(user)):
        page, path = page_for(draft_for(draft_id, current), page_id)
        with image_processing(), load_image(path, settings.max_pixels) as original:
            with correct_image(original, body.corners, body.rotation, body.mode) as image:
                image.thumbnail((1600, 1600))
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=88)
        return Response(buffer.getvalue(), media_type="image/jpeg")

    @app.put("/api/v1/drafts/{draft_id}/order")
    def order(draft_id: str, page_ids: list[str], current=Depends(user)):
        draft = draft_for(draft_id, current, True)
        if len(page_ids) != len(draft["pages"]) or set(page_ids) != {p["id"] for p in draft["pages"]}:
            raise ValueError("Page order must include each page exactly once")
        by_id = {p["id"]: p for p in draft["pages"]}
        draft["pages"] = [by_id[p] for p in page_ids]
        store.save_draft(draft, current["owner"])
        return draft

    @app.delete("/api/v1/drafts/{draft_id}/pages/{page_id}")
    def remove_page(draft_id: str, page_id: str, current=Depends(user)):
        draft = draft_for(draft_id, current, True)
        page, path = page_for(draft, page_id)
        draft["pages"].remove(page)
        store.save_draft(draft, current["owner"])
        path.unlink(missing_ok=True)
        return draft

    @app.post("/api/v1/drafts/{draft_id}/jobs")
    def export(draft_id: str, body: Export, current=Depends(user)):
        job_id = str(body.id)
        request_hash = hashlib.sha256((draft_id + body.model_dump_json()).encode()).hexdigest()
        previous = store.job(job_id, current["owner"])
        if previous:
            if previous["request_hash"] != request_hash:
                raise HTTPException(409, "Delivery identifier was used for a different request")
            return public_job(previous)
        draft = draft_for(draft_id, current, True)
        if not draft["pages"]:
            raise ValueError("Add at least one page")
        if body.destination not in store.destinations(current["owner"], current["session_id"]):
            raise ValueError("Unknown destination")
        destination = store.destinations(current['owner'], current['session_id'])[body.destination]
        if destination['kind'] == 'webdav' and not destination.get('connected'):
            raise HTTPException(409, 'Sign in to your WebDAV connection before uploading')
        if body.page_size not in ("natural", "a4") or not re.fullmatch(r"[\w .()-]+\.pdf", body.filename) or body.filename.startswith("."):
            raise ValueError("Choose a safe PDF filename and page size")
        allowed = {"description", "note", "title", "created", "document_type", "tags", "correspondent"}
        if "description" in body.metadata:
            description = body.metadata["description"]
            if not isinstance(description, str) or len(description) > 128:
                raise ValueError("Description must be text of at most 128 characters")
            if store.destinations(current["owner"], current["session_id"])[body.destination]["kind"] == "paperless":
                if not body.metadata.get("document_type"):
                    raise ValueError("Choose a document type")
                body.filename = "document.pdf"
            else:
                body.filename = export_filename(body.metadata.get("created"), description)
        if "note" in body.metadata and (not isinstance(body.metadata["note"], str) or len(body.metadata["note"]) > 2000):
            raise ValueError("Note must be text of at most 2000 characters")
        if set(body.metadata) - allowed:
            raise ValueError("Unsupported metadata field")
        for key in ("document_type", "correspondent"):
            if body.metadata.get(key) is not None and (type(body.metadata[key]) is not int or body.metadata[key] < 1):
                raise ValueError("Metadata identifiers must be positive integers")
        if body.metadata.get("tags") and (not isinstance(body.metadata["tags"], list) or any(type(t) is not int or t < 1 for t in body.metadata["tags"])):
            raise ValueError("Tags must be positive integer identifiers")
        value = {**body.model_dump(mode="json"), "credential": store.encrypt({"token": current.get("token") if destination["kind"] == "paperless" else None}), "request_hash": request_hash}
        if destination['kind'] == 'webdav':
            value['webdav_session'] = current['session_id']
        with store.connect() as db:
            try:
                db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (job_id, current["owner"], draft["id"], "queued", json.dumps(value)))
            except Exception:
                raise HTTPException(409, "Delivery identifier unavailable")
        return {"id": job_id, "draft_id": draft["id"], "status": "queued"}

    @app.get("/api/v1/jobs")
    def jobs(current=Depends(user)):
        with store.connect() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM jobs WHERE owner=? AND draft NOT IN (SELECT id FROM deleted_documents) ORDER BY rowid DESC LIMIT 100", (current["owner"],))]
        return [public_job(value) for i in ids if (value := store.job(i, current["owner"]))]

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str, current=Depends(user)):
        value = store.job(identifier(job_id), current["owner"])
        if not value:
            raise HTTPException(404, "Delivery not found")
        return public_job(value)

    @app.post("/api/v1/jobs/{job_id}/retry")
    def retry(job_id: str, current=Depends(user)):
        job_id = identifier(job_id)
        value = store.job(job_id, current["owner"])
        if not value or value["status"] not in ("failed", "uncertain"):
            raise HTTPException(409, "Delivery is not retryable")
        status = "reconciling" if value.pop("status") == "uncertain" else "queued"
        value.pop("id", None)
        value.pop("error", None)
        if value.pop("ingestion_failed", False):
            value.pop("task_id", None)
            value.pop("submission_started", None)
        destination = store.destinations(current['owner'], current['session_id']).get(value['destination'])
        if destination and destination['kind'] == 'webdav':
            if not destination.get('connected'):
                raise HTTPException(409, 'Sign in to your WebDAV connection before retrying')
            value['webdav_session'] = current['session_id']
        value["credential"] = store.encrypt({"token": current.get("token") if destination and destination['kind'] == 'paperless' else None})
        with store.connect() as db:
            db.execute("UPDATE jobs SET status=?,value=? WHERE id=?", (status, json.dumps(value), job_id))
        return {"id": job_id, "status": status}

    @app.get("/api/v1/jobs/{job_id}/download")
    def download(job_id: str, current=Depends(user)):
        value = store.job(identifier(job_id), current["owner"])
        if not value or value["status"] != "ready":
            raise HTTPException(404, "PDF not ready")
        return retained_file(settings.data_dir / "drafts" / value['draft_id'] / (job_id + ".pdf"), "application/pdf", value["filename"])

    def retained_file(path, media_type, filename=None):
        # Open before returning the response: a simultaneous explicit deletion
        # must not unlink the file between FileResponse's stat and open calls.
        try:
            source = path.open('rb')
        except FileNotFoundError:
            raise HTTPException(404, "Document file was deleted")
        headers = dict(FileResponse(path, filename=filename, media_type=media_type).headers)
        headers['Content-Length'] = str(os.fstat(source.fileno()).st_size)
        def chunks():
            with source:
                yield from iter(lambda: source.read(1024 * 1024), b'')
        return StreamingResponse(chunks(), headers=headers, media_type=media_type, background=BackgroundTask(source.close))

    @app.get("/api/v1/document-types")
    def types(current=Depends(user)):
        if not settings.paperless_url:
            return []
        return results(paperless(settings, current["token"], "GET", "document_types/", params={"page_size": 1000}))

    @app.get("/api/v1/metadata")
    def metadata(current=Depends(user)):
        if not settings.paperless_url:
            return {"tags": [], "correspondents": []}
        return {kind: results(paperless(settings, current["token"], "GET", kind+"/", params={"page_size": 1000})) for kind in ("tags", "correspondents")}

    @app.post("/api/v1/document-types")
    def new_type(body: dict, current=Depends(admin)):
        name = str(body.get("name", "")).strip()
        if not name or len(name) > 128:
            raise ValueError("Choose a document type name")
        payload = {"name": name, "matching_algorithm": 0}
        if settings.company_group_id:
            payload["set_permissions"] = {"view": {"groups": [settings.company_group_id]}, "change": {"groups": [settings.company_group_id]}}
        else:
            groups = results(paperless(settings, current["token"], "GET", "groups/", params={"page_size": 1000}))
            group = next((g for g in groups if g["name"] == "Company"), None)
            if not group:
                raise ValueError("Bootstrap the Company group before creating document types")
            payload["set_permissions"] = {"view": {"groups": [group["id"]]}, "change": {"groups": [group["id"]]}}
        return paperless(settings, current["token"], "POST", "document_types/", json=payload)

    def account_for(account_id, current):
        account_id = identifier(account_id)
        account = store.webdav_accounts(current['owner']).get(account_id)
        if not account:
            raise HTTPException(404, 'WebDAV connection not found')
        return account_id, account

    def public_account(account_id, account):
        return {'id': account_id, 'name': account['name'], 'url': account['url'], 'username': account['username']}

    def login_limit(current):
        if not store.webdav_login_allowed(current['owner']):
            raise HTTPException(429, 'Too many WebDAV login attempts; wait one minute')

    @app.get('/api/v1/webdav/accounts')
    def webdav_accounts(current=Depends(user)):
        return [{**public_account(key, account), 'connected': bool(store.webdav_password(current['owner'], current['session_id'], key))} for key, account in store.webdav_accounts(current['owner']).items()]

    @app.post('/api/v1/webdav/accounts')
    def connect_webdav(body: WebDAVLogin, current=Depends(user)):
        login_limit(current)
        if len(store.webdav_accounts(current['owner'])) >= 10:
            raise ValueError('Disconnect an unused WebDAV account before adding another')
        account = webdav.login(settings, body.url, body.username, body.password)
        account['name'] = body.name.strip() or urlparse(account['url']).hostname
        password = account.pop('password')
        account_id = next((key for key, value in store.webdav_accounts(current['owner']).items() if value['url'] == account['url'] and value['username'] == account['username']), str(uuid4()))
        with store.connect() as db:
            db.execute('INSERT OR REPLACE INTO webdav_accounts VALUES(?,?,?)', (account_id, current['owner'], store.encrypt(account)))
        store.remember_webdav_session(current['owner'], current['session_id'], account_id, password)
        return {**public_account(account_id, account), 'connected': True}

    @app.put('/api/v1/webdav/accounts/{account_id}/password')
    def update_webdav_password(account_id: str, body: WebDAVPassword, current=Depends(user)):
        account_id, account = account_for(account_id, current)
        login_limit(current)
        updated = webdav.login(settings, account['url'], account['username'], body.password)
        store.remember_webdav_session(current['owner'], current['session_id'], account_id, updated['password'])
        return {**public_account(account_id, account), 'connected': True}

    @app.get('/api/v1/webdav/accounts/{account_id}/folders')
    def webdav_folders(account_id: str, path: str = '/', current=Depends(user)):
        account_id, account = account_for(account_id, current)
        password = store.webdav_password(current['owner'], current['session_id'], account_id)
        if not password:
            raise HTTPException(409, 'Sign in to your WebDAV connection to browse folders')
        return webdav.browse(settings, {**account, 'password': password}, path)

    @app.post('/api/v1/webdav/accounts/{account_id}/destinations')
    def select_webdav_folder(account_id: str, body: WebDAVFolder, current=Depends(user)):
        account_id, account = account_for(account_id, current)
        path = webdav.clean_path(body.path)
        password = store.webdav_password(current['owner'], current['session_id'], account_id)
        if not password:
            raise HTTPException(409, 'Sign in to your WebDAV connection to choose a folder')
        webdav.browse(settings, {**account, 'password': password}, path, depth='0')
        existing = store.personal_destinations(current['owner'])
        destination_id = next((key for key, value in existing.items() if value['account_id'] == account_id and value['path'] == path and value['name'] == body.name.strip()), 'webdav-' + str(uuid4()))
        if len(existing) >= 30 and destination_id not in existing:
            raise ValueError('Remove an unused destination before adding another')
        destination = {'id': destination_id, 'kind': 'webdav', 'name': body.name.strip(),
                       'account_id': account_id, 'path': path, 'readonly': False, 'personal': True, 'default': body.default}
        if not destination['name']:
            raise ValueError('Enter a destination name')
        with store.connect() as db:
            if body.default:
                for key, value in existing.items():
                    value['default'] = False
                    db.execute('UPDATE user_destinations SET value=? WHERE id=? AND owner=?', (store.encrypt(value), key, current['owner']))
            db.execute('INSERT OR REPLACE INTO user_destinations VALUES(?,?,?)', (destination_id, current['owner'], store.encrypt(destination)))
        return public_destination(destination)

    def no_active_destination(db, ids, current):
        for destination_id in ids:
            for row in db.execute("SELECT value FROM jobs WHERE owner=? AND status IN ('queued','processing','waiting','uncertain','reconciling')", (current['owner'],)):
                if json.loads(row['value']).get('destination') == destination_id:
                    raise HTTPException(409, 'Finish or reconcile active uploads before disconnecting')

    @app.delete('/api/v1/webdav/accounts/{account_id}')
    def disconnect_webdav(account_id: str, current=Depends(user)):
        account_id, _ = account_for(account_id, current)
        ids = [key for key, value in store.personal_destinations(current['owner']).items() if value['account_id'] == account_id]
        with store.connect() as db:
            no_active_destination(db, ids, current)
            for key in ids:
                db.execute('DELETE FROM user_destinations WHERE id=? AND owner=?', (key, current['owner']))
            db.execute('DELETE FROM webdav_accounts WHERE id=? AND owner=?', (account_id, current['owner']))
        store.forget_webdav_session(current['owner'], account_id=account_id)
        return {'status': 'disconnected'}

    @app.delete('/api/v1/webdav/destinations/{destination_id}')
    def delete_personal_destination(destination_id: str, current=Depends(user)):
        if destination_id not in store.personal_destinations(current['owner']):
            raise HTTPException(404, 'Destination not found')
        with store.connect() as db:
            no_active_destination(db, [destination_id], current)
            db.execute('DELETE FROM user_destinations WHERE id=? AND owner=?', (destination_id, current['owner']))
        return {'status': 'deleted'}

    def public_destination(destination, administrator=False):
        keys = ("id", "name", "kind", "readonly", "default", "personal", "connected") + (("root", "url", "username") if administrator else ())
        return {k: destination[k] for k in keys if k in destination}

    @app.get("/api/v1/destinations")
    def destinations(current=Depends(user)):
        entries = sorted(store.destinations(current["owner"], current["session_id"]).values(), key=lambda d: (not (d.get("personal") and d.get("default")), not d.get("readonly", False), not d.get("default", False)))
        return [public_destination(d) for d in entries]

    @app.get("/api/v1/admin/destinations")
    def admin_destinations(current=Depends(admin)):
        return [public_destination(d, True) for d in store.destinations().values()]

    @app.put("/api/v1/admin/destinations/{destination_id}")
    def save_destination(destination_id: str, body: dict, current=Depends(admin)):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", destination_id):
            raise ValueError("Invalid destination identifier")
        if destination_id in settings.configured_destinations():
            raise HTTPException(409, "File/environment destination is read-only")
        old = store.destinations().get(destination_id, {})
        destination = {**old, **{k: v for k, v in body.items() if k in ("name", "kind", "root", "url", "username", "password", "default")}, "id": destination_id, "readonly": False}
        if not body.get("password") and old.get("password"):
            destination["password"] = old["password"]
        if destination.get('kind') == 'webdav':
            raise ValueError('Configure WebDAV in your personal connections')
        validate_destination(destination)
        with store.connect() as db:
            if destination.get("default"):
                for row in db.execute("SELECT * FROM destinations").fetchall():
                    value = store.decrypt(row["value"])
                    if row["id"] != destination_id and value.get("default"):
                        value["default"] = False
                        db.execute("UPDATE destinations SET value=? WHERE id=?", (store.encrypt(value), row["id"]))
            db.execute("INSERT OR REPLACE INTO destinations VALUES(?,?)", (destination_id, store.encrypt(destination)))
        return public_destination(destination, True)

    @app.delete("/api/v1/admin/destinations/{destination_id}")
    def remove_destination(destination_id: str, current=Depends(admin)):
        if destination_id in settings.configured_destinations():
            raise HTTPException(409, "Configured destination is read-only")
        with store.connect() as db:
            db.execute("DELETE FROM destinations WHERE id=?", (destination_id,))
        return {"status": "deleted"}

    @app.post("/api/v1/admin/destinations/{destination_id}/test")
    def test_destination(destination_id: str, current=Depends(admin)):
        destination = store.destinations().get(destination_id)
        if not destination:
            raise HTTPException(404, "Destination not found")
        if destination["kind"] == "folder":
            import os
            root = Path(destination["root"])
            if not root.is_dir() or not os.access(root, os.W_OK):
                raise ValueError("Destination folder must exist and be writable")
        elif destination["kind"] == "webdav":
            with client(settings) as connection:
                response = connection.request("PROPFIND", destination["url"], auth=(destination.get("username", ""), destination.get("password", "")), headers={"Depth": "0"})
                response.raise_for_status()
        elif destination["kind"] == "paperless":
            paperless(settings, current["token"], "GET", "profile/")
        return {"status": "ok"}

    static = Path(__file__).parent / "static"

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        target = (static / path).resolve()
        if not target.is_relative_to(static.resolve()):
            raise HTTPException(404)
        if target.is_file():
            return FileResponse(target, headers={"Cache-Control": "no-cache"} if path in ("sw.js", "manifest.webmanifest") else None)
        if path.startswith("api/") or not (static / "index.html").exists():
            raise HTTPException(404, "Build the frontend first: npm run build")
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def serve(arguments=None):
    parser = argparse.ArgumentParser(prog="scandoc serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(arguments)
    settings = Settings()
    if not settings.hosted and args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Network binding requires SCANDOC_HOSTED=true with authentication configured")
    if not settings.hosted:
        settings.origin = f"http://{args.host}:{args.port}"
    import uvicorn
    uvicorn.run(create_app(settings), host=args.host, port=args.port)
