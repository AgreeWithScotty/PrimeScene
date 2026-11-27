"""Read Prime Video storefront pages using caller-owned authentication cookies."""

import argparse
from dataclasses import asdict, dataclass, fields, replace
from html.parser import HTMLParser
import json
import logging
import re
from .image_quality import choose_image,html_image,source_image,POLICY_REVISION as ARTWORK_POLICY
from time import perf_counter
from secrets import token_hex
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener

try:
    from .prime_diagnostics import exception_details, route
    from .catalog_access import access_evidence, merge_evidence, prime_included
except ImportError:
    from prime_diagnostics import exception_details, route
    from catalog_access import access_evidence, merge_evidence, prime_included


ORIGIN = 'https://www.primevideo.com'
# Use Amazon's published Prime membership storefront, then split by title type.
# The broad /movie and /tv feeds also market rentals, channels and free-with-ads.
PAGES = {'watching': '/storefront', 'home': '/storefront', 'movies': '/storefront/subscription/prime',
         'tv': '/storefront/subscription/prime', 'sports': '/sports', 'channels': '/addons',
         'purchases': '/mystuff', 'watchlist': '/mystuff'}
from .regional_hosts import (PRIME_WEB_HOSTS,CATALOG_NAV_HOSTS,storefront_sso,storefront_relay,
    strip_redirect_headers,POLICY_REVISION,https_host,storefront_path,url_rejection)
STOREFRONT_HOSTS = CATALOG_NAV_HOSTS
IMAGE_HOSTS = {'m.media-amazon.com', 'images-na.ssl-images-amazon.com'}
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_JSON_NODES = 100000
MAX_JSON_DEPTH = 40


def _protocol_shape(document):
    """Report structure without serializing arbitrary account or content keys."""
    known = set(('props pageProps state widgets collections items titles results result data content '
                 'viewOutput page pagination paginator nextPage nextPageUrl paginationTargetUrl '
                 'paginationToken paginationServiceToken nextToken error errors message code '
                 'status statusCode body components resources entities features library legacy-library '
                 'view-filter filters viewType active applied isCurrentlyApplied baseOutput containers '
                 'emptyState errorCode errorType entitlementCues offerClassification providerLogo '
                 'entitlementType offerType').split())
    known.update(('init','preparations','config','selected','isSelected','isEmpty','loading','isLoading','estimatedTotal',
        'entities','hasMoreItems','startIndex','serviceToken','paginationServiceToken',
        'paginationTargetId','paginationStartIndex','swiftPageParameters','primeVideoCapability',
        'isContinueWatching','containerType','progress','percentage','resumeTime','playbackActions','runtime','entityType'))
    known.update(('__type','sharedArgs','pageType','pageId','pageContext','swiftRequestParameters','myStuffViewType'))
    stack, result = [('$', document, 0)], []
    while stack and len(result) < 60:
        path, value, depth = stack.pop()
        if isinstance(value, dict):
            keys = sorted(key for key in value if key in known)
            result.append({'path': path, 'type': 'object', 'known_fields': keys,
                           'other_field_count': len(value) - len(keys)})
            if depth < 7:
                stack.extend((path + '.' + key, value[key], depth + 1) for key in reversed(keys)
                             if isinstance(value[key], (dict, list)))
        elif isinstance(value, list):
            result.append({'path': path, 'type': 'array', 'length': len(value)})
            if value and depth < 7 and isinstance(value[0], (dict, list)):
                stack.append((path + '[]', value[0], depth + 1))
    return result


@dataclass(frozen=True)
class Card:
    title: str
    detail_url: str
    artwork_url: str
    fanart_url: str = ''
    poster_url: str = ''
    plot: str = ''
    year: str = ''
    duration: str = ''
    rating: str = ''
    genres: str = ''
    mediatype: str = ''
    season: str = ''
    episode: str = ''
    seasons: str = ''
    landscape_url: str = ''
    access_evidence: str = ''
    percentplayed: float = 0
    playcount: int = 0
    resume_seconds: float = None


@dataclass(frozen=True)
class CatalogPage:
    cards: list
    next_url: str = None
    library_confirmed: bool = False
    next_request: dict = None
    empty_confirmed: bool = False
    watchlist_confirmed: bool = False
    pagination_unavailable: bool = False
    entry_url: str = ''


def _personal_partial(result,page,observer,status,address,error,stage):
    """Retain confirmed cards without claiming the collection is complete."""
    if not result.cards:raise error
    _emit(observer,{'event':'personal_pagination_unavailable','stage':stage,
        'collection':page,'status':status,'address':route(address),'retained_cards':len(result.cards),
        'empty_confirmed':False,'exception':exception_details(error),'result':'partial'})
    return replace(result,pagination_unavailable=True,empty_confirmed=False)


def _remember_card(cards, key, card):
    """Keep richer metadata for the same title when HTML and JSON overlap."""
    previous = cards.get(key)
    if previous is None:
        cards[key] = card
        return
    updates = {}
    for field in fields(Card):
        old, new = getattr(previous, field.name), getattr(card, field.name)
        if field.name == 'access_evidence':
            combined = merge_evidence(old, new)
            if combined != old:
                updates[field.name] = combined
            continue
        if new and (not old or field.name == 'plot' and len(new) > len(old)):
            updates[field.name] = new
    if updates:
        cards[key] = replace(previous, **updates)


class PublicCatalogError(RuntimeError):
    pass

class PublicSigninRequired(PublicCatalogError):
    """The provider returned an actual password/verification form."""
    pass


def safe_url(value, host, base_url=ORIGIN):
    if not isinstance(value, str) or not value or '\\' in value or any(
            ord(character) <= 32 or ord(character) == 127 for character in value):
        return ''
    try:
        url = urljoin(base_url, value)
        parsed = urlsplit(url)
        hosts = {host} if isinstance(host, str) else host
        if (parsed.scheme == 'https' and parsed.hostname in hosts and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None):
            return url
    except ValueError:
        return ''
    return ''


def detail_id(url):
    match = re.search(r'/(?:detail|dp)/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})(?:/|$)', urlsplit(url).path)
    return match.group(1) if match else None


def _emit(observer, event):
    if observer is not None:
        try:
            observer(event)
        except Exception as error:
            # A failed diagnostic sink must be visible without exposing its arguments.
            logging.getLogger(__name__).error('catalog_diagnostic_write_failed %s', type(error).__name__)


from .display_text import clean

def _text(value):
    if isinstance(value, str):
        return ' '.join(clean(value).split())
    if isinstance(value, dict):
        for key in ('text', 'value', 'title', 'label'):
            if isinstance(value.get(key), str):
                return ' '.join(clean(value[key]).split())
    return ''


def _link(value, page_url):
    if isinstance(value, dict):
        if value.get('disabled') is True or value.get('isEnabled') is False:
            return ''
        value = value.get('href') or value.get('url')
    return safe_url(value, STOREFRONT_HOSTS, page_url)


def _continuation_link(value, page_url):
    # Some responses call an opaque pagination token "nextPage". Only an explicit
    # href/url object or a recognizable URL reference is safe to request.
    if isinstance(value, str) and not (value.startswith(('https://', '/', '?', './', '../'))
                                       or '/' in value or '?' in value):
        return ''
    return _link(value, page_url)


def _image(value, depth=0, *, original=True):
    return choose_image(value,lambda address:safe_url(address,IMAGE_HOSTS),depth,original)


def _shot_map(node):
    found = {}
    for key in ('images', 'image', 'artmeta', 'artwork'):
        blob = node.get(key)
        if isinstance(blob, str):
            url = safe_url(blob, IMAGE_HOSTS)
            if url:
                found.setdefault('image', url)
            continue
        if not isinstance(blob, dict):
            continue
        for name, value in blob.items():
            url = _image(value,original=False)
            if url:
                found[str(name).casefold()] = url
    for name in ('landscape', 'thumbnail', 'thumb', 'packshot', 'wide', 'poster', 'fanart', 'heroshot'):
        if name not in found:
            url = _image(node.get(name),original=False)
            if url:
                found[name] = url
    return found


def _first_shot(shots, names):
    if names and set(names).issubset({'heroshot','hero','fanart','background','backdrop'}):
        from .image_quality import declared_resolution
        candidates=[shots[name] for name in names if shots.get(name)]
        return source_image(max(candidates,key=declared_resolution)) if candidates else ''
    for name in names:
        if shots.get(name):
            return source_image(shots[name])
    return ''


def _year_value(node):
    for key in ('releaseYear', 'year'):
        value = node.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 1888 <= value <= 2100:
            return str(value)
        if isinstance(value, str):
            match = re.match(r'\s*(\d{4})\b', value)
            if match and 1888 <= int(match.group(1)) <= 2100:
                return match.group(1)
    for key in ('releaseDate', 'premiered', 'aired'):
        value = node.get(key)
        if isinstance(value, str):
            match = re.match(r'\s*(\d{4})\b', value)
            if match and 1888 <= int(match.group(1)) <= 2100:
                return match.group(1)
    return ''


def _duration_value(node):
    if isinstance(node.get('runtimeMillis'), (int, float)) and not isinstance(node.get('runtimeMillis'), bool):
        if node['runtimeMillis'] > 0:
            return str(int(node['runtimeMillis'] // 1000))
    if isinstance(node.get('runtimeSeconds'), (int, float)) and not isinstance(node.get('runtimeSeconds'), bool):
        if node['runtimeSeconds'] > 0:
            return str(int(node['runtimeSeconds']))
    for key in ('duration', 'runtime'):
        value = node.get(key)
        if isinstance(value, dict):
            if isinstance(value.get('valueMillis'), (int, float)) and value['valueMillis'] > 0:
                return str(int(value['valueMillis'] // 1000))
            if isinstance(value.get('seconds'), (int, float)) and value['seconds'] > 0:
                return str(int(value['seconds']))
            value = value.get('value') or value.get('text') or value.get('displayText')
        if isinstance(value, bool) or value in (None, ''):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return str(int(value))
        if isinstance(value, str) and value.strip():
            return ' '.join(clean(value).split())
    return ''


def _rating_value(node):
    for key in ('ratingBadge', 'maturityRating', 'contentRating', 'rating'):
        value = node.get(key)
        if isinstance(value, dict):
            text = _text(value.get('displayText') or value.get('text') or value.get('value') or value.get('label'))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            text = '%g' % value
        else:
            text = _text(value)
        if text:
            return text
    return ''


def _genre_value(node):
    raw = node.get('genres')
    if raw in (None, ''):
        raw = node.get('genre')
    names = []
    if isinstance(raw, str):
        names = [part.strip() for part in raw.split(',') if part.strip()]
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                names.append(entry.strip())
            elif isinstance(entry, dict):
                text = _text(entry.get('text') or entry.get('name') or entry.get('label'))
                if text:
                    names.append(text)
    return ', '.join(names[:3])


def _media_value(node):
    aliases = {
        'movie': 'movie', 'film': 'movie', 'movies': 'movie',
        'tv show': 'tvshow', 'tvshow': 'tvshow', 'show': 'tvshow', 'series': 'tvshow',
        'tv series': 'tvshow', 'tv': 'tvshow',
        'tvshow': 'tvshow', 'tvseries': 'tvshow',
        'season': 'season', 'tv season': 'season', 'tvseason': 'season',
        'episode': 'episode', 'tv episode': 'episode', 'tvepisode': 'episode',
        'event': 'event', 'live': 'live', 'live event': 'event',
    }
    for key in ('titleType', 'entityType', 'contentType', 'catalogType', 'mediaType', 'mediatype'):
        value = _text(node.get(key)).casefold().replace('_', ' ').replace('-', ' ').strip()
        if value in aliases:
            return aliases[value]
    if _count_value(node.get('episodeNumber')):
        return 'episode'
    if _count_value(node.get('seasonNumber')):
        return 'season'
    if _seasons_value(node):
        return 'tvshow'
    return ''


def _seasons_value(node):
    for key in ('seasonCount', 'numberOfSeasons', 'totalSeasons'):
        found = _count_value(node.get(key))
        if found:
            return found
    return ''


def _count_value(value):
    if isinstance(value, bool) or value in (None, ''):
        return ''
    if isinstance(value, (int, float)) and value > 0:
        return str(int(value))
    if isinstance(value, str) and value.strip().isdigit() and int(value.strip()) > 0:
        return str(int(value.strip()))
    return ''


def _plot_value(node):
    found = []
    for key in ('synopsis', 'plot', 'synopsisText', 'shortSynopsis', 'description'):
        text = _text(node.get(key))
        if text:
            found.append(text)
    return max(found, key=len) if found else ''


def _json_card(node, page_url):
    detail = ''
    for key in ('detailUrl', 'detailURL', 'detailPageUrl', 'href', 'url', 'link', 'titleLink'):
        candidate = _link(node.get(key), page_url)
        if candidate and detail_id(candidate):
            detail = candidate
            break
    if not detail and isinstance(node.get('link'), dict):
        candidate = _link(node.get('link'), page_url)
        if candidate and detail_id(candidate):
            detail = candidate
    if not detail:
        return None
    title = ''
    for key in ('title', 'displayTitle', 'heading', 'name', 'label'):
        title = _text(node.get(key))
        if title:
            break
    shots = _shot_map(node)
    landscape = _first_shot(shots, ('landscape', 'packshot', 'thumbnail', 'thumb', 'wide'))
    artwork = landscape or _first_shot(shots, ('image', 'src'))
    poster = _first_shot(shots, ('titleshot', 'covershot', 'poster', 'cover', 'boxart', 'tall'))
    fanart = _first_shot(shots, ('heroshot', 'hero', 'fanart', 'background', 'backdrop'))
    if not artwork:
        for key in ('image', 'images', 'imageUrl', 'imageURL', 'imageUrls', 'imageURLs',
                    'packshot', 'poster', 'thumbnail', 'thumb', 'cover', 'artwork', 'artmeta'):
            artwork = _image(node.get(key))
            if artwork:
                break
    if not poster:
        poster = artwork
    # A regional card with missing/unrecognized artwork still has a usable
    # provider title/link. Do not silently erase those real titles from a list.
    entity=bool(node.get('titleID') or node.get('titleId') or _media_value(node))
    if title and (artwork or entity):
        return Card(title, detail, artwork, fanart_url=fanart, poster_url=poster or artwork,
                    plot=_plot_value(node), year=_year_value(node), duration=_duration_value(node),
                    rating=_rating_value(node), genres=_genre_value(node), mediatype=_media_value(node),
                    season=_count_value(node.get('seasonNumber') if node.get('seasonNumber') not in (None, '')
                                        else node.get('season')),
                    episode=_count_value(node.get('episodeNumber') if node.get('episodeNumber') not in (None, '')
                                         else node.get('episode')),
                    seasons=_seasons_value(node), landscape_url=landscape,
                    access_evidence=access_evidence(node), **__import__(__package__+'.watch_home',fromlist=['progress_info']).progress_info(node))
    return None


def _direct_next_links(node, page_url):
    """Only explicit links: a token alone is not an endpoint specification."""
    candidates = []
    if not isinstance(node, dict):
        return candidates
    if node.get('hasMoreItems') is False:return candidates
    if node.get('disabled') is True or node.get('isEnabled') is False:
        return candidates
    for key in ('nextPage', 'nextPageUrl', 'nextPageURL', 'paginationTargetUrl'):
        found = _continuation_link(node.get(key), page_url)
        if found:
            candidates.append(found)
    for key in ('links', '_links'):
        links = node.get(key)
        if isinstance(links, dict):
            found = _continuation_link(links.get('next'), page_url)
            if found:
                candidates.append(found)
    pagination = node.get('pagination')
    if isinstance(pagination, dict):
        for key in ('nextPage', 'nextPageUrl', 'nextPageURL', 'next', 'paginationTargetUrl'):
            found = _continuation_link(pagination.get(key), page_url)
            if found:
                candidates.append(found)
    for owner in (node, pagination):
        if not isinstance(owner, dict):
            continue
        paginator = owner.get('paginator')
        if isinstance(paginator, dict):
            paginator = paginator.get('items') or paginator.get('pages') or [paginator]
        if isinstance(paginator, list):
            for item in paginator:
                if not isinstance(item, dict):
                    continue
                relation = item.get('type') or item.get('rel') or item.get('direction')
                if isinstance(relation, str) and relation.lower() in ('next', 'nextpage', 'next-page'):
                    found = _link(item, page_url)
                    if found:
                        candidates.append(found)
    return candidates


def _json_content(document, page_url, *, page_continuation=True):
    cards, all_links, page_links = {}, set(), set()

    def page_scope(node, depth=0):
        if not isinstance(node, dict) or depth > 4:
            return
        page_links.update(_direct_next_links(node, page_url))
        # Do not descend into lists of collections/widgets: those page independently.
        for key in ('result', 'results', 'data', 'content', 'viewOutput', 'page'):
            child = node.get(key)
            if isinstance(child, dict):
                page_scope(child, depth + 1)

    if page_continuation:
        page_scope(document)
    stack = [(document, 0)]
    nodes, limited, card_candidates, token_only = 0, False, 0, 0
    raw_records=0;unreadable=[];image_hosts={}
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            limited = True
            break
        if depth > MAX_JSON_DEPTH:
            limited = True
            continue
        if isinstance(node, dict):
            all_links.update(_direct_next_links(node, page_url))
            for key in ('url','src','imageUrl','imageURL'):
                value=node.get(key)
                if not isinstance(value,str) or not value.startswith('https://'):continue
                try:
                    image_parts=urlsplit(value)
                    if not re.search(r'\.(?:jpe?g|png|webp|avif)$',image_parts.path,re.I):continue
                    host=image_parts.hostname
                    if not host or not re.fullmatch(r'[a-z0-9.-]{1,253}',host):continue
                    reason=url_rejection(value,IMAGE_HOSTS)
                    identity=(host,reason)
                    if identity in image_hosts or len(image_hosts)<16:image_hosts[identity]=image_hosts.get(identity,0)+1
                except ValueError:continue
            token_only += sum(1 for key in ('nextPage', 'paginationToken', 'paginationServiceToken', 'nextToken')
                              if isinstance(node.get(key), str) and node[key]
                              and not _continuation_link(node[key], page_url))
            card = _json_card(node, page_url)
            if node.get('titleID') or node.get('titleId') or node.get('entityType'):
                raw_records+=1
                if not card and len(unreadable)<4:
                    known={'title','titleID','titleId','entityType','images','link','detailUrl','detailURL','displayTitle','progress','playbackActions'}
                    unreadable.append({'known_fields':sorted(set(node)&known),'other_field_count':len(set(node)-known),
                        'title_present':any(bool(_text(node.get(k))) for k in ('title','displayTitle','heading','name','label')),
                        'trusted_detail_present':any(bool(detail_id(u)) for k in ('detailUrl','detailURL','detailPageUrl','href','url','link','titleLink') for u in [_link(node.get(k),page_url)] if u),
                        'recognized_image_present':bool(_shot_map(node))})
            if card:
                card_candidates += 1
                _remember_card(cards, detail_id(card.detail_url), card)
            stack.extend((child, depth + 1) for child in reversed(list(node.values()))
                         if isinstance(child, (dict, list)))
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in reversed(node) if isinstance(child, (dict, list)))
    return cards, page_links, {'json_nodes': nodes, 'json_traversal_limited': limited,
                              'json_card_candidates': card_candidates,
                              'raw_title_records':raw_records,'unreadable_record_inventory':unreadable,
                              'cards_without_artwork':sum(not card.artwork_url for card in cards.values()),
                              'artwork_policy':ARTWORK_POLICY,'artificial_artwork_width_cap':False,
                              'image_host_inventory':[{'host':host,'policy_reason':reason,'references':count}
                                  for (host,reason),count in image_hosts.items()],
                              'ignored_nested_continuations': len(all_links - page_links),
                              'unsupported_token_continuations': token_only,
                              'json_root_type': type(document).__name__, 'protocol_structure': _protocol_shape(document)}


class StorefrontParser(HTMLParser):
    def __init__(self, page_url):
        super().__init__()
        self.page_url = page_url
        self.cards = {}
        self.depth = 0
        self.detail = ''
        self.artwork = ''
        self.label = ''
        self.anchor = False
        self.text = []
        self.scopes = []
        self.next_links = set()
        self.ignored_next_links = set()
        self.json_scripts = []
        self.script = None
        self.script_text = []
        self.article_candidates = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        scope = tag == 'article' or any(re.search(r'carousel|collection|widget|rail|shelf|(?:^|[-_ ])card(?:$|[-_ ])',
                                                attrs.get(key) or '', re.I)
                                      for key in ('data-testid', 'id', 'class'))
        if tag in ('link', 'a') and 'next' in (attrs.get('rel') or '').lower().split():
            found = _link(attrs.get('href'), self.page_url)
            if found:
                (self.next_links if attrs.get('data-testid')=='page-pagination-button' else
                 self.ignored_next_links if scope or any(value for _, value in self.scopes)
                 else self.next_links).add(found)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param',
                       'source', 'track', 'wbr'}:
            self.scopes.append((tag, scope))
        if tag == 'script' and (attrs.get('type') or '').split(';')[0].lower() in {
                'application/json', 'application/ld+json', 'text/template'}:
            self.script = True
            self.script_text = []
        if tag == 'article':
            if self.depth:
                self.depth += 1
            elif attrs.get('data-testid') in {'card', 'super-carousel-card', 'top-hero-card'}:
                self.depth = 1
                self.article_candidates += 1
                self.detail = self.artwork = self.label = ''
        if not self.depth:
            return

        if tag == 'a' and not self.detail:
            url = safe_url(attrs.get('href') or '', STOREFRONT_HOSTS, self.page_url)
            if url and detail_id(url):
                self.detail = url
                self.anchor = True
                self.text = []
                self.label = attrs.get('aria-label') or self.label
        elif tag == 'img' and not self.artwork:
            self.artwork = html_image(attrs,lambda value:safe_url(value,IMAGE_HOSTS,self.page_url))
            if not self.label and self.artwork:
                self.label = attrs.get('alt') or ''

    def handle_data(self, data):
        if self.script:
            self.script_text.append(data)
        if self.anchor:
            self.text.append(data)

    def handle_endtag(self, tag):
        for index in range(len(self.scopes) - 1, -1, -1):
            if self.scopes[index][0] == tag:
                del self.scopes[index:]
                break
        if tag == 'script' and self.script:
            self.json_scripts.append(''.join(self.script_text))
            self.script = None
            self.script_text = []
        if tag == 'a' and self.anchor:
            self.label = self.label or ''.join(self.text)
            self.anchor = False
        elif tag == 'article' and self.depth:
            self.depth -= 1
            if self.depth:
                return
            self.anchor = False
            title = ' '.join(self.label.split())
            if title and self.detail and self.artwork:
                # Deduplicate tracking variants. Keep the exact storefront link and image URL.
                _remember_card(self.cards, detail_id(self.detail), Card(title, self.detail, self.artwork))


def parse_cards(html, *, page_url=ORIGIN):
    parser = StorefrontParser(page_url)
    parser.feed(html)
    parser.close()
    for script in parser.json_scripts:
        try:
            document = json.loads(script)
        except (ValueError, RecursionError):
            continue
        cards, _, _ = _json_content(document, page_url, page_continuation=False)
        for key, card in cards.items():
            _remember_card(parser.cards, key, card)
    return list(parser.cards.values())


class CatalogCookies(HTTPCookieProcessor):
    """Observe what the canonical jar actually attaches; never log cookie data."""
    def __init__(self,jar,observer):
        super().__init__(jar);self.observer=observer

    def http_request(self,request):
        result=super().http_request(request)
        names=[part.partition('=')[0].strip() for part in (result.get_header('Cookie') or '').split(';') if '=' in part]
        _emit(self.observer,{'event':'catalog_cookie_scope','stage':getattr(request,'_prime_stage','catalog_cookie_scope'),
            'request_id':getattr(request,'_prime_request_id',None),'address':route(request.full_url),
            'cookie_count_attached':len(names),'authentication_cookies_attached':sum(name.startswith('at-') for name in names),
            'scope_policy':'canonical_cookiejar','result':'attached' if names else 'no_matching_cookies'})
        return result

    https_request=http_request


class StorefrontRedirect(HTTPRedirectHandler):
    # urllib on supported older Kodi Python does not register 308 itself.
    http_error_308=HTTPRedirectHandler.http_error_302
    def __init__(self, observer=None):
        self.observer = observer

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urljoin(req.full_url, newurl)
        is_sso = storefront_sso(req.full_url,newurl,code,req.get_method(),req.data is not None)
        is_relay=storefront_relay(req.full_url,newurl,code,req.get_method(),req.data is not None)
        allowed = bool(https_host(newurl,PRIME_WEB_HOSTS)) or is_sso or is_relay
        if code in (307,308) and req.data is not None and urlsplit(req.full_url).hostname!=urlsplit(newurl).hostname:
            allowed=False
        _emit(self.observer, {'event': 'catalog_redirect', 'stage':'storefront_redirect', 'status': code,
                              'request_id':getattr(req,'_prime_request_id',None),
                              'from': route(req.full_url), 'to': route(newurl), 'allowed': allowed,
                              'policy_revision':POLICY_REVISION,'regional_sso':is_sso,'regional_relay':is_relay,
                              'method':req.get_method(),'result':'allowed' if allowed else 'blocked'})
        if not allowed:
            raise PublicCatalogError('Prime Video redirected outside its storefront. The session may need sign-in again.')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            strip_redirect_headers(redirected)
            redirected._prime_request_id=getattr(req,'_prime_request_id',None)
            redirected._prime_stage=getattr(req,'_prime_stage','storefront_redirect')
        return redirected


def _next_url(candidates, current_url):
    current = urlsplit(current_url)._replace(fragment='')
    links = sorted(link for link in candidates if urlsplit(link)._replace(fragment='') != current)
    return (links[0] if len(links) == 1 else None), len(links)


def _provider_empty(document):
    """Only explicit native empty results qualify; unknown markup stays an error."""
    if not isinstance(document,dict):return False
    if document.get('hasFailed') is True or any(document.get(k) for k in ('error','errors','errorCode','pagination')):return False
    if document.get('hasMoreItems') is False and document.get('entities')==[]:return True
    body=((document.get('init') or {}).get('preparations') or {}).get('body') or document
    if not isinstance(body,dict) or body.get('hasFailed') is not False:return False
    if any(body.get(k) for k in ('error','errors','errorCode','pagination')):return False
    containers=body.get('containers')
    if not isinstance(containers,list):return False
    if not containers:return (body.get('swiftPageParameters') or {}).get('pageType') in (
        'browse','genre','home','merch','subscription','channel','movie','tv')
    return all(isinstance(c,dict) and c.get('containerType') in ('Grid','StandardCarousel')
        and c.get('entities')==[] and not c.get('paginationServiceToken') and not c.get('seeMore')
        for c in containers)

def _read_page(data, content_type, page_url):
    empty_confirmed=False
    if content_type == 'application/json' or content_type.endswith('+json'):
        document = json.loads(data)
        from .http_transport import server_errors
        empty_confirmed=_provider_empty(document)
        cards, links, details = _json_content(document, page_url)
        details['format'] = 'json'
        details['server_error_codes']=server_errors(document)
        details['provider_error']=isinstance(document,dict) and (document.get('hasFailed') is True or
            any(bool(document.get(k)) for k in ('error','errors','errorCode')))
    elif content_type in ('text/html', 'application/xhtml+xml'):
        parser = StorefrontParser(page_url)
        parser.feed(data)
        parser.close()
        cards, links = parser.cards, parser.next_links
        details = {'format': 'html', 'html_card_candidates': parser.article_candidates,
                   'embedded_json_documents': len(parser.json_scripts), 'embedded_json_failures': 0,
                   'ignored_nested_continuations': len(parser.ignored_next_links),
                   'unsupported_token_continuations': 0}
        for script in parser.json_scripts:
            try:
                document = json.loads(script)
            except (ValueError, RecursionError):
                details['embedded_json_failures'] += 1
                continue
            empty_confirmed=empty_confirmed or _provider_empty(document)
            found, _, structure = _json_content(document, page_url, page_continuation=False)
            for key, card in found.items():
                _remember_card(cards, key, card)
            details['ignored_nested_continuations'] += structure['ignored_nested_continuations']
            details['unsupported_token_continuations'] += structure['unsupported_token_continuations']
            details['json_traversal_limited'] = details.get('json_traversal_limited', False) or structure['json_traversal_limited']
            inventory=details.setdefault('unreadable_record_inventory',[])
            inventory.extend(structure['unreadable_record_inventory'][:max(0,4-len(inventory))])
            image_inventory=details.setdefault('image_host_inventory',[])
            image_inventory.extend(structure['image_host_inventory'][:max(0,16-len(image_inventory))])
            shapes = details.setdefault('embedded_protocol_structures', [])
            if len(shapes) < 5:
                shapes.append(structure['protocol_structure'])
        details['sign_in_form_present'] = bool(re.search(r'<input\b[^>]*\btype\s*=\s*[\"\']password[\"\']', data, re.I))
        details['verification_form_present'] = bool(re.search(r'<input\b[^>]*\b(?:name|id)\s*=\s*[\"\'][^\"\']*(?:otp|verificationCode)', data, re.I))
    else:
        raise PublicCatalogError('Prime Video returned an unsupported catalog content type.')
    next_url, count = _next_url(links, page_url)
    if details.get('provider_error'):empty_confirmed=False
    empty_confirmed=(empty_confirmed and not cards and not next_url
        and not details.get('sign_in_form_present') and not details.get('verification_form_present'))
    details.update(cards=len(cards), empty_confirmed=empty_confirmed, descriptions_ready=sum(bool(card.plot) for card in cards.values()),
                   artwork_policy=ARTWORK_POLICY,artificial_artwork_width_cap=False,
                   landscape_variants=sum(bool(card.landscape_url) for card in cards.values()),
                   prime_confirmed=sum(prime_included(card.access_evidence) for card in cards.values()),
                   access_unknown=sum(not card.access_evidence for card in cards.values()),
                   descriptions_missing=sum(not card.plot for card in cards.values()),
                   continuation_candidates=count, continuation_ambiguous=count > 1,
                   unknown_structure=not cards and not empty_confirmed, next_address=route(next_url) if next_url else None)
    return CatalogPage(list(cards.values()), next_url,empty_confirmed=empty_confirmed), details


def fetch_page(page, opener=None, continuation=None, observer=None, _library_depth=0, _library_seen=()):
    """Fetch one server page; continuation must be an actual link from its predecessor."""
    started = perf_counter()
    parent_observer=observer;request_id=token_hex(8)
    observer=lambda value:_emit(parent_observer,dict({'request_id':request_id},**value))
    address, stage = '', 'validate_request'
    try:
        if page not in PAGES:
            raise PublicCatalogError('Unknown Prime Video catalog section.')
        from . import collection_paging
        collection=isinstance(continuation,dict)
        address,request_data,extra_headers=collection_paging.request_arguments(continuation) if collection else (
            safe_url(continuation, STOREFRONT_HOSTS) if continuation is not None else ORIGIN + PAGES[page],None,{})
        if not address:
            raise PublicCatalogError('The catalog continuation is outside the trusted storefront.')
        _emit(observer, {'event': 'catalog_request_started', 'stage': 'fetch', 'page': page,
                         'continuation': continuation is not None, 'address': route(address), 'method': 'POST' if collection else 'GET',
                         'collection_request':collection})
        request_headers={'Accept':'text/html, application/json;q=0.9',**extra_headers}
        if page in ('watching','watchlist'):request_headers.update({'Cache-Control':'no-cache, no-store','Pragma':'no-cache'})
        request = Request(address, data=request_data, headers=request_headers)
        request._prime_request_id=request_id;request._prime_stage='catalog_fetch'
        opener = opener if opener is not None else build_opener(StorefrontRedirect(observer))
        stage = 'fetch'
        with opener.open(request, timeout=15) as response:
            final_url = response.geturl()
            status = response.getcode()
            content_type = response.headers.get_content_type()
            _emit(observer, {'event': 'catalog_response_received', 'stage': stage, 'status': status,
                             'address': route(final_url), 'content_type': content_type, 'fresh_requested':page in ('watching','watchlist'),
                             'cache_age':response.headers.get('Age'),'cache_control':response.headers.get('Cache-Control'),
                             'server_date':response.headers.get('Date'),
                             'server_request_id': response.headers.get('x-amzn-requestid') or response.headers.get('x-amz-rid'),
                             'elapsed_ms': round((perf_counter() - started) * 1000)})
            if not https_host(final_url,PRIME_WEB_HOSTS):
                raise PublicCatalogError('Prime Video did not return to its storefront.')
            stage = 'read_response'
            body_started=perf_counter()
            raw = response.read(MAX_PAGE_BYTES + 1)
            body_read_ms=round((perf_counter()-body_started)*1000)
            if len(raw) > MAX_PAGE_BYTES:
                raise PublicCatalogError('Prime Video returned more storefront data than expected.')
            wire_bytes=len(raw)
            decompress_started=perf_counter()
            if response.headers.get('Content-Encoding','').lower()=='gzip':
                import gzip
                from io import BytesIO
                raw=gzip.GzipFile(fileobj=BytesIO(raw)).read(MAX_PAGE_BYTES+1)
                if len(raw)>MAX_PAGE_BYTES:raise PublicCatalogError('Decoded storefront data exceeds its size limit.')
            decompress_ms=round((perf_counter()-decompress_started)*1000)
            decode_started=perf_counter()
            data = raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
            _emit(observer,{'event':'catalog_body_read','stage':stage,'status':status,
                'address':route(final_url),'bytes':len(raw),'wire_bytes':wire_bytes,'body_read_ms':body_read_ms,
                'decompress_ms':decompress_ms,
                'decode_ms':round((perf_counter()-decode_started)*1000),
                'content_encoding':response.headers.get('Content-Encoding','identity'),
                'elapsed_ms':round((perf_counter()-started)*1000),'result':'success'})
        stage = 'parse_response'
        if page in ('movies','tv') and continuation is not None and storefront_path(final_url).startswith(('/genre/','/kids')):
            from .genre_catalog import section_link
            scoped=section_link(data,final_url,page,observer=lambda value:_emit(observer,value))
            if scoped and scoped!=final_url and _library_depth<3:
                _emit(observer,{'event':'genre_section_followed','stage':'genre_scope','section':page,
                    'address':route(scoped),'result':'success'})
                return fetch_page(page,opener=opener,continuation=scoped,observer=observer,_library_depth=_library_depth+1)
            _emit(observer,{'event':'genre_section_link_unavailable','stage':'genre_scope','section':page,
                'fallback':'typed_prime_filtered_genre_page','result':'unavailable'})
        if page == 'watching' and collection:
            if continuation.get('scope')!='watching':
                raise PublicCatalogError('The In Progress continuation has no confirmed watch-history scope.')
            result, details = _read_page(data,content_type,final_url)
            next_request=collection_paging.advance(continuation,data,content_type,observer)
            if not result.cards and not result.empty_confirmed and next_request is None:
                _emit(observer,dict(details,event='progress_continuation_unreadable',stage=stage,result='failure'))
                raise PublicCatalogError('Amazon returned unreadable In Progress records; no empty history was confirmed.')
            result=replace(result,next_request=next_request)
            details.update(history_confirmed=True,progress_continuation=True)
        elif page == 'watching':
            from .home_catalog import read_home
            try:
                result, details = read_home(data, content_type, final_url, observer)
            except Exception:
                _emit(observer, {'event':'home_structure_unrecognized','stage':stage,
                    'status':status,'address':route(final_url),'bytes':len(raw),
                    'protocol_structure':_protocol_shape(json.loads(data)) if content_type.endswith('json') else
                        {'format':'html','json_script_count':data.count('application/json')}})
                raise
            next_request=collection_paging.discover(data,content_type,final_url,observer,scope='watching')
            if next_request:result=replace(result,next_request=next_request,empty_confirmed=False)
            if details.get('native_continuation_pending') and not (result.next_url or next_request):
                _emit(observer,dict(details,event='progress_continuation_missing',stage=stage,result='failure'))
                raise PublicCatalogError('Amazon declared more In Progress data without a usable continuation. Open Diagnostics.')
        elif page in ('purchases','watchlist') and collection:
            if continuation.get('scope')!=page:
                raise PublicCatalogError('The personal continuation has no confirmed collection scope.')
            result,details=_read_page(data,content_type,final_url)
            try:
                next_request=collection_paging.advance(continuation,data,content_type,observer)
            except PublicCatalogError as error:
                # A provider service failure is not usable collection content.
                if details.get('provider_error'):raise
                result=_personal_partial(result,page,observer,status,final_url,error,'collection_parse')
                next_request=None
            if page=='purchases':result=replace(result,cards=[replace(card,access_evidence=merge_evidence(card.access_evidence,'account_library')) for card in result.cards])
            result=replace(result,watchlist_confirmed=page=='watchlist',library_confirmed=page=='purchases',next_request=next_request)
        elif page in ('purchases','watchlist'):
            from .purchases_catalog import read_library,navigation_identity
            result, details, library_link = read_library(data, content_type, final_url,
                                                       selected=continuation is not None,collection=page)
            details.update(collection_confirmed=details.get('collection_scope_confirmed',details.get('library_confirmed',False)),
                watchlist_confirmed=bool(result and result.watchlist_confirmed),
                library_confirmed=bool(result and result.library_confirmed))
            _emit(observer, dict(details, event='library_response_parsed', stage=stage,
                status=status, address=route(final_url), bytes=len(raw),
                elapsed_ms=round((perf_counter() - started) * 1000)))
            if details.get('library_authentication_required'):
                raise PublicSigninRequired('Use Sign out, then sign in again to reconnect your Amazon session.')
            if library_link and _library_depth < 3:
                target=navigation_identity(library_link);current=navigation_identity(final_url)
                if target==current or target in _library_seen:
                    _emit(observer,{'event':'personal_navigation_cycle_blocked','stage':'library_discovery',
                        'collection':page,'address':route(library_link),'result':'failure'})
                    raise PublicCatalogError('Amazon repeated the selected personal collection page. Open Diagnostics.')
                _emit(observer, {'event': 'library_navigation_followed', 'stage': 'library_discovery',
                    'from': route(final_url), 'to': route(library_link), 'hop': _library_depth + 1,
                    'result': 'success'})
                return fetch_page(page, opener=opener, continuation=library_link, observer=observer,
                                  _library_depth=_library_depth + 1,_library_seen=_library_seen+(current,))
            if result is None:
                raise PublicCatalogError('Amazon did not expose a recognized %s collection. Open Diagnostics for the report.'%
                    ('Watchlist' if page=='watchlist' else 'Purchases & Rentals'))
            try:
                next_request=collection_paging.discover(data,content_type,final_url,observer,scope=page)
                if details.get('native_continuation_pending') and not (result.next_url or next_request):
                    raise PublicCatalogError('Amazon declared more personal collection data without a usable continuation. Open Diagnostics.')
            except PublicCatalogError as error:
                # A pagination layout error must not erase an already confirmed,
                # readable first page. Keep the incomplete state explicit.
                result=_personal_partial(result,page,observer,status,final_url,error,'collection_discovery')
                details.update(pagination_unavailable=True,collection_state='partial')
                next_request=None
            if next_request:result=replace(result,next_request=next_request,empty_confirmed=False)
        else:
            result, details = _read_page(data, content_type, final_url)
            next_request=(collection_paging.advance(continuation,data,content_type,lambda value:_emit(observer,value)) if collection
                else collection_paging.discover(data,content_type,final_url,lambda value:_emit(observer,value)))
            if next_request:result=replace(result,next_request=next_request)
            details['collection_continuation']=bool(next_request)
        if details.get('provider_error'):
            _emit(observer,dict(details,event='catalog_provider_error',stage=stage,status=status,address=route(final_url),result='failure'))
            raise PublicCatalogError('Amazon returned a catalog service error. Open Diagnostics for its code and structure.')
        _emit(observer, dict(details, event='catalog_page_parsed', stage=stage, bytes=len(raw),
                             status=status, address=route(final_url),
                             elapsed_ms=round((perf_counter() - started) * 1000)))
        if details.get('sign_in_form_present') or details.get('verification_form_present'):
            raise PublicSigninRequired('Use Sign out, then sign in again to reconnect your Amazon session.')
        if page in ('purchases','watchlist') and not result.cards and not result.next_url and not result.next_request and not result.empty_confirmed:
            _emit(observer,{'event':'personal_collection_unknown','stage':'parse_response','collection':page,
                'status':status,'address':route(final_url),'empty_confirmed':False,'result':'failure'})
            raise PublicCatalogError('The personal collection could not be read; an empty collection was not confirmed. Open Diagnostics.')
        if not result.cards and result.next_url is None and result.next_request is None and not result.library_confirmed and not result.watchlist_confirmed and not result.empty_confirmed and not collection:
            raise PublicCatalogError('No title cards or page continuation were found in the catalog response.')
        _emit(observer, {'event': 'catalog_request_completed', 'stage': 'complete', 'result': 'partial' if result.pagination_unavailable else 'success',
                         'pagination_unavailable':result.pagination_unavailable,
                         'cards': len(result.cards), 'has_next': bool(result.next_url or result.next_request),'empty_confirmed':result.empty_confirmed,
                         'elapsed_ms': round((perf_counter() - started) * 1000)})
        return replace(result,entry_url=final_url)
    except Exception as error:
        details = {'event': 'catalog_request_failed', 'stage': stage, 'result': 'failure',
                   'address': route(address), 'exception': exception_details(error),
                   'elapsed_ms': round((perf_counter() - started) * 1000)}
        if isinstance(error, HTTPError):
            details.update(status=error.code, response_address=route(error.geturl()),
                           content_type=error.headers.get_content_type() if error.headers else None)
            details['server_request_id'] = (error.headers.get('x-amzn-requestid') or error.headers.get('x-amz-rid')) if error.headers else None
            try:
                body = error.read(MAX_PAGE_BYTES + 1)
                details['response_bytes'] = len(body)
                if len(body)<=MAX_PAGE_BYTES and error.headers.get('Content-Encoding','').lower()=='gzip':
                    import gzip
                    from io import BytesIO
                    body=gzip.GzipFile(fileobj=BytesIO(body)).read(MAX_PAGE_BYTES+1)
                if len(body) <= MAX_PAGE_BYTES and details['content_type'] == 'application/json':
                    from .http_transport import server_errors
                    document=json.loads(body)
                    details['protocol_structure'] = _protocol_shape(document)
                    details['server_error_codes']=server_errors(document)
            except Exception as reading_error:
                details['error_response_read_failure'] = exception_details(reading_error)
            error.close()
        _emit(observer, details)
        raise


def fetch_cards(page, *, opener=None):
    return fetch_page(page, opener=opener).cards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('page', choices=PAGES, default='movies', nargs='?')
    parser.add_argument('--limit', type=int, default=5)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be zero or greater')
    start = perf_counter()
    cards = fetch_cards(args.page)
    print(json.dumps({'page': args.page, 'cards_found': len(cards),
                      'seconds': round(perf_counter() - start, 3),
                      'sample': [asdict(card) for card in cards[:args.limit]]}, indent=2))


if __name__ == '__main__':
    main()
