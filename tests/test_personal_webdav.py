"""Personal DAV credentials must stay in session memory, never persisted or shared."""
import io
import json
import socket
import time
from html import escape
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image

from scandoc.web import app as module, webdav, worker
from scandoc.web.config import Settings
from scandoc.web.store import Store

PASSWORD = 'disposable-unit-app-password'


def listing(path, children=()):
    paths = [(path, True), *children]
    return ('<d:multistatus xmlns:d="DAV:">' + ''.join(
        f'<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop><d:resourcetype>'
        + ('<d:collection/>' if folder else '')
        + '</d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
        for href, folder in paths) + '</d:multistatus>').encode()


@pytest.fixture
def personal(tmp_path, monkeypatch):
    calls = []
    def upstream(settings, token, method, path, **kwargs):
        if path == 'token/':
            return {'token': kwargs['json']['username']}
        return {'user': {'id': 1 if token == 'alice' else 2, 'username': token, 'is_superuser': token == 'alice'}}
    monkeypatch.setattr(module, 'paperless', upstream)
    def dav(request):
        calls.append((request.method, request.url.path))
        if request.headers.get('authorization') != 'Basic ' + __import__('base64').b64encode(('owner:'+PASSWORD).encode()).decode():
            return httpx.Response(401)
        if request.method == 'PROPFIND':
            path = request.url.path
            if path not in ('/dav/', '/dav/Receipts/', '/dav/Receipts/2026/'):
                return httpx.Response(404)
            children = [('/dav/Receipts/', True), ('/dav/ignore.pdf', False)] if path == '/dav/' else [('/dav/Receipts/2026/', True)] if path == '/dav/Receipts/' else []
            return httpx.Response(207, content=listing(path, children))
        return httpx.Response(404)
    monkeypatch.setattr(webdav, 'webdav_client', lambda settings: httpx.Client(transport=httpx.MockTransport(dav)))
    app = module.create_app(Settings(data_dir=tmp_path, hosted=True, origin='https://testserver', paperless_url='http://paperless', encryption_key=Fernet.generate_key().decode(), worker_enabled=False))
    def login(name):
        client = TestClient(app, base_url='https://testserver')
        result = client.post('/api/v1/session', json={'username':name, 'password':'scanner-password'}, headers={'Origin':'https://testserver'})
        result.raise_for_status()
        client.headers.update({'Origin':'https://testserver', 'X-CSRF-Token':result.json()['csrf']})
        return client
    return app, login, calls


def connect(client):
    response = client.post('/api/v1/webdav/accounts', json={'url':'https://cloud.test/dav/', 'username':'owner', 'password':PASSWORD, 'name':'My cloud'})
    response.raise_for_status()
    return response.json()['id']


def destination(client, account):
    response = client.post(f'/api/v1/webdav/accounts/{account}/destinations', json={'path':'/Receipts/2026/', 'name':'My receipts', 'default':True})
    response.raise_for_status()
    return response.json()['id']


def upload(client, dest):
    draft, page, job = (str(uuid4()) for _ in range(3))
    client.post('/api/v1/drafts', json={'id':draft}).raise_for_status()
    raw = io.BytesIO(); Image.new('RGB', (100,150), 'white').save(raw, 'PNG')
    client.put(f'/api/v1/drafts/{draft}/pages/{page}', files={'file':('receipt.png',raw.getvalue())}).raise_for_status()
    response = client.post(f'/api/v1/drafts/{draft}/jobs', json={'id':job, 'destination':dest})
    return draft, job, response


def test_login_browse_select_and_account_isolation(personal):
    app, login, calls = personal
    alice, bob = login('alice'), login('bob')
    account = connect(alice)
    folders = alice.get(f'/api/v1/webdav/accounts/{account}/folders').json()
    assert folders['folders'] == [{'name':'Receipts','path':'/Receipts/'}]
    assert alice.get(f'/api/v1/webdav/accounts/{account}/folders',params={'path':'/Receipts/'}).json()['folders'][0]['path'] == '/Receipts/2026/'
    dest = destination(alice, account)
    assert alice.get('/api/v1/destinations').json()[0]['id'] == dest
    assert not any(d['id'] == dest for d in bob.get('/api/v1/destinations').json())
    assert bob.get('/api/v1/webdav/accounts').json() == []
    assert bob.get(f'/api/v1/webdav/accounts/{account}/folders').status_code == 404
    assert bob.put(f'/api/v1/webdav/accounts/{account}/password',json={'password':PASSWORD}).status_code == 404
    assert bob.post(f'/api/v1/webdav/accounts/{account}/destinations',json={'name':'Bad'}).status_code == 404
    assert bob.delete(f'/api/v1/webdav/accounts/{account}').status_code == 404
    assert bob.delete(f'/api/v1/webdav/destinations/{dest}').status_code == 404
    _, _, result = upload(bob, dest)
    assert result.status_code == 422
    assert PASSWORD not in alice.get('/api/v1/webdav/accounts').text
    assert PASSWORD not in alice.get('/api/v1/destinations').text
    assert not any(d['id'] == dest for d in alice.get('/api/v1/admin/destinations').json())


def test_password_never_persisted_and_login_is_scoped_to_exact_session(personal):
    app, login, _ = personal
    alice = login('alice'); account = connect(alice); dest = destination(alice, account)
    for path in app.state.settings.data_dir.iterdir():
        if path.is_file():
            assert PASSWORD.encode() not in path.read_bytes()
    assert all('password' not in metadata for metadata in app.state.store.webdav_accounts('1').values())
    second = login('alice')
    assert second.get('/api/v1/webdav/accounts').json()[0]['connected'] is False
    assert second.get(f'/api/v1/webdav/accounts/{account}/folders').status_code == 409
    _, _, blocked = upload(second, dest)
    assert blocked.status_code == 409
    assert alice.get('/api/v1/webdav/accounts').json()[0]['connected'] is True
    second.put(f'/api/v1/webdav/accounts/{account}/password',json={'password':PASSWORD}).raise_for_status()
    assert second.get('/api/v1/webdav/accounts').json()[0]['connected'] is True
    alice.delete('/api/v1/session').raise_for_status()
    assert len(app.state.store.webdav_secrets) == 1
    assert second.get('/api/v1/webdav/accounts').json()[0]['connected'] is True


def test_session_expiry_and_server_restart_require_reconnection(personal):
    app, login, _ = personal
    alice = login('alice'); account = connect(alice); destination(alice, account)
    recovered = Store(app.state.settings)
    assert len(recovered.webdav_accounts('1')) == 1
    assert len(recovered.personal_destinations('1')) == 1
    assert not recovered.webdav_secrets
    key = next(iter(app.state.store.webdav_secrets))
    with app.state.store.connect() as db:
        db.execute('UPDATE sessions SET expires=? WHERE owner=?', (time.time()-1,'1'))
    app.state.store.prune_webdav_sessions()
    assert not app.state.store.webdav_secrets
    assert alice.get('/api/v1/webdav/accounts').status_code == 401
    fresh = login('alice')
    assert fresh.get('/api/v1/webdav/accounts').json()[0]['connected'] is False
    assert fresh.get(f'/api/v1/webdav/accounts/{account}/folders').status_code == 409
    assert connect(fresh) == account
    assert len(app.state.store.webdav_accounts('1')) == 1


def test_worker_reauthenticates_without_losing_sources_and_disconnect_blocks_active_jobs(personal, monkeypatch):
    app, login, _ = personal
    alice = login('alice'); account = connect(alice); dest = destination(alice, account)
    draft, job, response = upload(alice, dest); response.raise_for_status()
    assert alice.delete(f'/api/v1/webdav/accounts/{account}').status_code == 409
    assert alice.delete(f'/api/v1/webdav/destinations/{dest}').status_code == 409
    with app.state.store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
    recovered = Store(app.state.settings)
    with pytest.raises(ValueError, match='session ended'):
        worker.process(recovered, row)
    assert app.state.store.draft(draft,'1') is not None
    seen = []
    monkeypatch.setattr(worker, 'deliver_webdav', lambda settings, destination, pdf, filename: seen.append(destination['password']) or 'https://cloud.test/dav/delivered.pdf')
    worker.process(app.state.store, row)
    assert seen == [PASSWORD]
    assert app.state.store.job(job,'1')['status'] == 'delivered'
    assert 'webdav_session' not in alice.get('/api/v1/history').text
    alice.delete(f'/api/v1/webdav/accounts/{account}').raise_for_status()
    assert not app.state.store.webdav_secrets
    assert app.state.store.draft(draft,'1')['archived'] is True


def test_bad_login_unverified_folder_and_rate_limit_preserve_configuration(personal):
    app, login, _ = personal; alice = login('alice')
    result = alice.post('/api/v1/webdav/accounts',json={'url':'https://cloud.test/dav/','username':'owner','password':'bad'})
    assert result.status_code == 422 and alice.get('/api/v1/webdav/accounts').json() == []
    account = connect(alice)
    for path in ('/../', '/Receipts/../../', '/missing/', '/Receipts\\bad/'):
        response = alice.post(f'/api/v1/webdav/accounts/{account}/destinations',json={'path':path,'name':'Bad'})
        assert response.status_code == 422
    assert app.state.store.personal_destinations('1') == {}
    for _ in range(9):
        result = alice.put(f'/api/v1/webdav/accounts/{account}/password', json={'password':'bad'})
    assert result.status_code == 429


def test_old_shared_webdav_credentials_are_removed_without_deleting_documents(personal):
    app, login, _ = personal
    alice = login('alice'); draft, _, _ = upload(alice, 'download')
    with app.state.store.connect() as db:
        db.execute('INSERT INTO destinations VALUES(?,?)', ('legacy',app.state.store.encrypt({'kind':'webdav','password':PASSWORD})))
    restarted = Store(app.state.settings)
    assert 'legacy' not in restarted.destinations('1')
    with restarted.connect() as db:
        assert db.execute('SELECT * FROM destinations WHERE id=?',('legacy',)).fetchone() is None
    assert restarted.draft(draft,'1') is not None


@pytest.mark.parametrize('address', ['127.0.0.1','169.254.169.254','::1','::ffff:127.0.0.1','0.0.0.0','224.0.0.1'])
def test_dns_pinning_rejects_sensitive_addresses_before_connecting(monkeypatch, address):
    monkeypatch.setattr(socket,'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET,socket.SOCK_STREAM,0,'',(address,443))])
    with pytest.raises(ValueError, match='prohibited'):
        webdav.WebDAVNetworkBackend().connect_tcp('host.test',443)


def test_dns_resolution_is_pinned_and_lan_connections_are_allowed(monkeypatch):
    from httpcore._backends.sync import SyncBackend
    monkeypatch.setattr(socket,'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET,socket.SOCK_STREAM,0,'',('192.168.178.25',443))])
    seen=[]
    monkeypatch.setattr(SyncBackend,'connect_tcp', lambda self,host,*args: seen.append(host) or object())
    webdav.WebDAVNetworkBackend().connect_tcp('cloud.fritz.box',443)
    assert seen == ['192.168.178.25']


def test_untrusted_xml_and_external_hrefs_cannot_redirect_credentials(monkeypatch):
    account={'url':'https://cloud.test/dav/','username':'owner','password':PASSWORD}
    responses=[b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><d:multistatus xmlns:d="DAV:"/>',
               listing('/dav/', [('https://evil.test/dav/escape/',True),('/dav/../escape/',True),('/dav/real/',True),('/dav/deep/path/',True)])]
    def response(request):
        return httpx.Response(207,content=responses.pop(0))
    monkeypatch.setattr(webdav,'webdav_client',lambda settings:httpx.Client(transport=httpx.MockTransport(response)))
    with pytest.raises(ValueError,match='unsafe'):
        webdav.browse(None, account)
    assert webdav.browse(None,account)['folders'] == [{'name':'real','path':'/real/'}]


def test_folder_listing_is_bounded_and_https_required(monkeypatch):
    for url in ('http://cloud.test/', 'https://user:secret@cloud.test/', 'https://cloud.test/?secret=1','https://cloud.test/#fragment'):
        with pytest.raises(ValueError):webdav.server_url(url)
    monkeypatch.setattr(webdav,'webdav_client',lambda settings:httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(207,content=b'x'*(2*1024*1024+1)))))
    with pytest.raises(ValueError,match='too large'):
        webdav.browse(None,{'url':'https://cloud.test/','username':'owner','password':PASSWORD})
