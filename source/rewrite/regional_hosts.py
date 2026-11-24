"""Explicit Amazon website identities and separately scoped API capabilities.

Retail domains: Amazon's Creators API marketplace reference, plus Amazon's
South Africa launch announcement. These establish website ownership, not that
every private Prime Video flow has been exercised in every country.
https://affiliate-program.amazon.com/creatorsapi/docs/en-us/concepts/common-request-headers-and-parameters
https://www.aboutamazon.com/news/retail/amazon-south-africa
Regional account hosts/token regions: Amazon's Login with Amazon documentation.
Native German device API and existing media endpoints retain their prior scope.
Prime Video NA/EU/FE sites and the exact relay aliases below were separately
verified against their live HTTPS301/302 responses on2026-11-24. Public relay
verification establishes navigation/ownership, not a completed account login.
"""
import re
from urllib.parse import urlsplit

POLICY_REVISION='2026-11-24-r2'
AMAZON_DOMAINS=frozenset((
    'amazon.com','amazon.ca','amazon.com.mx','amazon.com.br',
    'amazon.co.uk','amazon.de','amazon.fr','amazon.it','amazon.es',
    'amazon.nl','amazon.com.be','amazon.ie','amazon.pl','amazon.se',
    'amazon.com.tr','amazon.ae','amazon.sa','amazon.eg','amazon.in',
    'amazon.co.jp','amazon.com.au','amazon.sg','amazon.co.za'))
AMAZON_WEB_HOSTS=frozenset(prefix+domain for domain in AMAZON_DOMAINS for prefix in ('','www.'))
AUTH_WEB_HOSTS=AMAZON_WEB_HOSTS|{'eu.account.amazon.com','apac.account.amazon.com'}
PRIME_WEB_HOSTS=frozenset(('www.primevideo.com','primevideo.com',
    'na.primevideo.com','eu.primevideo.com','fe.primevideo.com'))
# Each relay below returned a certificate-verified301/302 to its listed
# destination in the actual provider audit. Relays cannot receive auth forms.
VIDEO_ALIAS_TARGETS={
    'r.na.primevideo.com':'primevideo.com','r.eu.primevideo.com':'primevideo.com',
    'r.fe.primevideo.com':'primevideo.com','amazonvideo.com':'primevideo.com',
    'www.amazonvideo.com':'primevideo.com','av-na.amazon.com':'primevideo.com',
    'av-eu.amazon.com':'primevideo.com','av-fe.amazon.com':'primevideo.com',
    'r.amazon.com':'amazon.com','r.amazon.co.uk':'amazon.co.uk','r.amazon.co.jp':'amazon.co.jp'}
PRIME_REDIRECT_HOSTS=frozenset(host for host,target in VIDEO_ALIAS_TARGETS.items() if target=='primevideo.com')
AMAZON_REDIRECT_HOSTS=frozenset(VIDEO_ALIAS_TARGETS)-PRIME_REDIRECT_HOSTS
CATALOG_NAV_HOSTS=PRIME_WEB_HOSTS|PRIME_REDIRECT_HOSTS
SIGNIN_NAV_HOSTS=AUTH_WEB_HOSTS|CATALOG_NAV_HOSTS|AMAZON_REDIRECT_HOSTS
AUTH_API_DOMAINS=frozenset(('amazon.com','amazon.co.uk','amazon.de','amazon.co.jp'))
AUTH_API_HOSTS=frozenset('api.'+domain for domain in AUTH_API_DOMAINS)|{'atv-ps.amazon.com'}
VIDEO_DOMAINS=AUTH_API_DOMAINS|{'primevideo.com'}
MEDIA_API_HOSTS=frozenset('atv-ps'+suffix+'.'+domain for suffix in ('','-eu','-fe') for domain in VIDEO_DOMAINS)
SECURITY_IMAGE_HOSTS=SIGNIN_NAV_HOSTS|{'images-na.ssl-images-amazon.com','m.media-amazon.com',
    'opfcaptcha-prod.s3.amazonaws.com'}


def url_rejection(value,hosts):
    """Return a public policy reason, never URL credentials or query values."""
    if not isinstance(value,str):return 'invalid_url_type'
    if '\\' in value or any(ord(c)<=32 or ord(c)==127 for c in value):return 'invalid_url_characters'
    try:
        p=urlsplit(value)
        if p.scheme!='https':return 'https_required'
        if p.username is not None or p.password is not None:return 'embedded_credentials'
        if p.port not in (None,443):return 'nonstandard_port'
        if p.hostname not in hosts:return 'unknown_hostname'
    except ValueError:return 'malformed_url'
    return None


def https_host(value,hosts):
    return urlsplit(value).hostname if url_rejection(value,hosts) is None else None


def storefront_sso(source,target,code,method,has_body):
    """Only the existing provider SSO route may leave the catalog website."""
    if not https_host(source,SIGNIN_NAV_HOSTS):return False
    if not https_host(target,AUTH_WEB_HOSTS|AMAZON_REDIRECT_HOSTS):return False
    if urlsplit(target).path!='/ax/sso/redirect':return False
    if has_body and code in (307,308):return False
    return method in ('GET','HEAD') or method=='POST' and code in (301,302,303)


def storefront_relay(source,target,code,method,has_body):
    if not https_host(source,SIGNIN_NAV_HOSTS):return False
    if not https_host(target,PRIME_REDIRECT_HOSTS):return False
    if has_body and code in (307,308):return False
    return method in ('GET','HEAD') or method=='POST' and code in (301,302,303)


def canonical_video_domain(host):
    host=VIDEO_ALIAS_TARGETS.get(host,host)
    if host in PRIME_WEB_HOSTS:return 'primevideo.com'
    return host[4:] if host.startswith('www.') else host


def storefront_path(address):
    """Classify a provider URL without changing its request address.

    Amazon's live localized navigation uses /-/de/, /-/fr/ and /-/ja/.
    Regional and language prefixes are routing context, not the content type.
    """
    path=urlsplit(address).path
    for _ in range(2):
        path=re.sub(r'^/(?:region/[a-z]{2,4}|-/[a-z]{2,3}(?:-[A-Za-z]{2,4})?)(?=/|$)','',path)
    return path or '/'


def region_prefix(address):
    """The existing web pagination protocol uses the provider's region prefix."""
    match=re.search(r'(?:^|/)(region/[a-z]{2,4})(?=/|$)',urlsplit(address).path)
    return '/'+match.group(1) if match else ''


def storefront_transport(hosts):
    return bool(hosts) and set(hosts).issubset(CATALOG_NAV_HOSTS)


def strip_redirect_headers(request):
    for name in ('Cookie','Host','Authorization','Proxy-authorization','Origin','Referer'):
        request.remove_header(name)


def policy_inventory():
    return {'revision':POLICY_REVISION,'country_website_domains':len(AMAZON_DOMAINS),
        'sign_in_hosts':len(SIGNIN_NAV_HOSTS),'security_image_hosts':len(SECURITY_IMAGE_HOSTS),
        'catalog_hosts':len(CATALOG_NAV_HOSTS),'native_auth_api_domains':len(AUTH_API_DOMAINS),
        'prime_web_hosts':sorted(PRIME_WEB_HOSTS),'prime_redirect_hosts':sorted(PRIME_REDIRECT_HOSTS),
        'amazon_redirect_hosts':sorted(AMAZON_REDIRECT_HOSTS),
        'media_api_hosts':len(MEDIA_API_HOSTS),'catalog_sso_path':'/ax/sso/redirect',
        'country_host_wildcards':False,'cookie_scope':'provider_cookiejar'}
