"""Bounded read-ahead for exact public artwork URLs, inside the account profile."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .account_state import current_account, persist_if_current
from .prime_diagnostics import DiagnosticLog, exception_details, route
from .public_catalog import IMAGE_HOSTS, safe_url


class _ImageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_url(newurl, IMAGE_HOSTS):
            raise ValueError('Artwork redirected outside the image service')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ImageCache:
    def __init__(self, g, epoch):
        self.g, self.epoch = g, epoch
        self.folder = Path(g.DATA_PATH) / 'artwork-ahead'
        self.stop, self.lock = Event(), Lock()
        self.pending, self.failed = set(), set()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='PrimeArtwork')
        self.report = DiagnosticLog(g.DATA_PATH, g.addon.getAddonInfo('version'),
                                    active=lambda: not self.stop.is_set() and current_account(g.DATA_PATH, epoch),
                                    operation='artwork')

    def path(self, url):
        if not safe_url(url, IMAGE_HOSTS):
            return None
        extension = Path(urlsplit(url).path).suffix.lower()
        if extension not in ('.jpg', '.jpeg', '.png', '.webp'):
            return None
        return self.folder / (sha256(url.encode()).hexdigest() + extension)

    def local(self, url):
        path = self.path(url)
        return str(path) if path is not None and path.is_file() else url

    def warm(self, urls):
        for url in urls:
            path = self.path(url)
            if path is None or path.exists():
                continue
            with self.lock:
                if (self.stop.is_set() or url in self.pending or url in self.failed
                        or len(self.pending) >= 18):
                    continue
                self.pending.add(url)
                self.pool.submit(self._download, url, path)

    def _download(self, url, path):
        started, status = monotonic(), None
        try:
            if self.stop.is_set() or not current_account(self.g.DATA_PATH, self.epoch):
                return
            with build_opener(_ImageRedirect()).open(Request(url, headers={'Accept': 'image/*'}), timeout=8) as response:
                status = response.getcode()
                if response.headers.get_content_type() not in ('image/jpeg', 'image/png', 'image/webp'):
                    raise ValueError('Artwork response was not an image')
                content = response.read(3 * 1024 * 1024 + 1)
            if len(content) > 3 * 1024 * 1024:
                raise ValueError('Artwork exceeded its size limit')
            if not (content.startswith((b'\xff\xd8', b'\x89PNG\r\n\x1a\n'))
                    or content[:4] == b'RIFF' and content[8:12] == b'WEBP'):
                raise ValueError('Artwork image data was invalid')
            if self.stop.is_set():
                return

            def write():
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + '.new')
                temporary.write_bytes(content)
                temporary.replace(path)
            stored = persist_if_current(self.g.DATA_PATH, self.epoch, write)
            self.report({'event': 'artwork_ahead_completed', 'status': status, 'bytes': len(content),
                         'stage': 'image_download', 'address': route(url),
                         'elapsed_ms': round((monotonic() - started) * 1000),
                         'result': 'success' if stored else 'cancelled'})
        except Exception as error:
            with self.lock:
                self.failed.add(url)
            self.report({'event': 'artwork_ahead_failed', 'status': status,
                         'stage': 'image_download', 'address': route(url),
                         'elapsed_ms': round((monotonic() - started) * 1000),
                         'exception': exception_details(error), 'result': 'failure'})
        finally:
            with self.lock:
                self.pending.discard(url)

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True)
        with self.lock:
            self.pending.clear()
            self.failed.clear()
