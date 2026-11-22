"""Episode rows from declared title relationships and actual Amazon detail state."""
from copy import deepcopy
import json
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from .public_catalog import (MAX_PAGE_BYTES, STOREFRONT_HOSTS, PublicCatalogError, StorefrontParser,
    _count_value, _duration_value, _first_shot, _genre_value, _next_url, _plot_value,
    _rating_value, _shot_map, _text, _year_value, _direct_next_links, safe_url)
from .prime_diagnostics import exception_details, route


def _kind(info):
    value = str((info or {}).get('mediatype') or (info or {}).get('titleType') or '').lower().replace(' ', '').replace('_', '')
    return {'show': 'tvshow', 'series': 'tvshow', 'tvseries': 'tvshow',
            'tvseason': 'season', 'tvepisode': 'episode'}.get(value, value)


def _episode(identifier, title, info, pluginid):
    info = dict(info, mediatype='episode', contentType='episode')
    params = {'mode': 'PlayVideo', 'asin': identifier, 'name': title,
              'adult': info.get('isAdult', 0), 'trailer': 0, 'selbitrate': 0}
    return {'title': title, 'identity': identifier, 'video': True, 'info': info,
            'url': pluginid+'?'+urlencode(params)}





def _info(node):
    from .watch_home import progress_info
    shots = _shot_map(node)
    result = {'mediatype': _kind(node), 'plot': _plot_value(node), 'year': _year_value(node),
        'duration': _duration_value(node), 'mpaa': _rating_value(node), 'genre': _genre_value(node),
        'season': node.get('seasonNumber'), 'episode': node.get('episodeNumber'),
        'thumb': _first_shot(shots, ('packshot', 'landscape', 'thumbnail', 'thumb', 'image')),
        'poster': _first_shot(shots, ('covershot', 'poster', 'titleshot')),
        'fanart': _first_shot(shots, ('heroshot', 'hero', 'fanart', 'background'))}
    result.update(progress_info(node))
    return {key: value for key, value in result.items() if value not in (None, '', [], {})}


def parse_tv_page(data, content_type, address, pluginid, observer=None):
    if content_type.endswith('json'):
        documents = [json.loads(data)]
    else:
        parser = StorefrontParser(address)
        parser.feed(data)
        parser.close()
        documents = []
        for script in parser.json_scripts:
            try:
                documents.append(json.loads(script))
            except ValueError:
                pass
    states, stack, visited = [], list(documents), 0
    while stack:
        node = stack.pop()
        visited += 1
        if visited > 100000:
            raise PublicCatalogError('TV detail structure exceeds the parser limit.')
        if isinstance(node, dict):
            state = node.get('state')
            if isinstance(state, dict) and state.get('pageTitleId'):
                states.append(state)
            widgets = node.get('widgets')
            if isinstance(widgets, dict) and isinstance(widgets.get('episodeList'), dict):
                context = widgets.get('pageContext') or {}
                episodes = widgets['episodeList'].get('episodes') or []
                states.append({'pageTitleId': context.get('pageTitleId'),
                    'episodeList': dict(widgets['episodeList'], cardTitleIds=[entry.get('titleID') for entry in episodes]),
                    'detail': {entry.get('titleID'): entry.get('detail') or {} for entry in episodes},
                    'self': {entry.get('titleID'): entry.get('self') or {} for entry in episodes}})
            stack.extend(value for value in node.values() if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            stack.extend(value for value in node if isinstance(value, (dict, list)))
    candidates = [state for state in states if (state.get('episodeList') or {}).get('cardTitleIds')]
    if observer:
        observer({'event': 'tv_state_inventory', 'stage': 'tv_parse', 'json_documents': len(documents),
            'state_count': len(states), 'episode_list_states': len(candidates),
            'state_fields': [sorted(state) for state in states[:4]],
            'episode_list_fields': [sorted(state.get('episodeList') or {}) for state in states[:4]]})
    if not candidates:
        raise PublicCatalogError('Amazon did not return a recognized episode list; the TV parser report has its structure.')
    primary = max(candidates, key=lambda state: len(state['episodeList']['cardTitleIds']))
    selected_id = primary.get('pageTitleId')
    related = [state for state in states if state.get('pageTitleId') == selected_id]
    records, links, ordered, next_links, expected = {}, {}, [], set(), 0
    token_only = False
    for state in related:
        detail = state.get('detail') or {}
        details = detail.get('detail', detail)
        for mapping in (state.get('self') or {}, details, detail.get('headerDetail') or {}):
            if not isinstance(mapping, dict):
                continue
            for identifier, value in mapping.items():
                if isinstance(value, dict) and value.get('titleType'):
                    records.setdefault(identifier, {}).update({key: item for key, item in value.items()
                                                               if item not in (None, '', [], {})})
        for values in (state.get('seasons') or {}).values():
            for entry in values if isinstance(values, list) else []:
                if isinstance(entry, dict) and entry.get('seasonId') and entry.get('seasonLink'):
                    links[entry['seasonId']] = dict(entry, link=entry['seasonLink'], titleType='season',
                        seasonNumber=entry.get('seasonNumber', entry.get('sequenceNumber')))
        listing = state.get('episodeList') or {}
        for identifier in listing.get('cardTitleIds') or []:
            if identifier and identifier not in ordered:
                ordered.append(identifier)
        try:
            expected = max(expected, int(listing.get('totalCardSize') or 0))
        except (TypeError, ValueError):
            pass
        actions = listing.get('actions') or {}
        next_links.update(_direct_next_links(actions, address))
        for page in actions.get('pagination') or []:
            if not isinstance(page, dict) or str(page.get('tokenType') or '').lower() != 'nextpage':
                continue
            found = next((safe_url(page.get(key), STOREFRONT_HOSTS, address) for key in ('url', 'href', 'apiUrl')
                          if safe_url(page.get(key), STOREFRONT_HOSTS, address)), None)
            if found:
                next_links.add(found)
            elif page.get('token'):
                token_only = True
    selected = records.get(selected_id) or {}
    season_number = selected.get('seasonNumber', selected.get('sequenceNumber'))
    rows = []
    for identifier in ordered:
        episode = records.get(identifier) or {}
        if _kind(episode) != 'episode' or not _text(episode.get('title') or episode.get('displayTitle')):
            continue
        info = _info(episode)
        if info.get('season') is None and season_number is not None:
            info['season'] = season_number
        rows.append(_episode(identifier, _text(episode.get('title') or episode.get('displayTitle')), info, pluginid))
    for identifier, entry in records.items():
        if _kind(entry) == 'season':
            links.setdefault(identifier, entry)
    selected_route = dict(links.get(selected_id) or selected, titleType='season')
    if not safe_url(selected_route.get('link'), STOREFRONT_HOSTS, address):
        selected_route['link'] = address
    links[selected_id] = selected_route
    seasons = []
    for identifier, entry in links.items():
        link = safe_url(entry.get('link') or entry.get('seasonLink'), STOREFRONT_HOSTS, address)
        if not link:
            continue
        info = _info(entry)
        info['mediatype'] = 'season'
        if not info.get('season') and entry.get('sequenceNumber') is not None:
            info['season'] = entry['sequenceNumber']
        seasons.append({'identity': identifier, 'title': _text(entry.get('title')) or
                        'Season '+str(info.get('season') or ''), 'detail_url': link, 'info': info})
    next_url, next_count = _next_url(next_links, address)
    details = {'source_states': len(related), 'episode_ids': len(ordered), 'episodes_parsed': len(rows),
        'unresolved_episode_ids': len(ordered)-len(rows), 'expected_total': expected,
        'season_routes': len(seasons), 'continuation_candidates': next_count,
        'unsupported_token_continuation': token_only, 'selected_kind': _kind(selected)}
    return {'rows': rows, 'info': dict(_info(selected), mediatype='season'), 'seasons': seasons,
            'selected_id': selected_id, 'next_url': next_url, 'episode_diagnostics': details}


def fetch_tv_page(address, pluginid, opener, observer):
    started = monotonic()
    if not safe_url(address, STOREFRONT_HOSTS):
        raise PublicCatalogError('The show did not provide a trusted episode-list address.')
    stage = 'tv_fetch'
    status = None
    observer({'event': 'tv_request_started', 'stage': stage, 'address': route(address), 'method': 'GET'})
    try:
        with opener.open(Request(address, headers={'Accept': 'text/html, application/json;q=0.9','Cache-Control':'no-cache','Pragma':'no-cache'}), timeout=15) as response:
            status, final = response.getcode(), response.geturl()
            content_type = response.headers.get_content_type()
            observer({'event': 'tv_response_received', 'stage': stage, 'status': status,
                'address': route(final), 'content_type': content_type,
                'server_request_id': response.headers.get('x-amzn-requestid') or response.headers.get('x-amz-rid')})
            if not safe_url(final, STOREFRONT_HOSTS):
                raise PublicCatalogError('The TV response left its trusted storefront.')
            raw = response.read(MAX_PAGE_BYTES+1)
            if len(raw) > MAX_PAGE_BYTES:
                raise PublicCatalogError('TV detail data exceeds its size limit.')
            data = raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
        stage = 'tv_parse'
        packet = parse_tv_page(data, content_type, final, pluginid, observer)
        observer(dict(packet['episode_diagnostics'], event='tv_episode_state_parsed', stage=stage,
            status=status, address=route(final), bytes=len(raw),
            elapsed_ms=round((monotonic()-started)*1000), result='success'))
        return packet
    except Exception as error:
        details = {'event': 'tv_request_failed', 'stage': stage, 'address': route(address),
            'status': error.code if isinstance(error, HTTPError) else status,
            'exception': exception_details(error), 'elapsed_ms': round((monotonic()-started)*1000), 'result': 'failure'}
        if isinstance(error, HTTPError):
            details['response_address'] = route(error.geturl())
            details['server_request_id'] = error.headers.get('x-amzn-requestid') or error.headers.get('x-amz-rid')
            details['content_type'] = error.headers.get_content_type()
            try:
                body = error.read(8193)
                details['error_response_bytes'] = len(body)
                if len(body) <= 8192 and details['content_type'].endswith('json'):
                    from .public_catalog import _protocol_shape
                    details['error_structure'] = _protocol_shape(json.loads(body))
            except Exception as reading_error:
                details['error_response_read_failure'] = exception_details(reading_error)
            finally:
                error.close()
        observer(details)
        raise
