"""Prime Video website sign-in through native prompts and an HTTPS cookie session."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookiejar import LoadError, LWPCookieJar
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

try:
    from .public_catalog import ORIGIN, StorefrontRedirect
except ImportError:
    from public_catalog import ORIGIN, StorefrontRedirect


ACCOUNT = ORIGIN + '/settings/'
PRIME_HOST = 'www.primevideo.com'
AMAZON_HOST = 'www.amazon.com'
PRIME_HOSTS = {PRIME_HOST, 'primevideo.com', 'na.primevideo.com'}
AMAZON_HOSTS = {prefix + domain for domain in ('amazon.com', 'amazon.de', 'amazon.co.uk', 'amazon.co.jp')
                for prefix in ('', 'www.')}
HEADERS = {'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9',
           'User-Agent': 'PrimeScene'}
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
CODE_FIELDS = {'otpcode', 'otppin', 'verificationcode', 'cvf_otp_input', 'mfa_otp_code'}
CODE_FORM_IDS = {'fwcim-form', 'verification-code-form', 'auth-mfa-form'}
CAPTCHA_FIELDS = {'guess', 'cvf_captcha_input', 'field-keywords'}
IMAGE_HOSTS = {'www.amazon.com', 'images-na.ssl-images-amazon.com', 'm.media-amazon.com',
               'opfcaptcha-prod.s3.amazonaws.com'}


@dataclass
class VerificationChoice:
    name: str
    value: str
    input_id: str = ''
    label: str = ''


class SignInError(RuntimeError):
    pass


class SignInHTTPError(SignInError):
    def __init__(self, status, stage):
        self.status, self.stage = int(status), stage
        super().__init__(f'Amazon returned HTTP {self.status} during {stage}.')


class AuthorizationReturn(Exception):
    """A native authorization callback is an address to consume, not a page."""
    def __init__(self, url):
        self.url = url
        super().__init__('Amazon returned device authorization.')


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

    def __init__(self, observer=None, capture_authorization=False):
        self.observer = observer
        self.capture_authorization = capture_authorization

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
        if (self.capture_authorization and destination in AMAZON_HOSTS
                and urlsplit(newurl).path == '/ap/maplanding'
                and 'openid.oa2.authorization_code' in parse_qs(urlsplit(newurl).query)):
            raise AuthorizationReturn(newurl)
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


class SecurityImageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not security_image_host(newurl):
            raise SignInError('Amazon redirected the security image to an unexpected address.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def security_image_host(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.hostname in IMAGE_HOSTS and parts.port in (None, 443)
                and parts.username is None and '\\' not in url
                and not any(ord(character) <= 32 for character in url))
    except ValueError:
        return False


def read_security_image(cookies, url, *, user_agent=None):
    if not security_image_host(url):
        raise SignInError('Amazon returned an unexpected security image address.')
    opener = build_opener(SecurityImageRedirect(), HTTPCookieProcessor(cookies))
    request = Request(url, headers={'User-Agent': user_agent or HEADERS['User-Agent'], 'Accept': 'image/*'})
    extensions = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}
    with opener.open(request, timeout=12) as response:
        kind = response.headers.get_content_type()
        content = response.read(1024 * 1024 + 1)
        if kind not in extensions or len(content) > 1024 * 1024:
            raise SignInError('Amazon did not return a supported security image.')
        return content, extensions[kind]


@dataclass
class Form:
    action: str
    identity: str = ''
    hidden: list = field(default_factory=list)
    prompts: list = field(default_factory=list)
    submit: tuple = ()
    choices: list = field(default_factory=list)
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
        self.approval_status = ''
        self.captcha_required = False
        self.captcha_image = ''
        self.cookies_required = 'Please Enable Cookies to Continue' in html
        self.script_data = None
        self.labels = []
        self.radio_inputs = {}
        self.label_text = {}
        self.country_select = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = attrs.get('name') or ''
        element_id = attrs.get('id') or ''
        if tag == 'input' and name == 'transactionApprovalStatus':
            self.approval_status = attrs.get('value') or ''
        if tag == 'img' and 'captcha' in (element_id + ' ' + (attrs.get('alt') or '')).lower():
            self.captcha_image = attrs.get('src') or ''
            self.captcha_required = True
        if tag == 'label':
            self.labels.append({'target': attrs.get('for') or '', 'text': [], 'choices': []})
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
            if name == 'countryCode':
                self.country_select = {'first': None, 'selected': None}
            else:
                self.form.other_controls = True
        if tag == 'option' and self.country_select is not None:
            value = attrs.get('value')
            if self.country_select['first'] is None:
                self.country_select['first'] = value
            if 'selected' in attrs:
                self.country_select['selected'] = value
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
        elif name in CAPTCHA_FIELDS:
            self.form.prompts.append((name, 'Amazon security image', False))
            self.captcha_required = True
        elif input_type == 'radio':
            choice = VerificationChoice(name, attrs.get('value') or 'on', element_id,
                                        self.label_text.get(element_id) or attrs.get('aria-label') or '')
            self.form.choices.append(choice)
            if element_id:
                self.radio_inputs[element_id] = choice
            for label in self.labels:
                label['choices'].append(choice)
        elif input_type == 'checkbox':
            if 'checked' in attrs:
                self.form.hidden.append((name, attrs.get('value') or 'on'))
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
        if tag == 'select' and self.country_select is not None:
            value = self.country_select['selected'] or self.country_select['first']
            if self.form is not None and value is not None:
                self.form.hidden.append(('countryCode', value))
            self.country_select = None
        if tag == 'label' and self.labels:
            label = self.labels.pop()
            text = ' '.join(''.join(label['text']).split())
            if label['target'] and text:
                self.label_text[label['target']] = text
            choices = list(label['choices'])
            if label['target'] in self.radio_inputs:
                choices.append(self.radio_inputs[label['target']])
            for choice in choices:
                if text:
                    choice.label = text
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
        else:
            for label in self.labels:
                label['text'].append(data)


def request_document(opener, url, *, fields=None, previous=None, observer=None, headers=None, stage='sign-in'):
    if not trusted_host(url):
        raise SignInError('Amazon returned an unexpected sign-in address.')
    headers = dict(HEADERS, **(headers or {}))
    if previous is not None:
        headers['Origin'] = 'https://' + urlsplit(previous).netloc
        headers['Referer'] = previous
    body = urlencode(fields).encode('ascii') if fields is not None else None
    try:
        response = opener.open(Request(url, data=body, headers=headers), timeout=12)
    except AuthorizationReturn as result:
        return result.url, SignInDocument('')
    except HTTPError as error:
        response = error
    with response:
        status = getattr(response, 'status', None) or response.code
        if status >= 400 and response.headers.get_content_type() != 'text/html':
            if observer:
                observer({'event': 'http_error', 'status': status, 'stage': stage,
                          'host': diagnostic_host(response.url)})
            raise SignInHTTPError(status, stage)
        if not trusted_host(response.url) or response.headers.get_content_type() != 'text/html':
            raise SignInError('Amazon did not return a sign-in page.')
        content = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(content) > MAX_DOCUMENT_BYTES:
            raise SignInError('Amazon returned a sign-in page that is too large.')
        document = SignInDocument(content.decode(response.headers.get_content_charset() or 'utf-8', 'replace'))
        if status >= 400:
            if observer:
                observer({'event': 'http_error', 'status': status, 'stage': stage,
                          'host': diagnostic_host(response.url)})
            # Amazon can use HTTP 400 for a rejected code while returning the
            # same usable verification form. Let the user correct that code.
            recoverable = status in (400, 401, 403, 422) and any(
                (form.prompts or form.choices) and not form.other_controls for form in document.forms)
            if not recoverable:
                raise SignInHTTPError(status, stage)
        if observer:
            observer({'event': 'page', 'host': trusted_host(response.url),
                      'signed_in_navigation': document.signed_in_navigation, 'challenge': document.challenge,
                      'authenticated_context': document.authenticated_context,
                      'document_bytes': len(content), 'cookies_required': document.cookies_required,
                      'approval_required': document.approval_required, 'captcha_required': document.captcha_required,
                      'verification_choices': sum(len(form.choices) for form in document.forms),
                      'prompt_count': sum(len(form.prompts) for form in document.forms),
                      'form_count': len(document.forms)})
        return response.url, document


def authenticate(cookies, prompt, *, observer=None, approve=None, choose=None, captcha=None, state=None,
                 entry_url=None, completion=None, headers=None):
    """Return true after Prime Video shows signed-in navigation. False means canceled."""
    opener = build_opener(SignInRedirect(observer, capture_authorization=completion is not None), HTTPCookieProcessor(cookies))
    state = {} if state is None else state

    def read_page(target, **kwargs):
        options = {'headers': headers} if headers else {}
        result = request_document(opener, target, observer=observer, **options, **kwargs)
        state['response'] = result
        return result

    url, page = state.get('response') or read_page(entry_url or ACCOUNT)
    attempted = state.setdefault('attempted_fields', set())
    checked_storefront = False
    for _ in range(12):
        host = trusted_host(url)
        if completion is not None and completion(url, page, cookies):
            state.clear()
            return True
        if host in PRIME_HOSTS and (page.signed_in_navigation or page.authenticated_context):
            authenticated = has_authentication_cookie(cookies)
            if observer:
                observer({'event': 'session_check', 'authentication_cookie_present': authenticated})
            if authenticated:
                state.clear()
                return True
            raise SignInError('Amazon identified the account, but a complete sign-in session has not been confirmed.')
        code_entry = any(label == 'Amazon verification code' for form in page.forms
                         for _, label, _ in form.prompts)
        image_entry = any(label == 'Amazon security image' for form in page.forms
                          for _, label, _ in form.prompts)
        if page.approval_required and not code_entry:
            form = next((form for form in page.forms if form.identity == 'pollingForm'), None)
            if approve is None or form is None:
                raise SignInError('Amazon is waiting for approval through its sign-in message.')
            if not approve('Approve the sign-in using the message from Amazon, then choose Continue.'):
                return False
            target = urljoin(url, form.action)
            if trusted_host(target) != host or not urlsplit(target).path.startswith('/ap/'):
                raise SignInError('Amazon returned an unexpected approval form address.')
            return_url = state.setdefault('approval_return', parse_qs(urlsplit(url).query).get('openid.return_to', [ACCOUNT])[0])
            fields = list(form.hidden)
            if form.submit:
                fields.append(form.submit)
            url, page = read_page(target, fields=fields, previous=url, stage='sign-in approval')
            status = page.approval_status
            if status in ('TransactionCompleted', 'TransactionCompletionTimeout'):
                url, page = read_page(return_url)
            elif status == 'TransactionExpired':
                raise SignInError('Amazon sign-in approval expired. Start a fresh sign-in attempt.')
            continue
        if page.captcha_required and not code_entry and not image_entry:
            raise SignInError('Amazon requested a security challenge without a supported image-entry form.')
        for form in page.forms:
            target = urljoin(url, form.action)
            if (host in AMAZON_HOSTS and trusted_host(target) == host
                    and urlsplit(target).path.startswith('/ap/') and (form.prompts or form.choices)
                    and not form.other_controls):
                break
        else:
            if host in PRIME_HOSTS and not checked_storefront:
                checked_storefront = True
                url, page = read_page(ORIGIN + '/')
                continue
            raise SignInError('Prime Scene could not verify your Prime Video session or read the next Amazon sign-in step.')
        names = {name for name, label, _ in form.prompts if label not in ('Amazon verification code', 'Amazon security image')}
        if names & attempted and not image_entry:
            raise SignInError('Amazon asked for the same information again. Check your details and retry.')
        attempted.update(names)
        fields = list(form.hidden)
        for group in dict.fromkeys(choice.name for choice in form.choices):
            options = [choice for choice in form.choices if choice.name == group]
            if choose is None:
                raise SignInError('Amazon asked you to select a verification method.')
            selected = choose('Choose how to verify your Amazon sign-in',
                              [choice.label or f'Verification option {index + 1}' for index, choice in enumerate(options)])
            if selected is None or selected < 0:
                return False
            if selected >= len(options):
                raise SignInError('The verification selection was invalid.')
            fields.append((options[selected].name, options[selected].value))
        for name, label, masked in form.prompts:
            if label == 'Amazon security image':
                if captcha is None or not page.captcha_image:
                    raise SignInError('Amazon requested a security image that could not be displayed.')
                image_options = {'user_agent': headers.get('User-Agent')} if headers else {}
                content, extension = read_security_image(cookies, urljoin(url, page.captcha_image), **image_options)
                value = captcha(content, extension)
            else:
                value = prompt(label, masked)
            if not value:
                return False
            fields.append((name, value))
        if form.submit:
            fields.append(form.submit)
        stage = ('verification-code submission' if code_entry else 'security-image submission' if image_entry
                 else 'verification-method selection' if form.choices else 'credential submission')
        url, page = read_page(target, fields=fields, previous=url, stage=stage)
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
