"""Account-scoped storefront requests, shared in-flight work and cached pages."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from secrets import token_hex
from threading import Event, Lock, RLock
from time import monotonic, time

from .account_state import account_epoch, current_account, persist_if_current
from .catalog_gateway import CatalogGateway
from .prime_diagnostics import DiagnosticLog, exception_details
from .public_catalog import STOREFRONT_HOSTS, safe_url

ROOTS = ('__watching__', '__prime_movies__', '__prime_tv__', '__prime_sports__', '__service_directory__',
    '__prime_purchases__','__prime_watchlist__','__purchases__/movies','__purchases__/tv')
PERSONAL_ROOTS=('__watching__','__prime_watchlist__','__prime_purchases__','__purchases__/movies','__purchases__/tv')
FRESH_ROOTS=('__watching__','__prime_watchlist__')
FRESH_SECONDS = 15 * 60
MAX_AGE = 24 * 60 * 60


class StorefrontLoader:
    """Independent readers do not serialize unrelated catalog sections."""
    def __init__(self, g, ready=None):
        self.g = g
        self.epoch = account_epoch(g.DATA_PATH)
        self.stop = Event()
        self.lock = RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='PrimePages')
        self.background = ThreadPoolExecutor(max_workers=2, thread_name_prefix='PrimePreload')
        self.preview = ThreadPoolExecutor(max_workers=2, thread_name_prefix='PrimeGenrePreview')
        self.personal = ThreadPoolExecutor(max_workers=2,thread_name_prefix='PrimePersonal')
        self.ready = ready
        self.readers = {root: (CatalogGateway(g), Lock()) for root in ROOTS}
        self.routes = {root: root for root in ROOTS}
        self.cache, self.futures = {}, {}
        self.revalidate=set()
        self.background_paths, self.saved_checked = set(), set()
        self.focus_path = None
        self.focus_paths = ()
        self.folder = Path(g.DATA_PATH) / 'navigation-cache'
        self.report = DiagnosticLog(g.DATA_PATH, g.addon.getAddonInfo('version'),
                                    active=self.current, operation='navigation')
        for root in ROOTS:
            self._load_saved(root)

    def current(self):
        return not self.stop.is_set() and current_account(self.g.DATA_PATH, self.epoch)

    @staticmethod
    def canonical(path):
        return '__prime_movies__' if path in ('root', '__prime_home__') else path

    @staticmethod
    def diagnostic_section(path):
        if path.startswith('__genre__/'):return 'genre_'+path.split('/',2)[1]
        if path.startswith('__service__/'):return 'service'
        return path

    def register_genre(self,path):
        if not path.startswith(('__genre__/','__service__/')):raise ValueError('Invalid catalog route')
        with self.lock:
            if path not in self.routes:
                self.routes[path]=path
                self.readers[path]=(CatalogGateway(self.g),Lock())

    def preload_startup(self):
        """Warm main catalogs, then the service index; never private history."""
        for path in ('__prime_movies__','__prime_tv__','__service_directory__'):
            self.request(path, background=True)
        self.report({'event':'startup_catalog_preload_started','stage':'preload',
            'sections':['movies','tv','service_directory'],'background_workers':2,'foreground_workers':2,'result':'success'})

    def preload_focus(self,path,adjacent=()):
        """Warm first pages for the focused genre and at most one neighbour."""
        if path.startswith('__genre__/'):self.register_genre(path)
        if not self.handles(path):return
        paths=tuple(dict.fromkeys((path,)+tuple(adjacent[:1])))
        for candidate in paths:
            if candidate.startswith('__genre__/'):self.register_genre(candidate)
        with self.lock:
            previous=self.focus_paths
            if previous==paths:return
            self.focus_path=path
            self.focus_paths=paths
            for old in previous:
                if old in paths or old not in self.background_paths:continue
                future=self.futures.get(old)
                if future is not None and future.cancel():
                    self.report({'event':'catalog_preload_cancelled','stage':'preload_queue',
                        'section':self.diagnostic_section(old),'reason':'focus_changed','result':'cancelled'})
        for candidate in paths:
            if self.handles(candidate):self.request(candidate,background=True,priority=True)
        self.report({'event':'focused_catalog_preload_requested','stage':'preload',
            'section':self.diagnostic_section(path),'bounded_first_pages':len(paths),
            'startup_queue_bypassed':True,'result':'success'})

    def handles(self, path):
        with self.lock:
            return self.canonical(path) in self.routes

    def peek(self, path):
        """Show account-owned cards immediately; preview is never a fresh result."""
        if not self.current():return None
        path=self.canonical(path)
        if path=='__watching__':
            return None
        with self.lock:
            entry = self.cache.get(path)
            if not entry:return None
            packet=deepcopy(entry[1]);packet['next']=None
            packet['_cache_preview']=True;packet['_cache_age_seconds']=max(0,round(time()-entry[0]))
            return packet

    def request(self, path, background=False, priority=False,refresh=False):
        path = self.canonical(path)
        with self.lock:
            future = self.futures.get(path)
            if path=='__watching__':
                # Progress settlement after playback needs a new provider read,
                # even if an earlier history request is still finishing.
                self.cache.pop(path,None)
                if future is not None and not future.done():future.cancel()
            elif path=='__prime_watchlist__' and future is not None and not future.done():return future
            if path not in FRESH_ROOTS and future is not None and not future.done():
                # A click bypasses queued speculation. An already running exact
                # request is shared, never duplicated or put behind another root.
                if background or path not in self.background_paths or not future.cancel():return future
                self.report({'event':'catalog_preload_promoted','stage':'preload_queue',
                    'section':self.diagnostic_section(path),'result':'success'})
            entry = self.cache.get(path)
            if (not refresh and path not in FRESH_ROOTS and future is not None and future.done() and not future.cancelled()
                    and future.exception() is None and entry and path not in self.revalidate and time() - entry[0] < FRESH_SECONDS
                    and not entry[1].get('pagination_unavailable')
                    and (path!='__service_directory__' or entry[1].get('directory_complete'))):
                if (path in ROOTS or path.startswith(('__genre__/','__service__/'))) and entry[1].get('next'):self.request(entry[1]['next'],background=True)
                return future
            if self.stop.is_set():
                raise RuntimeError('The catalog session has ended')
            if background:self.background_paths.add(path)
            else:self.background_paths.discard(path)
            executor=self.personal if self.routes.get(path) in PERSONAL_ROOTS else self.preview if priority else self.background if background else self.pool
            self.futures[path] = executor.submit(
                self._load,path,monotonic(),token_hex(8),background,priority,refresh)
            return self.futures[path]

    def _filename(self, path):
        return self.folder / (hashlib.sha256(path.encode()).hexdigest() + '.json')

    def _load_saved(self, root):
        self.saved_checked.add(root)
        if root == '__watching__':
            try:
                from .progress_cache import discard_stored
                discard_stored(self.g.DATA_PATH,self.epoch,self.report)
            except Exception as error:
                self.report({'event':'progress_cache_cleanup_failed','exception':exception_details(error),'result':'failure'})
            return
        path = self._filename(root)
        if not path.exists():
            return
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Catalog cache exceeds its size limit')
            saved = json.loads(path.read_text(encoding='utf-8'))
            if (saved.get('schema') != 7 or saved.get('epoch') != self.epoch
                    or saved.get('root') != root or not 0 <= time() - saved['saved'] < MAX_AGE):
                return
            packet = saved['packet']
            policy = ('prime_only' if root in ('__prime_movies__', '__prime_tv__') or root.startswith('__genre__/') else
                      'account_library' if root == '__prime_purchases__' or root.startswith('__purchases__/') else 'watch_progress' if root == '__watching__' else
                      'services' if root=='__service_directory__' else 'watchlist' if root=='__prime_watchlist__' else 'sports')
            if packet.get('catalog_policy') != policy:
                return
            if root in PERSONAL_ROOTS and packet.get('personal_schema')!=2:return
            if root=='__prime_watchlist__' and packet.get('watchlist_confirmed') is not True:return
            if not isinstance(packet.get('rows'), list) or len(packet['rows']) > 2000:
                raise ValueError('Invalid catalog cache rows')
            for row in packet['rows']:
                if (not isinstance(row, dict) or not isinstance(row.get('info'), dict)
                        or not str(row.get('url', '')).startswith(self.g.pluginid)):
                    raise ValueError('Invalid cached title route')
            bridge, _ = self.readers[root]
            if root=='__service_directory__':
                from .service_catalog import service_address
                if any(not service_address(row.get('address')) for row in packet.get('tiles') or []):raise ValueError('Invalid cached service catalog route')
                if not packet.get('directory_complete'):return
            staged={}
            for alias, state in saved.get('continuations', {}).items():
                if (not alias.startswith('__prime_page__/') or not alias.endswith('/nextPage')
                        or not isinstance(state,(list,tuple)) or len(state) != 4 or state[0] != self.epoch
                        or not self._valid_continuation(state[2])):
                    raise ValueError('Invalid cached continuation')
                staged[alias]=(state[0],state[1],state[2],tuple(state[3]))
            if packet.get('next') and packet['next'] not in staged:
                raise ValueError('Cached next page has no validated continuation')
            bridge.continuations.update(staged)
            for alias in staged:
                self.routes[alias] = root
            # A restart may follow a VPN/territory change without changing the
            # account epoch. Keep the warm display, but obtain a fresh first
            # page before using old regional continuation tokens.
            held=bool(packet.get('next'));packet['next']=None
            self.cache[root] = (saved['saved'], packet)
            self.revalidate.add(root)
            self.report({'event': 'saved_catalog_loaded', 'section': self.diagnostic_section(root), 'cards': len(packet['rows']),
                         'age_seconds': round(time() - saved['saved']), 'session_revalidation_required':True,
                         'regional_continuation_held':held,'result': 'display_only'})
        except Exception as error:
            self.report({'event': 'saved_catalog_load_failed', 'section': self.diagnostic_section(root),
                         'exception': exception_details(error), 'result': 'failure'})

    def release(self, path, future):
        with self.lock:
            if self.routes.get(path)=='__watching__' and self.futures.get(path) is future:
                self.futures.pop(path,None)
                if path!='__watching__':
                    self.routes.pop(path,None);self.readers.pop(path,None)

    @staticmethod
    def _valid_continuation(value):
        if isinstance(value,dict):
            from .collection_paging import valid_request
            return valid_request(value)
        return bool(safe_url(value,STOREFRONT_HOSTS))

    def _save(self, root, packet, bridge, saved):
        if packet.get('pagination_unavailable'):return
        if root=='__watching__' or root=='__service_directory__' and not packet.get('directory_complete'):return
        if root=='__prime_watchlist__' and packet.get('watchlist_confirmed') is not True:return
        if root=='__prime_watchlist__' and not packet.get('rows') and not packet.get('empty_confirmed'):return
        # Only root/genre first-page caches survive a restart. Their real continuation
        # mapping is saved alongside them; no endpoint or fake page is invented.
        snapshot=deepcopy(packet)
        if root=='__prime_watchlist__':snapshot['next']=None
        content = json.dumps({'schema': 7, 'epoch': self.epoch, 'root': root,
                              'saved': saved, 'packet': snapshot,
                              'continuations':{} if root=='__prime_watchlist__' else bridge.continuations}, ensure_ascii=True)
        target = self._filename(root)

        def write():
            self.folder.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.new')
            temporary.write_text(content, encoding='utf-8')
            temporary.replace(target)
        stored = persist_if_current(self.g.DATA_PATH, self.epoch, write)
        self.report({'event': 'catalog_cache_saved', 'section': self.diagnostic_section(root), 'stored': stored})

    def _load(self, path, queued, attempt, background, priority=False,refresh=False):
        started = monotonic()
        self.report({'event':'catalog_load_started','stage':'preload' if background else 'navigation',
            'refresh_revision':'confirmed-preview-live-refresh-v1',
            'attempt_id':attempt,'section':self.diagnostic_section(path),
            'request_lane':'personal' if self.routes.get(path) in PERSONAL_ROOTS else 'genre_preview' if priority else 'background' if background else 'catalog',
            'queue_wait_ms':round((started-queued)*1000),'background':background,'result':'started'})
        if path.startswith('__genre__/') and path not in self.saved_checked:
            self._load_saved(path)
        with self.lock:
            root = self.routes[path]
            entry = self.cache.get(path)
        with self.lock:bridge, reader_lock = self.readers.get(path,self.readers[root])
        fresh = root=='__watching__' or path=='__prime_watchlist__'
        try:
            if path=='__watching__':
                from .progress_cache import discard_stored
                discard_stored(self.g.DATA_PATH,self.epoch,self.report)
                bridge.operation_observer=DiagnosticLog(self.g.DATA_PATH,self.g.addon.getAddonInfo('version'),
                    active=self.current,operation='in_progress')
                entry=None
            elif path=='__prime_watchlist__':
                entry=None
            if (not refresh and entry and path not in self.revalidate and 0 <= time() - entry[0] < FRESH_SECONDS
                    and not entry[1].get('pagination_unavailable')
                    and (path!='__service_directory__' or entry[1].get('directory_complete'))):
                packet = deepcopy(entry[1])
                source = 'cache'
            else:
                if not self.current():
                    return None
                packet = None

                def publish(value):
                    nonlocal packet
                    packet = value
                    # Service rows are usable before the final directory page.
                    # Incomplete batches live only in account-scoped memory.
                    if path=='__service_directory__' and self.current():
                        with self.lock:self.cache[path]=(time(),deepcopy(value))
                        self.report({'event':'service_directory_memory_batch','stage':'directory_publish',
                            'attempt_id':attempt,'services':len(value.get('tiles') or []),
                            'complete':bool(value.get('directory_complete')),
                            'load_elapsed_ms':round((monotonic()-started)*1000),'result':'published'})
                reader_started=monotonic()
                with reader_lock:
                    if refresh:bridge.public_pages.clear()
                    observe=DiagnosticLog(self.g.DATA_PATH,self.g.addon.getAddonInfo('version'),
                        active=self.current,operation='watchlist' if root=='__prime_watchlist__' else 'in_progress' if fresh else 'catalog_preload' if background else 'catalog')
                    observe({'event':'catalog_reader_started','stage':'reader',
                        'parent_attempt_id':attempt,'section':self.diagnostic_section(root),
                        'reader_wait_ms':round((monotonic()-reader_started)*1000),'result':'started'})
                    bridge.operation_observer=observe
                    try:bridge.browse_progressive(path, publish, self.current)
                    finally:bridge.operation_observer=None
                if packet is None or not self.current():
                    return None
                source = 'network'
                saved = time()
                with self.lock:
                    self.revalidate.discard(path)
                    if root!='__watching__': self.cache[path] = (saved, deepcopy(packet))
                    if packet.get('next'):
                        self.routes[packet['next']] = root
                        if root in FRESH_ROOTS:self.readers[packet['next']]=(bridge,reader_lock)
                if (path in ROOTS or path.startswith('__genre__/')) and root!='__watching__':
                    try:
                        self._save(root, packet, bridge, saved)
                    except Exception as error:
                        self.report({'event': 'catalog_cache_save_failed', 'section': self.diagnostic_section(root),
                                     'exception': exception_details(error), 'result': 'failure'})
            self.report({'event': 'catalog_page_ready', 'section': self.diagnostic_section(root), 'source': source,
                         'attempt_id':attempt,'background':background,
                         'cards': len(packet.get('rows') or []), 'has_next': bool(packet.get('next')),
                         'fresh_provider_request':source=='network','fresh_required':fresh,
                         'service_tiles':len(packet.get('tiles') or []),
                         'saved_or_memory_cache_used':not fresh and source == 'cache',
                         'pagination_unavailable':bool(packet.get('pagination_unavailable')),
                         'load_elapsed_ms': round((monotonic() - started) * 1000), 'result': 'partial' if packet.get('pagination_unavailable') else 'success'})
            # Warm exactly the first two actual pages. Page three is requested
            # by the UI on entering page two, and so on for later pages.
            if self.ready is not None and self.current():self.ready(path,deepcopy(packet))
            if (path in ROOTS or path.startswith(('__genre__/','__service__/'))) and (not fresh or path=='__prime_watchlist__') and packet.get('next') and self.current():
                if not background or path in ROOTS or path==self.focus_path:
                    self.request(packet['next'],background=True)
            return deepcopy(packet)
        except Exception as error:
            self.report({'event': 'catalog_page_failed', 'section': self.diagnostic_section(root),
                         'attempt_id':attempt,'background':background,
                         'load_elapsed_ms': round((monotonic() - started) * 1000),
                         'exception': exception_details(error), 'result': 'failure'})
            raise

    def close(self):
        self.stop.set()
        with self.lock:
            for future in self.futures.values():
                future.cancel()
        self.pool.shutdown(wait=True)
        self.background.shutdown(wait=True)
        self.preview.shutdown(wait=True)
        self.personal.shutdown(wait=True)
        for bridge in {id(pair[0]):pair[0] for pair in self.readers.values()}.values():bridge.close()
        with self.lock:
            self.cache.clear()
            self.revalidate.clear()
            self.futures.clear()
            self.routes.clear()
            self.readers.clear()
            self.background_paths.clear()
            self.focus_paths=()
            self.saved_checked.clear()
            self.ready=None
