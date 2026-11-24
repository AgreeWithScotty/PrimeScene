"""Read-only Grid continuation from Amazon's declared web collection protocol.

The public DVWebClient PaginateCollection operation posts these form fields.
Opaque service tokens and targets are carried unchanged, never decoded/rebuilt.
Only a single page-owned Grid qualifies; shelf/carousel tokens are not promoted.
"""
from copy import deepcopy
import hashlib,json,re
from urllib.parse import urlencode,urlsplit,urlunsplit
from .public_catalog import PublicCatalogError,STOREFRONT_HOSTS,safe_url,_protocol_shape

FIELDS={'pageType','pageId','collectionType','paginationTargetId','serviceToken',
    'startIndex','actionScheme','payloadScheme','widgetScheme','decorationScheme',
    'featureScheme','dynamicFeatures','variant','journeyIngressContext'}

def identity(target):
    return ('collection:'+hashlib.sha256(json.dumps(target,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if isinstance(target,dict) else target)

def valid_request(value):
    if not isinstance(value,dict) or value.get('kind')!='collection':return False
    if value.get('scope') not in (None,'watching'):return False
    address=safe_url(value.get('address'),STOREFRONT_HOSTS)
    if not address or not re.fullmatch(r'(?:/region/[a-z]{2,4})?/api/paginateCollection',urlsplit(address).path):return False
    if urlsplit(address).query or not safe_url(value.get('source'),STOREFRONT_HOSTS):return False
    payload=value.get('payload')
    if not isinstance(payload,dict) or set(payload)-FIELDS:return False
    for key in ('pageType','pageId','paginationTargetId','serviceToken','widgetScheme','decorationScheme','featureScheme'):
        if not isinstance(payload.get(key),str) or not 0<len(payload[key])<=32768:return False
    return (payload.get('collectionType')=='Container' and payload.get('actionScheme')=='default'
        and payload.get('payloadScheme')=='default' and isinstance(payload.get('startIndex'),int)
        and 0<=payload['startIndex']<=1000000
        and all(isinstance(v,(str,int,bool,list)) for v in payload.values())
        and isinstance(payload.get('dynamicFeatures',[]),list)
        and all(isinstance(x,str) and len(x)<200 for x in payload.get('dynamicFeatures',[])))

def discover(data,content_type,address,observe,scope=None):
    from .detail_catalog import documents
    for document in documents(data,content_type,address):
        prep=(document.get('init') or {}).get('preparations') or {} if isinstance(document,dict) else {}
        body=prep.get('body') or {}
        containers=body.get('containers') or []
        if scope=='watching':
            from .home_catalog import is_progress_collection
            candidates=[c for c in containers if is_progress_collection(c) and c.get('paginationServiceToken')]
            observe({'event':'progress_continuation_inventory','stage':'collection_discovery',
                'recognized_containers':sum(is_progress_collection(c) for c in containers),
                'continuable_containers':len(candidates),'result':'success'})
            if len(candidates)>1:raise PublicCatalogError('Amazon returned multiple In Progress continuations. Open Diagnostics.')
            if not candidates:continue
            grid=candidates[0]
        else:
            if len(containers)!=1 or containers[0].get('containerType')!='Grid':continue
            grid=containers[0]
        if not grid.get('paginationServiceToken'):continue
        page=body.get('swiftPageParameters') or {}
        capability=((body.get('sitewide') or {}).get('sitewide-alexa') or {}).get('primeVideoCapability') or {}
        observe({'event':'collection_grid_inventory','stage':'collection_discovery','grid_fields':sorted(grid),
            'target_present':bool(grid.get('paginationTargetId')),'token_present':bool(grid.get('paginationServiceToken')),
            'capability_fields':sorted(capability),'page_parameter_fields':sorted(page),'result':'success'})
        if not page or not capability:
            observe({'event':'collection_protocol_missing','stage':'collection_discovery',
                'has_page_parameters':bool(page),'has_capability':bool(capability),'result':'failure'})
            raise PublicCatalogError('Amazon did not declare the genre pagination capability. Open Diagnostics.')
        parts=urlsplit(address);prefix=re.match(r'^/region/[a-z]{2,4}(?=/)',parts.path)
        endpoint=urlunsplit(('https',parts.netloc,(prefix.group() if prefix else '')+'/api/paginateCollection','',''))
        payload={k:capability[k] for k in ('widgetScheme','decorationScheme','featureScheme','dynamicFeatures','variant') if k in capability}
        payload.update({k:page[k] for k in ('pageType','pageId') if k in page})
        payload.update(collectionType='Container',paginationTargetId=grid.get('paginationTargetId'),
            serviceToken=grid['paginationServiceToken'],startIndex=grid.get('paginationStartIndex'),
            actionScheme='default',payloadScheme='default')
        if grid.get('journeyIngressContext'):payload['journeyIngressContext']=grid['journeyIngressContext']
        result={'kind':'collection','address':endpoint,'source':address,'payload':payload}
        if scope=='watching':result['scope']='watching'
        if not valid_request(result):raise PublicCatalogError('Amazon returned an unsupported genre pagination structure. Open Diagnostics.')
        observe({'event':'collection_continuation_discovered','stage':'collection_discovery',
            'start_index':payload['startIndex'],'entity_count':len(grid.get('entities') or []),
            'estimated_total':grid.get('estimatedTotal'),'request_fields':sorted(payload),
            'token_present':True,'result':'success'})
        return result

def request_arguments(value):
    if not valid_request(value):raise PublicCatalogError('The collection continuation is invalid or expired.')
    return value['address'],urlencode(value['payload'],doseq=True).encode('utf-8'),{
        'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json',
        'Referer':value['source'],'Origin':'https://'+urlsplit(value['address']).netloc}

def advance(value,data,content_type,observe):
    if not content_type.endswith('json'):raise PublicCatalogError('Amazon returned a non-JSON collection page.')
    document=json.loads(data);more=document.get('hasMoreItems')
    from .http_transport import server_errors
    pagination=document.get('pagination') or {};start=document.get('startIndex')
    observe({'event':'collection_page_state','stage':'collection_parse','has_more_items':more,
        'start_index':start,'requested_start_index':value['payload']['startIndex'],
        'entity_count':len(document.get('entities') or []),'pagination_fields':sorted(pagination),
        'protocol_structure':_protocol_shape(document),'server_error_codes':server_errors(document),
        'end_reason':'provider_declared_end' if more is False else 'provider_continuation' if more is True else 'unrecognized_state',
        'result':'success' if isinstance(more,bool) else 'failure'})
    if more is False:return None
    if more is not True or not isinstance(start,int) or start<=value['payload']['startIndex'] or not pagination.get('serviceToken'):
        raise PublicCatalogError('Amazon returned an incomplete collection continuation. Open Diagnostics.')
    result=deepcopy(value);result['payload'].update(startIndex=start,serviceToken=pagination['serviceToken'])
    if not valid_request(result):raise PublicCatalogError('Amazon returned an invalid collection continuation.')
    return result
