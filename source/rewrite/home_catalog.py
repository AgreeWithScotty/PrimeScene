"""Read only explicitly labelled Amazon Continue Watching collections."""
import re
from time import monotonic
from .public_catalog import CatalogPage, PublicCatalogError, PublicSigninRequired, _json_content, _protocol_shape
from .provider_documents import read_documents


def read_home(data, content_type, address, observer=None):
    documents,extraction,_=read_documents(data,content_type)
    if extraction.get('sign_in_form_present') or extraction.get('verification_form_present'):
        if observer:observer(dict(extraction,event='home_authentication_required',stage='home_document_extract',result='authentication_required'))
        raise PublicSigninRequired('Use Sign out, then sign in again to reconnect your Amazon session.')
    scan_started=monotonic()
    stack=list(documents); collections=[]; visited=0
    while stack:
        node=stack.pop();visited+=1
        if visited>100000: raise PublicCatalogError('Home structure exceeds its limit')
        if isinstance(node,dict):
            labels=[node.get(k) for k in ('collectionType','widgetType','type','id','title','heading','name')]
            recognized=node.get('isContinueWatching') is True or any(isinstance(label,str) and re.sub('[^a-z]','',label.lower()) in
                ('continuewatching','watchnext','continuewatchingcarousel') for label in labels)
            if recognized:
                collections.append(node)
            else:
                stack.extend(v for v in node.values() if isinstance(v,(dict,list)))
        elif isinstance(node,list):stack.extend(node)
    scanned=monotonic();cards={}
    for collection in collections:
        found,_,_= _json_content(collection,address,page_continuation=False)
        cards.update(found)
    details={'format':'home','progress_collections':len(collections),'cards':len(cards),
        **extraction,'progress_scope_nodes':visited,'progress_scope_ms':round((scanned-scan_started)*1000),
        'progress_card_parse_ms':round((monotonic()-scanned)*1000),
        'json_documents':len(documents),'protocol_structure':[_protocol_shape(d) for d in documents[:3]],
        'history_confirmed':bool(collections),'result':'success' if collections else 'unrecognized'}
    if not collections:
        if observer:
            observer(dict(details,event='home_history_unrecognized',stage='home_parse',result='failure'))
        raise PublicCatalogError('Amazon did not expose a recognized Continue Watching collection. See Home diagnostics.')
    # Empty is valid only after the collection itself was recognized.
    return CatalogPage(list(cards.values()),None,True),details


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
