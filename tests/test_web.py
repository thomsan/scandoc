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


def test_folder_delivery_cleanup_and_repeated_request(application,client,tmp_path):
    destination={'kind':'folder','name':'Archive','root':str(tmp_path/'output')}
    assert client.put('/api/v1/admin/destinations/archive',json=destination).status_code==200
    draft,page,_=make_draft(client)
    download_job=str(uuid4())
    client.post(f'/api/v1/drafts/{draft}/jobs',json={'id':download_job,'destination':'download'});run_job(application,download_job)
    job=str(uuid4());body={'id':job,'destination':'archive'}
    client.post(f'/api/v1/drafts/{draft}/jobs',json=body);run_job(application,job)
    assert client.get(f'/api/v1/jobs/{job}').json()['status']=='delivered'
    assert client.get(f'/api/v1/drafts/{draft}').status_code==404
    assert not (tmp_path/'drafts'/draft).exists()
    assert client.get(f'/api/v1/jobs/{download_job}/download').status_code == 404
    assert len(list((tmp_path/'output').glob('*.pdf')))==1
    assert client.post(f'/api/v1/drafts/{draft}/jobs',json=body).json()['status']=='delivered'
    assert client.post('/api/v1/drafts',json={'id':draft}).status_code==409


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
