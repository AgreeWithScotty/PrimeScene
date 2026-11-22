"""Genre navigation from explicit provider category links, never guessed routes."""
import json,re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from .detail_catalog import documents
from .public_catalog import ORIGIN,STOREFRONT_HOSTS,safe_url,_text,_protocol_shape

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
    return sorted((row for row in rows if safe_url(row.get('address'),STOREFRONT_HOSTS)),key=lambda row:row['title'])

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
    parser=GenreSection(address);parser.feed(data);parser.close()
    if observer:observer({'event':'genre_section_inventory','stage':'genre_scope','section':section,
        'see_more_markers':parser.see_more_count,'recognized_sections':sorted(parser.links),
        'section_link_found':section in parser.links,'result':'success' if section in parser.links else 'unavailable'})
    return parser.links.get(section)

def genre_name(text):
    text=re.sub(r'\b(movies?|tv|shows?|series|television)\b','',_text(text),flags=re.I)
    key=re.sub('[^a-z]','',text.lower().replace('&','and'))
    return NAMES.get(key)

class Anchors(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.current=[dict(attrs).get('href',''),'']
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
            label=next((_text(node.get(k)) for k in ('label','text','title','heading','displayName') if _text(node.get(k))), '')
            link=next((safe_url(node.get(k),STOREFRONT_HOSTS,address) for k in ('url','href','link','targetUrl')
                if safe_url(node.get(k),STOREFRONT_HOSTS,address)),None)
            if link:
                candidates.append((link,label))
                if (node.get('enrichNav')=='GET_CATEGORIES' or label.casefold() in ('categories','genres')):
                    categories.append(link)
            stack.extend(v for v in node.values() if isinstance(v,(dict,list)))
        elif isinstance(node,list):stack.extend(node)
    if not content_type.endswith('json'):
        anchors=Anchors();anchors.feed(data);anchors.close();candidates.extend(anchors.links)
    rows=[];seen=set()
    for link,label in candidates:
        target=safe_url(link,STOREFRONT_HOSTS,address);name=genre_name(label)
        if not target or not name or not any(p in urlsplit(target).path for p in ('/genre/','/kids')):continue
        if (name,target) in seen:continue
        seen.add((name,target));rows.append({'title':name,'address':target})
    observer({'event':'genre_navigation_parsed','stage':'genre_discovery','documents':len(docs),
        'linked_genres':len(rows),'category_links':len(set(categories)),
        'protocol_structure':[_protocol_shape(d) for d in docs[:1]],'result':'success'})
    return rows,list(dict.fromkeys(categories))

def discover(client,section,observer):
    from .public_catalog import PAGES
    response=client.request(ORIGIN+PAGES[section],stage='genre_navigation_fetch')
    rows,links=navigation(response.data.decode('utf-8',errors='replace'),response.headers.get_content_type(),response.address,observer)
    if links:
        for address in links[:2]:
            response=client.request(address,stage='genre_categories_fetch')
            found,_=navigation(response.data.decode('utf-8',errors='replace'),response.headers.get_content_type(),response.address,observer)
            rows.extend(found)
    # Keep separate movie/TV links where the provider supplied them. Generic
    # genre links are valid too; the card reader still enforces type and Prime.
    result={}
    for row in rows:
        path=urlsplit(row['address']).path.lower()
        wrong=('/tv/' in path and section=='movies') or ('/movie/' in path and section=='tv')
        if wrong:continue
        result.setdefault(row['title'],row)
    return sorted(result.values(),key=lambda row:row['title'])
