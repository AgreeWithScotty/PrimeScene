"""Bounded read-ahead for exact public artwork URLs, inside the account profile."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError

from .account_state import current_account, persist_if_current
from .prime_diagnostics import DiagnosticLog, exception_details, route
from .public_catalog import IMAGE_HOSTS, safe_url
from .image_quality import pixel_dimensions,source_image,source_facts,POLICY_REVISION as ARTWORK_POLICY
from secrets import token_hex

MAX_IMAGE_BYTES=32*1024*1024


class _ImageRedirect(HTTPRedirectHandler):
    def __init__(self,report,request_id):
        self.report,self.request_id,self.hops=report,request_id,0
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops+=1;allowed=bool(safe_url(newurl, IMAGE_HOSTS)) and self.hops<=6
        self.report({'event':'artwork_redirect','request_id':self.request_id,'stage':'image_download',
            'status':code,'method':req.get_method(),'source':route(req.full_url),'destination':route(newurl),
            'hop':self.hops,'allowed':allowed,'result':'allowed' if allowed else 'blocked'})
        if not allowed:
            raise ValueError('Artwork redirected outside the image service')
        redirected=super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected._prime_request_id=self.request_id;redirected._prime_stage='image_download'
        return redirected


class ImageCache:
    def __init__(self, g, epoch, *, genre=False):
        self.g, self.epoch = g, epoch
        self.folder = Path(g.DATA_PATH) / ('genre-artwork-ahead' if genre else 'artwork-ahead')
        self.metadata={}
        self.stop, self.lock = Event(), Lock()
        self.pending, self.failed = set(), {}
        self.failure_details={}
        self.futures={};self.priority_signature=None
        self.native_only=set()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='PrimeArtwork')
        self.report = DiagnosticLog(g.DATA_PATH, g.addon.getAddonInfo('version'),
                                    active=lambda: not self.stop.is_set() and current_account(g.DATA_PATH, epoch),
                                    operation='genre_artwork' if genre else 'artwork')
        from .catalog_connections import CatalogConnections
        self.connections=CatalogConnections(lambda:not self.stop.is_set() and current_account(g.DATA_PATH,epoch))

    def _connection_event(self,event):
        self.report(dict(event,event='artwork_connection'))

    def path(self, url):
        url=source_image(url)
        if not safe_url(url, IMAGE_HOSTS):
            return None
        extension = Path(urlsplit(url).path).suffix.lower()
        if extension not in ('.jpg', '.jpeg', '.png', '.webp'):
            return None
        return self.folder / (sha256(url.encode()).hexdigest() + extension)

    def local(self, url):
        url=source_image(url)
        path = self.path(url)
        return str(path) if path is not None and path.is_file() else url

    def dimensions(self,url):
        url=source_image(url)
        with self.lock:
            if url in self.metadata:return self.metadata[url]
        path=self.path(url)
        if path is None or not path.is_file():return None
        try:
            with path.open('rb') as source:dimensions=pixel_dimensions(source.read(256*1024))
            with self.lock:self.metadata[url]=dimensions
            return dimensions
        except Exception as error:
            self.report({'event':'artwork_cached_header_failed','stage':'image_cache_metadata','address':route(url),
                'exception':exception_details(error),'result':'failure'})
            return None

    def state(self,url):
        url=source_image(url)
        facts=source_facts(url)
        if self.local(url)!=url:return dict(facts,
            state='ready_original' if facts['resolution_policy']=='provider_original_master' else 'ready_provider_source',
            pixel_dimensions=self.dimensions(url))
        with self.lock:
            state='image_download_pending' if url in self.pending else 'image_download_failed' if url in self.failed else 'image_not_queued'
            return dict(self.failure_details.get(url) or {},state=state)

    def warm(self, urls,priority=False):
        urls=list(dict.fromkeys(source_image(url) for url in urls if url))
        signature=tuple(urls)
        if priority and signature!=self.priority_signature:
            cancelled=0
            with self.lock:
                self.priority_signature=signature
                for address,future in list(self.futures.items()):
                    if future.cancel():
                        self.pending.discard(address);self.futures.pop(address,None);cancelled+=1
            self.report({'event':'visible_artwork_prioritized','stage':'artwork_queue',
                'visible_requests':len(urls),'obsolete_queued_requests_cancelled':cancelled,'result':'success'})
        for url in urls:
            path = self.path(url)
            if path is None and safe_url(url,IMAGE_HOSTS) and url not in self.native_only:
                self.native_only.add(url)
                self.report(dict(source_facts(url),event='artwork_native_loader_required',stage='artwork_queue',
                    address=route(url),download_observed=False,pixel_dimensions=None,result='delegated'))
            if path is None or path.exists():
                continue
            with self.lock:
                if (self.stop.is_set() or url in self.pending or monotonic()-self.failed.get(url,float('-inf'))<30
                        or len(self.pending) >= 18):
                    continue
                self.pending.add(url)
                self.futures[url]=self.pool.submit(self._download, url, path)

    def _download(self, url, path):
        url=source_image(url);path=self.path(url)
        started, status = monotonic(), None
        response_type=None;server_request_id=None
        request_id=token_hex(8);final=url
        self.report(dict(source_facts(url),event='artwork_ahead_started',request_id=request_id,stage='image_download',
            address=route(url),method='GET',result='started'))
        try:
            if path is None:raise ValueError('Artwork requires the native image loader')
            if self.stop.is_set() or not current_account(self.g.DATA_PATH, self.epoch):
                self.report({'event':'artwork_ahead_cancelled','request_id':request_id,'stage':'image_download',
                    'elapsed_ms':round((monotonic()-started)*1000),'result':'cancelled'})
                return
            from .catalog_connections import KeepAliveHTTPSHandler
            request=Request(url,headers={'Accept':'image/*','Accept-Encoding':'identity'})
            request._prime_request_id=request_id;request._prime_stage='image_download'
            with build_opener(_ImageRedirect(self.report,request_id),
                    KeepAliveHTTPSHandler(self.connections,self._connection_event)).open(request,timeout=8) as response:
                status = response.getcode()
                final=response.geturl()
                response_type=response.headers.get_content_type()
                server_request_id=response.headers.get('x-amzn-requestid') or response.headers.get('x-amz-rid')
                if source_image(final)!=final:raise ValueError('Amazon redirected original artwork to a delivery variant')
                if response_type not in ('image/jpeg', 'image/png', 'image/webp'):
                    raise ValueError('Artwork response was not an image')
                content = response.read(MAX_IMAGE_BYTES + 1)
            if len(content) > MAX_IMAGE_BYTES:
                raise ValueError('Artwork exceeded its size limit')
            if not (content.startswith((b'\xff\xd8', b'\x89PNG\r\n\x1a\n'))
                    or content[:4] == b'RIFF' and content[8:12] == b'WEBP'):
                raise ValueError('Artwork image data was invalid')
            if self.stop.is_set():
                self.report({'event':'artwork_ahead_cancelled','request_id':request_id,'stage':'image_download',
                    'elapsed_ms':round((monotonic()-started)*1000),'result':'cancelled'})
                return

            def write():
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + '.new')
                temporary.write_bytes(content)
                temporary.replace(path)
            stored = persist_if_current(self.g.DATA_PATH, self.epoch, write)
            if stored:
                with self.lock:self.metadata[url]=pixel_dimensions(content)
            self.report({'event': 'artwork_ahead_completed', 'request_id':request_id,'status': status, 'bytes': len(content),
                         'artwork_policy':ARTWORK_POLICY,'resolution_policy':source_facts(final)['resolution_policy'],
                         'artificial_width_cap':False,'recompressed':False,
                         'content_type':response_type,'server_request_id':server_request_id,
                         'pixel_dimensions':pixel_dimensions(content),'response_address':route(final),'size_limit_bytes':MAX_IMAGE_BYTES,
                         'stage': 'image_download', 'address': route(url),
                         'elapsed_ms': round((monotonic() - started) * 1000),
                         'result': 'success' if stored else 'cancelled'})
        except Exception as error:
            if isinstance(error,HTTPError):
                final=error.geturl()
                if error.headers:
                    response_type=error.headers.get_content_type()
                    server_request_id=error.headers.get('x-amzn-requestid') or error.headers.get('x-amz-rid')
            with self.lock:
                self.failed[url]=monotonic()
                self.failure_details[url]={'request_id':request_id,'status':status or getattr(error,'code',None),
                    'exception':exception_details(error),'stage':'image_download'}
            self.report({'event': 'artwork_ahead_failed', 'request_id':request_id,'status': status or getattr(error,'code',None),
                         'artwork_policy':ARTWORK_POLICY,'lower_quality_retry':False,
                         'content_type':response_type,'server_request_id':server_request_id,
                         'response_address':route(final),'size_limit_bytes':MAX_IMAGE_BYTES,
                         'retry_after_seconds':30,
                         'stage': 'image_download', 'address': route(url),
                         'elapsed_ms': round((monotonic() - started) * 1000),
                         'exception': exception_details(error), 'result': 'failure'})
        finally:
            with self.lock:
                self.pending.discard(url)
                self.futures.pop(url,None)

    def close(self):
        self.stop.set()
        with self.lock:
            for future in self.futures.values():future.cancel()
        self.pool.shutdown(wait=True)
        self.connections.close()
        with self.lock:
            self.pending.clear()
            self.failed.clear()
            self.failure_details.clear()
            self.futures.clear();self.priority_signature=None
            self.metadata.clear()
            self.native_only.clear()
