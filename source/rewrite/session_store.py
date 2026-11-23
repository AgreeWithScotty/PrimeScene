"""Full website sessions and short-lived native sign-in challenges.

Cookie domains, paths, flags and expiry come from Amazon's Cookie objects. A
legacy name/value dictionary is deliberately not a website cookie session.
"""

from copy import deepcopy
from dataclasses import dataclass
from http.cookiejar import CookieJar, LWPCookieJar
import json
import os
from pathlib import Path
from threading import RLock
from time import monotonic
from urllib.parse import urljoin, urlsplit

from . import prime_signin


COOKIE_FILE = 'prime-video-session.cookies'
HEADERS_FILE = 'prime-video-session.headers.json'
PENDING_SECONDS = 10 * 60
_pending = {}
_lock = RLock()


@dataclass
class _Challenge:
    epoch: str
    jar: CookieJar
    state: dict
    started: float


def _profile_key(profile):
    return os.path.normcase(str(Path(profile).resolve()))


def copy_cookie_session(jar, path=None):
    """Copy a real CookieJar to LWP format without changing any cookie scope."""
    if not isinstance(jar, CookieJar):
        raise TypeError('A website session requires a CookieJar')
    result = LWPCookieJar(str(path) if path is not None else None)
    for cookie in jar:
        result.set_cookie(deepcopy(cookie))
    return result


def load_session(profile):
    """Load the canonical jar only while its account generation is current."""
    from . import account_state
    path = Path(profile) / COOKIE_FILE
    jars = []
    epoch = account_state.account_epoch(profile)
    account_state.persist_if_current(profile, epoch,
                                     lambda: jars.append(prime_signin.load_cookies(path)))
    return jars[0] if jars else LWPCookieJar(str(path))


def credential_cookies(profile):
    jar=load_session(profile)
    if prime_signin.has_authentication_cookie(jar):return jar
    jar.clear()
    return None


def public_session_headers(values):
    """Only public browser preferences may enter a plaintext header file."""
    result={}
    if not isinstance(values,dict):return result
    for key in ('User-Agent','Accept-Language'):
        value=values.get(key)
        if (isinstance(value,str) and value and len(value)<=4096
            and all(32<=ord(character)<127 for character in value)):result[key]=value
    return result


def session_headers(profile):
    """Read only the non-secret headers used by the authenticated web session."""
    from . import account_state
    result = {}
    path = Path(profile) / HEADERS_FILE

    def read():
        if not path.is_file():
            return
        if path.stat().st_size > 16 * 1024:
            raise ValueError('The website session header file is too large')
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, dict):
            raise ValueError('The website session header file is invalid')
        result.update(public_session_headers(values))

    epoch = account_state.account_epoch(profile)
    account_state.persist_if_current(profile, epoch, read)
    return result


def persist_session(profile, expected_epoch, jar):
    """Refresh an existing canonical session; never create one from a fallback."""
    from . import account_state
    path = Path(profile) / COOKIE_FILE
    if not path.is_file():
        return False
    copied = copy_cookie_session(jar, path)
    written = False

    def write():
        nonlocal written
        # Recheck inside the account transaction: sign-out may have removed it
        # since the caller began its request.
        if path.is_file() and prime_signin.has_authentication_cookie(copied):
            prime_signin.save_cookies(copied, path)
            written = True

    try:
        current = account_state.persist_if_current(profile, expected_epoch, write)
        return bool(current and written)
    finally:
        copied.clear()


def _resumable(state):
    """Recognize a current supported challenge, excluding credential retries."""
    response = state.get('response') if isinstance(state, dict) else None
    if not isinstance(response, (tuple, list)) or len(response) != 2:
        return False
    url, page = response
    if not isinstance(url, str) or not isinstance(page, prime_signin.SignInDocument):
        return False
    host = prime_signin.trusted_host(url)
    if host not in prime_signin.AMAZON_HOSTS:
        return False
    if getattr(page, 'approval_status', '') in (
            'TransactionExpired', 'TransactionCompleted', 'TransactionCompletionTimeout'):
        return False
    for form in page.forms:
        target = urljoin(url, form.action)
        if (prime_signin.trusted_host(target) != host
                or not urlsplit(target).path.startswith('/ap/') or form.other_controls):
            continue
        labels = {label for _, label, _ in form.prompts}
        if labels & {'Amazon verification code', 'Amazon security image'}:
            return True
        if form.choices:
            return True
        if page.approval_required and form.identity == 'pollingForm':
            return True
    return False


def _erase(challenge):
    """Release cookies, hidden fields and the parsed HTML held for a retry."""
    challenge.jar.clear()
    response = challenge.state.get('response')
    if isinstance(response, (tuple, list)) and len(response) == 2:
        page = response[1]
        if isinstance(page, prime_signin.SignInDocument):
            for form in page.forms:
                form.hidden.clear()
                form.prompts.clear()
                form.choices.clear()
                form.action = ''
                form.submit = None
            # HTMLParser and SignInDocument keep input/HTML fragments in their
            # instance fields. Nothing from this parser is reused after erasure.
            vars(page).clear()
    challenge.state.clear()


def discard_pending(profile, clear=True):
    """Forget a challenge; clear=False transfers ownership back to the caller."""
    with _lock:
        challenge = _pending.pop(_profile_key(profile), None)
        if challenge is not None and clear:
            _erase(challenge)


def clear_all_pending():
    """Erase all account challenge data held by this interpreter."""
    with _lock:
        pending = list(_pending.values())
        _pending.clear()
        for challenge in pending:
            _erase(challenge)


def begin_signin(profile, epoch):
    """Resume only the same generation's supported, locally recent challenge."""
    from . import account_state
    if not account_state.current_account(profile, epoch, allow_signed_out=True):
        discard_pending(profile)
        raise prime_signin.SignInError('The account session changed. Start a new sign-in.')
    key = _profile_key(profile)
    with _lock:
        challenge = _pending.get(key)
        if challenge is not None:
            age = monotonic() - challenge.started
            if challenge.epoch == epoch and 0 <= age < PENDING_SECONDS and _resumable(challenge.state):
                return challenge.jar, challenge.state, True
            _pending.pop(key)
            _erase(challenge)
    jar = load_session(profile)
    if not account_state.current_account(profile, epoch, allow_signed_out=True):
        jar.clear()
        discard_pending(profile)
        raise prime_signin.SignInError('The account session changed. Start a new sign-in.')
    return jar, {}, False


def retain_challenge(profile, epoch, jar, state):
    """Keep the actual current challenge for a retry, never entered credentials.

    A True result transfers ownership of jar/state to this module. The caller
    must not clear them until success, cancellation or account cleanup.
    """
    from . import account_state
    if (not isinstance(jar, CookieJar) or not _resumable(state)
            or not account_state.current_account(profile, epoch, allow_signed_out=True)):
        discard_pending(profile)
        return False
    key = _profile_key(profile)
    with _lock:
        previous = _pending.get(key)
        started = monotonic()
        if previous is not None:
            same = previous.epoch == epoch and previous.jar is jar and previous.state is state
            if same:
                started = previous.started
                if not 0 <= monotonic() - started < PENDING_SECONDS:
                    _pending.pop(key)
                    _erase(previous)
                    return False
            else:
                _erase(previous)
        _pending[key] = _Challenge(epoch, jar, state, started)
    return True
