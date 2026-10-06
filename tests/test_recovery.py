"""Exercise durable retries and failures without discarding source documents."""
import errno
import io
import json
import threading
import time
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from scandoc.web.app import create_app
from scandoc.web.config import Settings
from scandoc.web import worker
from scandoc.web.delivery import UncertainDelivery, deliver_folder


def queued(tmp_path, destination='download'):
    app = create_app(Settings(data_dir=tmp_path, origin='http://testserver', worker_enabled=False))
    client = TestClient(app)
    draft, page, job = (str(uuid4()) for _ in range(3))
    client.post('/api/v1/drafts', json={'id': draft}).raise_for_status()
    image = io.BytesIO(); Image.new('RGB', (100, 150), 'white').save(image, 'PNG')
    client.put(f'/api/v1/drafts/{draft}/pages/{page}', files={'file': ('receipt.png', image.getvalue())}).raise_for_status()
    if destination == 'paperless':
        app.state.settings.paperless_url = 'http://paperless'
    client.post(f'/api/v1/drafts/{draft}/jobs', json={'id': job, 'destination': destination}).raise_for_status()
    return app, client, draft, job


def run_worker_until(store, job, status):
    stop = threading.Event()
    thread = threading.Thread(target=worker.run, args=(store.settings, stop))
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if store.job(job, 'local')['status'] == status:
                return
            time.sleep(.05)
        raise AssertionError(f'Job did not reach {status}')
    finally:
        stop.set(); thread.join(timeout=5)
    assert not thread.is_alive()


def row(store, job):
    with store.connect() as db:
        return dict(db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone())


def test_interrupted_processing_restarts(tmp_path):
    app, client, draft, job = queued(tmp_path)
    directory = tmp_path / 'drafts' / draft
    leftovers = [directory / (job+'.pending'), directory / ('.'+job+'.pending.abandoned.tmp')]
    for temporary in leftovers:
        temporary.write_bytes(b'%PDF-interrupted')
    with app.state.store.connect() as db:
        db.execute("UPDATE jobs SET status='processing' WHERE id=?", (job,))
    run_worker_until(app.state.store, job, 'ready')
    assert client.get(f'/api/v1/jobs/{job}/download').content.startswith(b'%PDF')
    assert client.get(f'/api/v1/drafts/{draft}').status_code == 200
    assert all(not temporary.exists() for temporary in leftovers)


def test_full_storage_retains_sources_and_retry_recovers(tmp_path, monkeypatch):
    app, client, draft, job = queued(tmp_path)
    original = worker.write_pdf
    def full(*args, **kwargs):
        args[1].write_bytes(b'%PDF-interrupted-write')
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(worker, 'write_pdf', full)
    run_worker_until(app.state.store, job, 'failed')
    assert client.get(f'/api/v1/drafts/{draft}').status_code == 200
    assert list((tmp_path/'drafts'/draft).glob('*.png'))
    monkeypatch.setattr(worker, 'write_pdf', original)
    client.post(f'/api/v1/jobs/{job}/retry').raise_for_status()
    run_worker_until(app.state.store, job, 'ready')
    from pypdf import PdfReader
    assert len(PdfReader(io.BytesIO(client.get(f'/api/v1/jobs/{job}/download').content)).pages) == 1


def test_ambiguous_paperless_post_never_blindly_resends(tmp_path, monkeypatch):
    app, client, draft, job = queued(tmp_path, 'paperless')
    sent = []
    def upstream(settings, token, method, path, **kwargs):
        if method == 'POST':
            sent.append(path)
            raise httpx.ReadTimeout('Response lost after upload')
        return {'results': []}
    monkeypatch.setattr(worker, 'paperless', upstream)
    run_worker_until(app.state.store, job, 'uncertain')
    assert len(sent) == 1
    client.post(f'/api/v1/jobs/{job}/retry').raise_for_status()
    run_worker_until(app.state.store, job, 'uncertain')
    assert len(sent) == 1
    assert client.get(f'/api/v1/drafts/{draft}').status_code == 200
    def reconciled(settings, token, method, path, **kwargs):
        assert method == 'GET'
        return {'results': [{'id': 42, 'title': 'Automatic Paperless title', 'original_file_name': f'document-{job}.pdf'}]}
    monkeypatch.setattr(worker, 'paperless', reconciled)
    client.post(f'/api/v1/jobs/{job}/retry').raise_for_status()
    run_worker_until(app.state.store, job, 'delivered')
    assert len(sent) == 1
    assert client.get(f'/api/v1/drafts/{draft}').status_code == 404


def test_restart_after_submission_marker_reconciles(tmp_path, monkeypatch):
    app, client, draft, job = queued(tmp_path, 'paperless')
    item = row(app.state.store, job)
    value = json.loads(item['value']); value['submission_started'] = True
    worker.update(app.state.store, job, 'processing', value)
    def upstream(settings, token, method, path, **kwargs):
        assert method == 'GET', 'Restart must not repeat an uncertain upload'
        return {'results': [{'id': 42, 'title': 'Automatic Paperless title', 'original_file_name': f'document-{job}.pdf'}]}
    monkeypatch.setattr(worker, 'paperless', upstream)
    run_worker_until(app.state.store, job, 'delivered')


def test_completed_delivery_cleanup_recovers_on_restart(tmp_path):
    app, client, draft, job = queued(tmp_path)
    value = json.loads(row(app.state.store, job)['value'])
    worker.update(app.state.store, job, 'delivered', value)
    run_worker_until(app.state.store, job, 'delivered')
    assert not (tmp_path/'drafts'/draft).exists()
    assert app.state.store.draft(draft, 'local') is None


def test_folder_recovers_abandoned_staging_file(tmp_path):
    source = tmp_path/'source.pdf'; source.write_bytes(b'complete PDF')
    output = tmp_path/'output'; output.mkdir()
    (output/'.receipt.pdf.part').write_bytes(b'interrupted')
    deliver_folder({'root': str(output)}, source, 'receipt.pdf')
    assert (output/'receipt.pdf').read_bytes() == b'complete PDF'


def test_paperless_http_error_after_acceptance_is_uncertain(tmp_path, monkeypatch):
    app, client, draft, job = queued(tmp_path, 'paperless')
    sent=[]
    def upstream(settings, token, method, path, **kwargs):
        if method == 'POST':
            sent.append(path)
            httpx.Response(503,request=httpx.Request('POST','http://paperless/api/documents/post_document/')).raise_for_status()
        return {'results': []}
    monkeypatch.setattr(worker,'paperless',upstream)
    run_worker_until(app.state.store, job, 'uncertain')
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json={'id':str(uuid4()),'destination':'paperless'}).status_code==409
    client.post(f'/api/v1/jobs/{job}/retry').raise_for_status()
    run_worker_until(app.state.store, job, 'uncertain')
    assert len(sent)==1


def test_definite_paperless_rejection_can_retry(tmp_path, monkeypatch):
    app, client, draft, job = queued(tmp_path, 'paperless')
    sent=[]
    def upstream(settings, token, method, path, **kwargs):
        if method == 'POST':
            sent.append(path)
            if len(sent)==1:
                httpx.Response(401,request=httpx.Request('POST','http://paperless/api/documents/post_document/')).raise_for_status()
            return 'accepted-task'
        return {'results':[{'status':'success','related_document':42}]}
    monkeypatch.setattr(worker,'paperless',upstream)
    run_worker_until(app.state.store, job, 'failed')
    assert not app.state.store.job(job,'local').get('submission_started')
    client.post(f'/api/v1/jobs/{job}/retry').raise_for_status()
    run_worker_until(app.state.store, job, 'delivered')
    assert len(sent)==2


def test_lost_note_response_reconciles_without_resending_document(tmp_path, monkeypatch):
    app, client, draft, job = queued(tmp_path, 'paperless')
    item = row(app.state.store, job)
    value = json.loads(item['value'])
    value['metadata'] = {'note': 'Supplies for the summer event'}
    worker.update(app.state.store, job, 'queued', value)
    notes = []
    uploads = []
    writes = []
    def upstream(settings, token, method, path, **kwargs):
        if path == 'documents/post_document/':
            assert 'title' not in kwargs['data'] and 'created' not in kwargs['data']
            uploads.append(path)
            return 'ingestion-task'
        if path == 'tasks/':
            return [{'status': 'SUCCESS', 'related_document': 42}]
        if path == 'documents/42/notes/':
            if method == 'POST':
                writes.append(path)
                notes.append({'note': kwargs['json']['note'], 'user': {'id': 'local'}})
                raise httpx.ReadTimeout('Note saved but response lost')
            return notes
        raise AssertionError(path)
    monkeypatch.setattr(worker, 'paperless', upstream)
    run_worker_until(app.state.store, job, 'uncertain')
    assert client.get(f'/api/v1/drafts/{draft}').status_code == 200
    client.post(f'/api/v1/jobs/{job}/retry').raise_for_status()
    run_worker_until(app.state.store, job, 'delivered')
    assert len(uploads) == len(writes) == len(notes) == 1
    assert client.get(f'/api/v1/drafts/{draft}').status_code == 404
