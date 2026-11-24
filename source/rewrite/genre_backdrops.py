"""One genre landing page and declared title backdrops; never catalog pagination."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event,Lock
from time import monotonic
import json
from .account_state import current_account,persist_if_current
from .catalog_gateway import CatalogGateway
from .detail_catalog import documents,state_records
from .image_quality import source_image
from .prime_diagnostics import DiagnosticLog,exception_details,route
from .public_catalog import _read_page,_shot_map,_first_shot,safe_url,IMAGE_HOSTS,STOREFRONT_HOSTS

def candidates(data,content_type,address):
    page,structure=_read_page(data,content_type,address)
    groups={section:[] for section in ('movies','tv')}
    for card in page.cards:
        section='movies' if card.mediatype=='movie' else 'tv' if card.mediatype in ('tvshow','season','episode') else None
        if section:groups[section].append(card)
    return {section:next((card for card in cards if card.fanart_url),cards[0] if cards else None)
        for section,cards in groups.items()},structure

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
    if report:report({'event':'genre_backdrop_title_inventory','stage':'genre_backdrop_title_parse',
        'declared_states':len(states),'selected_state_present':True,'record_fields':sorted(record),
        'image_roles':sorted(shots),'backdrop_available':bool(image),'result':'success' if image else 'unavailable'})
    return image

def read_backdrops(client,address,report,active):
    response=client.request(address,stage='genre_backdrop_metadata',headers={'Accept':'text/html,application/json'})
    choices,structure=candidates(response.data.decode('utf-8',errors='replace'),response.headers.get_content_type(),response.address)
    report({'event':'genre_backdrop_candidates','stage':'genre_backdrop_metadata',
        'media_kinds_available':[s for s,c in choices.items() if c],'protocol_structure':structure,
        'pagination_requested':False,'result':'success'})
    result={};details=0
    for section,card in choices.items():
        if not active():break
        if card is None:continue
        try:
            artwork=source_image(card.fanart_url)
            if not artwork:
                details+=1
                detail=client.request(card.detail_url,stage='genre_backdrop_title',headers={'Accept':'text/html,application/json'})
                artwork=selected_backdrop(detail.data.decode('utf-8',errors='replace'),detail.headers.get_content_type(),detail.address,report)
            if safe_url(artwork,IMAGE_HOSTS):result[section]=artwork
        except Exception as error:
            report({'event':'genre_backdrop_section_failed','stage':'genre_backdrop_title','section':section,
                'address':route(card.detail_url),'exception':exception_details(error),'result':'failure'})
    report({'event':'genre_backdrop_metadata_complete','stage':'genre_backdrop_metadata','genre_pages_read':1,
        'title_pages_read':details,'sections_with_backdrop':sorted(result),'cover_fallback_used':False,
        'pagination_requested':False,'result':'success' if result else 'unavailable'})
    return result

class GenreBackdrops:
    def __init__(self,g,epoch):
        self.g,self.epoch=g,epoch;self.stop=Event();self.lock=Lock()
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix='PrimeGenreArt')
        self.futures,self.images={},{};self.failed=set()
        self.path=Path(g.DATA_PATH)/'genre-backdrops.json'
        self.report=DiagnosticLog(g.DATA_PATH,g.addon.getAddonInfo('version'),active=self.current,operation='genre_backdrop_preload')
        self._restore()
    def current(self):return not self.stop.is_set() and current_account(self.g.DATA_PATH,self.epoch)
    def _restore(self):
        if not self.path.exists():return
        try:
            if self.path.stat().st_size>1024*1024:raise ValueError('Genre artwork metadata exceeds its limit')
            value=json.loads(self.path.read_text())
            if value.get('schema')!=1 or value.get('epoch')!=self.epoch:return
            restored={address:{s:source_image(url) for s,url in sections.items()
                if s in ('movies','tv') and safe_url(url,IMAGE_HOSTS)}
                for address,sections in value.get('images',{}).items()
                if safe_url(address,STOREFRONT_HOSTS) and isinstance(sections,dict)}
            self.images=restored
            self.report({'event':'genre_backdrop_cache_restored','stage':'genre_backdrop_cache','genres':len(restored),'result':'success'})
        except Exception as error:
            self.report({'event':'genre_backdrop_cache_failed','stage':'genre_backdrop_cache','exception':exception_details(error),'result':'failure'})
    def request(self,rows):
        for row in rows:
            address=row.get('address')
            if not safe_url(address,STOREFRONT_HOSTS):continue
            with self.lock:
                if self.stop.is_set() or address in self.futures:continue
                self.futures[address]=self.pool.submit(self._read,address)
    def _read(self,address):
        started=monotonic()
        try:
            if not self.current():return
            gateway=CatalogGateway(self.g)
            with gateway.session(self.current,self.report) as client:
                result=read_backdrops(client,address,self.report,self.current)
            if not self.current():
                self.report({'event':'genre_backdrop_preload_cancelled','stage':'genre_backdrop_preload','result':'cancelled'});return
            with self.lock:
                if self.stop.is_set():return
                self.images[address]=result
                saved={'schema':1,'epoch':self.epoch,'images':dict(self.images)}
                def write():
                    self.path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=self.path.with_suffix('.new');temporary.write_text(json.dumps(saved));temporary.replace(self.path)
                stored=persist_if_current(self.g.DATA_PATH,self.epoch,write)
            self.report({'event':'genre_backdrop_preload_complete','stage':'genre_backdrop_preload','address':route(address),
                'sections':sorted(result),'elapsed_ms':round((monotonic()-started)*1000),
                'result':'success' if stored and result else 'unavailable' if stored else 'cancelled'})
        except Exception as error:
            with self.lock:
                if not self.stop.is_set():self.failed.add(address)
            self.report({'event':'genre_backdrop_preload_failed','stage':'genre_backdrop_preload','address':route(address),
                'elapsed_ms':round((monotonic()-started)*1000),'exception':exception_details(error),'result':'failure'})
    def image(self,section,address):
        with self.lock:return (self.images.get(address) or {}).get(section,'')
    def pending(self,address):
        with self.lock:
            future=self.futures.get(address)
            return future is None or not future.done()
    def close(self):
        self.stop.set()
        with self.lock:
            for future in self.futures.values():future.cancel()
            self.images.clear();self.failed.clear();self.futures.clear()
        self.pool.shutdown(wait=False)
