"""Read only the account library selected by Amazon's actual navigation links."""
from dataclasses import replace
import re
from urllib.parse import parse_qsl, urlsplit
from .provider_documents import read_documents,library_label


def read_library(data, content_type, page_url, selected=False):
    from .public_catalog import (CatalogPage, PublicCatalogError,
        STOREFRONT_HOSTS, _direct_next_links, _json_card, _json_content, _link,
        _next_url, _protocol_shape, _remember_card, safe_url, _text)
    from .catalog_access import merge_evidence
    documents,extraction,html_links=read_documents(data,content_type)
    payloads, candidates, shapes = [], [], []
    active_library = False;explicit_payload=False
    inventory={'library_labels':0,'watchlist_labels':0,'native_containers':0,'explicit_empty_states':0,
        'html_library_links':len(html_links)}
    candidates.extend((1,target) for value in html_links for target in [safe_url(value,STOREFRONT_HOSTS,page_url)] if target)
    nodes = 0

    def content_node(value):
        if not isinstance(value, (dict, list)):
            return False
        if isinstance(value, list):
            return True
        return any(key in value for key in ('content', 'baseOutput', 'containers', 'items',
                                             'entities', 'results', 'emptyState'))

    for document in documents:
        if len(shapes) < 3:
            shapes.append(_protocol_shape(document))
        # Existing account-library responses expose their main collection in
        # results. This is accepted only after following a real library link.
        if selected and isinstance(document, dict):
            for field in ('results', 'content'):
                if content_node(document.get(field)):
                    payloads.append(document[field])
        stack = [(document, 0)]
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if nodes > 100000 or depth > 40:
                raise PublicCatalogError('The library response exceeded its structural limit.')
            if isinstance(node, dict):
                identifier = str(node.get('viewType') or node.get('id') or '')
                normalized = re.sub(r'[^a-z]', '', identifier.casefold())
                label=next((_text(node.get(k)) for k in ('label','text','title','displayTitle') if _text(node.get(k))),'')
                inventory['native_containers']+=node.get('containerType') in ('Grid','StandardCarousel')
                inventory['explicit_empty_states']+='emptyState' in node
                inventory['library_labels']+=library_label(label)
                inventory['watchlist_labels']+=re.sub('[^a-z]','',label.casefold()) in ('watchlist','yourwatchlist')
                library_filter = (normalized.endswith(('library', 'purchases', 'purchasesandrentals'))
                    or library_label(label))
                if library_filter:
                    active_library |= any(node.get(key) is True for key in ('active', 'applied', 'isCurrentlyApplied'))
                    for key in ('apiUrl', 'href', 'url','link'):
                        found = _link(node.get(key), page_url)
                        if found and safe_url(found, STOREFRONT_HOSTS):
                            candidates.append((0 if key == 'apiUrl' else 1, found))
                            break
                    if content_node(node.get('content')):
                        payloads.append(node['content'])
                        explicit_payload=True
                    if node.get('containerType') in ('Grid','StandardCarousel') and isinstance(node.get('entities'),list):
                        payloads.append(node);explicit_payload=True
                for key, value in node.items():
                    name = re.sub(r'[^a-z]', '', str(key).casefold())
                    if name in ('legacylibrary', 'library', 'purchases', 'purchasesandrentals') and content_node(value):
                        payloads.append(value)
                        explicit_payload=True
                    if isinstance(value, (dict, list)):
                        stack.append((value, depth + 1))
            elif isinstance(node, list):
                stack.extend((value, depth + 1) for value in node if isinstance(value, (dict, list)))
    extraction['library_inventory']=inventory
    confirmed = selected or active_library or explicit_payload
    provider_error=False
    for document in documents:
        if not isinstance(document,dict):continue
        body=((document.get('init') or {}).get('preparations') or {}).get('body') or {}
        if any(isinstance(node,dict) and (node.get('hasFailed') is True or bool(node.get('error') or node.get('errors') or node.get('errorCode')))
            for node in (document,body)):provider_error=True
    if provider_error:
        return None, {**extraction,'library_confirmed': confirmed, 'library_provider_error': True,
                      'protocol_structure': shapes}, None
    if extraction.get('sign_in_form_present') or extraction.get('verification_form_present'):
        return None,{**extraction,'library_confirmed':False,'library_authentication_required':True,'protocol_structure':shapes},None
    if confirmed and payloads:
        cards, links = {}, set()
        for payload in payloads:
            found, direct, _ = _json_content(payload, page_url)
            for key, card in found.items():
                _remember_card(cards, key, replace(card,
                    access_evidence=merge_evidence(card.access_evidence, 'account_library')))
            links.update(direct)
            # The library's own nested collection is a page, unlike unrelated
            # recommendation carousels, which are not part of these payloads.
            stack = [payload]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    if _json_card(node, page_url):
                        continue
                    links.update(_direct_next_links(node, page_url))
                    stack.extend(value for value in node.values() if isinstance(value, (dict, list)))
                elif isinstance(node, list):
                    stack.extend(value for value in node if isinstance(value, (dict, list)))
        next_url, count = _next_url(links, page_url)
        if count > 1:
            return None, {**extraction,'library_confirmed': True, 'continuation_candidates': count,
                          'library_pagination_ambiguous': True, 'protocol_structure': shapes}, None
        return CatalogPage(list(cards.values()), next_url, library_confirmed=True,empty_confirmed=not cards and not next_url), {
            **extraction,'explicit_library_payload':explicit_payload,
            'library_confirmed': True, 'library_payloads': len(payloads), 'cards': len(cards),
            'continuation_candidates': count, 'protocol_structure': shapes,
            'library_empty': not cards and not next_url}, None
    current = urlsplit(page_url)._replace(fragment='')
    candidates = [(rank, link) for rank, link in candidates if urlsplit(link)._replace(fragment='') != current]
    if candidates:
        priority = min(rank for rank, _link_value in candidates)
        unique = {}
        for rank, link in candidates:
            if rank != priority:
                continue
            parts = urlsplit(link)
            key = (parts.scheme, parts.hostname, parts.path, tuple(sorted(
                (key, value) for key, value in parse_qsl(parts.query) if key not in ('ref', 'ref_'))))
            unique.setdefault(key, link)
        links = list(unique.values())
        if len(links) == 1:
            return None, {**extraction,'library_confirmed': False, 'library_navigation_candidates': 1,
                          'protocol_structure': shapes}, links[0]
    # Do not substitute Watchlist, recommendations or a guessed library URL.
    return None, {**extraction,'library_confirmed': False, 'library_navigation_candidates': len(candidates),
                  'library_payloads': len(payloads), 'protocol_structure': shapes,
                  'library_structure_unrecognized': True}, None
