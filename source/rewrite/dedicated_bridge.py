"""Independent storefront pages with lazy access to existing playback routes."""
from copy import deepcopy
from os.path import join
import re
from secrets import token_hex
from time import monotonic
from urllib.parse import urlencode, urljoin, urlsplit
from .category_art import apply_category_art, cached_node, first_artwork
from .public_catalog import PublicCatalogError, STOREFRONT_HOSTS, detail_id, fetch_page, safe_url
from .prime_diagnostics import DiagnosticLog, exception_details
from .account_state import account_epoch, current_account


HOME_CAPACITY = 8
STOREFRONT_PAGES = {'__prime_home__': 'home', '__prime_movies__': 'movies', '__prime_tv__': 'tv'}
LEGACY_SECTIONS = {'__legacy_profile__': 'profile', '__legacy_mystuff__': 'mystuff',
                   '__legacy_sports__': 'sports', '__legacy_news__': 'news', '__legacy_live__': 'live'}


class CatalogSigninRequired(PublicCatalogError):
    """The saved account does not have the full website cookie session yet."""


def dashboard_packet(g, account_name=''):
    """Legacy category tiles. Aurora home loads the signed-in storefront instead."""
    profile_name = g.addon.getSetting('ProfileName') or account_name or 'Profiles'
    tiles = ((profile_name, '__legacy_profile__', 'profile'),
             ('My Stuff', '__legacy_mystuff__', 'my-stuff'),
             ('Home', '__prime_home__', 'home'),
             ('Movies', '__prime_movies__', 'cinema'),
             ('TV shows', '__prime_tv__', 'tv'),
             ('Sports', '__legacy_sports__', 'sports'),
             ('News', '__legacy_news__', 'news'),
             ('Live TV', '__legacy_live__', 'live-tv'))
    rows = []
    for title, path, artwork in tiles:
        image = join(g.PLUGIN_PATH, 'resources', 'skins', 'Default', 'media',
                     'category-' + artwork + '.webp')
        rows.append({'title': title, 'url': g.pluginid + 'pv/browse/' + path,
                     'video': False, 'info': {'thumb': image, 'poster': image, 'fanart': image}})
    return {'rows': rows, 'next': None}


_TITLE_TYPES = ('tvshow', 'season', 'episode', 'movie', 'video', 'event', 'live',
                'storefront', 'film', 'show', 'series')


def is_title_card(info, playable=False):
    return playable or (info or {}).get('mediatype') in _TITLE_TYPES


def _card_info(card):
    """Listing fields the storefront actually returned. Missing fields stay absent."""
    info = {
        'thumb': card.artwork_url,
        'poster': card.poster_url or card.artwork_url,
        'mediatype': card.mediatype or 'storefront',
    }
    if card.fanart_url:
        info['fanart'] = card.fanart_url
    if card.plot:
        info['plot'] = card.plot
    if card.year:
        info['year'] = card.year
    if card.duration:
        info['duration'] = card.duration
    if card.rating:
        info['mpaa'] = card.rating
    if card.genres:
        info['genre'] = [part.strip() for part in card.genres.split(',') if part.strip()]
    if card.season:
        info['season'] = card.season
    if card.episode:
        info['episode'] = card.episode
    if card.seasons:
        info['seasons'] = card.seasons
    return info


class DirectorySink:
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    def addSortMethod(self, *args, **kwargs):
        pass

    def endOfDirectory(self, *args, **kwargs):
        pass


class ProviderBridge:
    def __init__(self, provider, globals_instance, initialize_provider=None):
        self.module = None
        self.provider = provider
        self.g = globals_instance
        self.initialize_provider = initialize_provider
        self.epoch = account_epoch(self.g.DATA_PATH)
        self.public_pages = {}
        self.continuations = {}
        self.continuation_paths = {}
        self.details = {}
        self.legacy_routes = {}
        self.operation_observer = None

    def _active(self, active):
        if not current_account(self.g.DATA_PATH, self.epoch):
            self.public_pages.clear()
            self.continuations.clear()
            self.continuation_paths.clear()
            self.details.clear()
            self.legacy_routes.clear()
            if self.provider is not None:
                self.provider._prime_scene_details = {}
            self.provider = None
            return False
        return active()

    def ensure_provider(self):
        if not self._active(lambda: True):
            raise CatalogSigninRequired('The account changed. Open Prime Scene and sign in again.')
        if self.provider is None:
            initialized = self.initialize_provider() if self.initialize_provider is not None else None
            self.provider = initialized if initialized not in (None, False, True) else getattr(self.g, 'pv', None)
            if self.provider is None:
                raise CatalogSigninRequired('Sign in to Prime Scene before opening this section.')
        if self.module is None:
            from . import web_api
            self.module = web_api
        # Storefront detail URLs remain the exact server links. The established
        # detail parser supplies the actual title/episode playback identifiers.
        details = getattr(self.provider, '_prime_scene_details', {})
        details.update(self.details)
        self.provider._prime_scene_details = details
        return self.provider

    def _observe(self, active):
        return self.operation_observer or DiagnosticLog(
            self.g.DATA_PATH, self.g.addon.getAddonInfo('version'),
            active=lambda: current_account(self.g.DATA_PATH, self.epoch), operation='catalog')

    def _continuation(self, page, next_url, previous_urls):
        if not next_url:
            return None
        if next_url in previous_urls:
            raise PublicCatalogError('Amazon returned a repeated catalog continuation. Reopen the category.')
        key = (page, next_url, previous_urls)
        path = self.continuation_paths.get(key)
        if path is None:
            path = '__prime_page__/' + token_hex(12) + '/nextPage'
            self.continuations[path] = (self.epoch, page, next_url, previous_urls)
            self.continuation_paths[key] = path
        return path

    def public_packet(self, page, active, continuation=None, previous_urls=()):
        if not self._active(active):
            return None
        observe = self._observe(active)
        key = (self.epoch, page, continuation)
        cached = self.public_pages.get(key)
        if cached and monotonic() - cached[0] < 120:
            result = cached[1]
            observe({'event': 'catalog_cache_hit', 'section': page, 'cards': len(result.cards),
                     'has_next': bool(result.next_url)})
        else:
            from . import prime_signin, session_store
            jar = None
            try:
                jar = session_store.load_session(self.g.DATA_PATH)
                for cookie in jar:
                    observe.secret(cookie.value)
                if not self._active(active):
                    return None
                if not prime_signin.has_authentication_cookie(jar):
                    observe({'event': 'catalog_session_missing', 'section': page,
                             'result': 'failure', 'cookie_objects': len(list(jar))})
                    raise CatalogSigninRequired('Use the account button and choose Sign in again to reconnect the website session.')
                opener = prime_signin.catalog_opener(
                    jar, observer=observe, headers=session_store.session_headers(self.g.DATA_PATH))
                try:
                    result = fetch_page(page, opener=opener, continuation=continuation, observer=observe)
                finally:
                    for cookie in jar:
                        observe.secret(cookie.value)
                if not self._active(active):
                    return None
                persisted = session_store.persist_session(self.g.DATA_PATH, self.epoch, jar)
                observe({'event': 'catalog_session_refresh', 'stored': persisted,
                         'cookie_objects': len(list(jar))})
            except Exception as error:
                observe({'event': 'catalog_bridge_failed', 'section': page, 'result': 'failure',
                         'exception': exception_details(error)})
                raise
            finally:
                if jar is not None:
                    jar.clear()
            self.public_pages[key] = (monotonic(), result)
        if not self._active(active):
            return None
        rows = []
        for card in result.cards:
            url = safe_url(card.detail_url, STOREFRONT_HOSTS)
            identity = detail_id(url) if url else None
            if not identity:
                continue
            self.details.setdefault(identity, {'title': card.title, 'lazyLoadURL': url})
            rows.append({'title': card.title,
                         'url': self.g.pluginid + 'pv/browse/__storefront__/' + identity,
                         'video': False, 'identity': identity,
                         'info': _card_info(card)})
        visited = previous_urls + ((continuation,) if continuation else ())
        next_path = self._continuation(page, result.next_url, visited)
        observe({'event': 'catalog_packet_ready', 'section': page, 'rows': len(rows),
                 'has_next': bool(next_path), 'result': 'success'})
        return {'rows': rows, 'next': next_path}

    def _legacy_section(self, section, publish, active):
        self.ensure_provider()
        packet = self.browse('root', lambda packet: None, incremental=False, whole_page=True)
        if not self._active(active):
            return
        matches = []
        for row in packet['rows']:
            url = row['url']
            title = re.sub(r'\[/?[^\]]+\]', '', row['title'].decode('utf-8', 'replace')
                           if isinstance(row['title'], bytes) else row['title']).strip().casefold()
            node = cached_node(self.provider, url[len(self.g.pluginid):]) or {}
            address = node.get('ref') or node.get('lazyLoadURL') or ''
            route_path = urlsplit(address).path.casefold()
            match = ((section == 'profile' and row.get('profile_route'))
                     or (section == 'mystuff' and (re.search(r'\b(my stuff|watch ?list|my library)\b', title)
                                                   or '/watchlist' in route_path or url.endswith('/Watchlist')))
                     or (section == 'sports' and (re.search(r'\bsports?\b', title) or '/sports' in route_path))
                     or (section == 'news' and (re.search(r'\bnews\b', title) or '/news' in route_path))
                     or (section == 'live' and (re.search(r'\blive (tv|television|channels)\b', title)
                                               or route_path.rstrip('/').endswith(('/live', '/live-tv', '/livetv')))))
            if match:
                matches.append(row)
        observe = self._observe(active)
        observe({'event': 'legacy_section_resolved', 'section': section, 'root_rows': len(packet['rows']),
                 'matches': len(matches), 'result': 'success' if len(matches) == 1 else 'failure'})
        if len(matches) != 1:
            raise PublicCatalogError('Amazon did not return one available ' +
                                     {'profile': 'Profiles', 'mystuff': 'My Stuff', 'sports': 'Sports',
                                      'news': 'News', 'live': 'Live TV'}[section] + ' section.')
        prefix = self.g.pluginid + 'pv/browse/'
        target = matches[0]['url']
        if not target.startswith(prefix):
            raise PublicCatalogError('Amazon returned an unsupported section route.')
        self.browse(target[len(prefix):], publish, incremental=False, whole_page=True)

    def _profile_route(self, observed_path):
        for alias, (kind, path) in self.legacy_routes.items():
            if kind == 'profile' and path == observed_path:
                return alias
        alias = '__prime_profile__/' + token_hex(12)
        self.legacy_routes[alias] = ('profile', observed_path)
        return alias

    def browse_progressive(self, path, publish, active, query=None):
        observe = self._observe(active)
        previous_observer = self.operation_observer
        self.operation_observer = observe
        started = monotonic()
        section = STOREFRONT_PAGES.get(path) or LEGACY_SECTIONS.get(path)
        operation = ('search' if query is not None else 'dashboard' if path == 'root'
                     else 'storefront' if path in STOREFRONT_PAGES
                     else 'continuation' if path.startswith('__prime_page__/')
                     else 'section' if path in LEGACY_SECTIONS else 'details_or_section')
        observe({'event': 'catalog_bridge_started', 'stage': operation, 'section': section})
        try:
            self._browse_progressive(path, publish, active, query)
            observe({'event': 'catalog_bridge_completed', 'stage': operation,
                     'result': 'success' if self._active(active) else 'cancelled',
                     'elapsed_ms': round((monotonic() - started) * 1000)})
        except BaseException as error:
            observe({'event': 'catalog_bridge_operation_failed', 'stage': operation, 'result': 'failure',
                     'exception': exception_details(error), 'elapsed_ms': round((monotonic() - started) * 1000)})
            raise
        finally:
            self.operation_observer = previous_observer

    def _browse_progressive(self, path, publish, active, query=None):
        if not self._active(active):
            return
        if path == 'root' and query is None:
            # The landing screen is the signed-in storefront, not category tiles.
            packet = self.public_packet('home', active)
            if packet is not None and self._active(active):
                publish(packet)
            return
        if query is None and path in STOREFRONT_PAGES:
            packet = self.public_packet(STOREFRONT_PAGES[path], active)
            if packet is not None and self._active(active):
                publish(packet)
            return
        if query is None and path.startswith('__prime_page__/'):
            saved = self.continuations.get(path)
            if saved is None or saved[0] != self.epoch:
                raise PublicCatalogError('This catalog page expired. Reopen the category.')
            _, page, continuation, visited = saved
            packet = self.public_packet(page, active, continuation, visited)
            if packet:
                publish(packet)
            return
        if query is None and path in LEGACY_SECTIONS:
            self._legacy_section(LEGACY_SECTIONS[path], publish, active)
            return
        self.ensure_provider()
        self.browse(path, publish, query=query, incremental=False, whole_page=True)

    def browse(self, path, publish, query=None, incremental=True, whole_page=False):
        """Used only by one background worker; restore all bindings on exit."""
        provider = self.ensure_provider()
        module = self.module
        saved = {key: getattr(module, key) for key in
                 ('addDir', 'addVideo', 'setContentAndView', 'xbmcplugin')}
        previous_progress = getattr(provider, '_stream_progress', None)
        previous_listing = getattr(provider, '_prime_scene_listing_card', None)
        capture = []

        def identity(entry):
            if not entry:
                return None
            url = entry.get('ref') or entry.get('lazyLoadURL') or ''
            return detail_id(url) or entry.get('metadata', {}).get('compactGTI')

        def listing_card(node, item, url, title=None, artwork=None):
            base = getattr(self.g, 'BaseUrl', '')
            if not isinstance(url, str) or not url or not base:
                return
            try:
                url = urljoin(base, url)
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or parsed.netloc != urlsplit(base).netloc:
                    return
            except ValueError:
                return
            label = title or item.get('title') or item.get('heading') or item.get('displayTitle')
            if isinstance(label, dict):
                label = label.get('text') or label.get('title')
            if not isinstance(label, str) or not label.strip():
                return
            match = provider._reURN.search(parsed.path) if not title else None
            cover = artwork if isinstance(artwork, str) and artwork.startswith('https://') else first_artwork(item)
            key = 'listed_' + (match.group(1) if match else str(len(node)))
            node.setdefault(key, {'title': label, 'lazyLoadURL': url, 'metadata': {
                'prime_scene_card': bool(match), 'artmeta': {'thumb': cover} if cover else {}}})

        def directory(name, mode='', url='', infoLabels=None, opt='', catalog='Browse',
                      cm=None, page=1, export=False, thumb=None):
            info = deepcopy(infoLabels or {})
            node = cached_node(provider, url)
            if node and node.get('metadata', {}).get('prime_scene_card'):
                info['mediatype'] = 'storefront'
            if thumb and not info.get('thumb'):
                info['thumb'] = thumb
            if not is_title_card(info):
                apply_category_art(info, provider, url, self.g.PLUGIN_PATH, name, curated=(path == 'root'))
            profile_route = url.startswith('pv/profiles/')
            if profile_route:
                url = 'pv/browse/' + self._profile_route(url[len('pv/profiles/'):])
            capture.append({'title': name, 'url': self.g.pluginid + url,
                            'video': False, 'info': info,
                            'profile_route': profile_route,
                            'identity': identity(node) if is_title_card(info) else None})

        def video(name, asin, infoLabels, cm=None, export=False):
            # Identical parameters to the upstream itemlisting.addVideo route.
            params = {'asin': asin, 'mode': 'PlayVideo', 'name': name,
                      'adult': infoLabels.get('isAdult', 0)}
            url = self.g.pluginid + '?' + urlencode(params)
            url += '&trailer={}&selbitrate=0'.format(
                {'live': 2, 'event': 3}.get(infoLabels['contentType'], 0))
            capture.append({'title': name, 'url': url, 'video': True,
                            'info': deepcopy(infoLabels),
                            'identity': identity(provider._videodata.get(asin))})

        def packet():
            prefix = self.g.pluginid + 'pv/browse/'
            rows, next_path = [], None
            for row in capture:
                if row['url'].startswith(prefix) and row['url'].endswith('/nextPage'):
                    next_path = row['url'][len(prefix):]
                else:
                    rows.append(row)
            return {'rows': deepcopy(rows), 'next': next_path}

        def progress(node, breadcrumb):
            if not incremental:
                # Publish a continuation in one grid update. The provider
                # still suppresses its own page-loading notifications.
                return
            # Reuse the original renderer against the partially populated node.
            # It supplies the same metadata and playback identifiers as Classic.
            old_traverse = provider._TraverseCatalog
            provider._TraverseCatalog = lambda *a, **k: (node, breadcrumb)
            capture.clear()
            try:
                provider.Browse('/'.join(breadcrumb))
                publish(packet())
            finally:
                provider._TraverseCatalog = old_traverse
                capture.clear()

        try:
            module.addDir, module.addVideo = directory, video
            module.setContentAndView = lambda *a, **kw: None
            module.xbmcplugin = DirectorySink(saved['xbmcplugin'])
            provider._stream_progress = progress
            provider._prime_scene_listing_card = listing_card if whole_page else None
            if query is not None:
                provider.Search(query)
            elif path in self.legacy_routes:
                kind, observed_path = self.legacy_routes[path]
                if kind != 'profile':
                    raise PublicCatalogError('This section route is unavailable.')
                provider.Profile(observed_path)
                self.public_pages.clear()
                self.continuations.clear()
                self.continuation_paths.clear()
            elif path == 'root':
                provider.BrowseRoot()
            else:
                provider.Browse(path)
            result = packet()
            publish(result)
            return result
        finally:
            provider._stream_progress = previous_progress
            provider._prime_scene_listing_card = previous_listing
            for key, value in saved.items():
                setattr(module, key, value)
