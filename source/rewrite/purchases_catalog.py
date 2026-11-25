"""Read a provider-selected personal collection; never infer empty from parser failure."""
from dataclasses import replace
import hashlib,re
from urllib.parse import parse_qsl,urlsplit
from .provider_documents import read_documents,library_label,watchlist_label,collection_route

def navigation_identity(address):
    """Tracking markers do not turn a collection's own link into another page."""
    p=urlsplit(address)
    return (p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/') or '/',
        tuple(sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k not in ('ref','ref_'))))

def _empty_evidence(payload):
    stack=[payload];proof=[];records=0;pending=False;visited=0
    while stack:
        node=stack.pop();visited+=1
        if visited>100000:break
        if isinstance(node,list):
            if not node:proof.append('selected_array_empty')
            else:records+=len(node)
            continue
        if not isinstance(node,dict):continue
        pending|=bool(node.get('hasMoreItems') is not False and
            (node.get('paginationServiceToken') or node.get('nextPage') or node.get('nextToken'))
            or node.get('isLoading') is True or node.get('loading') is True or node.get('hasMoreItems') is True)
        if node.get('isEmpty') is True:proof.append('provider_is_empty')
        if isinstance(node.get('emptyState'),dict) and node['emptyState']:proof.append('selected_empty_state')
        if type(node.get('estimatedTotal')) is int and node['estimatedTotal']==0:proof.append('provider_zero_total')
        for key in ('entities','items','results','content','baseOutput','containers'):
            value=node.get(key)
            if isinstance(value,list):
                if key=='containers':
                    if not value:proof.append('selected_containers_empty')
                    stack.extend(value)
                elif not value:proof.append('selected_'+key+'_empty')
                else:records+=len(value)
            elif isinstance(value,dict):stack.append(value)
    return proof,records,pending

def read_library(data,content_type,page_url,selected=False,collection='purchases'):
    from .public_catalog import (CatalogPage,PublicCatalogError,STOREFRONT_HOSTS,
        _direct_next_links,_json_card,_json_content,_link,_next_url,_protocol_shape,_remember_card,safe_url,_text)
    from .catalog_access import merge_evidence
    from .http_transport import server_errors
    if collection not in ('purchases','watchlist'):raise ValueError('Unknown personal collection')
    matches=watchlist_label if collection=='watchlist' else library_label
    identifiers=('watchlist','yourwatchlist','mywatchlist') if collection=='watchlist' else ('library','purchases','purchasesandrentals')
    documents,extraction,html_links=read_documents(data,content_type,matches,collection)
    current=navigation_identity(page_url);candidates=[];self_links=0
    for value in html_links:
        target=safe_url(value,STOREFRONT_HOSTS,page_url)
        if not target:continue
        if navigation_identity(target)==current:self_links+=1
        else:candidates.append((1,target))
    payloads=[];grids=[];empty_bodies=[];shapes=[];active=bool(extraction.get('selected_collection_navigation'))
    explicit=False;provider_error=False;codes=[];nodes=0
    inventory={'library_labels':0,'watchlist_labels':0,'native_containers':0,'explicit_empty_states':0,
        'html_library_links':len(html_links),'self_navigation_links_ignored':self_links,'grid_inventory':[]}
    def content_node(value):
        return isinstance(value,list) or isinstance(value,dict) and any(k in value for k in
            ('content','baseOutput','containers','items','entities','results','emptyState'))
    for document in documents:
        if len(shapes)<3:shapes.append(_protocol_shape(document))
        codes.extend(server_errors(document))
        if isinstance(document,dict) and any(document.get(k) for k in ('error','errors','errorCode')):provider_error=True
        if selected and isinstance(document,dict):
            for key in ('results','content'):
                if content_node(document.get(key)):payloads.append(document[key])
        stack=[(document,'$',0)]
        while stack:
            node,path,depth=stack.pop();nodes+=1
            if nodes>100000 or depth>40:raise PublicCatalogError('The personal collection response exceeded its structural limit.')
            if isinstance(node,dict):
                if node.get('hasFailed') is True:
                    provider_error=True
                names=[re.sub('[^a-z]','',str(node.get(k) or '').casefold()) for k in ('viewType','id','filterId')]
                label=next((_text(node.get(k)) for k in ('label','text','title','displayTitle') if _text(node.get(k))),'')
                kind=node.get('containerType')
                if (node.get('hasFailed') is False and node.get('containers')==[]
                        and (path.endswith('.body') or isinstance(node.get('swiftPageParameters'),dict))):empty_bodies.append(node)
                inventory['native_containers']+=kind in ('Grid','StandardCarousel')
                inventory['explicit_empty_states']+='emptyState' in node
                inventory['library_labels']+=library_label(label);inventory['watchlist_labels']+=watchlist_label(label)
                if kind=='Grid':
                    grids.append(node)
                    if len(inventory['grid_inventory'])<8:
                        inventory['grid_inventory'].append({'path':path,'entity_count':len(node['entities']) if isinstance(node.get('entities'),list) else None,
                            'entity_fields':sorted(set(node['entities'][0]) & {'title','titleID','titleId','images','link','entityType','type','contentType'}) if node.get('entities') and isinstance(node['entities'][0],dict) else [],
                            'continuation_present':bool(node.get('paginationServiceToken'))})
                declared=next((_link(node.get(key),page_url) for key in ('apiUrl','href','url','link') if _link(node.get(key),page_url)),None)
                matched=any(n.endswith(identifiers) for n in names) or matches(label) or bool(declared and collection_route(declared)==collection)
                if matched:
                    active|=any(node.get(k) is True for k in ('active','applied','isCurrentlyApplied','selected','isSelected'))
                    for key in ('apiUrl','href','url','link'):
                        target=_link(node.get(key),page_url)
                        if target and safe_url(target,STOREFRONT_HOSTS):
                            if navigation_identity(target)==current:inventory['self_navigation_links_ignored']+=1
                            else:candidates.append((0 if key=='apiUrl' else 1,target))
                            break
                    if content_node(node.get('content')):payloads.append(node['content']);explicit=True
                    if kind in ('Grid','StandardCarousel') and isinstance(node.get('entities'),list):payloads.append(node);explicit=True
                    elif isinstance(node.get('emptyState'),dict):payloads.append({'emptyState':node['emptyState']});explicit=True
                for key,value in node.items():
                    name=re.sub('[^a-z]','',str(key).casefold())
                    if name in (identifiers if collection=='watchlist' else ('legacylibrary',)+identifiers) and content_node(value):
                        payloads.append(value);explicit=True
                    if isinstance(value,(dict,list)):
                        segment=key if key in ('init','preparations','body','props','pageProps','data','content','collections','containers','widgets','results','entities','items') else '#'+hashlib.sha256(str(key).encode()).hexdigest()[:8]
                        stack.append((value,path+'.'+segment,depth+1))
            elif isinstance(node,list):stack.extend((v,path+'[]',depth+1) for v in node if isinstance(v,(dict,list)))
    confirmed=selected or active or explicit or self_links>0
    # A provider-selected collection can place its Grid outside the initial
    # body wrapper. Its declared navigation scopes the sole Grid; unrelated
    # recommendation carousels do not qualify as the selected collection.
    if confirmed and not payloads and len(grids)==1:payloads.append(grids[0])
    if confirmed and not payloads and len(empty_bodies)==1:payloads.append(empty_bodies[0])
    extraction.update(personal_collection=collection,library_inventory=inventory,protocol_structure=shapes,
        collection_scope_confirmed=confirmed,scope_from_followed_provider_link=selected,
        selected_zero_container_pages=len(empty_bodies),
        scope_from_selected_navigation=active or self_links>0,explicit_library_payload=explicit,
        library_payloads=len(payloads),server_error_codes=sorted(set(codes))[:30])
    if extraction.get('sign_in_form_present') or extraction.get('verification_form_present') or extraction.get('rendered_signin_required'):
        return None,dict(extraction,library_authentication_required=True,collection_state='authentication_required'),None
    if provider_error:
        return None,dict(extraction,library_provider_error=True,collection_state='provider_error'),None
    cards={};links=set();proof=[];records=0;pending=False
    for payload in payloads:
        found,direct,_=_json_content(payload,page_url)
        for key,card in found.items():
            _remember_card(cards,key,replace(card,access_evidence=merge_evidence(card.access_evidence,'account_library'))
                if collection=='purchases' else card)
        links.update(direct)
        reasons,count,more=_empty_evidence(payload);proof.extend(reasons);records+=count;pending|=more
        stack=[payload]
        while stack:
            node=stack.pop()
            if isinstance(node,dict):
                if _json_card(node,page_url):continue
                links.update(_direct_next_links(node,page_url))
                stack.extend(v for v in node.values() if isinstance(v,(dict,list)))
            elif isinstance(node,list):stack.extend(v for v in node if isinstance(v,(dict,list)))
    next_url,count=_next_url(links,page_url)
    if count>1:return None,dict(extraction,library_pagination_ambiguous=True,collection_state='unrecognized',continuation_candidates=count),None
    if extraction.get('rendered_empty_proof'):proof.append(extraction['rendered_empty_proof'])
    empty=confirmed and not cards and not next_url and not pending and records==0 and bool(proof) and not extraction.get('rendered_error_cue')
    extraction.update(raw_collection_records=records,parsed_collection_cards=len(cards),empty_proof=sorted(set(proof)),
        empty_confirmed=empty,continuation_candidates=count,native_continuation_pending=pending)
    if confirmed and (cards or empty or next_url or pending):
        state='items' if cards else 'empty' if empty else 'continuation'
        return CatalogPage(list(cards.values()),next_url,library_confirmed=collection=='purchases',
            watchlist_confirmed=collection=='watchlist',empty_confirmed=empty),dict(extraction,collection_state=state,library_confirmed=True),None
    unique={}
    for rank,target in candidates:
        identity=navigation_identity(target)
        if identity==current:continue
        if identity not in unique or rank<unique[identity][0]:unique[identity]=(rank,target)
    if unique:
        rank=min(pair[0] for pair in unique.values());links=[target for priority,target in unique.values() if priority==rank]
        if len(links)==1:return None,dict(extraction,library_navigation_candidates=1,collection_state='navigation'),links[0]
    return None,dict(extraction,library_navigation_candidates=len(unique),library_structure_unrecognized=True,
        collection_state='unrecognized',empty_confirmed=False),None
