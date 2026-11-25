"""Read-only Grid continuation from Amazon's declared web collection protocol.

The public DVWebClient PaginateCollection operation posts these form fields.
Opaque service tokens and targets are carried unchanged, never decoded/rebuilt.
Only a single page-owned Grid qualifies; shelf/carousel tokens are not promoted.
"""
from copy import deepcopy
import hashlib,json,re
from urllib.parse import urlencode,urlsplit,urlunsplit
from .public_catalog import PublicCatalogError,STOREFRONT_HOSTS,safe_url,_protocol_shape
from .regional_hosts import storefront_path,region_prefix

FIELDS={'pageType','pageId','collectionType','paginationTargetId','serviceToken',
    'startIndex','actionScheme','payloadScheme','widgetScheme','decorationScheme',
    'featureScheme','dynamicFeatures','variant','journeyIngressContext','myStuffViewType','isPurchaseAndRentalCarousel'}

def identity(target):
    return ('collection:'+hashlib.sha256(json.dumps(target,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if isinstance(target,dict) else target)

def valid_request(value):
    if not isinstance(value,dict) or value.get('kind')!='collection':return False
    if value.get('scope') not in (None,'watching','watchlist','purchases'):return False
    address=safe_url(value.get('address'),STOREFRONT_HOSTS)
    if not address or storefront_path(address)!='/api/paginateCollection':return False
    if urlsplit(address).query or not safe_url(value.get('source'),STOREFRONT_HOSTS):return False
    payload=value.get('payload')
    if not isinstance(payload,dict) or set(payload)-FIELDS:return False
    if 'myStuffViewType' in payload and payload['myStuffViewType']!={'watchlist':'watchlist','purchases':'library'}.get(value.get('scope')):return False
    if 'isPurchaseAndRentalCarousel' in payload and (type(payload['isPurchaseAndRentalCarousel']) is not bool or value.get('scope')!='purchases'):return False
    for key in ('pageType','pageId','paginationTargetId','serviceToken','widgetScheme','decorationScheme','featureScheme'):
        if not isinstance(payload.get(key),str) or not 0<len(payload[key])<=32768:return False
    return (payload.get('collectionType')=='Container' and payload.get('actionScheme')=='default'
        and payload.get('payloadScheme')=='default' and type(payload.get('startIndex')) is int
        and 0<=payload['startIndex']<=1000000
        and all(isinstance(v,(str,int,bool,list)) for v in payload.values())
        and isinstance(payload.get('dynamicFeatures',[]),list)
        and all(isinstance(x,str) and len(x)<200 for x in payload.get('dynamicFeatures',[])))

def discover(data,content_type,address,observe,scope=None):
    from .provider_documents import read_documents,objects,page_contexts
    from .home_catalog import is_progress_collection
    from .public_catalog import _emit
    report=lambda event:_emit(observe,event)
    docs,_,_=read_documents(data,content_type)
    contexts=list(page_contexts(docs))
    # Some regional/personal documents place the collection outside .body.
    # Borrow metadata only when there is one unambiguous declared value.
    if scope in ('watching','watchlist','purchases'):
        nodes=list(objects(docs));pages={};caps={}
        for node,_ in nodes:
            for key,target in (('swiftPageParameters',pages),('primeVideoCapability',caps)):
                if isinstance(node.get(key),dict):target[json.dumps(node[key],sort_keys=True)]=node[key]
        selected=[node for node,_ in nodes if is_progress_collection(node)] if scope=='watching' else [
            node for node,_ in nodes if node.get('containerType')=='Grid']
        covered={id(node) for containers,_,_,_ in contexts for node,_ in objects(containers)}
        outside=[node for node in selected if id(node) not in covered]
        if outside:contexts.append((outside,next(iter(pages.values())) if len(pages)==1 else {},
            next(iter(caps.values())) if len(caps)==1 else {},'$'))
    requests={};pending=0;recognized=0
    for containers,page,capability,path in contexts:
        if scope=='watching':
            candidates=[node for node,_ in objects(containers) if is_progress_collection(node)]
            recognized+=len(candidates)
        elif scope in ('watchlist','purchases'):
            # The selected personal Grid can share a page with unrelated
            # recommendation carousels. Those shelves do not cancel its token.
            grids=[node for node,_ in objects(containers) if node.get('containerType')=='Grid']
            candidates=grids if len(grids)==1 else []
            if len(grids)>1 and any(grid.get('paginationServiceToken') for grid in grids):
                report({'event':'personal_continuation_ambiguous','stage':'collection_discovery',
                    'collection':scope,'grid_count':len(grids),'context_path':path,'result':'failure'})
                raise PublicCatalogError('Amazon returned multiple personal collection Grids without a unique continuation scope. Open Diagnostics.')
        else:
            candidates=containers if len(containers)==1 and isinstance(containers[0],dict) and containers[0].get('containerType')=='Grid' else []
        for grid in candidates:
            if not grid.get('paginationServiceToken') or grid.get('hasMoreItems') is False:continue
            pending+=1
            request=_declared_request(grid,page,capability,address,scope,path,report)
            requests[identity(request)]=request
    report({'event':'progress_continuation_inventory' if scope=='watching' else 'collection_continuation_inventory',
        'stage':'collection_discovery','collection':scope,'page_contexts':len(contexts),
        'recognized_containers':recognized,'continuable_containers':pending,'unique_requests':len(requests),
        'result':'failure' if len(requests)>1 else 'success'})
    if len(requests)>1:raise PublicCatalogError('Amazon returned ambiguous collection continuations. Open Diagnostics.')
    return next(iter(requests.values())) if requests else None


def _declared_request(grid,page,capability,address,scope,path,observe):
    observe({'event':'collection_grid_inventory','stage':'collection_discovery','grid_fields':sorted(grid),
        'collection':scope,'context_path':path,
        'target_present':bool(grid.get('paginationTargetId')),'token_present':bool(grid.get('paginationServiceToken')),
        'capability_fields':sorted(capability),'page_parameter_fields':sorted(page),
        'page_context_source':'provider_mystuff_view_type' if page.get('myStuffViewType') else 'declared_page_parameters',
        'result':'success'})
    if not page or not capability:
        observe({'event':'collection_protocol_missing','stage':'collection_discovery',
            'has_page_parameters':bool(page),'has_capability':bool(capability),'result':'failure'})
        raise PublicCatalogError('Amazon did not declare the collection pagination capability. Open Diagnostics.')
    parts=urlsplit(address)
    endpoint=urlunsplit(('https',parts.netloc,region_prefix(address)+'/api/paginateCollection','',''))
    payload={k:capability[k] for k in ('widgetScheme','decorationScheme','featureScheme','dynamicFeatures','variant') if k in capability}
    payload.update({k:page[k] for k in ('pageType','pageId') if k in page})
    if page.get('myStuffViewType'):payload['myStuffViewType']=page['myStuffViewType']
    if scope=='purchases' and grid.get('isPurchaseAndRentalCarousel') is True:payload['isPurchaseAndRentalCarousel']=True
    payload.update(collectionType='Container',paginationTargetId=grid.get('paginationTargetId'),
        serviceToken=grid['paginationServiceToken'],startIndex=grid.get('paginationStartIndex'),
        actionScheme='default',payloadScheme='default')
    if grid.get('journeyIngressContext'):payload['journeyIngressContext']=grid['journeyIngressContext']
    result={'kind':'collection','address':endpoint,'source':address,'payload':payload}
    if scope in ('watching','watchlist','purchases'):result['scope']=scope
    if not valid_request(result):raise PublicCatalogError('Amazon returned an unsupported collection pagination structure. Open Diagnostics.')
    observe({'event':'collection_continuation_discovered','stage':'collection_discovery',
        'start_index':payload['startIndex'],'entity_count':len(grid.get('entities') or []),
        'estimated_total':grid.get('estimatedTotal'),'request_fields':sorted(payload),
        'token_present':True,'result':'success'})
    return result

def request_arguments(value):
    if not valid_request(value):raise PublicCatalogError('The collection continuation is invalid or expired.')
    payload={key:('true' if item else 'false') if type(item) is bool else item for key,item in value['payload'].items()}
    return value['address'],urlencode(payload,doseq=True).encode('utf-8'),{
        'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json',
        'Referer':value['source'],'Origin':'https://'+urlsplit(value['address']).netloc}

def advance(value,data,content_type,observe):
    if not (content_type.endswith('json') or content_type.endswith('+json')):raise PublicCatalogError('Amazon returned a non-JSON collection page.')
    document=json.loads(data)
    if not isinstance(document,dict):raise PublicCatalogError('Amazon returned a non-object collection page.')
    more=document.get('hasMoreItems')
    from .http_transport import server_errors
    raw_pagination=document.get('pagination')
    pagination=raw_pagination if isinstance(raw_pagination,dict) else {};start=document.get('startIndex')
    observe({'event':'collection_page_state','stage':'collection_parse','has_more_items':more,
        'start_index':start,'requested_start_index':value['payload']['startIndex'],
        'entity_count':len(document.get('entities') or []),'pagination_fields':sorted(pagination),
        'protocol_structure':_protocol_shape(document),'server_error_codes':server_errors(document),
        'end_reason':'provider_declared_end' if more is False else 'provider_continuation' if more is True else 'unrecognized_state',
        'result':'success' if isinstance(more,bool) else 'failure'})
    if document.get('hasFailed') is True or any(document.get(k) for k in ('error','errors','errorCode')):
        raise PublicCatalogError('Amazon returned a collection service error. Open Diagnostics.')
    if more is False:return None
    if more is not True or type(start) is not int or start<=value['payload']['startIndex'] or not pagination.get('serviceToken'):
        raise PublicCatalogError('Amazon returned an incomplete collection continuation. Open Diagnostics.')
    result=deepcopy(value);result['payload'].update(startIndex=start,serviceToken=pagination['serviceToken'])
    if not valid_request(result):raise PublicCatalogError('Amazon returned an invalid collection continuation.')
    return result
