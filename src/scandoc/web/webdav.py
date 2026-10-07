"""Account-scoped WebDAV discovery with bounded XML and pinned DNS resolution."""
import ipaddress
import socket
import ssl
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from httpcore._backends.sync import SyncBackend
from defusedxml import ElementTree

DAV = '{DAV:}'
PROPERTIES = b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>'


class WebDAVNetworkBackend(SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = list(dict.fromkeys(entry[4][0] for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except OSError:
            raise ValueError('WebDAV server could not be resolved') from None
        for address in addresses:
            ip = ipaddress.ip_address(address)
            ip = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
            if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                raise ValueError('WebDAV server resolves to a prohibited address')
        if not addresses:
            raise ValueError('WebDAV server could not be resolved')
        # Connect to the exact validated address. httpcore still uses the URL's
        # original host for TLS SNI/certificate verification and the Host header.
        for index, address in enumerate(addresses):
            try:
                return super().connect_tcp(address, port, timeout, local_address, socket_options)
            except Exception:
                if index == len(addresses) - 1:
                    raise


def webdav_client(settings):
    verify = ssl.create_default_context()
    if settings.ca_file:
        verify.load_verify_locations(cafile=settings.ca_file)
    transport = httpx.HTTPTransport(verify=verify, retries=0)
    # The installed httpcore/httpx versions are pinned; this backend change keeps
    # their TLS implementation and streaming behavior while preventing rebinding.
    transport._pool._network_backend = WebDAVNetworkBackend()
    return httpx.Client(transport=transport, timeout=httpx.Timeout(20, connect=8), follow_redirects=False, trust_env=False)


def server_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('Enter a valid HTTPS WebDAV server URL')
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('Enter a valid HTTPS WebDAV server URL') from None
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('WebDAV requires HTTPS without embedded credentials, query or fragment')
    # Rebuild paths segment by segment; never reinterpret encoded path separators.
    path = unquote(parsed.path, errors='strict')
    segments = path.split('/')
    if any(part in ('.', '..') or '\\' in part or any(ord(c) < 32 for c in part) for part in segments):
        raise ValueError('Invalid WebDAV server path')
    return parsed._replace(path=quote(path.rstrip('/') + '/', safe='/'), query='', fragment='').geturl()


def clean_path(value):
    if not isinstance(value, str) or len(value) > 2048 or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid destination folder')
    if value == '/':
        return '/'
    if not value.startswith('/'):
        raise ValueError('Choose a folder beneath this WebDAV account')
    parts = value.strip('/').split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Choose a folder beneath this WebDAV account')
    return '/' + '/'.join(parts) + '/'


def collection_url(root, path):
    return root.rstrip('/') + '/' + quote(clean_path(path).strip('/'), safe='/') + ('/' if path != '/' else '')


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or 443


def browse(settings, account, path='/', depth='1'):
    path = clean_path(path)
    url = collection_url(account['url'], path)
    root_path = unquote(urlsplit(account['url']).path, errors='strict').rstrip('/') + '/'
    requested = unquote(urlsplit(url).path, errors='strict').rstrip('/') + '/'
    raw = bytearray()
    with webdav_client(settings) as connection:
        with connection.stream('PROPFIND', url, auth=(account['username'], account['password']),
                               headers={'Depth': depth, 'Content-Type': 'application/xml'}, content=PROPERTIES) as response:
            if response.status_code in (401, 403):
                raise ValueError('WebDAV login rejected or folder access denied; check your username and app password')
            if response.status_code != 207:
                raise ValueError('Server did not return a WebDAV folder listing; check the server URL')
            for chunk in response.iter_bytes(65536):
                raw.extend(chunk)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError('WebDAV folder listing is too large; choose a smaller collection')
    try:
        document = ElementTree.fromstring(bytes(raw), forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception:
        raise ValueError('WebDAV returned an invalid or unsafe folder listing') from None
    if document.tag != DAV + 'multistatus':
        raise ValueError('Server did not return a WebDAV folder listing')
    folders = {}
    found_self = False
    for resource in document.findall(DAV + 'response'):
        properties = [p.find(DAV + 'prop') for p in resource.findall(DAV + 'propstat') if ' 200 ' in (p.findtext(DAV + 'status') or '')]
        if not any(prop is not None and prop.find(DAV + 'resourcetype/' + DAV + 'collection') is not None for prop in properties):
            continue
        href = resource.findtext(DAV + 'href')
        if not href:
            continue
        resolved = urljoin(url, href)
        parsed = urlsplit(resolved)
        if origin(resolved) != origin(url) or parsed.query or parsed.fragment or parsed.username or parsed.password:
            continue
        remote = unquote(parsed.path, errors='strict').rstrip('/') + '/'
        if remote == requested:
            found_self = True
            continue
        if not remote.startswith(requested) or not remote.startswith(root_path):
            continue
        name = remote[len(requested):].strip('/')
        if not name or '/' in name or '\\' in name or name in ('.', '..'):
            continue
        child = clean_path('/' + remote[len(root_path):])
        folders[child] = {'name': name, 'path': child}
        if len(folders) > 1000:
            raise ValueError('Too many WebDAV folders; choose a smaller collection')
    if not found_self:
        raise ValueError('Selected WebDAV folder does not exist or is inaccessible')
    return {'path': path, 'folders': sorted(folders.values(), key=lambda item: item['name'].casefold())}


def login(settings, url, username, password):
    root = server_url(url.strip())
    account = {'url': root, 'username': username.strip(), 'password': password}
    if not account['username'] or not isinstance(password, str) or not password:
        raise ValueError('Enter your WebDAV username and app password')
    try:
        browse(settings, account, depth='0')
        return account
    except ValueError as original:
        # Accept ownCloud/Nextcloud server URLs as well as explicit DAV roots.
        # Never follow a server-provided redirect with credentials.
        if '/remote.php/' in urlsplit(root).path:
            raise
        account['url'] = root + 'remote.php/dav/files/' + quote(account['username'], safe='') + '/'
        browse(settings, account, depth='0')
        return account
