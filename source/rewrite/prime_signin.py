"""Prime Video website sign-in through native prompts and an HTTPS cookie session."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookiejar import LoadError, LWPCookieJar
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from public_catalog import ORIGIN, StorefrontRedirect


ACCOUNT = ORIGIN + '/settings/'
PRIME_HOST = 'www.primevideo.com'
AMAZON_HOST = 'www.amazon.com'
PRIME_HOSTS = {PRIME_HOST, 'primevideo.com', 'na.primevideo.com'}
AMAZON_HOSTS = {AMAZON_HOST, 'amazon.com'}
HEADERS = {'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9',
           'User-Agent': 'PrimeScene/0.0.4'}
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
CODE_FIELDS = {'otpcode', 'otppin', 'verificationcode', 'cvf_otp_input', 'mfa_otp_code'}
CODE_FORM_IDS = {'fwcim-form', 'verification-code-form', 'auth-mfa-form'}


class SignInError(RuntimeError):
    pass


def trusted_host(url):
    if '\\' in url or any(ord(character) <= 32 or ord(character) == 127 for character in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme == 'https' and parts.port in (None, 443) and parts.username is None
                and host in PRIME_HOSTS | AMAZON_HOSTS):
            return host
    except ValueError:
        return None
    return None


def diagnostic_host(url):
    try:
        host = urlsplit(url).hostname or ''
    except ValueError:
        return 'invalid-host'
    return host if re.fullmatch(r'[a-z0-9.-]{1,253}', host) else 'invalid-host'


def has_authentication_cookie(cookies):
    for cookie in cookies:
        domain = cookie.domain.lstrip('.')
        if (domain in AMAZON_HOSTS | PRIME_HOSTS and cookie.name.startswith('at-')
                and cookie.value and cookie.secure and not cookie.is_expired()):
            return True
    return False


class SignInRedirect(HTTPRedirectHandler):
    http_error_308 = HTTPRedirectHandler.http_error_302

    def __init__(self, observer=None):
        self.observer = observer

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = trusted_host(req.full_url)
        destination = trusted_host(newurl)
        method = req.get_method()
        if self.observer:
            self.observer({'event': 'redirect', 'status': code, 'method': method,
                           'from_host': diagnostic_host(req.full_url), 'to_host': diagnostic_host(newurl)})
        if destination is None:
            raise SignInError(f'Amazon redirected to an unrecognized address '
                              f'(HTTP {code} {method} from {source} to {diagnostic_host(newurl)}).')
        if code in (307, 308):
            # These responses retain the method and body. Only an Amazon auth
            # route on the original origin may receive a form submission again.
            if req.data is not None and (destination != source or destination not in AMAZON_HOSTS
                                         or not urlsplit(newurl).path.startswith('/ap/')):
                raise SignInError(f'Amazon redirected a sign-in submission outside its original sign-in route '
                                  f'(HTTP {code} from {source} to {destination}).')
            forwarded = {name: value for name, value in req.headers.items()
                         if name.lower() not in ('authorization', 'proxy-authorization', 'content-length', 'cookie', 'host')}
            return Request(newurl, data=req.data, headers=forwarded, method=method,
                           origin_req_host=req.origin_req_host, unverifiable=True)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        for name in ('Cookie', 'Host', 'Authorization', 'Proxy-authorization'):
            redirected.remove_header(name)
        if source != destination:
            redirected.remove_header('Origin')
            redirected.remove_header('Referer')
        return redirected


@dataclass
class Form:
    action: str
    identity: str = ''
    hidden: list = field(default_factory=list)
    prompts: list = field(default_factory=list)
    submit: tuple = ()
    other_controls: bool = False


class SignInDocument(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.form = None
        self.signed_in_navigation = False
        self.authenticated_context = False
        self.challenge = False
        self.approval_required = False
        self.captcha_required = False
        self.cookies_required = 'Please Enable Cookies to Continue' in html
        self.script_data = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = attrs.get('name') or ''
        element_id = attrs.get('id') or ''
        if tag == 'script' and attrs.get('type') in ('application/json', 'a-state'):
            self.script_data = []
        if tag == 'a':
            link = attrs.get('href') or ''
            if link.startswith('/') and not link.startswith('//'):
                link = ORIGIN + link
            if trusted_host(link) in PRIME_HOSTS:
                parts = urlsplit(link)
                params = parse_qs(parts.query)
                if (parts.path.rstrip('/') == '/auth-redirect'
                        and (params.get('signout') == ['1'] or params.get('signin') == ['0'])):
                    self.signed_in_navigation = True
        if attrs.get('data-automation-id') == 'nav-active-profile-name':
            self.signed_in_navigation = True
        if tag == 'form':
            self.form = None
            if (attrs.get('method') or '').lower() == 'post':
                self.form = Form(attrs.get('action') or '', element_id or name)
        if element_id in ('pollingForm', 'auth-polling-container'):
            self.approval_required = True
        if 'captcha' in (name + ' ' + element_id).lower():
            self.captcha_required = True
        if self.approval_required or self.captcha_required:
            self.challenge = True
        if self.form is None or 'disabled' in attrs:
            return
        if tag == 'select':
            self.form.other_controls = True
        if tag not in ('input', 'button') or not name:
            return
        input_type = (attrs.get('type') or ('submit' if tag == 'button' else 'text')).lower()
        if input_type == 'hidden':
            self.form.hidden.append((name, attrs.get('value') or ''))
        elif input_type == 'submit':
            if not self.form.submit:
                self.form.submit = (name, attrs.get('value') or '')
        elif input_type in ('button', 'reset', 'image'):
            return
        elif input_type in ('radio', 'checkbox'):
            if 'checked' in attrs:
                self.form.hidden.append((name, attrs.get('value') or 'on'))
            if input_type == 'radio':
                self.form.other_controls = True
        elif element_id == 'ap-credential-autofill-hint' or 'hidden' in attrs:
            return
        elif (attrs.get('autocomplete') == 'one-time-code' or name.lower() in CODE_FIELDS
              or (name == 'code' and (self.form.identity in CODE_FORM_IDS or element_id == 'cvf-input-code'))):
            self.form.prompts.append((name, 'Amazon verification code', True))
        elif name == 'email' and input_type in ('text', 'tel', 'email'):
            self.form.prompts.append((name, 'Amazon email or mobile number', False))
        elif name == 'password' and input_type == 'password':
            self.form.prompts.append((name, 'Amazon password', True))
        else:
            self.form.other_controls = True

    def handle_endtag(self, tag):
        if tag == 'script' and self.script_data is not None:
            try:
                data = json.loads(''.join(self.script_data))
                context = data.get('RequestContext') if isinstance(data, dict) else None
                customer = context.get('customerID') if isinstance(context, dict) else None
                if isinstance(customer, str) and customer.strip().lower() not in ('', '0', 'null', 'undefined'):
                    self.authenticated_context = True
            except ValueError:
                pass
            self.script_data = None
        if tag == 'form' and self.form is not None:
            self.forms.append(self.form)
            self.form = None

    def handle_data(self, data):
        if self.script_data is not None:
            self.script_data.append(data)


def request_document(opener, url, *, fields=None, previous=None, observer=None):
    if not trusted_host(url):
        raise SignInError('Amazon returned an unexpected sign-in address.')
    headers = dict(HEADERS)
    if previous is not None:
        headers['Origin'] = 'https://' + urlsplit(previous).netloc
        headers['Referer'] = previous
    body = urlencode(fields).encode('ascii') if fields is not None else None
    with opener.open(Request(url, data=body, headers=headers), timeout=12) as response:
        if not trusted_host(response.url) or response.headers.get_content_type() != 'text/html':
            raise SignInError('Amazon did not return a sign-in page.')
        content = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(content) > MAX_DOCUMENT_BYTES:
            raise SignInError('Amazon returned a sign-in page that is too large.')
        document = SignInDocument(content.decode(response.headers.get_content_charset() or 'utf-8', 'replace'))
        if observer:
            observer({'event': 'page', 'host': trusted_host(response.url),
                      'signed_in_navigation': document.signed_in_navigation, 'challenge': document.challenge,
                      'authenticated_context': document.authenticated_context,
                      'document_bytes': len(content), 'cookies_required': document.cookies_required,
                      'approval_required': document.approval_required, 'captcha_required': document.captcha_required,
                      'prompt_count': sum(len(form.prompts) for form in document.forms),
                      'form_count': len(document.forms)})
        return response.url, document


def authenticate(cookies, prompt, *, observer=None, approve=None, state=None):
    """Return true after Prime Video shows signed-in navigation. False means canceled."""
    opener = build_opener(SignInRedirect(observer), HTTPCookieProcessor(cookies))
    state = {} if state is None else state

    def read_page(target, **kwargs):
        result = request_document(opener, target, observer=observer, **kwargs)
        state['response'] = result
        return result

    url, page = state.get('response') or read_page(ACCOUNT)
    attempted = state.setdefault('attempted_fields', set())
    checked_storefront = False
    for _ in range(12):
        host = trusted_host(url)
        if host in PRIME_HOSTS and (page.signed_in_navigation or page.authenticated_context):
            authenticated = has_authentication_cookie(cookies)
            if observer:
                observer({'event': 'session_check', 'authentication_cookie_present': authenticated})
            if authenticated:
                state.clear()
                return True
            raise SignInError('Amazon identified the account, but a complete sign-in session has not been confirmed.')
        if page.approval_required:
            form = next((form for form in page.forms if form.identity == 'pollingForm'), None)
            if approve is None or form is None:
                raise SignInError('Amazon is waiting for approval through its sign-in message.')
            if not approve('Approve the sign-in using the message from Amazon, then choose Continue.'):
                return False
            target = urljoin(url, form.action)
            if trusted_host(target) != host or not urlsplit(target).path.startswith('/ap/'):
                raise SignInError('Amazon returned an unexpected approval form address.')
            return_url = parse_qs(urlsplit(url).query).get('openid.return_to', [ACCOUNT])[0]
            fields = list(form.hidden)
            if form.submit:
                fields.append(form.submit)
            url, page = read_page(target, fields=fields, previous=url)
            status = next((value for form in page.forms for name, value in form.hidden
                           if name == 'transactionApprovalStatus'), '')
            if status == 'TransactionCompleted':
                url, page = read_page(return_url)
            elif status in ('TransactionExpired', 'TransactionCompletionTimeout'):
                raise SignInError('Amazon sign-in approval expired. Start a fresh sign-in attempt.')
            continue
        if page.captcha_required:
            raise SignInError('Amazon requested a verification challenge this preview cannot display.')
        for form in page.forms:
            target = urljoin(url, form.action)
            if (host in AMAZON_HOSTS and trusted_host(target) == host
                    and urlsplit(target).path.startswith('/ap/') and form.prompts and not form.other_controls):
                break
        else:
            if host in PRIME_HOSTS and not checked_storefront:
                checked_storefront = True
                url, page = read_page(ORIGIN + '/')
                continue
            raise SignInError('Prime Scene could not verify your Prime Video session or read the next Amazon sign-in step.')
        names = {name for name, label, _ in form.prompts if label != 'Amazon verification code'}
        if names & attempted:
            raise SignInError('Amazon asked for the same information again. Check your details and retry.')
        attempted.update(names)
        fields = list(form.hidden)
        for name, label, masked in form.prompts:
            value = prompt(label, masked)
            if not value:
                return False
            fields.append((name, value))
        if form.submit:
            fields.append(form.submit)
        url, page = read_page(target, fields=fields, previous=url)
    raise SignInError('Amazon did not finish signing in.')


def load_cookies(path):
    jar = LWPCookieJar(str(path))
    try:
        jar.load(ignore_discard=True)
    except (OSError, LoadError):
        jar.clear()
    return jar


def save_cookies(jar, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        jar.save(str(temporary), ignore_discard=True)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def catalog_opener(jar):
    opener = build_opener(StorefrontRedirect(), HTTPCookieProcessor(jar))
    opener.addheaders = list(HEADERS.items())
    return opener
