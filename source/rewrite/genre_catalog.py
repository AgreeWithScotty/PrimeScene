"""Genre navigation from explicit provider category links, never guessed routes."""
import json,re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from .detail_catalog import documents
from .public_catalog import ORIGIN,STOREFRONT_HOSTS,IMAGE_HOSTS,safe_url,_text,_protocol_shape
from .regional_hosts import storefront_path

NAMES={'action':'Action','actionandadventure':'Action & adventure','adventure':'Adventure',
    'animation':'Animation','anime':'Anime','comedy':'Comedy','documentary':'Documentary',
    'documentaries':'Documentary','drama':'Drama','fantasy':'Fantasy','horror':'Horror',
    'kidsandfamily':'Kids & family','family':'Family','mystery':'Mystery','mysteryandthriller':'Mystery & thriller',
    'romance':'Romance','romanticcomedy':'Romantic comedy','sciencefiction':'Science fiction',
    'sciencefictionandfantasy':'Science fiction & fantasy','scifi':'Science fiction',
    'suspense':'Suspense','thriller':'Thriller','western':'Western','westerns':'Western',
    'crime':'Crime','musicals':'Musicals','music':'Music','sports':'Sports'}
NAMES.update(kids='Kids & family',mysteryandthrillers='Mystery & thriller',historical='Historical')

def published_genres():
    rows=json.loads(Path(__file__).with_name('genre_links.json').read_text(encoding='utf-8'))['rows']
    return sorted(({'title':row['title'],'address':row['address']} for row in rows
        if safe_url(row.get('address'),STOREFRONT_HOSTS)),key=lambda row:row['title'])


@lru_cache(maxsize=1)
def _route_names():
    return {storefront_path(row['address']).rstrip('/'):row['title'] for row in published_genres()}

class GenreSection(HTMLParser):
    def __init__(self,address):
        super().__init__();self.address=address;self.heading='';self.collect=False;self.last='';self.anchor=None;self.links={};self.see_more_count=0
    def handle_starttag(self,tag,attrs):
        if tag in ('h2','h3'):self.heading='';self.collect=True
        if tag=='a':
            attributes=dict(attrs)
            self.anchor=[attributes.get('href',''),'',attributes.get('data-testid'),attributes.get('aria-label','')]
    def handle_data(self,data):
        if self.collect:self.heading+=data
        if self.anchor:self.anchor[1]+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor:
            address,label,testid,accessible=self.anchor;self.anchor=None
            heading=(self.heading if self.collect else self.last).lower().replace('see more','').strip()
            section='movies' if heading=='movies' else 'tv' if heading in ('tv shows','tv') else None
            target=safe_url(address,STOREFRONT_HOSTS,self.address)
            # Arrow SVGs contribute "Link Arrow" to the raw anchor text. The
            # provider's explicit see-more marker/accessible label establish
            # the link even when that decorative text follows "See more".
            expected=('movies',) if section=='movies' else ('tv shows','tv')
            explicit=testid=='see-more' and accessible.strip().casefold() in expected
            if testid=='see-more':self.see_more_count+=1
            if section and target and (explicit or label.strip().lower() in ('see more','movies','tv shows')):
                self.links.setdefault(section,target)
        if tag in ('h2','h3'):self.collect=False;self.last=self.heading

def section_link(data,address,section,observer=None):
    # Metadata describes movie/TV scope independently of the rendered language.
    from .provider_documents import objects
    from .public_catalog import _json_content
    from .catalog_access import prime_included
    targets=set();typed=[]
    for node,_ in objects(documents(data,'text/html',address)):
        if not isinstance(node.get('seeMore'),dict):continue
        link=node['seeMore'].get('link')
        if not isinstance(link,dict):continue
        metadata=link.get('metadata') or {}
        kind=metadata.get('contentType') if isinstance(metadata,dict) else None
        target=safe_url(link.get('url'),STOREFRONT_HOSTS,address)
        if not target:continue
        if kind in (('movie','movies') if section=='movies' else ('tv','tvshow','season','series')):targets.add(target)
        # Prime's translated genre shelves keep typed title records even when
        # seeMore.metadata is empty. Preserve provider order: the main typed
        # shelf precedes secondary popularity shelves in the native page.
        found,_,_=_json_content(node,address,page_continuation=False)
        expected={'movie'} if section=='movies' else {'tvshow','season','episode'}
        if found and all(card.mediatype in expected and prime_included(card.access_evidence) for card in found.values()):
            typed.append(target)
    if len(targets)==1:
        if observer:observer({'event':'genre_section_inventory','stage':'genre_scope','section':section,
            'scope_source':'provider_content_type','section_link_found':True,'result':'success'})
        return next(iter(targets))
    parser=GenreSection(address);parser.feed(data);parser.close()
    target=parser.links.get(section)
    if not target and typed:target=typed[0]
    if observer:observer({'event':'genre_section_inventory','stage':'genre_scope','section':section,
        'see_more_markers':parser.see_more_count,'recognized_sections':sorted(parser.links),
        'scope_source':'native_heading' if section in parser.links else 'provider_ordered_prime_typed_shelf' if target else 'unrecognized',
        'typed_prime_shelves':len(typed),'section_link_found':bool(target),'result':'success' if target else 'unavailable'})
    return target

def genre_name(text):
    text=re.sub(r'\b(movies?|tv|shows?|series|television)\b','',_text(text),flags=re.I)
    key=re.sub('[^a-z]','',text.lower().replace('&','and'))
    return NAMES.get(key)

class Anchors(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.links=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if tag=='a':self.current=[attributes.get('href',''),'','']
        elif tag=='img' and self.current is not None:
            self.current[2]=safe_url(attributes.get('src') or attributes.get('data-src'),IMAGE_HOSTS) or ''
            if not self.current[1]:self.current[1]=attributes.get('alt') or ''
    def handle_data(self,data):
        if self.current:self.current[1]+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.current:self.links.append(self.current);self.current=None

def navigation(data,content_type,address,observer):
    docs=documents(data,content_type,address);stack=list(docs);candidates=[];categories=[];visited=0
    while stack:
        node=stack.pop();visited+=1
        if visited>100000:raise ValueError('Genre navigation exceeds its structure limit')
        if isinstance(node,dict):
            label=next((_text(node.get(k)) for k in ('displayTitle','label','text','title','heading','displayName') if _text(node.get(k))), '')
            raw_link=node.get('link')
            linked=raw_link.get('url') if isinstance(raw_link,dict) else raw_link
            link=next((target for target in (safe_url(node.get('url'),STOREFRONT_HOSTS,address),
                safe_url(node.get('href'),STOREFRONT_HOSTS,address),safe_url(linked,STOREFRONT_HOSTS,address),
                safe_url(node.get('targetUrl'),STOREFRONT_HOSTS,address)) if target),None)
            images=node.get('images') or {}
            from .public_catalog import _image
            artwork=next((target for key in ('hero','cover','logo') if isinstance(images,dict)
                for target in [_image(images.get(key))] if target),'')
            if link:
                candidates.append((link,label,artwork,bool(node.get('overlayTextPosition'))))
                if node.get('enrichNav')=='GET_CATEGORIES' or label.casefold() in ('categories','genres'):categories.append(link)
            stack.extend(v for v in node.values() if isinstance(v,(dict,list)))
        elif isinstance(node,list):stack.extend(node)
    if not content_type.endswith('json'):
        anchors=Anchors();anchors.feed(data);anchors.close();candidates.extend(tuple(row)+(False,) for row in anchors.links)
    rows={}
    for link,label,artwork,overlay in candidates:
        target=safe_url(link,STOREFRONT_HOSTS,address)
        if not target:continue
        path=storefront_path(target)
        # The provider's actual genre slug is stable across translated labels.
        # Use only known genre slugs from declared links; never construct a URL.
        match=re.fullmatch(r'/genre/([a-z-]+)/?',path)
        name=_route_names().get(path.rstrip('/')) or genre_name(label)
        if not name and match:name=genre_name(match.group(1).replace('-',' '))
        if not name or not (path.startswith('/genre/') or path.startswith('/kids')):continue
        key=(name,target);row={'title':name,'address':target}
        if artwork:
            row['artwork']=artwork
            if overlay:row['artwork_label']=name
        if key not in rows or artwork and not rows[key].get('artwork'):rows[key]=row
    result=list(rows.values())
    observer({'event':'genre_navigation_parsed','stage':'genre_discovery','documents':len(docs),
        'linked_genres':len(result),'artwork_supplied':sum(bool(row.get('artwork')) for row in result),
        'category_links':len(set(categories)),'protocol_structure':[_protocol_shape(d) for d in docs[:1]],'result':'success'})
    return result,list(dict.fromkeys(categories))

def discover(client,section,observer):
    # This is Amazon's published category-navigation page. Its Grid supplies
    # both genre targets and cover art; no genre/title pages are needed here.
    response=client.request(ORIGIN+'/categories',stage='genre_categories_fetch')
    rows,_=navigation(response.data.decode('utf-8',errors='replace'),response.headers.get_content_type(),response.address,observer)
    result={}
    for row in rows:
        path=storefront_path(row['address']).lower()
        if ('/tv/' in path and section=='movies') or ('/movie/' in path and section=='tv'):continue
        old=result.get(row['title'])
        if old is None or row.get('artwork') and not old.get('artwork'):result[row['title']]=row
    return sorted(result.values(),key=lambda row:row['title'])
