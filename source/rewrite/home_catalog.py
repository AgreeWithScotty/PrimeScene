"""Read explicitly scoped Amazon Continue Watching; unreadable is never empty."""
import re
from time import monotonic
from .public_catalog import (CatalogPage, PublicCatalogError, PublicSigninRequired,
    _json_content, _protocol_shape, _text, _next_url, _remember_card, _emit)
from .provider_documents import read_documents, objects, response_context

_MARKERS=('collectionType','widgetType','containerType','type','id','name','featureType')
_PROGRESS={'continuewatching','watchnext','continuewatchingcarousel'}

def progress_scope(node):
    if not isinstance(node,dict):return None
    if type(node.get('isContinueWatching')) is bool:
        return 'provider_flag' if node['isContinueWatching'] else None
    # A title called Watch Next is not a history collection.
    if not any(key in node for key in ('entities','items','content','containers','emptyState')):return None
    for key in _MARKERS+('title','heading','displayTitle'):
        label=_text(node.get(key))
        if re.sub('[^a-z]','',label.casefold()) in _PROGRESS:
            return 'provider_marker_'+key if key in _MARKERS else 'collection_label_'+key
    return None

def is_progress_collection(node):
    return progress_scope(node) is not None


def read_home(data, content_type, address, observer=None):
    from .purchases_catalog import _empty_evidence
    from .http_transport import server_errors
    documents,extraction,_=read_documents(data,content_type)
    context=response_context(documents,address)
    if extraction.get('sign_in_form_present') or extraction.get('verification_form_present'):
        _emit(observer,dict(extraction,event='home_authentication_required',stage='home_document_extract',
            response_context=context,result='authentication_required'))
        raise PublicSigninRequired('Amazon did not return a signed-in website session for In Progress. Open Diagnostics.')
    scan_started=monotonic();collections=[];inventory=[];visited=0;seen=set()
    for node,path in objects(documents):
        visited+=1;reason=progress_scope(node)
        entities=node.get('entities')
        if isinstance(entities,list) and len(inventory)<24:
            inventory.append({'path':path,'continue_watching':bool(reason),'scope_evidence':reason,
                'entity_count':len(entities),'pagination_token_present':bool(node.get('paginationServiceToken')),
                'pagination_start_index':node.get('paginationStartIndex') if type(node.get('paginationStartIndex')) is int else None,
                'continuation_target_present':bool(node.get('paginationTargetId')),
                'marker_fields':[k for k in _MARKERS+('isContinueWatching','title','heading','displayTitle') if k in node],
                'entity_known_fields':sorted(set(entities[0]) & {'title','titleID','titleId','images','link','progress','playbackActions','entityType'})
                    if entities and isinstance(entities[0],dict) else []})
        if reason and id(node) not in seen:
            seen.add(id(node));collections.append((node,path,reason))
    scanned=monotonic();cards={};candidates=0;links=set();proof=[];records=0;pending=False;failed=False;codes=[];limited=False;unreadable=[];image_hosts=[]
    for collection,path,reason in collections:
        found,direct,parsed=_json_content(collection,address)
        candidates+=parsed.get('json_card_candidates',0)
        limited|=parsed.get('json_traversal_limited',False)
        unreadable.extend(dict(row,collection_path=path) for row in parsed.get('unreadable_record_inventory',[])[:max(0,4-len(unreadable))])
        image_hosts.extend(parsed.get('image_host_inventory',[])[:max(0,16-len(image_hosts))])
        for key,card in found.items():_remember_card(cards,key,card)
        links.update(direct)
        reasons,count,more=_empty_evidence(collection)
        proof.extend(reasons);records+=count;pending|=more
        for node,_ in objects([collection]):
            failed|=node.get('hasFailed') is True or bool(node.get('error') or node.get('errors') or node.get('errorCode'))
        codes.extend(server_errors(collection))
    next_url,next_count=_next_url(links,address)
    empty=bool(collections) and not cards and not pending and not next_url and records==0 and bool(proof) and not failed and not limited
    details={'format':'home','progress_collections':len(collections),'cards':len(cards),
        **extraction,'response_context':context,'progress_scope_nodes':visited,
        'progress_scope_ms':round((scanned-scan_started)*1000),
        'progress_card_parse_ms':round((monotonic()-scanned)*1000),
        'protocol_structure':[_protocol_shape(d) for d in documents[:3]],
        'history_confirmed':bool(collections),'history_collection_inventory':inventory,
        'progress_card_candidates':candidates,'raw_collection_records':records,
        'unreadable_record_inventory':unreadable,'cards_without_artwork':sum(not c.artwork_url for c in cards.values()),
        'image_host_inventory':image_hosts,
        'empty_proof':sorted(set(proof)),'empty_confirmed':empty,'native_continuation_pending':pending,
        'continuation_candidates':next_count,'server_error_codes':sorted(set(codes))[:30],
        'json_traversal_limited':limited,'provider_error':failed,
        'progress_collections_with_continuation':sum(bool(c.get('paginationServiceToken')) for c,_,_ in collections),
        'provider_position_fields':sum(c.resume_seconds is not None for c in cards.values()),
        'provider_progress_fields':sum(c.percentplayed>0 for c in cards.values())}
    readable=bool(collections) and not failed and not limited and next_count<=1 and bool(cards or empty or next_url or pending)
    details['result']='success' if readable else 'failure'
    _emit(observer,dict(details,event='home_history_parsed' if readable else 'home_history_unrecognized',stage='home_parse'))
    if not readable:
        raise PublicCatalogError('Amazon In Progress could not be read; no empty history was confirmed. Open Diagnostics.')
    return CatalogPage(list(cards.values()),next_url=next_url,empty_confirmed=empty),details


def unique_progress_rows(rows):
    """Collapse repeated actual product identities, never unrelated same-name films."""
    from .public_catalog import detail_id
    result=[];seen=set();duplicates=0
    for row in rows:
        identity=detail_id(row.get('detail_url') or '') or row.get('identity') or row.get('url')
        if identity in seen:
            duplicates+=1;continue
        seen.add(identity);result.append(row)
    return result,duplicates
