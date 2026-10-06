import json
import multiprocessing
import os
import shutil
import time
from pathlib import Path

import httpx
from ..scan import load_image, correct_image, write_pdf
from .store import Store
from .delivery import paperless, results, deliver_folder, deliver_webdav, UncertainDelivery


def update(store, job_id, status, value):
    with store.connect() as db:
        db.execute("UPDATE jobs SET status=?, value=? WHERE id=?", (status, json.dumps(value), job_id))


def process(store, row):
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
    if destination["kind"] == "paperless" and not value.get("task_id") and row["status"] != "uncertain":
        metadata = value["metadata"]
        if metadata.get("document_type"):
            paperless(settings, token, "GET", f"document_types/{metadata['document_type']}/")
    if not pdf.exists() and not value.get("task_id"):
        def pages():
            for page in draft["pages"]:
                yield correct_image(load_image(directory / (page["id"] + ".png"), settings.max_pixels), page["corners"], page.get("rotation", 0), page.get("mode", "color"))
        temporary_pdf = pdf.with_suffix(".pending")
        write_pdf(pages(), temporary_pdf, value["page_size"])
        with temporary_pdf.open("rb") as completed_pdf:
            os.fsync(completed_pdf.fileno())
        os.replace(temporary_pdf, pdf)
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
        # A stable title marker allows reconciliation after a lost POST response.
        marker = f"scandoc-{row['id']}"
        if (row["status"] == "uncertain" or value.get("submission_started")) and not value.get("task_id"):
            candidates = results(paperless(settings, token, "GET", "documents/", params={"title__icontains": marker}))
            exact = [d for d in candidates if marker in d["title"]]
            if len(exact) != 1:
                raise UncertainDelivery("No unique completed document found yet; reconcile again later. No file was resent.")
            value["document_id"] = exact[0]["id"]
        if not value.get("task_id") and not value.get("document_id"):
            metadata = value["metadata"]
            data = {k: str(v) for k, v in metadata.items() if v and k != "tags"}
            data["title"] = f"{str(metadata.get('title') or value['filename'][:-4])[:80]} [{marker}]"
            if metadata.get("tags"):
                data["tags"] = [str(tag) for tag in metadata["tags"]]
            # Commit the uncertain state BEFORE sending to prevent blind POST replay.
            value["submission_started"] = True
            update(store, row["id"], "uncertain", value)
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
        public = settings.paperless_public_url or settings.paperless_url
        value["location"] = public.rstrip("/") + f"/documents/{value['document_id']}/details"
    value.pop("credential", None)
    update(store, row["id"], "delivered", value)
    # Save the delivery receipt before cleanup. Startup also completes interrupted cleanup.
    shutil.rmtree(directory, ignore_errors=True)
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='discarded' WHERE draft=? AND status='ready'", (draft["id"],))
        db.execute("DELETE FROM drafts WHERE id=? AND owner=?", (draft["id"], row["owner"]))


def run(settings, stop):
    store = Store(settings)
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
                process(store, row)
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


def start(settings):
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    process = context.Process(target=run, args=(settings, stop), daemon=True)
    process.start()
    return process, stop
