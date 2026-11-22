"""Resolve account service metadata from Amazon's startup response.

Marketplace IDs and hostnames are provider protocol data, not executable login
logic. The settings index is kept compatible with installed account settings.
"""
import re
import secrets
from urllib.parse import urlencode, urlsplit

from .auth_transport import json_request, structure
from .prime_signin import SignInError, AMAZON_HOSTS, PRIME_HOSTS

MARKETS = {
    'ATVPDKIKX0DER': ('amazon.com', 'us', 'en_US', ''),
    'A1F83G8C2ARO7P': ('amazon.co.uk', 'uk', 'en_US', '-eu'),
    'A1PA6795UKMFR9': ('amazon.de', 'de', 'de_DE', '-eu'),
    'A1VC38T7YXB528': ('amazon.co.jp', 'jp', 'ja_JP', '-fe'),
    'A3K6Y4MI8GDYMT': ('amazon.com', 'us', 'en_US', '-eu'),
    'A2MFUE2XK8ZSSY': ('amazon.com', 'us', 'en_US', '-eu'),
    'A15PK738MTQHSO': ('amazon.com', 'us', 'en_US', '-fe'),
    'ART4WZ8MWBX2Y': ('amazon.com', 'us', 'en_US', ''),
}
CHOICES = (None, 'A1PA6795UKMFR9', 'A1F83G8C2ARO7P', 'ATVPDKIKX0DER',
           'A1VC38T7YXB528', 'A3K6Y4MI8GDYMT', 'A2MFUE2XK8ZSSY', 'A15PK738MTQHSO', 'ART4WZ8MWBX2Y')


def _https(value):
    if not isinstance(value, str):
        raise SignInError('Amazon omitted an account website address.')
    p = urlsplit(value)
    if (p.scheme != 'https' or p.hostname not in AMAZON_HOSTS | PRIME_HOSTS
            or p.port not in (None, 443) or p.username or p.password):
        raise SignInError('Amazon returned an unsupported account website.')
    return value.rstrip('/')


def decode_startup(value, serial):
    try:
        territory, customer = value['territoryConfig'], value['customerConfig']
        market = territory['avMarketplace']
        site = _https(territory['defaultVideoWebsite'])
        base = _https(territory['primeSignupBaseUrl'])
        region = customer['homeRegion'].upper()
        suffix = {'NA': '', 'EU': '-eu', 'FE': '-fe'}[region]
        if not isinstance(market, str) or not re.fullmatch('[A-Z0-9]{8,32}', market):
            raise ValueError('Invalid marketplace identifier')
        public_host = urlsplit(site).hostname
        country_defaults = {'www.amazon.de': ('amazon.de','de','de_DE','-eu'),
            'www.amazon.co.uk': ('amazon.co.uk','uk','en_US','-eu'),
            'www.amazon.co.jp': ('amazon.co.jp','jp','ja_JP','-fe')}
        domain, locale, language, _ = MARKETS.get(market,
            country_defaults.get(public_host, ('amazon.com','us','en_US','')))
        preferred = customer.get('locale', {}).get('uxLocale')
        if isinstance(preferred, str) and re.fullmatch('[a-z]{2}_[A-Z]{2}', preferred):
            language = preferred
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise SignInError('Amazon returned an unrecognized account region. The discovery report contains its structure.') from error
    host = urlsplit(site).hostname
    if host in PRIME_HOSTS:
        host = 'primevideo.com'
    if host.startswith('www.'):
        host = host[4:]
    return {'deviceid': serial, 'mid': market, 'sidomain': domain, 'locale': locale, 'lang': language,
            'pv': host == 'primevideo.com', 'baseurl': base, 'atvurl': 'https://atv-ps'+suffix+'.'+host}


def discover_account(selection, *, observer, user_agent=None):
    serial = secrets.token_hex(16)
    observer.secret(serial)
    try:
        selected = int(selection)
        if selected < 0 or selected >= len(CHOICES):
            raise ValueError('Invalid region selection')
        choice = CHOICES[selected]
    except (ValueError, TypeError, IndexError):
        raise SignInError('The account region setting is invalid.') from None
    if choice is not None:
        domain, locale, language, suffix = MARKETS[choice]
        prime = int(selection) >= 5
        host = 'primevideo.com' if prime else domain
        result = {'deviceid': serial, 'mid': choice, 'sidomain': domain, 'locale': locale, 'lang': language,
            'pv': prime, 'baseurl': 'https://www.'+host, 'atvurl': 'https://atv-ps'+suffix+'.'+host}
        observer({'event': 'account_region_resolved', 'stage': 'region', 'source': 'explicit_setting',
            'primevideo_site': prime, 'domain': domain, 'result': 'success'})
        return result
    parameters = {'deviceTypeID': 'A28RQHJKHM2A2W', 'deviceID': serial, 'firmware': '1',
                  'version': '1', 'supportedLocales': 'en_US', 'format': 'json'}
    value = json_request('https://atv-ps.amazon.com/cdp/usage/v3/GetAppStartupConfig?'+urlencode(parameters),
        observer=observer, stage='region discovery', headers={'User-Agent': user_agent} if user_agent else {})
    try:
        result = decode_startup(value, serial)
    except Exception:
        observer({'event': 'account_region_unrecognized', 'stage': 'region', 'structure': structure(value), 'result': 'failure'})
        raise
    observer({'event': 'account_region_resolved', 'stage': 'region', 'source': 'amazon_startup',
        'domain': result['sidomain'], 'language': result['lang'], 'primevideo_site': result['pv'], 'result': 'success'})
    return result
