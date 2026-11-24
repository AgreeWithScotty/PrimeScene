"""Service directory from provider Nodes, distinct from promoted title shelves."""
import re
from time import monotonic
from urllib.parse import urlsplit
from .detail_catalog import documents
from .public_catalog import ORIGIN,STOREFRONT_HOSTS,IMAGE_HOSTS,safe_url,StorefrontParser,_protocol_shape
from .prime_diagnostics import route

def service_address(value,base=ORIGIN):
    address=safe_url(value,STOREFRONT_HOSTS,base)
    if not address:return None
    path=re.sub(r'^/region/[a-z]{2,4}','',urlsplit(address).path).rstrip('/')
    return address if (path.startswith('/channel/') or path.startswith('/storefront/subscription/')) and path not in (
        '/storefront/subscription/default','/storefront/subscription/prime') else None

def read_directory(data,content_type,address,observe):
    docs=documents(data,content_type,address);rows=[];links=[];groups=[];rejected=0
    for document in docs:
        if not isinstance(document,dict):continue
        body=((document.get('init') or {}).get('preparations') or {}).get('body') or document
        for container in body.get('containers') or []:
            if not isinstance(container,dict) or container.get('containerType') not in ('Nodes','Grid'):continue
            count=0
            for node in container.get('entities') or []:
                if not isinstance(node,dict):continue
                link=node.get('link') or {};metadata=link.get('metadata') or {}
                target=service_address(link.get('url'),address)
                label=node.get('displayTitle') or node.get('title') or ''
                if not target or not isinstance(label,str) or not label.strip() or metadata.get('pageType') not in ('subscription','channel'):
                    rejected+=1;continue
                image=next((safe_url((node.get('images',{}).get(k) or {}).get('url'),IMAGE_HOSTS)
                    for k in ('cover','logo','hero') if isinstance(node.get('images',{}).get(k),dict)
                    and safe_url(node['images'][k].get('url'),IMAGE_HOSTS)), '')
                rows.append({'title':label,'address':target,'artwork':image,'caption':'Subscription service'})
                count+=1
            more=(container.get('seeMore') or {}).get('link') or {}
            target=safe_url(more.get('url'),STOREFRONT_HOSTS,address)
            if target and re.fullmatch(r'(?:/region/[a-z]{2,4})?/storefront/subscription/default/?',urlsplit(target).path):links.append(target)
            groups.append({'container_type':container.get('containerType'),'entities':len(container.get('entities') or []),
                'services':count,'estimated_total':container.get('estimatedTotal'),'has_pagination_token':bool(container.get('paginationServiceToken'))})
    unique={}
    for row in rows:
        key=row['title'].casefold().strip()
        old=unique.get(key)
        if old is None or '/storefront/subscription/' in row['address']:unique[key]=row
    parser=StorefrontParser(address);parser.feed(data);parser.close()
    next_links=sorted(parser.next_links)
    observe({'event':'service_directory_parsed','stage':'service_directory_parse','documents':len(docs),
        'groups':groups,'services':len(unique),'rejected_non_catalog_nodes':rejected,
        'directory_links':len(set(links)),'next_links':len(next_links),
        'protocol_structure':[_protocol_shape(d) for d in docs[:2]],'subscription_filter_applied':False,
        'result':'success' if unique else 'unavailable'})
    return sorted(unique.values(),key=lambda row:row['title'].casefold()),list(dict.fromkeys(links)),next_links

def discover(client,observe,publish=None,active=lambda:True):
    started=monotonic();batches=0
    if not active():return [],False
    response=client.request(ORIGIN+'/addons',stage='service_directory_discovery')
    def read(reply):return read_directory(reply.data.decode(reply.headers.get_content_charset() or 'utf-8',errors='replace'),
        reply.headers.get_content_type(),reply.address,observe)
    initial,links,_=read(response)
    # The marketing landing has services linked to sign-up offers. Its declared
    # Browse all page supplies actual catalog links (including STARZ), not checkout.
    rows={row['title'].casefold():row for row in initial};pending=links[:1];seen={response.address};complete=False
    def emit():
        nonlocal batches
        if not active():return
        result=sorted(rows.values(),key=lambda row:row['title'].casefold())
        batches+=1
        if publish is not None:publish(result,complete)
        observe({'event':'service_directory_batch_ready','stage':'service_directory_publish',
            'batch':batches,'pages':len(seen),'services':len(result),'complete':complete,
            'elapsed_ms':round((monotonic()-started)*1000),'result':'complete' if complete else 'partial'})
    emit()
    for hop in range(12):
        if not active():
            observe({'event':'service_directory_cancelled','stage':'service_directory_fetch',
                'pages':len(seen),'elapsed_ms':round((monotonic()-started)*1000),'result':'cancelled'})
            return [],False
        if not pending:break
        address=pending.pop(0)
        if address in seen:continue
        seen.add(address)
        observe({'event':'service_directory_link_followed','stage':'service_directory_fetch','hop':hop+1,'address':route(address)})
        response=client.request(address,stage='service_directory_fetch')
        found,_,next_links=read(response)
        if hop==0 and found:rows={}
        rows.update({row['title'].casefold():row for row in found})
        pending.extend(u for u in next_links if u not in seen and u not in pending)
        complete=not pending
        emit()
    result=sorted(rows.values(),key=lambda row:row['title'].casefold())
    observe({'event':'service_directory_ready','stage':'service_directory_complete','services':len(result),
        'pages':len(seen),'all_services_directory_followed':bool(links),'complete':complete,
        'subscription_filter_applied':False,'batches':batches,
        'elapsed_ms':round((monotonic()-started)*1000),
        'result':'success' if complete else 'partial' if result else 'failure'})
    return result,complete
