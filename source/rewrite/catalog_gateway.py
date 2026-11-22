"""Native storefront/title gateway; no provider monkey-patching or fallback."""
from contextlib import contextmanager
from copy import deepcopy
from secrets import token_hex
from time import monotonic
from urllib.parse import quote_plus,unquote_plus,urlsplit
from .account_state import account_epoch,current_account
from .catalog_access import prime_included
from .public_catalog import PublicCatalogError,STOREFRONT_HOSTS,ORIGIN,detail_id,fetch_page,safe_url,_read_page
from .prime_diagnostics import DiagnosticLog,exception_details
from .http_transport import HttpTransport
from .detail_catalog import parse_detail,search_request

STOREFRONT_PAGES={'__watching__':'watching','__prime_home__':'movies','__prime_movies__':'movies',
    '__prime_tv__':'tv','__prime_sports__':'sports','__prime_purchases__':'purchases'}
class CatalogSigninRequired(PublicCatalogError):pass

def _card_info(card):
    """Listing fields the storefront actually returned. Missing fields stay absent."""
    info = {
        'percentplayed': card.percentplayed, 'playcount': card.playcount,
        'thumb': card.landscape_url or card.artwork_url,
        'poster': card.poster_url or card.artwork_url,
        'mediatype': card.mediatype or 'storefront',
    }
    if card.landscape_url:
        info['landscape'] = card.landscape_url
    info['primeincluded'] = prime_included(card.access_evidence)
    info['access_evidence'] = card.access_evidence
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

class CatalogGateway:
    def __init__(self,globals_instance):
        self.g=globals_instance;self.epoch=account_epoch(self.g.DATA_PATH)
        self.public_pages={};self.continuations={};self.continuation_paths={};self.details={}
        self.operation_observer=None

    def _active(self,active):
        valid=current_account(self.g.DATA_PATH,self.epoch) and active()
        if not valid:
            self.public_pages.clear();self.continuations.clear();self.continuation_paths.clear();self.details.clear()
        return valid

    def _observe(self,active):
        return self.operation_observer or DiagnosticLog(self.g.DATA_PATH,self.g.addon.getAddonInfo('version'),
            active=lambda:self._active(active),operation='catalog')

    @contextmanager
    def session(self,active,observe):
        from .session_store import load_session,persist_session,session_headers
        from .prime_signin import has_authentication_cookie
        if not self._active(active):raise CatalogSigninRequired('The account changed.')
        jar=load_session(self.g.DATA_PATH)
        try:
            if not has_authentication_cookie(jar):raise CatalogSigninRequired('Use the account button to sign in again.')
            headers=session_headers(self.g.DATA_PATH)
            headers.setdefault('User-Agent',self.g.configured_user_agent())
            client=HttpTransport(self.g.DATA_PATH,self.epoch,observe,hosts=STOREFRONT_HOSTS,
                jar=jar,headers=headers)
            yield client
            if self._active(active):persist_session(self.g.DATA_PATH,self.epoch,jar)
        finally:jar.clear()

    def _routes(self,packet,address,previous=()):
        for row in packet.get('seasons') or []:
            self.details[row['identity']]={'title':row['title'],'lazyLoadURL':row['detail_url']}
            row['url']=self.g.pluginid+'pv/browse/__storefront__/'+quote_plus(row['identity'])
            row['video']=False
        packet['next']=self._continuation('tv_detail',packet.pop('next_url',None),previous+(address,))
        return packet

    def read_tv(self,path,active):
        return self.detail(path,active)

    def detail(self,path,active):
        observe=self._observe(active);identifier=unquote_plus(path.rsplit('/',1)[-1])
        saved=self.continuations.get(path);previous=()
        if saved and saved[0]==self.epoch and saved[1]=='tv_detail':
            address,previous=saved[2],saved[3]
        else:
            entry=self.details.get(identifier) or {}
            address=entry.get('lazyLoadURL') or entry.get('ref')
        if not safe_url(address,STOREFRONT_HOSTS):raise PublicCatalogError('The selected title has no trusted detail URL.')
        try:
            with self.session(active,observe) as client:
                response=client.request(address,headers={'Accept':'text/html,application/json','Cache-Control':'no-cache'},stage='title_fetch')
                data=response.data.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
                if saved:
                    from .tv_catalog import parse_tv_page
                    packet=parse_tv_page(data,response.headers.get_content_type(),response.address,self.g.pluginid,observe)
                else:packet=parse_detail(data,response.headers.get_content_type(),response.address,self.g.pluginid,observe)
            if not self._active(active):return None
            return self._routes(packet,address,previous)
        except Exception as error:
            observe({'event':'title_read_failed','stage':'title_fetch_or_parse','exception':exception_details(error),'result':'failure'})
            raise

    def search(self,phrase,active):
        observe=self._observe(active)
        with self.session(active,observe) as client:
            landing=client.request(ORIGIN+'/storefront',stage='search_form_discovery')
            address,data=search_request(landing.data.decode('utf-8',errors='replace'),landing.address,phrase)
            response=client.request(address,data=data,headers={'Content-Type':'application/x-www-form-urlencoded'},stage='search_fetch')
            result,structure=_read_page(response.data.decode('utf-8',errors='replace'),response.headers.get_content_type(),response.address)
        rows=[]
        for card in result.cards:
            identifier=detail_id(card.detail_url)
            if not identifier:continue
            self.details[identifier]={'title':card.title,'lazyLoadURL':card.detail_url}
            rows.append({'title':card.title,'identity':identifier,'detail_url':card.detail_url,'video':False,
                'url':self.g.pluginid+'pv/browse/__storefront__/'+quote_plus(identifier),'info':_card_info(card)})
        observe(dict(structure,event='search_parsed',stage='search_parse',cards=len(rows),result='success'))
        return {'rows':rows,'next':self._continuation('search',result.next_url,(response.address,))}

    def browse_progressive(self,path,publish,active,query=None):
        if not self._active(active):return
        if query is not None:packet=self.search(query,active)
        elif path in STOREFRONT_PAGES:packet=self.public_packet(STOREFRONT_PAGES[path],active)
        elif path.startswith('__prime_page__/'):
            saved=self.continuations.get(path)
            if not saved or saved[0]!=self.epoch:raise PublicCatalogError('This page expired. Reopen its category.')
            _,page,address,previous=saved
            if page=='tv_detail':packet=self.detail(path,active)
            elif page=='search':
                # Search continuations are exact provider links, with the normal
                # card parser and no guess at its pagination token protocol.
                observe=self._observe(active)
                with self.session(active,observe) as client:
                    response=client.request(address,stage='search_next_page')
                    result,structure=_read_page(response.data.decode('utf-8',errors='replace'),response.headers.get_content_type(),response.address)
                rows=[]
                for card in result.cards:
                    identifier=detail_id(card.detail_url)
                    if identifier:
                        self.details[identifier]={'title':card.title,'lazyLoadURL':card.detail_url}
                        rows.append({'title':card.title,'identity':identifier,'detail_url':card.detail_url,'video':False,
                            'url':self.g.pluginid+'pv/browse/__storefront__/'+quote_plus(identifier),'info':_card_info(card)})
                packet={'rows':rows,'next':self._continuation('search',result.next_url,previous+(address,))}
                observe(dict(structure,event='search_page_parsed',result='success'))
            else:packet=self.public_packet(page,active,address,previous)
        elif path.startswith('__storefront__/'):packet=self.detail(path,active)
        else:raise PublicCatalogError('Unsupported catalog route. Reopen a top category.')
        if packet is not None and self._active(active):publish(packet)
        return packet

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
        if page == 'watching': self.public_pages.pop(key,None)
        cached = self.public_pages.get(key) if page != 'watching' else None
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
            if page != 'watching': self.public_pages[key] = (monotonic(), result)
        if not self._active(active):
            return None
        rows = []
        excluded, unknown, wrong_type = 0, 0, 0
        for card in result.cards:
            if page in ('movies', 'tv') and not prime_included(card.access_evidence):
                excluded += 1
                unknown += not bool(card.access_evidence)
                continue
            if ((page == 'movies' and card.mediatype != 'movie')
                    or (page == 'tv' and card.mediatype not in ('tvshow', 'season', 'episode'))):
                wrong_type += 1
                continue
            if page == 'purchases' and not result.library_confirmed:
                raise PublicCatalogError('The account library could not be confirmed. Open Diagnostics for details.')
            url = safe_url(card.detail_url, STOREFRONT_HOSTS)
            identity = detail_id(url) if url else None
            if not identity:
                continue
            self.details.setdefault(identity, {'title': card.title, 'lazyLoadURL': url})
            rows.append({'title': card.title,
                         'url': self.g.pluginid + 'pv/browse/__storefront__/' + identity,
                         'detail_url': url,
                         'video': False, 'identity': identity,
                         'info': _card_info(card)})
        if page == 'watching':
            from .watch_home import complete
            from .home_catalog import unique_progress_rows
            rows, duplicates = unique_progress_rows([row for row in rows if not complete(row.get('info') or {})])
            observe({'event':'progress_snapshot_ready','fresh':True,'source':'amazon_only',
                'duplicates_removed':duplicates,'rows':len(rows),'result':'success'})
            for row in rows: row['watching_only'] = True
        visited = previous_urls + ((continuation,) if continuation else ())
        next_path = self._continuation(page, result.next_url, visited)
        observe({'event': 'catalog_packet_ready', 'section': page, 'rows': len(rows),
                 'source_cards': len(result.cards), 'prime_only': page in ('movies', 'tv'),
                 'access_excluded': excluded, 'access_unknown': unknown,
                 'type_excluded': wrong_type,
                 'library_confirmed': result.library_confirmed,
                 'has_next': bool(next_path), 'result': 'success'})
        return {'rows': rows, 'next': next_path, 'library_confirmed': result.library_confirmed,
                'catalog_policy': 'prime_only' if page in ('movies', 'tv') else
                                  'account_library' if page == 'purchases' else 'watch_progress' if page == 'watching' else 'sports'}
