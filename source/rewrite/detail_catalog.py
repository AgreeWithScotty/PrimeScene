"""Read a selected storefront title directly from its declared page state."""
import json
from html.parser import HTMLParser
from urllib.parse import urlencode,urljoin
from .public_catalog import (StorefrontParser,PublicCatalogError,STOREFRONT_HOSTS,
    safe_url,_text,_protocol_shape,_read_page)
from .tv_catalog import _info,_kind,parse_tv_page


def documents(data,content_type,address):
    from .provider_documents import read_documents
    return read_documents(data,content_type)[0]


def state_records(docs):
    states=[];stack=list(docs);seen=0
    while stack:
        node=stack.pop();seen+=1
        if seen>100000:raise PublicCatalogError('Title state exceeds its structure limit')
        if isinstance(node,dict):
            if isinstance(node.get('pageTitleId'),str):states.append(node)
            stack.extend(v for v in node.values() if isinstance(v,(dict,list)))
        elif isinstance(node,list):stack.extend(node)
    return states


def playable(identifier,label,info,pluginid,material='Feature'):
    live=material.lower() in ('live','livestreaming')
    params={'mode':'PlayVideo','asin':identifier,'name':label,'adult':int(bool(info.get('isAdult'))),
        'trailer':2 if live else 1 if material.lower()=='trailer' else 0}
    return {'identity':identifier,'title':label,'info':dict(info,contentType='live' if live else 'episode'
        if _kind(info)=='episode' else 'movie'), 'video':True,'url':pluginid+'?'+urlencode(params)}


def parse_detail(data,content_type,address,pluginid,observer):
    from .provider_documents import read_documents
    docs,extraction,_=read_documents(data,content_type);states=state_records(docs)
    # Only pageTitleId declares the selected product; recommendations are never
    # used to invent a title's playback ID or season relationships.
    selected=next((s for s in states if s.get('detail') or s.get('self')),None)
    observer({'event':'title_state_inventory','stage':'title_parse','documents':len(docs),
        'declared_page_states':len(states),'protocol_structure':[_protocol_shape(d) for d in docs[:3]]})
    if selected is None:raise PublicCatalogError('Amazon did not declare the selected title state. Open Diagnostics.')
    identifier=selected['pageTitleId'];related=[s for s in states if s['pageTitleId']==identifier]
    record={};seasons={};actions=[]
    for state in related:
        detail=state.get('detail') or {}
        for mapping in (state.get('self') or {},detail.get('detail',detail),detail.get('headerDetail') or {}):
            if isinstance(mapping,dict) and isinstance(mapping.get(identifier),dict):record.update(mapping[identifier])
        for entries in (state.get('seasons') or {}).values():
            for entry in entries if isinstance(entries,list) else []:
                if isinstance(entry,dict) and entry.get('seasonId'):
                    link=safe_url(entry.get('seasonLink'),STOREFRONT_HOSTS,address)
                    if link:
                        seasons[entry['seasonId']]={'identity':entry['seasonId'],'detail_url':link,
                            'title':_text(entry.get('title')) or 'Season '+str(entry.get('seasonNumber',entry.get('sequenceNumber',''))),
                            'info':dict(_info(entry),mediatype='season',season=entry.get('seasonNumber',entry.get('sequenceNumber')))}
        for name in ('action','actions'):
            container=(state.get(name) or {}).get(identifier) or {}
            if isinstance(container,dict):
                main=(container.get('playbackActions') or {}).get('main') or {}
                if isinstance(main,dict):actions.extend(main.get('children') or [])
    if not record:raise PublicCatalogError('Selected title metadata is missing. Open Diagnostics.')
    info=_info(record);kind=_kind(record);label=_text(record.get('title') or record.get('displayTitle'))
    if identifier in seasons:
        seasons[identifier]['info']=dict(info,**{k:v for k,v in seasons[identifier]['info'].items() if v not in (None,'',[],{})})
    if not label:raise PublicCatalogError('Selected title has no title label')
    if kind in ('tvshow','season'):
        if any((s.get('episodeList') or {}).get('cardTitleIds') for s in related):
            return parse_tv_page(data,content_type,address,pluginid,observer,
                decoded_documents=docs,document_extraction=extraction)
        return {'rows':[],'info':info,'seasons':list(seasons.values()),'selected_id':identifier,'next_url':None}
    rows=[];used=set()
    for action in actions:
        if not isinstance(action,dict):continue
        pid=action.get('playbackID');material=action.get('videoMaterialType','Feature')
        if not isinstance(pid,str) or not pid or material.lower()=='trailer' or pid in used:continue
        used.add(pid);rows.append(playable(pid,_text(action.get('label')) or label,info,pluginid,material))
    if not rows and kind in ('movie','episode','video','live'):
        pid=record.get('playbackID') or record.get('asin') or identifier
        rows=[playable(pid,label,info,pluginid,'LiveStreaming' if kind=='live' else 'Feature')]
    if not rows:raise PublicCatalogError('Amazon returned no playable action for this selected title')
    observer({'event':'title_actions_parsed','stage':'title_parse','selected_kind':kind,
        'playable_actions':len(rows),'linked_seasons':len(seasons),'result':'success'})
    return {'rows':rows,'info':info,'seasons':list(seasons.values()),'selected_id':identifier,'next_url':None}


class SearchForm(HTMLParser):
    def __init__(self):super().__init__();self.forms=[];self.current=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='form':self.current={'action':a.get('action',''),'method':a.get('method','get').lower(),'fields':{},'query':None}
        elif tag=='input' and self.current is not None and a.get('name'):
            if a.get('type')=='hidden':self.current['fields'][a['name']]=a.get('value','')
            if a.get('type') in ('search','text') and a['name'] in ('phrase','k','query','searchText'):
                self.current['query']=a['name']
    def handle_endtag(self,tag):
        if tag=='form' and self.current is not None:self.forms.append(self.current);self.current=None


def search_request(html,address,phrase):
    parser=SearchForm();parser.feed(html);parser.close()
    forms=[f for f in parser.forms if f['query'] and safe_url(f['action'],STOREFRONT_HOSTS,address)]
    if not forms:raise PublicCatalogError('Amazon did not expose a usable search form. Open Diagnostics.')
    # Desktop/mobile headers can render the same search twice. Use the first
    # declared usable form rather than rejecting that valid page structure.
    form=forms[0];fields=dict(form['fields']);fields[form['query']]=phrase
    target=urljoin(address,form['action']);encoded=urlencode(fields)
    if form['method']=='get':return target+('&' if '?' in target else '?')+encoded,None
    if form['method']=='post':return target,encoded.encode()
    raise PublicCatalogError('Unsupported Amazon search form method')
