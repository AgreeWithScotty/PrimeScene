"""Read public Prime Video storefront cards without an account or backend imports."""

import argparse
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
import re
from time import perf_counter
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ORIGIN = 'https://www.primevideo.com'
PAGES = {'home': '/storefront', 'movies': '/movie', 'tv': '/tv'}
STOREFRONT_HOSTS = {'www.primevideo.com', 'primevideo.com', 'na.primevideo.com'}
MAX_PAGE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Card:
    title: str
    detail_url: str
    artwork_url: str


class PublicCatalogError(RuntimeError):
    pass


def safe_url(value, host, base_url=ORIGIN):
    if '\\' in value or any(ord(character) <= 32 or ord(character) == 127 for character in value):
        return ''
    try:
        url = urljoin(base_url, value)
        parsed = urlsplit(url)
        hosts = {host} if isinstance(host, str) else host
        if (parsed.scheme == 'https' and parsed.hostname in hosts and parsed.port in (None, 443)
                and parsed.username is None):
            return url
    except ValueError:
        return ''
    return ''


def detail_id(url):
    match = re.search(r'/detail/([A-Za-z0-9_-]{1,128})(?:/|$)', urlsplit(url).path)
    return match.group(1) if match else None


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

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'article':
            if self.depth:
                self.depth += 1
            elif attrs.get('data-testid') in {'card', 'super-carousel-card', 'top-hero-card'}:
                self.depth = 1
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
            self.artwork = safe_url(attrs.get('src') or '', 'm.media-amazon.com')
            if not self.label and self.artwork:
                self.label = attrs.get('alt') or ''

    def handle_data(self, data):
        if self.anchor:
            self.text.append(data)

    def handle_endtag(self, tag):
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
                self.cards.setdefault(detail_id(self.detail), Card(title, self.detail, self.artwork))


def parse_cards(html, *, page_url=ORIGIN):
    parser = StorefrontParser(page_url)
    parser.feed(html)
    parser.close()
    return list(parser.cards.values())


class StorefrontRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        sso = safe_url(newurl, {'www.amazon.com', 'amazon.com'})
        is_sso = bool(sso) and urlsplit(sso).path == '/ax/sso/redirect'
        if not safe_url(newurl, STOREFRONT_HOSTS) and not is_sso:
            raise PublicCatalogError('Prime Video redirected outside its storefront. The session may need sign-in again.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_cards(page, *, opener=None):
    request = Request(ORIGIN + PAGES[page], headers={'Accept': 'text/html'})
    opener = opener if opener is not None else build_opener(StorefrontRedirect())
    with opener.open(request, timeout=8) as response:
        if response.headers.get_content_type() != 'text/html':
            raise PublicCatalogError('Prime Video did not return a storefront page.')
        data = response.read(MAX_PAGE_BYTES + 1)
        if len(data) > MAX_PAGE_BYTES:
            raise PublicCatalogError('Prime Video returned more storefront data than expected.')
        html = data.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
    cards = parse_cards(html, page_url=response.url)
    if not cards:
        raise PublicCatalogError('No public title cards were found on this page.')
    return cards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('page', choices=PAGES, default='home', nargs='?')
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
