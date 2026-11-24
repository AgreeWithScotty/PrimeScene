"""Original Amazon backdrops with account-scoped, cross-card artwork reservations."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event,Lock
from time import monotonic
from urllib.parse import urlsplit,parse_qsl,urlunsplit,urlencode
import json,re
from .account_state import current_account,persist_if_current
from .catalog_gateway import CatalogGateway
from .detail_catalog import documents,state_records
from .image_quality import source_image
from .prime_diagnostics import DiagnosticLog,exception_details,route
from .regional_hosts import PRIME_WEB_HOSTS,url_rejection,storefront_path
from .public_catalog import _read_page,_shot_map,_first_shot,safe_url,detail_id,IMAGE_HOSTS,STOREFRONT_HOSTS

_EXTRA={'__prime_movies__':'movies','__prime_tv__':'tv','__purchases__/movies':'movies','__purchases__/tv':'tv'}

def genre_identity(address):
    """Cache identity only; requests still use Amazon's exact supplied URL."""
    if address in _EXTRA:return address
    parts=urlsplit(address)
    if parts.scheme!='https' or parts.hostname not in PRIME_WEB_HOSTS:return address
    path=storefront_path(address).rstrip('/') or '/'
    query=urlencode(sorted((k,v) for k,v in parse_qsl(parts.query) if k not in ('ref','ref_','jic')))
    return urlunsplit(('https','www.primevideo.com',path,query,''))

def artwork_identity(address):
    parts=urlsplit(source_image(address) or '')
    # These are the same Amazon image service. Delivery query parameters do not
    # make an original image a different background.
    return parts.path if parts.hostname in IMAGE_HOSTS else ''

def image_host_inventory(values,address,report,stage):
    stack=list(values);hosts={};visited=0
    while stack and visited<100000:
        node=stack.pop();visited+=1
        if isinstance(node,dict):stack.extend(node.values())
        elif isinstance(node,list):stack.extend(node)
        elif isinstance(node,str) and node.startswith(('https://','http://')):
            try:
                parts=urlsplit(node)
                if not re.search(r'\.(?:jpe?g|png|webp|avif)$',parts.path,re.I):continue
                reason=url_rejection(node,IMAGE_HOSTS);key=(parts.hostname,reason)
                if key in hosts or len(hosts)<16:hosts[key]=hosts.get(key,0)+1
            except ValueError:continue
    report({'event':'genre_image_host_inventory','stage':stage,'address':route(address),
        'hosts':[{'host':host,'policy_reason':reason,'allowed':reason is None,'image_references':count}
            for (host,reason),count in hosts.items()],'scan_limited':bool(stack),'result':'inspected'})

def candidates(data,content_type,address):
    page,structure=_read_page(data,content_type,address)
    groups={section:[] for section in ('movies','tv')};seen=set()
    for card in page.cards:
        section='movies' if card.mediatype=='movie' else 'tv' if card.mediatype in ('tvshow','season','episode') else None
        if section and card.detail_url not in seen and len(groups[section])<32:
            groups[section].append(card);seen.add(card.detail_url)
    return groups,structure

def selected_backdrop(data,content_type,address,report=None):
    states=state_records(documents(data,content_type,address))
    selected=next((s for s in states if s.get('detail') or s.get('self')),None)
    if selected is None:
        if report:report({'event':'genre_backdrop_title_inventory','stage':'genre_backdrop_title_parse',
            'declared_states':len(states),'selected_state_present':False,'result':'unavailable'})
        return ''
    identifier=selected['pageTitleId'];record={}
    for state in states:
        if state.get('pageTitleId')!=identifier:continue
        detail=state.get('detail') or {}
        for mapping in (state.get('self') or {},detail.get('detail',detail),detail.get('headerDetail') or {}):
            if isinstance(mapping,dict) and isinstance(mapping.get(identifier),dict):record.update(mapping[identifier])
    shots=_shot_map(record);image=source_image(_first_shot(shots,('heroshot','hero','fanart','background','backdrop')))
    if report:image_host_inventory([record],address,report,'genre_backdrop_title_parse')
    if report:report({'event':'genre_backdrop_title_inventory','stage':'genre_backdrop_title_parse',
        'declared_states':len(states),'selected_state_present':True,'record_fields':sorted(record),
        'image_roles':sorted(shots),'backdrop_available':bool(image),'result':'success' if image else 'unavailable'})
    return image

def read_backdrops(client,address,report,active,*,claim=None,release=None,choices=None,candidate_ready=None,preferred=None,section_ready=None):
    pages=0
    if choices is None:
        response=client.request(address,stage='genre_backdrop_metadata',headers={'Accept':'text/html,application/json'})
        text=response.data.decode('utf-8',errors='replace');content_type=response.headers.get_content_type()
        image_host_inventory(documents(text,content_type,response.address),response.address,report,'genre_backdrop_metadata')
        choices,structure=candidates(text,content_type,response.address)
        pages=1
        if candidate_ready:candidate_ready(choices)
        report({'event':'genre_backdrop_candidates','stage':'genre_backdrop_metadata',
            'candidate_counts':{s:len(cards) for s,cards in choices.items()},'protocol_structure':structure,
            'pagination_requested':False,'result':'success'})
    result={};details=0
    for section,cards in choices.items():
        attempts=duplicates=0;chosen=False
        preferred_title=(preferred or {}).get(section)
        ordered=sorted(cards,key=lambda c:(c.detail_url!=preferred_title,not bool(c.fanart_url)))
        for card in ordered:
            if not active():break
            if claim and not claim(section,card.detail_url,''):duplicates+=1;continue
            try:
                artwork=source_image(card.fanart_url)
                if not artwork:
                    if attempts>=3:continue
                    attempts+=1;details+=1
                    detail=client.request(card.detail_url,stage='genre_backdrop_title',headers={'Accept':'text/html,application/json'})
                    artwork=selected_backdrop(detail.data.decode('utf-8',errors='replace'),detail.headers.get_content_type(),detail.address,report)
                if not safe_url(artwork,IMAGE_HOSTS):continue
                if claim and not claim(section,card.detail_url,artwork):duplicates+=1;continue
                result[section]=artwork;chosen=True
                if section_ready:section_ready(section,artwork,card.detail_url)
                break
            except Exception as error:
                report({'event':'genre_backdrop_section_failed','stage':'genre_backdrop_title','section':section,
                    'address':route(card.detail_url),'exception':exception_details(error),'result':'failure'})
            finally:
                if not chosen and release:release(section,card.detail_url)
        report({'event':'genre_backdrop_unique_selection','stage':'genre_backdrop_selection','section':section,
            'candidates':len(cards),'duplicate_candidates_skipped':duplicates,'detail_reads':attempts,
            'duplicate_fallback_used':False,'result':'success' if chosen else 'cancelled' if not active() else 'unavailable'})
    report({'event':'genre_backdrop_metadata_complete','stage':'genre_backdrop_metadata','genre_pages_read':pages,
        'title_pages_read':details,'sections_with_backdrop':sorted(result),'cover_fallback_used':False,
        'pagination_requested':False,'result':'success' if result else 'unavailable'})
    return result

class GenreBackdrops:
    def __init__(self,g,epoch):
        self.g,self.epoch=g,epoch;self.stop=Event();self.lock=Lock()
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='PrimeGenreArt')
        self.futures,self.images,self.titles,self.pools={},{},{},{}
        self.claims={s:{} for s in ('movies','tv')};self.order=();self.failed=set();self.retry_after={};self.attempts={};self.sources={};self.failure_details={}
        self.path=Path(g.DATA_PATH)/'genre-backdrops.json'
        self.report=DiagnosticLog(g.DATA_PATH,g.addon.getAddonInfo('version'),active=self.current,operation='genre_backdrop_preload')
        self._restore()
    def current(self):return not self.stop.is_set() and current_account(self.g.DATA_PATH,self.epoch)
    def _restore(self):
        if not self.path.exists():return
        try:
            if self.path.stat().st_size>1024*1024:raise ValueError('Genre artwork metadata exceeds its limit')
            value=json.loads(self.path.read_text())
            if value.get('schema') not in (1,2,3) or value.get('epoch')!=self.epoch:return
            self.images={genre_identity(owner):{s:source_image(url) for s,url in sections.items()
                if s in ('movies','tv') and safe_url(url,IMAGE_HOSTS)}
                for owner,sections in value.get('images',{}).items()
                if (owner in _EXTRA or safe_url(owner,STOREFRONT_HOSTS)) and isinstance(sections,dict)}
            self.titles={genre_identity(owner):{s:url for s,url in sections.items() if s in ('movies','tv') and safe_url(url,STOREFRONT_HOSTS)}
                for owner,sections in value.get('titles',{}).items() if genre_identity(owner) in self.images and isinstance(sections,dict)}
            self.report({'event':'genre_backdrop_cache_restored','stage':'genre_backdrop_cache',
                'genres':len(self.images),'schema':value.get('schema'),'deduplication_pending':True,'result':'success'})
        except Exception as error:
            self.report({'event':'genre_backdrop_cache_failed','stage':'genre_backdrop_cache','exception':exception_details(error),'result':'failure'})
    def request(self,rows):
        sources={genre_identity(row['address']):row['address'] for row in rows if safe_url(row.get('address'),STOREFRONT_HOSTS)}
        order=tuple(sources)
        with self.lock:
            if self.stop.is_set():return
            self.sources.update(sources)
            if order!=self.order:
                self.order=order;self.claims={s:{} for s in ('movies','tv')};removed=0
                for owner in order+tuple(_EXTRA):
                    for section,url in list(self.images.get(owner,{}).items()):
                        image='image:'+artwork_identity(url);title=self.titles.get(owner,{}).get(section)
                        keys=[image]+(['title:'+str(navigation_title(title))] if title else [])
                        if any(k in self.claims[section] for k in keys):
                            self.images[owner].pop(section,None);self.titles.get(owner,{}).pop(section,None);removed+=1
                        else:
                            for key in keys:self.claims[section][key]=owner
                self.report({'event':'genre_cached_duplicates_removed','stage':'genre_backdrop_cache',
                    'removed':removed,'section_scoped':True,'result':'success'})
            for owner in order+tuple(_EXTRA):
                future=self.futures.get(owner)
                expected=(_EXTRA[owner],) if owner in _EXTRA else tuple(s for s,cards in self.pools.get(owner,{}).items() if cards) or ('movies','tv')
                missing=any(not (self.images.get(owner) or {}).get(s) for s in expected)
                if future is not None and (not future.done() or not missing or monotonic()<self.retry_after.get(owner,0)):continue
                self.attempts[owner]=self.attempts.get(owner,0)+1
                delay=min(120,5*2**min(self.attempts[owner]-1,5));self.retry_after[owner]=monotonic()+delay
                self.futures[owner]=self.pool.submit(self._read_extra,owner,_EXTRA[owner]) if owner in _EXTRA else self.pool.submit(self._read,owner)
                self.report({'event':'genre_metadata_attempt_queued','stage':'genre_backdrop_preload',
                    'address':route(owner) if owner not in _EXTRA else {'tile':owner},
                    'artwork_attempt':self.attempts[owner],'retry_delay_seconds':delay,
                    'result':'retrying' if self.attempts[owner]>1 else 'started'})
    def _claim(self,owner,section,title,image):
        with self.lock:
            if self.stop.is_set():return False
            keys=['title:'+str(navigation_title(title))]
            if image:keys.append('image:'+artwork_identity(image))
            if any(self.claims[section].get(key,owner)!=owner for key in keys):return False
            for key in keys:self.claims[section][key]=owner
            return True
    def _release(self,owner,section,title):
        key='title:'+str(navigation_title(title))
        with self.lock:
            if self.titles.get(owner,{}).get(section)==title:return
            if self.claims[section].get(key)==owner:self.claims[section].pop(key,None)
    def _select(self,client,owner,choices=None,candidate_ready=None,section_ready=None):
        selected={}
        def claim(section,title,image):
            accepted=self._claim(owner,section,title,image)
            if accepted and image:selected[section]=title
            return accepted
        images=read_backdrops(client,self.sources.get(owner,owner),self.report,self.current,claim=claim,
            release=lambda s,t:self._release(owner,s,t),choices=choices,candidate_ready=candidate_ready,
            preferred=self.titles.get(owner,{}),section_ready=section_ready)
        return images,selected
    def _commit(self,owner,result,titles,started):
        if not self.current():return
        with self.lock:
            if self.stop.is_set():return
            for section,url in result.items():
                keep={'image:'+artwork_identity(url),'title:'+str(navigation_title(titles[section]))}
                for key,address in list(self.claims[section].items()):
                    if address==owner and key not in keep:self.claims[section].pop(key,None)
            self.images.setdefault(owner,{}).update(result);self.titles.setdefault(owner,{}).update(titles)
            if result:self.failed.discard(owner);self.failure_details.pop(owner,None)
            self.retry_after[owner]=monotonic()+min(120,5*2**min(self.attempts.get(owner,1)-1,5))
            saved={'schema':3,'epoch':self.epoch,'images':dict(self.images),'titles':dict(self.titles)}
            def write():
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temporary=self.path.with_suffix('.new');temporary.write_text(json.dumps(saved));temporary.replace(self.path)
            stored=persist_if_current(self.g.DATA_PATH,self.epoch,write)
        self.report({'event':'genre_backdrop_preload_complete','stage':'genre_backdrop_preload',
            'address':route(owner) if owner not in _EXTRA else {'tile':owner},'sections':sorted(result),
            'elapsed_ms':round((monotonic()-started)*1000),'duplicate_fallback_used':False,
            'result':'success' if stored and result else 'unavailable' if stored else 'cancelled'})
    def _read(self,address):
        started=monotonic()
        try:
            if not self.current():return
            def ready(choices):
                with self.lock:
                    if not self.stop.is_set():self.pools[address]=choices
            with CatalogGateway(self.g).session(self.current,self.report) as client:
                result,titles=self._select(client,address,candidate_ready=ready,
                    section_ready=lambda s,url,title:self._commit(address,{s:url},{s:title},started))
            if not result:self._commit(address,result,titles,started)
            with self.lock:
                if not self.stop.is_set():
                    self.retry_after[address]=monotonic()+min(120,5*2**min(self.attempts.get(address,1)-1,5))
        except Exception as error:self._failed(address,error,started)
    def _read_extra(self,owner,section):
        started=monotonic()
        try:
            if not self.current():return
            with self.lock:
                choices={section:[card for address in self.order for card in (self.pools.get(address) or {}).get(section,[])]}
            if not choices[section]:
                self.report({'event':'genre_extra_artwork_unavailable','stage':'genre_backdrop_selection',
                    'tile':owner,'section':section,'metadata_pages_requested':0,'result':'unavailable'})
                return
            with CatalogGateway(self.g).session(self.current,self.report) as client:
                result,titles=self._select(client,owner,choices=choices)
            self._commit(owner,result,titles,started)
        except Exception as error:self._failed(owner,error,started)
    def _failed(self,owner,error,started):
        with self.lock:
            if not self.stop.is_set():
                self.failed.add(owner)
                self.failure_details[owner]={'stage':'genre_metadata_or_title','exception':exception_details(error)}
                self.retry_after[owner]=monotonic()+min(120,5*2**min(self.attempts.get(owner,1)-1,5))
        self.report({'event':'genre_backdrop_preload_failed','stage':'genre_backdrop_preload',
            'address':route(owner) if owner not in _EXTRA else {'tile':owner},
            'elapsed_ms':round((monotonic()-started)*1000),'exception':exception_details(error),'result':'failure'})
    def image(self,section,address):
        with self.lock:return (self.images.get(genre_identity(address)) or {}).get(section,'')
    def state(self,section,address):
        owner=genre_identity(address)
        with self.lock:
            future=self.futures.get(owner)
            if (self.images.get(owner) or {}).get(section):return {'state':'metadata_ready'}
            if future is None or not future.done():
                return {'state':'metadata_received_resolving_backdrop' if owner in self.pools else 'metadata_pending'}
            return dict(self.failure_details.get(owner) or {},state='metadata_request_failed' if owner in self.failed else 'no_usable_backdrop')
    def pending(self,address):
        with self.lock:
            future=self.futures.get(genre_identity(address))
            return future is None or not future.done()
    def close(self):
        self.stop.set()
        with self.lock:
            for future in self.futures.values():future.cancel()
            self.images.clear();self.titles.clear();self.pools.clear();self.claims.clear();self.failed.clear();self.futures.clear();self.retry_after.clear();self.attempts.clear();self.sources.clear();self.failure_details.clear()
        self.pool.shutdown(wait=False)

def navigation_title(address):
    identity=detail_id(address)
    if identity:return identity
    parts=urlsplit(address)
    return parts.hostname,parts.path.rstrip('/'),tuple(sorted((k,v) for k,v in parse_qsl(parts.query) if k not in ('ref','ref_')))
