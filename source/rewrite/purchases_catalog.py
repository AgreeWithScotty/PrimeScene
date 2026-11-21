"""Read only the account library selected by Amazon's actual navigation links."""
from dataclasses import replace
import json
import re
from urllib.parse import parse_qsl, urlsplit


def read_library(data, content_type, page_url, selected=False):
    from .public_catalog import (CatalogPage, PublicCatalogError, StorefrontParser,
        STOREFRONT_HOSTS, _direct_next_links, _json_card, _json_content, _link,
        _next_url, _protocol_shape, _remember_card, safe_url)
    from .catalog_access import merge_evidence
    parser = None
    if content_type.endswith('json') or content_type.endswith('+json'):
        documents = [json.loads(data)]
    else:
        parser = StorefrontParser(page_url)
        parser.feed(data)
        parser.close()
        documents = []
        for script in parser.json_scripts:
            try:
                documents.append(json.loads(script))
            except (ValueError, RecursionError):
                pass
    payloads, candidates, shapes = [], [], []
    active_library = False
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
                label = node.get('label') or node.get('text')
                library_filter = (normalized.endswith(('library', 'purchases', 'purchasesandrentals'))
                    or isinstance(label, str) and label.strip().casefold() in
                    ('purchases', 'purchases & rentals', 'purchases and rentals', 'your purchases'))
                if library_filter:
                    active_library |= any(node.get(key) is True for key in ('active', 'applied', 'isCurrentlyApplied'))
                    for key in ('apiUrl', 'href', 'url'):
                        found = _link(node.get(key), page_url)
                        if found and safe_url(found, STOREFRONT_HOSTS):
                            candidates.append((0 if key == 'apiUrl' else 1, found))
                            break
                    if content_node(node.get('content')):
                        payloads.append(node['content'])
                for key, value in node.items():
                    name = re.sub(r'[^a-z]', '', str(key).casefold())
                    if name in ('legacylibrary', 'library', 'purchases', 'purchasesandrentals') and content_node(value):
                        payloads.append(value)
                    if isinstance(value, (dict, list)):
                        stack.append((value, depth + 1))
            elif isinstance(node, list):
                stack.extend((value, depth + 1) for value in node if isinstance(value, (dict, list)))
    confirmed = selected or active_library
    provider_error = any(isinstance(document, dict) and bool(document.get('error') or document.get('errors'))
                         for document in documents)
    if provider_error:
        return None, {'library_confirmed': confirmed, 'library_provider_error': True,
                      'protocol_structure': shapes}, None
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
            return None, {'library_confirmed': True, 'continuation_candidates': count,
                          'library_pagination_ambiguous': True, 'protocol_structure': shapes}, None
        return CatalogPage(list(cards.values()), next_url, library_confirmed=True), {
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
            return None, {'library_confirmed': False, 'library_navigation_candidates': 1,
                          'protocol_structure': shapes}, links[0]
    # Do not substitute Watchlist, recommendations or a guessed library URL.
    return None, {'library_confirmed': False, 'library_navigation_candidates': len(candidates),
                  'library_payloads': len(payloads), 'protocol_structure': shapes,
                  'library_structure_unrecognized': True}, None
