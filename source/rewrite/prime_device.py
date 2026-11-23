"""Account authorization and token exchange for Prime Scene's native login.

OpenID field names and the device profile describe Amazon's existing wire
protocol. The form workflow, transport, validation and account assembly live here.
"""
import base64
from dataclasses import dataclass, field
import hashlib
import json
import secrets
import time
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from .prime_signin import AMAZON_HOSTS, SignInError, SignInHTTPError, trusted_host
    from .prime_diagnostics import route
except ImportError:
    from prime_signin import AMAZON_HOSTS, SignInError, SignInHTTPError, trusted_host
    from prime_diagnostics import route


class NoTokenRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SignInError('Amazon redirected the device authorization request unexpectedly.')


def url64(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


@dataclass
class DeviceAuthorization:
    account: dict
    device_type: str
    headers: dict
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    registered: dict = None
    observer: object = None

    def record(self, event):
        if self.observer:
            self.observer(event)

    def record_result(self, value, status):
        def hide_private(data, private=False):
            if isinstance(data, dict):
                for key, child in data.items():
                    hide_private(child, private or any(word in key.lower() for word in
                                 ('token', 'cookie', 'customer', 'authorization', 'device', 'secret', 'verifier')))
            elif isinstance(data, list):
                for child in data:
                    hide_private(child, private)
            elif private and hasattr(self.observer, 'secret'):
                self.observer.secret(data)
        hide_private(value)
        details = []
        def errors(data):
            if isinstance(data, dict):
                for key, child in data.items():
                    if key.lower() in ('error', 'errorcode', 'error_code', 'error_description', 'message', 'type', 'reason') and isinstance(child, str):
                        details.append({key: child})
                    elif isinstance(child, (dict, list)):
                        errors(child)
            elif isinstance(data, list):
                for child in data:
                    errors(child)
        errors(value)
        self.record({'event': 'device_authorization_result', 'status': status,
                     'top_level_keys': sorted(value) if isinstance(value, dict) else [], 'server_errors': details})

    def __post_init__(self):
        self.account = dict(self.account)
        self.domain = self.account.get('sidomain', '')
        if 'www.' + self.domain not in AMAZON_HOSTS:
            raise SignInError('Amazon returned an unsupported account region.')
        serial = self.account.get('deviceid', '')
        if len(serial) != 32 or not all(c in '0123456789abcdef' for c in serial.lower()):
            raise SignInError('The Smooth Prime device identifier is invalid.')
        self.client = (serial + '#A1MPSLFC7L5AFK').encode('ascii').hex()

    @property
    def landing_url(self):
        return 'https://www.' + self.domain + '/ap/maplanding'

    def entry_url(self):
        params = {
            'openid.ns': 'http://specs.openid.net/auth/2.0',
            'openid.mode': 'checkid_setup',
            'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
            'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
            'openid.assoc_handle': 'amzn_piv_android_v2_' + self.account.get('locale', 'us'),
            'openid.return_to': self.landing_url,
            'openid.ns.oa2': 'http://www.amazon.com/ap/ext/oauth/2',
            'openid.oa2.client_id': 'device:' + self.client,
            'openid.oa2.response_type': 'code',
            'openid.oa2.scope': 'device_auth_access',
            'openid.oa2.code_challenge_method': 'S256',
            'openid.oa2.code_challenge': url64(hashlib.sha256(self.verifier.encode('ascii')).digest()),
            'openid.ns.pape': 'http://specs.openid.net/extensions/pape/1.0',
            'openid.pape.max_auth_age': '0',
            'pageId': 'amzn_dv_ios_blue',
            'accountStatusPolicy': 'P1',
            'language': self.account.get('lang', 'en_US'),
            'disableLoginPrepopulate': '0',
        }
        return 'https://www.' + self.domain + '/ap/signin?' + urlencode(params)

    def device_description(self):
        # Retain the registered playback device identity when updating an account.
        return {'device_serial': self.account['deviceid'], 'device_type': self.device_type,
                'domain': 'DeviceLegacy', 'app_name': 'com.amazon.avod.thirdpartyclient',
                'app_version': '296016847', 'device_model': 'mdarcy/nvidia/SHIELD Android TV',
                'os_version': 'NVIDIA/mdarcy/mdarcy:11/RQ1A.210105.003/7094531_2971.7725:user/release-keys'}

    def complete(self, url, page, cookies):
        parts = urlsplit(url)
        code = parse_qs(parts.query).get('openid.oa2.authorization_code', [])
        if not code:
            return False
        if (trusted_host(url) != 'www.' + self.domain or parts.path != '/ap/maplanding'
                or len(code) != 1 or not code[0]):
            raise SignInError('Amazon returned an invalid device authorization response.')
        payload = {
            'auth_data': {'authorization_code': code[0], 'client_id': self.client,
                          'code_verifier': self.verifier, 'code_algorithm': 'SHA-256',
                          'client_domain': 'DeviceLegacy'},
            'registration_data': self.device_description(),
            'requested_token_type': ['bearer', 'website_cookies'],
            'requested_extensions': ['device_info', 'customer_info'],
            'cookies': {'domain': '.' + self.domain, 'website_cookies': []},
        }
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in ('cookie', 'authorization', 'host')}
        headers.update({'Content-Type': 'application/json', 'Accept': 'application/json',
                        'x-amzn-identity-auth-domain': 'api.' + self.domain,
                        'x-amzn-requestid': secrets.token_hex(16)})
        request = Request('https://api.' + self.domain + '/auth/register',
                          data=json.dumps(payload).encode('utf-8'), headers=headers)
        if hasattr(self.observer, 'secret'):
            for private in (code[0], self.client, self.verifier, self.account['deviceid']):
                self.observer.secret(private)
        started = monotonic()
        self.record({'event': 'device_authorization_started', 'address': route(request.full_url),
                     'device_type': self.device_type, 'pkce_method': 'S256',
                     'payload_sections': sorted(payload), 'authorization_fields': sorted(payload['auth_data']),
                     'registration_fields': sorted(payload['registration_data']),
                     'requested_token_types': payload['requested_token_type']})
        try:
            response = build_opener(NoTokenRedirect()).open(request, timeout=20)
        except HTTPError as error:
            status = error.code
            self.record({'event': 'device_authorization_http_error', 'status': status,
                         'network_ms': round((monotonic() - started) * 1000),
                         'content_type': error.headers.get_content_type(),
                         'server_request_id': error.headers.get('x-amzn-requestid') or error.headers.get('x-amz-rid')})
            try:
                self.record_result(json.loads(error.read(1024 * 1024)), status)
            except (ValueError, UnicodeError):
                self.record({'event': 'device_authorization_error_body', 'format': 'not-json'})
            error.close()
            raise SignInHTTPError(status, 'device authorization') from None
        with response:
            status = response.status
            self.record({'event': 'device_authorization_response', 'status': status,
                         'network_ms': round((monotonic() - started) * 1000),
                         'content_type': response.headers.get_content_type(),
                         'server_request_id': response.headers.get('x-amzn-requestid') or response.headers.get('x-amz-rid')})
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise SignInError('Amazon returned an oversized device authorization response.')
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError):
                raise SignInError('Amazon did not return a device authorization result.') from None
        self.record_result(result, status)
        self.registered = self.parse_registration(result)
        self.record({'event': 'device_authorization_validated', 'access_token_present': True,
                     'refresh_token_present': True, 'website_cookie_count': len(self.registered['cookie']),
                     'expires_in_seconds': self.registered['token']['expires'] - int(time.time())})
        return True

    def parse_registration(self, response):
        try:
            result = response['response']['success']
            bearer = result['tokens']['bearer']
            access, refresh = bearer['access_token'], bearer['refresh_token']
            lifetime = int(bearer['expires_in'])
            values = result['tokens']['website_cookies']
            cookie_values = {cookie['Name']: cookie['Value'] for cookie in values}
            if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
                    or lifetime <= 0 or not cookie_values
                    or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in cookie_values.items())
                    or not any(k.startswith('at-') and v for k, v in cookie_values.items())):
                raise ValueError('Incomplete credentials')
            if self.account.get('pv'):
                cookie_values = {key + '-av' if key.endswith('-main') else key: value
                                 for key, value in cookie_values.items()}
            customer = result.get('extensions', {}).get('customer_info', {})
            display_name = customer.get('given_name') or customer.get('name') or 'Prime Video'
            if not isinstance(display_name, str) or not display_name.strip():
                display_name = 'Prime Video'
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SignInError('Amazon did not complete device authorization. Your saved account was kept.') from None
        return dict(self.account, name=display_name, cookie=cookie_values,
                    token={'access': access, 'refresh': refresh, 'expires': int(time.time()) + lifetime})
