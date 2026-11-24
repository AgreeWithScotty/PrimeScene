"""Read only explicitly labelled Amazon Continue Watching collections."""
import re
from time import monotonic
from .public_catalog import CatalogPage, PublicCatalogError, PublicSigninRequired, _json_content, _protocol_shape, _text
from .provider_documents import read_documents

def is_progress_collection(node):
    if not isinstance(node,dict):return False
    labels=[_text(node.get(k)) for k in ('collectionType','widgetType','type','id','title','heading','name','displayTitle')]
    return node.get('isContinueWatching') is True or any(re.sub('[^a-z]','',label.lower()) in
        ('continuewatching','watchnext','continuewatchingcarousel') for label in labels)


def read_home(data, content_type, address, observer=None):
    documents,extraction,_=read_documents(data,content_type)
    if extraction.get('sign_in_form_present') or extraction.get('verification_form_present'):
        if observer:observer(dict(extraction,event='home_authentication_required',stage='home_document_extract',result='authentication_required'))
        raise PublicSigninRequired('Use Sign out, then sign in again to reconnect your Amazon session.')
    scan_started=monotonic()
    stack=list(documents); collections=[]; visited=0;inventory=[]
    while stack:
        node=stack.pop();visited+=1
        if visited>100000: raise PublicCatalogError('Home structure exceeds its limit')
        if isinstance(node,dict):
            recognized=is_progress_collection(node)
            if isinstance(node.get('entities'),list) and len(inventory)<24:
                inventory.append({'continue_watching':recognized,'entity_count':len(node['entities']),
                    'pagination_token_present':bool(node.get('paginationServiceToken')),
                    'pagination_start_index':node.get('paginationStartIndex') if isinstance(node.get('paginationStartIndex'),int) else None,
                    'continuation_target_present':bool(node.get('paginationTargetId')),
                    'entity_known_fields':sorted(set(node['entities'][0]) & {'title','titleID','images','link','progress','playbackActions','entityType'})
                        if node['entities'] and isinstance(node['entities'][0],dict) else []})
            if recognized:
                collections.append(node)
            else:
                stack.extend(v for v in node.values() if isinstance(v,(dict,list)))
        elif isinstance(node,list):stack.extend(node)
    scanned=monotonic();cards={};candidates=0
    for collection in collections:
        found,_,parsed= _json_content(collection,address,page_continuation=False)
        candidates+=parsed.get('json_card_candidates',0)
        cards.update(found)
    details={'format':'home','progress_collections':len(collections),'cards':len(cards),
        **extraction,'progress_scope_nodes':visited,'progress_scope_ms':round((scanned-scan_started)*1000),
        'progress_card_parse_ms':round((monotonic()-scanned)*1000),
        'json_documents':len(documents),'protocol_structure':[_protocol_shape(d) for d in documents[:3]],
        'history_confirmed':bool(collections),'history_collection_inventory':inventory,
        'progress_card_candidates':candidates,
        'progress_collections_with_continuation':sum(bool(c.get('paginationServiceToken')) for c in collections),
        'provider_position_fields':sum(c.resume_seconds is not None for c in cards.values()),
        'provider_progress_fields':sum(c.percentplayed>0 for c in cards.values()),
        'result':'success' if collections else 'unrecognized'}
    if not collections:
        if observer:
            observer(dict(details,event='home_history_unrecognized',stage='home_parse',result='failure'))
        raise PublicCatalogError('Amazon did not expose a recognized Continue Watching collection. See Home diagnostics.')
    # Empty is valid only after the collection itself was recognized.
    return CatalogPage(list(cards.values()),empty_confirmed=not cards),details


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
