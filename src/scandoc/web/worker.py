import json
from contextlib import nullcontext
import multiprocessing
import os
import shutil
import time
from pathlib import Path

import httpx
from ..scan import load_image, correct_image, write_pdf
from .store import Store
from .memory import release_image_memory
from .delivery import paperless, results, deliver_folder, deliver_webdav, UncertainDelivery


def update(store, job_id, status, value):
    with store.connect() as db:
        db.execute("UPDATE jobs SET status=?, value=? WHERE id=?", (status, json.dumps(value), job_id))


def process(store, row, processing_slot=None):
    settings = store.settings
    value = json.loads(row["value"])
    draft = store.draft(row["draft"], row["owner"])
    if not draft:
        raise ValueError("Draft no longer exists")
    directory = settings.data_dir / "drafts" / draft["id"]
    pdf = directory / (row["id"] + ".pdf")
    destination = store.destinations().get(value["destination"])
    if not destination:
        raise ValueError("Destination no longer exists")
    credential = store.decrypt(value["credential"])
    token = credential.get("token")
    if destination["kind"] == "paperless" and not value.get("task_id") and not value.get("document_id") and row["status"] != "uncertain":
        metadata = value["metadata"]
        if metadata.get("document_type"):
            paperless(settings, token, "GET", f"document_types/{metadata['document_type']}/")
    if not pdf.exists() and not value.get("task_id"):
        value["phase"] = "pdf"
        update(store, row["id"], "processing", value)
        started = time.monotonic()
        def pages():
            for page in draft["pages"]:
                yield correct_image(load_image(directory / (page["id"] + ".png"), settings.max_pixels), page["corners"], page.get("rotation", 0), page.get("mode", "color"))
        temporary_pdf = pdf.with_suffix(".pending")
        with processing_slot if processing_slot is not None else nullcontext():
            try:
                write_pdf(pages(), temporary_pdf, value["page_size"], max_bytes=settings.max_document_bytes)
            finally:
                release_image_memory()
        with temporary_pdf.open("rb") as completed_pdf:
            os.fsync(completed_pdf.fileno())
        os.replace(temporary_pdf, pdf)
        value.setdefault("timings", {})["pdf_seconds"] = round(time.monotonic()-started, 3)
    filename = value["filename"][:-4] + "-" + row["id"] + ".pdf"
    if destination["kind"] == "download":
        value["download"] = f"/api/v1/jobs/{row['id']}/download"
        value.pop("credential", None)
        update(store, row["id"], "ready", value)
        return
    if destination["kind"] == "folder":
        value["location"] = deliver_folder(destination, pdf, filename)
    elif destination["kind"] == "webdav":
        value["location"] = deliver_webdav(settings, destination, pdf, filename)
    elif destination["kind"] == "paperless":
        # Original filenames remain stable when Paperless workflows rename titles.
        if (row["status"] == "uncertain" or value.get("submission_started")) and not value.get("task_id") and not value.get("document_id"):
            candidates = results(paperless(settings, token, "GET", "documents/", params={"original_filename__iexact": filename}))
            exact = [d for d in candidates if d.get("original_file_name", "").casefold() == filename.casefold()]
            if len(exact) != 1:
                raise UncertainDelivery("No unique completed document found yet; reconcile again later. No file was resent.")
            value["document_id"] = exact[0]["id"]
        if not value.get("task_id") and not value.get("document_id"):
            metadata = value["metadata"]
            # Paperless extracts the date and owns title formatting. Old clients'
            # title values become notes instead of overriding archive metadata.
            data = {k: str(v) for k, v in metadata.items() if v and k in ("document_type", "correspondent")}
            if metadata.get("tags"):
                data["tags"] = [str(tag) for tag in metadata["tags"]]
            # Commit the uncertain state BEFORE sending to prevent blind POST replay.
            value["submission_started"] = True
            value["phase"] = "upload"
            update(store, row["id"], "uncertain", value)
            started = time.monotonic()
            try:
                with pdf.open("rb") as file:
                    value["task_id"] = paperless(settings, token, "POST", "documents/post_document/", data=data, files={"document": (filename, file, "application/pdf")})
            except httpx.TransportError as exc:
                raise UncertainDelivery("Paperless response lost; use Reconcile to find the document") from exc
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500 and exc.response.status_code != 408:
                    value.pop("submission_started", None)
                    update(store, row["id"], "failed", value)
                    raise
                raise UncertainDelivery("Paperless upload outcome is uncertain; reconcile before resending") from exc
            value.setdefault("timings", {})["upload_seconds"] = round(time.monotonic()-started, 3)
            value["ingestion_started_at"] = time.time()
            value["phase"] = "ocr"
            update(store, row["id"], "waiting", value)
        if value.get("task_id") and not value.get("document_id"):
            tasks = results(paperless(settings, token, "GET", "tasks/", params={"task_id": value["task_id"]}))
            if not tasks:
                raise UncertainDelivery("Paperless task is unavailable; reconcile the document before resending")
            task = tasks[0]
            if task["status"].lower() in ("failure", "revoked"):
                value["ingestion_failed"] = True
                update(store, row["id"], "failed", value)
                raise ValueError("Paperless ingestion failed: " + str(task.get("result", "Check Paperless tasks")))
            if task["status"].lower() != "success":
                update(store, row["id"], "waiting", value)
                return
            value["document_id"] = task.get("related_document") or next(iter(task.get("related_document_ids", [])), None)
            if not value["document_id"]:
                raise UncertainDelivery("Paperless completed without a document identifier")
            if value.get("ingestion_started_at"):
                value.setdefault("timings", {})["paperless_seconds"] = round(time.time()-value["ingestion_started_at"], 3)
        note = str(value["metadata"].get("note", value["metadata"].get("title", "")) or "").strip()
        if note:
            # Persist the document ID before adding a note: restart/retry must
            # finish metadata delivery without uploading a second document.
            value["phase"] = "note"
            update(store, row["id"], "waiting", value)
            path = f"documents/{value['document_id']}/notes/"
            def contains_note(notes):
                return any(n.get("note") == note and str((n.get("user") or {}).get("id")) == str(row["owner"]) for n in notes)
            try:
                notes = paperless(settings, token, "GET", path)
                if not contains_note(notes):
                    notes = paperless(settings, token, "POST", path, json={"note": note})
                if not contains_note(notes):
                    raise UncertainDelivery("Paperless note was not confirmed; reconcile before retrying")
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                raise UncertainDelivery("Document ingested; note delivery needs reconciliation. No PDF will be resent.") from exc
        public = settings.paperless_public_url or settings.paperless_url
        value["location"] = public.rstrip("/") + f"/documents/{value['document_id']}/details"
    value.pop("credential", None)
    update(store, row["id"], "delivered", value)
    # Save the delivery receipt before cleanup. Startup also completes interrupted cleanup.
    shutil.rmtree(directory, ignore_errors=True)
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='discarded' WHERE draft=? AND status='ready'", (draft["id"],))
        db.execute("DELETE FROM drafts WHERE id=? AND owner=?", (draft["id"], row["owner"]))


def run(settings, stop, processing_slot=None):
    store = Store(settings)
    # No writer is active before this instance's sole worker starts. Discard
    # interrupted temporary PDFs; committed PDFs and source pages remain intact.
    for pattern in ("*/*.pending", "*/.*.pending.*.tmp"):
        for temporary in (settings.data_dir / "drafts").glob(pattern):
            temporary.unlink(missing_ok=True)
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE status='processing'")
        for row in db.execute("SELECT draft,owner FROM jobs WHERE status='delivered'").fetchall():
            shutil.rmtree(settings.data_dir / "drafts" / row["draft"], ignore_errors=True)
            db.execute("DELETE FROM drafts WHERE id=? AND owner=?", (row["draft"], row["owner"]))
            db.execute("UPDATE jobs SET status='discarded' WHERE draft=? AND status='ready'", (row["draft"],))
    while not stop.is_set():
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status IN ('queued','waiting','reconciling') ORDER BY CASE WHEN status='waiting' THEN 1 ELSE 0 END, rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='processing' WHERE id=?", (row["id"],))
        if row:
            if row["status"] == "reconciling":
                row = dict(row)
                row["status"] = "uncertain"
            try:
                process(store, row, processing_slot)
            except Exception as exc:
                # Read persisted state: it may contain a task ID acquired in this attempt.
                value = store.job(row["id"], row["owner"])
                value.pop("status", None)
                value.pop("id", None)
                uncertain = isinstance(exc, UncertainDelivery) or bool(value.get("submission_started") and not value.get("task_id") and not value.get("ingestion_failed"))
                pending_error = isinstance(exc, httpx.TransportError) or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500)
                if pending_error and value.get("task_id"):
                    update(store, row["id"], "waiting", value)
                else:
                    if isinstance(exc, httpx.HTTPError) and value.get("task_id") and not value.get("ingestion_failed"):
                        uncertain = True
                    if isinstance(exc, httpx.HTTPStatusError):
                        message = f"Destination returned HTTP {exc.response.status_code}; check access and document type"
                    else:
                        message = str(exc)
                    value["error"] = message
                    update(store, row["id"], "uncertain" if uncertain else "failed", value)
        stop.wait(1)


def start(settings, processing_slot=None):
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    process = context.Process(target=run, args=(settings, stop, processing_slot), daemon=True)
    process.start()
    return process, stop
