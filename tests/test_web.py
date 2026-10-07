import hashlib
import io
import json
import sqlite3
import time
from uuid import uuid4
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from scandoc.web.app import create_app
from scandoc.web.config import Settings
from scandoc.web.worker import process
from scandoc.web.delivery import deliver_folder, validate_destination


@pytest.fixture
def application(tmp_path):
    return create_app(Settings(data_dir=tmp_path,origin="http://testserver",worker_enabled=False))


@pytest.fixture
def client(application):
    return TestClient(application)


def make_draft(client):
    draft=str(uuid4());page=str(uuid4())
    assert client.post('/api/v1/drafts',json={'id':draft}).status_code==200
    raw=io.BytesIO();Image.new('RGB',(100,150),'white').save(raw,'PNG')
    result=client.put(f'/api/v1/drafts/{draft}/pages/{page}',files={'file':('x.png',raw.getvalue(),'image/png')})
    assert result.status_code==200,result.text
    return draft,page,result.json()


def run_job(application,job_id):
    store=application.state.store
    with store.connect() as connection:row=connection.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    process(store,row)


def test_upload_edit_preview_pdf_idempotency(application,client):
    draft,page,uploaded=make_draft(client)
    edit={'corners':uploaded['corners'],'rotation':90,'mode':'grayscale'}
    assert client.patch(f'/api/v1/drafts/{draft}/pages/{page}',json=edit).status_code==200
    assert client.post(f'/api/v1/drafts/{draft}/pages/{page}/preview',json=edit).headers['content-type']=='image/jpeg'
    job=str(uuid4());body={'id':job,'destination':'download','filename':'receipt.pdf'}
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).status_code==200
    assert client.delete(f'/api/v1/drafts/{draft}').status_code==409
    run_job(application,job)
    assert client.get(f'/api/v1/jobs/{job}/download').content.startswith(b'%PDF')
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).json()['status']=='ready'
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json={**body,'filename':'different.pdf'}).status_code==409
    assert client.get(f'/api/v1/drafts/{draft}').status_code==200
    assert client.delete(f'/api/v1/drafts/{draft}').status_code==200
    assert client.get(f'/api/v1/jobs/{job}/download').status_code==404


def test_destination_export_requirements_and_generated_name(application, client):
    draft, _, _ = make_draft(client)
    job = str(uuid4())
    body = {'id':job, 'destination':'download', 'filename':'ignored.pdf',
            'metadata':{'description':'Extension cables / workshop'}}
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).status_code == 422
    body['metadata']['created'] = '2026-02-30'
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).status_code == 422
    body['metadata']['created'] = '2026-10-06'
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).status_code == 200
    run_job(application, job)
    result = client.get(f'/api/v1/jobs/{job}').json()
    assert result['filename'] == '2026-10-06 Extension cables - workshop.pdf'
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).json()['status'] == 'ready'
    assert '2026-10-06' in client.get(f'/api/v1/jobs/{job}/download').headers['content-disposition']


def test_description_folder_name_and_collision_preserve_draft(application, client, tmp_path):
    root = tmp_path/'output'
    client.put('/api/v1/admin/destinations/archive',json={'kind':'folder','name':'Archive','root':str(root)}).raise_for_status()
    draft, _, _ = make_draft(client)
    job = str(uuid4())
    body = {'id':job,'destination':'archive','metadata':{'description':'Cables','created':'2026-10-06'}}
    client.post(f'/api/v1/drafts/{draft}/jobs',json=body).raise_for_status()
    run_job(application,job)
    target = root/'2026-10-06 Cables.pdf'
    assert target.read_bytes().startswith(b'%PDF')
    draft, _, _ = make_draft(client)
    job = str(uuid4())
    target.write_bytes(b'existing different document')
    client.post(f'/api/v1/drafts/{draft}/jobs',json={**body,'id':job}).raise_for_status()
    with pytest.raises(ValueError, match='different file'):
        run_job(application,job)
    assert client.get(f'/api/v1/drafts/{draft}').status_code == 200
    assert target.read_bytes() == b'existing different document'


def test_folder_delivery_retains_sources_download_and_repeated_request(application,client,tmp_path):
    destination={'kind':'folder','name':'Archive','root':str(tmp_path/'output')}
    assert client.put('/api/v1/admin/destinations/archive',json=destination).status_code==200
    draft,page,_=make_draft(client)
    download_job=str(uuid4())
    client.post(f'/api/v1/drafts/{draft}/jobs',json={'id':download_job,'destination':'download'});run_job(application,download_job)
    job=str(uuid4());body={'id':job,'destination':'archive'}
    client.post(f'/api/v1/drafts/{draft}/jobs',json=body);run_job(application,job)
    assert client.get(f'/api/v1/jobs/{job}').json()['status']=='delivered'
    assert client.get(f'/api/v1/drafts/{draft}').json()['archived'] is True
    assert (tmp_path/'drafts'/draft).exists()
    assert client.get(f'/api/v1/jobs/{download_job}/download').content.startswith(b'%PDF')
    history = client.get('/api/v1/history').json()
    assert len(history) == 1 and len(history[0]['jobs']) == 2
    assert history[0]['available'] is True
    assert len(list((tmp_path/'output').glob('*.pdf')))==1
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).json()['status']=='delivered'
    assert client.post('/api/v1/drafts',json={'id':draft}).status_code==200
    assert client.delete(f'/api/v1/history/{draft}').status_code == 200
    assert not (tmp_path/'drafts'/draft).exists()
    assert len(list((tmp_path/'output').glob('*.pdf'))) == 1
    assert client.post('/api/v1/drafts',json={'id':draft}).status_code == 409


def test_secrets_redacted_encrypted_and_readonly(application,client):
    destination={'kind':'webdav','name':'Cloud','url':'https://cloud.test/collection','username':'team','password':'very-secret'}
    assert client.put('/api/v1/admin/destinations/cloud',json=destination).status_code==200
    assert 'very-secret' not in client.get('/api/v1/destinations').text
    assert 'very-secret' not in client.get('/api/v1/admin/destinations').text
    assert b'very-secret' not in application.state.store.path.read_bytes()
    assert client.put('/api/v1/admin/destinations/download',json=destination).status_code==409
    assert client.put('/api/v1/admin/destinations/cloud',json={**destination,'url':'http://cloud.test/'}).status_code==422


def test_limits_malformed_and_host_protection(application,client):
    assert client.post('/api/v1/drafts',json={'id':'../../oops'}).status_code==422
    assert client.post('/api/v1/drafts',json={'id':str(uuid4())},headers={'origin':'https://evil.test'}).status_code==403
    assert client.get('/api/v1/session',headers={'host':'evil.test'}).status_code==400
    application.state.settings.max_image_bytes=10
    draft=str(uuid4());client.post('/api/v1/drafts',json={'id':draft})
    assert client.put(f'/api/v1/drafts/{draft}/pages/{uuid4()}',files={'file':('x',b'x'*11)}).status_code==413


def test_page_order_removal_invalid_corners(client):
    draft,page,uploaded=make_draft(client)
    assert client.put(f'/api/v1/drafts/{draft}/order',json=[page,page]).status_code==422
    assert client.patch(f'/api/v1/drafts/{draft}/pages/{page}',json={'corners':[[0,0]]*4}).status_code==422
    assert client.delete(f'/api/v1/drafts/{draft}/pages/{page}').json()['pages']==[]


def test_hosted_security_and_account_isolation(tmp_path,monkeypatch):
    import scandoc.web.app as module
    def upstream(settings,token,method,path,**kwargs):
        if path=='token/':return {'token':kwargs['json']['username']+'-secret-token'}
        return {'user':{'id':1 if token.startswith('alice') else 2,'username':token.split('-')[0],'is_superuser':token.startswith('alice')}}
    monkeypatch.setattr(module,'paperless',upstream)
    app=create_app(Settings(data_dir=tmp_path,hosted=True,origin='https://testserver',paperless_url='http://paperless',encryption_key=Fernet.generate_key().decode(),worker_enabled=False))
    alice=TestClient(app,base_url='https://testserver');bob=TestClient(app,base_url='https://testserver')
    assert alice.get('/api/v1/session').status_code==401
    for connection,name in ((alice,'alice'),(bob,'bob')):
        result=connection.post('/api/v1/session',json={'username':name,'password':'secret'},headers={'origin':'https://testserver'})
        assert result.status_code==200
        assert 'secret-token' not in result.text
        assert 'HttpOnly' in result.headers['set-cookie'] and 'Secure' in result.headers['set-cookie']
        connection.headers.update({'origin':'https://testserver','x-csrf-token':result.json()['csrf']})
    draft,page,_=make_draft(alice)
    assert bob.get(f'/api/v1/drafts/{draft}').status_code==404
    assert bob.get('/api/v1/admin/destinations').status_code==403
    assert alice.post('/api/v1/drafts',json={'id':str(uuid4())},headers={'x-csrf-token':'wrong'}).status_code==403
    job = str(uuid4())
    alice.post(f'/api/v1/drafts/{draft}/jobs', json={'id':job,'destination':'download'}).raise_for_status()
    run_job(app, job)
    assert alice.get('/api/v1/history').json()[0]['id'] == draft
    assert bob.get('/api/v1/history').json() == []
    assert bob.delete(f'/api/v1/history/{draft}').status_code == 404
    assert bob.delete('/api/v1/history').json()['ids'] == []
    assert alice.get(f'/api/v1/jobs/{job}/download').status_code == 200
    assert b'alice-secret-token' not in app.state.store.path.read_bytes()
    assert alice.delete('/api/v1/session').status_code==200
    assert alice.get('/api/v1/session').status_code==401


def test_missing_authentication_fails_closed(tmp_path):
    with pytest.raises(ValueError):create_app(Settings(data_dir=tmp_path,hosted=True))


def test_destination_no_overwrite(tmp_path):
    pdf=tmp_path/'source.pdf';pdf.write_bytes(b'first');destination={'root':str(tmp_path/'output')}
    deliver_folder(destination,pdf,'stable.pdf');deliver_folder(destination,pdf,'stable.pdf')
    pdf.write_bytes(b'second')
    with pytest.raises(ValueError):deliver_folder(destination,pdf,'stable.pdf')
    assert (tmp_path/'output/stable.pdf').read_bytes()==b'first'


def test_destination_defaults_and_file_override(application, client, tmp_path):
    for name in ('first', 'second'):
        response = client.put('/api/v1/admin/destinations/'+name, json={'kind':'folder','name':name,'root':str(tmp_path/name),'default':True})
        assert response.status_code == 200
    defaults = [d['id'] for d in client.get('/api/v1/destinations').json() if d.get('default')]
    assert defaults == ['second']
    config = tmp_path/'destinations.json'
    config.write_text(json.dumps({'destinations':[{'id':'managed','kind':'folder','name':'Managed','root':str(tmp_path/'managed'),'default':True}]}))
    application.state.settings.config_file = config
    entries = client.get('/api/v1/destinations').json()
    assert next(d['id'] for d in entries if d.get('default')) == 'managed'
    assert client.put('/api/v1/admin/destinations/managed', json={'name':'Change'}).status_code == 409


def test_upstream_login_rate_limit_is_actionable(tmp_path, monkeypatch):
    import httpx
    import scandoc.web.app as module
    def throttled(*args, **kwargs):
        response = httpx.Response(429, headers={'Retry-After':'30'},request=httpx.Request('POST','http://paperless/api/token/'))
        response.raise_for_status()
    monkeypatch.setattr(module,'paperless',throttled)
    app=create_app(Settings(data_dir=tmp_path,hosted=True,origin='https://testserver',paperless_url='http://paperless',encryption_key=Fernet.generate_key().decode(),worker_enabled=False))
    client=TestClient(app,base_url='https://testserver')
    response=client.post('/api/v1/session',json={'username':'alice','password':'secret'},headers={'Origin':'https://testserver'})
    assert response.status_code==429
    assert response.headers['Retry-After']=='30'
    assert 'Wait' in response.json()['detail']


@pytest.mark.parametrize('format',['GIF','BMP'])
def test_web_rejects_unsupported_image_formats(client,format):
    draft=str(uuid4());client.post('/api/v1/drafts',json={'id':draft})
    raw=io.BytesIO();Image.new('RGB',(100,150)).save(raw,format)
    result=client.put(f'/api/v1/drafts/{draft}/pages/{uuid4()}',files={'file':('disguised.png',raw.getvalue(),'image/png')})
    assert result.status_code == 422
    assert 'Unsupported image' in result.json()['detail']
    assert not client.get(f'/api/v1/drafts/{draft}').json()['pages']


def test_page_and_document_limits_preserve_draft(application,client):
    draft,_,page=make_draft(client)
    application.state.settings.max_pages=1
    raw=io.BytesIO();Image.new('RGB',(100,150)).save(raw,'PNG')
    url=f'/api/v1/drafts/{draft}/pages/{uuid4()}'
    assert client.put(url,files={'file':('receipt.png',raw.getvalue())}).status_code==413
    application.state.settings.max_pages=20
    application.state.settings.max_document_bytes=page['bytes']
    assert client.put(url,files={'file':('receipt.png',raw.getvalue())}).status_code==413
    assert len(client.get(f'/api/v1/drafts/{draft}').json()['pages'])==1


def test_history_bulk_delete_preserves_drafts_and_refuses_active_exports(application, client, tmp_path):
    finished = []
    for _ in range(2):
        draft, _, _ = make_draft(client)
        job = str(uuid4())
        client.post(f'/api/v1/drafts/{draft}/jobs', json={'id':job,'destination':'download'}).raise_for_status()
        run_job(application, job)
        finished.append(draft)
    unfinished, _, _ = make_draft(client)
    pending = str(uuid4())
    client.post(f'/api/v1/drafts/{finished[0]}/jobs', json={'id':pending,'destination':'download'}).raise_for_status()
    assert client.delete('/api/v1/history').status_code == 409
    assert all((tmp_path/'drafts'/item).exists() for item in finished)
    run_job(application, pending)
    result = client.delete('/api/v1/history')
    assert result.status_code == 200
    assert set(result.json()['ids']) == set(finished)
    assert client.get('/api/v1/history').json() == []
    assert all(not (tmp_path/'drafts'/item).exists() for item in finished)
    assert client.get(f'/api/v1/drafts/{unfinished}').status_code == 200
    for item in finished:
        assert client.post('/api/v1/drafts', json={'id':item}).status_code == 409


def test_pending_manual_deletion_recovers_on_restart(application, client, tmp_path, monkeypatch):
    import scandoc.web.store as module
    draft, _, _ = make_draft(client)
    job = str(uuid4())
    client.post(f'/api/v1/drafts/{draft}/jobs', json={'id':job,'destination':'download'}).raise_for_status()
    run_job(application, job)
    original = module.shutil.rmtree
    def interrupted(*args, **kwargs):
        raise OSError('Deletion interrupted')
    monkeypatch.setattr(module.shutil, 'rmtree', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        application.state.store.delete_documents([draft], 'local')
    assert application.state.store.draft(draft, 'local') is None
    assert client.post('/api/v1/drafts', json={'id':draft}).status_code == 409
    monkeypatch.setattr(module.shutil, 'rmtree', original)
    recovered = module.Store(application.state.settings)
    assert not (tmp_path/'drafts'/draft).exists()
    assert recovered.job(job, 'local') is None
    assert recovered.history('local') == []


def test_history_is_account_scoped_and_redacts_job_credentials(application, client):
    draft, _, _ = make_draft(client)
    job = str(uuid4())
    client.post(f'/api/v1/drafts/{draft}/jobs', json={'id':job,'destination':'download'}).raise_for_status()
    run_job(application, job)
    store = application.state.store
    with store.connect() as connection:
        value = json.loads(connection.execute('SELECT value FROM jobs WHERE id=?', (job,)).fetchone()['value'])
        value['credential'] = store.encrypt('private-token')
        connection.execute('UPDATE jobs SET value=? WHERE id=?', (json.dumps(value), job))
    assert 'credential' not in client.get('/api/v1/history').text
    assert 'private-token' not in client.get('/api/v1/history').text
    assert store.history('another-account') == []
    with pytest.raises(ValueError, match='not found'):
        store.delete_documents([draft], 'another-account')
    assert store.draft(draft, 'local') is not None
