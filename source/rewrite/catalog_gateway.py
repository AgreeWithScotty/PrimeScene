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
from .collection_paging import identity as continuation_identity
from . import collection_paging
from .detail_catalog import parse_detail,search_request

STOREFRONT_PAGES={'__watching__':'watching','__prime_home__':'movies','__prime_movies__':'movies',
    '__prime_tv__':'tv','__prime_sports__':'sports','__prime_channels__':'channels','__prime_purchases__':'purchases',
    '__prime_watchlist__':'watchlist','__purchases__/movies':'purchased_movies','__purchases__/tv':'purchased_tv'}
from .public_catalog import PublicSigninRequired as CatalogSigninRequired

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
    if card.resume_seconds is not None:info['resume_seconds']=card.resume_seconds
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
            text=response.data.decode('utf-8',errors='replace');content_type=response.headers.get_content_type()
            result,structure=_read_page(text,content_type,response.address)
            observe(dict(structure,event='search_response_parsed',stage='search_parse',result='success'))
            if structure.get('provider_error'):raise PublicCatalogError('Amazon returned a search service error. Open Diagnostics.')
            if structure.get('sign_in_form_present') or structure.get('verification_form_present'):
                raise CatalogSigninRequired('Use Sign out, then sign in again to reconnect your Amazon session.')
            next_request=collection_paging.discover(text,content_type,response.address,observe)
        rows=[]
        for card in result.cards:
            identifier=detail_id(card.detail_url)
            if not identifier:continue
            self.details[identifier]={'title':card.title,'lazyLoadURL':card.detail_url}
            rows.append({'title':card.title,'identity':identifier,'detail_url':card.detail_url,'video':False,
                'url':self.g.pluginid+'pv/browse/__storefront__/'+quote_plus(identifier),'info':_card_info(card)})
        observe(dict(structure,event='search_parsed',stage='search_parse',cards=len(rows),result='success'))
        return {'rows':rows,'next':self._continuation('search',next_request or result.next_url,(response.address,))}

    def browse_progressive(self,path,publish,active,query=None):
        if not self._active(active):return
        if query is not None:packet=self.search(query,active)
        elif path=='__service_directory__':return self.services(active,publish)
        elif path.startswith('__service__/'):
            from .service_catalog import service_address
            address=service_address(unquote_plus(path[len('__service__/'):]))
            if not address:raise PublicCatalogError('The service has no trusted catalog address. Open Diagnostics.')
            packet=self.public_packet('channels',active,address)
        elif path in STOREFRONT_PAGES:packet=self.public_packet(STOREFRONT_PAGES[path],active)
        elif path.startswith('__genre__/'):
            _,section,encoded=path.split('/',2)
            if section not in ('movies','tv'):raise PublicCatalogError('Invalid genre section')
            address=safe_url(unquote_plus(encoded),STOREFRONT_HOSTS)
            if not address:raise PublicCatalogError('Invalid provider genre address')
            packet=self.public_packet(section,active,address)
        elif path.startswith('__prime_page__/'):
            saved=self.continuations.get(path)
            if not saved or saved[0]!=self.epoch:raise PublicCatalogError('This page expired. Reopen its category.')
            _,page,address,previous=saved
            if page=='tv_detail':packet=self.detail(path,active)
            elif page=='search':
                # Follow only provider-declared links or validated Grid tokens.
                observe=self._observe(active)
                with self.session(active,observe) as client:
                    if isinstance(address,dict):
                        target,data,headers=collection_paging.request_arguments(address)
                        response=client.request(target,data=data,headers=headers,stage='search_next_page')
                    else:response=client.request(address,stage='search_next_page')
                    text=response.data.decode('utf-8',errors='replace');content_type=response.headers.get_content_type()
                    result,structure=_read_page(text,content_type,response.address)
                    observe(dict(structure,event='search_response_parsed',stage='search_parse',result='success'))
                    if structure.get('provider_error'):raise PublicCatalogError('Amazon returned a search service error. Open Diagnostics.')
                    if structure.get('sign_in_form_present') or structure.get('verification_form_present'):
                        raise CatalogSigninRequired('Use Sign out, then sign in again to reconnect your Amazon session.')
                    next_request=(collection_paging.advance(address,text,content_type,observe) if isinstance(address,dict) else
                        collection_paging.discover(text,content_type,response.address,observe))
                rows=[]
                for card in result.cards:
                    identifier=detail_id(card.detail_url)
                    if identifier:
                        self.details[identifier]={'title':card.title,'lazyLoadURL':card.detail_url}
                        rows.append({'title':card.title,'identity':identifier,'detail_url':card.detail_url,'video':False,
                            'url':self.g.pluginid+'pv/browse/__storefront__/'+quote_plus(identifier),'info':_card_info(card)})
                following=next_request if isinstance(address,dict) else next_request or result.next_url
                packet={'rows':rows,'next':self._continuation('search',following,previous+(continuation_identity(address),))}
                observe(dict(structure,event='search_page_parsed',result='success'))
            else:packet=self.public_packet(page,active,address,previous)
        elif path.startswith('__storefront__/'):packet=self.detail(path,active)
        else:raise PublicCatalogError('Unsupported catalog route. Reopen a top category.')
        if packet is not None and self._active(active):publish(packet)
        return packet

    def services(self,active,publish=None):
        from .service_catalog import discover
        observe=self._observe(active);started=monotonic()
        def packet(tiles,complete):
            return {'rows':[],'tiles':tiles,'next':None,'catalog_policy':'services','directory_complete':complete,
                'empty_confirmed':not tiles and complete}
        def receive(tiles,complete):
            if publish is not None and self._active(active):publish(packet(tiles,complete))
        try:
            with self.session(active,observe) as client:
                tiles,complete=discover(client,observe,receive,lambda:self._active(active))
        except Exception as error:
            observe({'event':'service_directory_failed','stage':'service_directory_fetch_or_parse',
                'elapsed_ms':round((monotonic()-started)*1000),
                'exception':exception_details(error),'result':'failure'})
            raise
        if not self._active(active):return None
        if not tiles and not complete:raise PublicCatalogError('Amazon did not return its service directory. Open Diagnostics.')
        return packet(tiles,complete)

    def _continuation(self, page, next_url, previous_urls):
        if not next_url:
            return None
        signature=continuation_identity(next_url)
        if signature in previous_urls:
            raise PublicCatalogError('Amazon returned a repeated catalog continuation. Reopen the category.')
        key = (page, signature, previous_urls)
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
        key = (self.epoch, page, continuation_identity(continuation))
        if page in ('watching','watchlist'): self.public_pages.pop(key,None)
        cached = self.public_pages.get(key) if page not in ('watching','watchlist') else None
        if cached and monotonic() - cached[0] < 120:
            result = cached[1]
            observe({'event': 'catalog_cache_hit', 'section': page, 'cards': len(result.cards),
                     'has_next': bool(result.next_url or result.next_request)})
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
                    raise CatalogSigninRequired('Use Sign out, then sign in again to reconnect the website session.')
                opener = prime_signin.catalog_opener(
                    jar, observer=observe, headers=session_store.session_headers(self.g.DATA_PATH))
                try:
                    result = fetch_page('purchases' if page.startswith('purchased_') else page,
                        opener=opener, continuation=continuation, observer=observe)
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
            if page not in ('watching','watchlist'): self.public_pages[key] = (monotonic(), result)
        if not self._active(active):
            return None
        rows = []
        excluded, unknown, wrong_type,invalid_detail = 0, 0, 0,0
        for card in result.cards:
            if page in ('movies', 'tv') and not prime_included(card.access_evidence):
                excluded += 1
                unknown += not bool(card.access_evidence)
                continue
            if ((page in ('movies','purchased_movies') and card.mediatype != 'movie')
                    or (page in ('tv','purchased_tv') and card.mediatype not in ('tvshow', 'season', 'episode'))):
                wrong_type += 1
                continue
            if (page == 'purchases' or page.startswith('purchased_')) and not result.library_confirmed:
                raise PublicCatalogError('The account library could not be confirmed. Open Diagnostics for details.')
            if page=='watchlist' and not result.watchlist_confirmed:
                raise PublicCatalogError('The Amazon Watchlist could not be confirmed. Open Diagnostics for details.')
            url = safe_url(card.detail_url, STOREFRONT_HOSTS)
            identity = detail_id(url) if url else None
            if not identity:
                invalid_detail+=1
                continue
            self.details.setdefault(identity, {'title': card.title, 'lazyLoadURL': url})
            info=_card_info(card)
            if page in ('sports','channels'):
                info['access_label']=('Included with Prime' if info['primeincluded'] else
                    'Add-on subscription' if page=='channels' else 'Check event access')
            rows.append({'title': card.title,
                         'url': self.g.pluginid + 'pv/browse/__storefront__/' + identity,
                         'detail_url': url,
                         'video': False, 'identity': identity,
                         'info': info})
        if page == 'watching':
            from .watch_home import complete
            from .home_catalog import unique_progress_rows
            # A completed episode does not complete its containing TV series.
            finished=[row for row in rows if (row.get('info') or {}).get('mediatype') in ('movie','episode')
                      and complete(row.get('info') or {})]
            rows, duplicates = unique_progress_rows([row for row in rows if row not in finished])
            observe({'event':'progress_snapshot_ready','fresh':True,'source':'amazon_only',
                'duplicates_removed':duplicates,'completed_titles_removed':len(finished),
                'movie_rows':sum((row.get('info') or {}).get('mediatype')=='movie' for row in rows),
                'tv_rows':sum((row.get('info') or {}).get('mediatype') in ('tvshow','season','episode') for row in rows),
                'position_fields':sum('resume_seconds' in (row.get('info') or {}) for row in rows),
                'rows':len(rows),'result':'success'})
            for row in rows: row['watching_only'] = True
        visited = previous_urls + ((continuation_identity(continuation),) if continuation else ())
        next_path = self._continuation(page, result.next_request or result.next_url, visited)
        personal=page in ('watchlist','purchases','purchased_movies','purchased_tv')
        filtered_empty=bool(result.cards) and wrong_type==len(result.cards) and not next_path and not result.pagination_unavailable
        empty_confirmed=result.empty_confirmed or (filtered_empty if personal else bool(result.cards) and not rows and not next_path)
        if page=='watching':
            empty_confirmed=result.empty_confirmed or bool(result.cards) and len(finished)==len(result.cards) and not next_path
        if (personal or page=='watching') and not rows and not next_path and not empty_confirmed:
            observe({'event':'personal_collection_unknown','stage':'card_mapping','section':page,
                'source_cards':len(result.cards),'invalid_detail_rows':invalid_detail,
                'empty_confirmed':False,'result':'failure'})
            raise PublicCatalogError('The personal list could not be read; no empty account list was confirmed.')
        observe({'event': 'catalog_packet_ready', 'section': page, 'rows': len(rows),
                 'empty_confirmed':result.empty_confirmed,'empty_after_filtering':bool(result.cards) and not rows and not next_path,
                 'source_cards': len(result.cards), 'prime_only': page in ('movies', 'tv'),
                 'access_excluded': excluded, 'access_unknown': unknown,
                 'type_excluded': wrong_type,
                 'library_confirmed': result.library_confirmed,
                 'watchlist_confirmed':result.watchlist_confirmed,
                 'pagination_unavailable':result.pagination_unavailable,
                 'has_next': bool(next_path), 'result': 'partial' if result.pagination_unavailable else 'success'})
        return {'rows': rows, 'next': next_path, 'library_confirmed': result.library_confirmed,
                'watchlist_confirmed':result.watchlist_confirmed,
                'empty_confirmed':empty_confirmed,
                'personal_schema':2 if personal else None,
                'pagination_unavailable':result.pagination_unavailable,
                'notice':('Some titles are shown, but more could not be retrieved. Select Watchlist again to retry.' if page=='watchlist' else
                    'Some titles are shown, but more could not be retrieved. Reopen Purchases to retry.') if result.pagination_unavailable else '',
                'collection_state':'partial' if result.pagination_unavailable else 'items' if rows else 'empty' if empty_confirmed else 'continuation' if next_path else 'unknown',
                'catalog_policy': 'prime_only' if page in ('movies', 'tv') else
                                  'account_library' if page == 'purchases' or page.startswith('purchased_') else 'watch_progress' if page == 'watching' else page}
