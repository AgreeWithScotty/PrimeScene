"""Account-scoped storefront requests, shared in-flight work and cached pages."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import Event, Lock, RLock
from time import monotonic, time

from .account_state import account_epoch, current_account, persist_if_current
from .dedicated_bridge import ProviderBridge
from .prime_diagnostics import DiagnosticLog, exception_details
from .public_catalog import STOREFRONT_HOSTS, safe_url

ROOTS = ('root', '__prime_movies__', '__prime_tv__')
FRESH_SECONDS = 15 * 60
MAX_AGE = 24 * 60 * 60


class StorefrontLoader:
    """Public readers never hold the legacy detail/playback provider lock."""
    def __init__(self, g):
        self.g = g
        self.epoch = account_epoch(g.DATA_PATH)
        self.stop = Event()
        self.lock = RLock()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='PrimePages')
        self.readers = {root: (ProviderBridge(None, g), Lock()) for root in ROOTS}
        self.routes = {root: root for root in ROOTS}
        self.cache, self.futures = {}, {}
        self.folder = Path(g.DATA_PATH) / 'navigation-cache'
        self.report = DiagnosticLog(g.DATA_PATH, g.addon.getAddonInfo('version'),
                                    active=self.current, operation='navigation')
        for root in ROOTS:
            self._load_saved(root)

    def current(self):
        return not self.stop.is_set() and current_account(self.g.DATA_PATH, self.epoch)

    @staticmethod
    def canonical(path):
        return 'root' if path == '__prime_home__' else path

    def handles(self, path):
        with self.lock:
            return self.canonical(path) in self.routes

    def peek(self, path):
        """Memory lookup only: no filesystem, account database or network on click."""
        with self.lock:
            entry = self.cache.get(self.canonical(path))
            return deepcopy(entry[1]) if entry else None

    def request(self, path):
        path = self.canonical(path)
        with self.lock:
            future = self.futures.get(path)
            if future is not None and not future.done():
                return future
            entry = self.cache.get(path)
            if future is not None and entry and time() - entry[0] < FRESH_SECONDS:
                return future
            if self.stop.is_set():
                raise RuntimeError('The catalog session has ended')
            self.futures[path] = self.pool.submit(self._load, path)
            return self.futures[path]

    def warm(self):
        for root in ROOTS:
            self.request(root)

    def _filename(self, path):
        return self.folder / (hashlib.sha256(path.encode()).hexdigest() + '.json')

    def _load_saved(self, root):
        path = self._filename(root)
        if not path.exists():
            return
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Catalog cache exceeds its size limit')
            saved = json.loads(path.read_text(encoding='utf-8'))
            if (saved.get('schema') != 2 or saved.get('epoch') != self.epoch
                    or saved.get('root') != root or not 0 <= time() - saved['saved'] < MAX_AGE):
                return
            packet = saved['packet']
            if not isinstance(packet.get('rows'), list) or len(packet['rows']) > 2000:
                raise ValueError('Invalid catalog cache rows')
            for row in packet['rows']:
                if (not isinstance(row, dict) or not isinstance(row.get('info'), dict)
                        or not str(row.get('url', '')).startswith(self.g.pluginid)):
                    raise ValueError('Invalid cached title route')
            bridge, _ = self.readers[root]
            for alias, state in saved.get('continuations', {}).items():
                if (not alias.startswith('__prime_page__/') or not alias.endswith('/nextPage')
                        or len(state) != 4 or state[0] != self.epoch
                        or not safe_url(state[2], STOREFRONT_HOSTS)):
                    raise ValueError('Invalid cached continuation')
                bridge.continuations[alias] = (state[0], state[1], state[2], tuple(state[3]))
                self.routes[alias] = root
            self.cache[root] = (saved['saved'], packet)
            self.report({'event': 'saved_catalog_loaded', 'section': root, 'cards': len(packet['rows']),
                         'age_seconds': round(time() - saved['saved']), 'result': 'success'})
        except Exception as error:
            self.report({'event': 'saved_catalog_load_failed', 'section': root,
                         'exception': exception_details(error), 'result': 'failure'})

    def _save(self, root, packet, bridge, saved):
        # Only first-page caches survive a restart. Their real continuation
        # mapping is saved alongside them; no endpoint or fake page is invented.
        content = json.dumps({'schema': 2, 'epoch': self.epoch, 'root': root,
                              'saved': saved, 'packet': packet,
                              'continuations': bridge.continuations}, ensure_ascii=True)
        target = self._filename(root)

        def write():
            self.folder.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.new')
            temporary.write_text(content, encoding='utf-8')
            temporary.replace(target)
        stored = persist_if_current(self.g.DATA_PATH, self.epoch, write)
        self.report({'event': 'catalog_cache_saved', 'section': root, 'stored': stored})

    def _load(self, path):
        started = monotonic()
        with self.lock:
            root = self.routes[path]
            entry = self.cache.get(path)
        bridge, reader_lock = self.readers[root]
        try:
            if entry and 0 <= time() - entry[0] < FRESH_SECONDS:
                packet = deepcopy(entry[1])
                source = 'cache'
            else:
                if not self.current():
                    return None
                packet = None

                def publish(value):
                    nonlocal packet
                    packet = value
                with reader_lock:
                    bridge.browse_progressive(path, publish, self.current)
                if packet is None or not self.current():
                    return None
                source = 'network'
                saved = time()
                with self.lock:
                    self.cache[path] = (saved, deepcopy(packet))
                    if packet.get('next'):
                        self.routes[packet['next']] = root
                if path in ROOTS:
                    try:
                        self._save(root, packet, bridge, saved)
                    except Exception as error:
                        self.report({'event': 'catalog_cache_save_failed', 'section': root,
                                     'exception': exception_details(error), 'result': 'failure'})
            self.report({'event': 'catalog_page_ready', 'section': root, 'source': source,
                         'cards': len(packet.get('rows') or []), 'has_next': bool(packet.get('next')),
                         'elapsed_ms': round((monotonic() - started) * 1000), 'result': 'success'})
            # Warm exactly the first two actual pages. Page three is requested
            # by the UI on entering page two, and so on for later pages.
            if path in ROOTS and packet.get('next') and self.current():
                self.request(packet['next'])
            return deepcopy(packet)
        except Exception as error:
            self.report({'event': 'catalog_page_failed', 'section': root,
                         'elapsed_ms': round((monotonic() - started) * 1000),
                         'exception': exception_details(error), 'result': 'failure'})
            raise

    def close(self):
        self.stop.set()
        with self.lock:
            for future in self.futures.values():
                future.cancel()
        self.pool.shutdown(wait=True)
        with self.lock:
            self.cache.clear()
            self.futures.clear()
            self.routes.clear()
            self.readers.clear()
