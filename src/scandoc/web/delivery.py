"""Delivery adapters. Uncertain remote writes are deliberately not retried blindly."""
from pathlib import Path
from urllib.parse import urlparse, quote
import hashlib
import os
import ssl
import httpx


class UncertainDelivery(Exception):
    pass


def client(settings, token=None):
    verify = ssl.create_default_context(cafile=settings.ca_file) if settings.ca_file else True
    return httpx.Client(verify=verify, timeout=30, follow_redirects=False, headers={"Authorization": f"Token {token}"} if token else {})


def paperless(settings, token, method, path, **kwargs):
    with client(settings, token) as connection:
        response = connection.request(method, settings.paperless_url.rstrip("/") + "/api/" + path.lstrip("/"), **kwargs)
        response.raise_for_status()
        return response.json()


def results(value):
    return value.get("results", []) if isinstance(value, dict) else value


def validate_destination(destination):
    kind = destination.get("kind")
    if kind not in ("folder", "webdav"):
        raise ValueError("Only folder and WebDAV destinations may be configured")
    if not str(destination.get("name", "")).strip():
        raise ValueError("A destination name is required")
    if kind == "folder":
        root = Path(destination.get("root", ""))
        if not root.is_absolute():
            raise ValueError("Folder root must be an absolute path")
    else:
        url = urlparse(destination.get("url", ""))
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("WebDAV requires an HTTPS collection URL without embedded credentials")


def file_digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").digest()


def remote_digest(connection, url):
    with connection.stream("GET", url) as response:
        if response.status_code == 404:
            return None
        response.raise_for_status()
        digest = hashlib.sha256()
        for chunk in response.iter_bytes(1024 * 1024):
            digest.update(chunk)
        return digest.digest()


def deliver_folder(destination, pdf, filename):
    root = Path(destination["root"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / filename
    # Unique staging files let a retry recover even after a process died mid-write.
    import uuid
    temporary = root / ("." + filename + "." + str(uuid.uuid4()) + ".part")
    digest = file_digest(pdf)
    if target.exists():
        if target.is_symlink() or file_digest(target) != digest:
            raise ValueError("Destination already contains a different file")
        return str(target)
    try:
        with temporary.open("xb") as output, pdf.open("rb") as source:
            import shutil
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        # A hard link publishes the complete file atomically without overwriting.
        os.link(temporary, target)
        if os.name == "posix":
            directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)


def deliver_webdav(settings, destination, pdf, filename):
    url = destination["url"].rstrip("/") + "/" + quote(filename)
    from .webdav import webdav_client
    with webdav_client(settings) as connection:
        connection.auth = (destination.get("username", ""), destination.get("password", ""))
        expected = file_digest(pdf)
        existing = remote_digest(connection, url)
        if existing is not None:
            if existing != expected:
                raise ValueError("Destination already contains a different file")
            return url
        try:
            with pdf.open("rb") as source:
                chunks = iter(lambda: source.read(1024 * 1024), b"")
                response = connection.put(url, content=chunks, headers={"If-None-Match": "*", "Content-Type": "application/pdf", "Content-Length": str(pdf.stat().st_size)})
                response.raise_for_status()
            if remote_digest(connection, url) != expected:
                raise UncertainDelivery("WebDAV content verification failed; the draft was retained")
        except httpx.TransportError as exc:
            raise UncertainDelivery("WebDAV response lost; reconcile the destination before retrying") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code in (408, 412):
                raise UncertainDelivery("WebDAV upload outcome is uncertain; reconcile before resending") from exc
            raise
    return url
