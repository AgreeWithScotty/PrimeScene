"""Canonical credential selection and independent device-token renewal."""
from copy import deepcopy
from time import time, monotonic
from pathlib import Path
import sqlite3

from .account_state import current_account
from .auth_transport import AUTH_DOMAINS, json_request
from .prime_signin import SignInError


def _exchange_device_token(account, *, g, epoch, observer, actor_id=None):
    from .prime_device import DeviceAuthorization
    from .accounts import updateUser
    if not current_account(g.DATA_PATH, epoch):
        return False
    domain = account.get('sidomain')
    saved = account.get('token')
    if domain not in AUTH_DOMAINS or not isinstance(saved, dict) or not saved.get('refresh'):
        observer({'event': 'token_refresh_unavailable', 'stage': 'credential_validation', 'result': 'failure'})
        return False
    for value in (account.get('name'), account.get('deviceid'), saved.get('access'), saved.get('refresh'), actor_id):
        observer.secret(value)
    description = DeviceAuthorization(account, g.dtid_android, dict(g.headers_android)).device_description()
    payload = dict(description, source_token_type='refresh_token')
    if actor_id:
        payload.update(requested_token_type='actor_access_token', actor_id=actor_id,
            source_device_tokens=[{'device_type': g.dtid_android,
                                  'account_refresh_token': {'token': saved['refresh']}}])
    else:
        payload.update(requested_token_type='access_token', source_token=saved['refresh'])
    headers = {k:v for k,v in g.headers_android.items()
               if k.lower() not in ('x-gasc-enabled', 'x-requested-with', 'cookie', 'authorization')}
    headers.update({'x-amzn-identity-auth-domain': 'api.'+domain, 'Accept-Language': 'en-US'})
    result = json_request('https://api.'+domain+'/auth/token', observer=observer,
                          stage='device token renewal', payload=payload, headers=headers)
    access = result.get('access_token')
    refresh = result.get('refresh_token', saved['refresh'])
    observer.secret(access)
    observer.secret(refresh)
    try:
        lifetime = int(result['expires_in'])
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or lifetime <= 0:
            raise ValueError('Incomplete token response')
    except (KeyError, TypeError, ValueError):
        raise SignInError('Amazon did not return a complete renewed authorization.') from None
    renewed = dict(access=access, refresh=refresh, expires=int(time())+lifetime)
    stored = updateUser('token', renewed, expected_epoch=epoch)
    observer({'event': 'token_renewal_complete', 'stage': 'token_persistence',
        'lifetime_seconds': lifetime, 'actor_requested': bool(actor_id),
        'result': 'success' if stored else 'cancelled'})
    return renewed if stored else False


def renew_device_token(account, *, g, epoch, observer, actor_id=None):
    """Serialize renewal with native sign-in and re-read after waiting."""
    from .accounts import loadUser
    if not current_account(g.DATA_PATH, epoch):
        return False
    lock = sqlite3.connect(str(Path(g.DATA_PATH)/'signin-lock.db'), timeout=20)
    started = monotonic()
    try:
        observer({'event': 'token_renewal_lock_started', 'stage': 'authorization_lock'})
        lock.execute('BEGIN EXCLUSIVE')
        observer({'event': 'token_renewal_lock_acquired', 'stage': 'authorization_lock',
            'wait_ms': round((monotonic()-started)*1000), 'result': 'success'})
        if not current_account(g.DATA_PATH, epoch):
            return False
        latest = loadUser()
        saved = latest.get('token')
        if (not actor_id and isinstance(saved, dict) and isinstance(saved.get('access'), str)
                and saved.get('access') and float(saved.get('expires') or 0) > time()+60):
            observer({'event': 'token_renewal_reused', 'stage': 'authorization_lock', 'result': 'success'})
            return deepcopy(saved)
        return _exchange_device_token(latest, g=g, epoch=epoch, observer=observer, actor_id=actor_id)
    except sqlite3.OperationalError as error:
        observer({'event': 'token_renewal_lock_failed', 'stage': 'authorization_lock',
            'sqlite_code': getattr(error, 'sqlite_errorcode', None),
            'sqlite_name': getattr(error, 'sqlite_errorname', None),
            'wait_ms': round((monotonic()-started)*1000), 'result': 'failure'})
        raise SignInError('Another authorization operation has not finished. Please retry.') from error
    finally:
        lock.close()


def refresh_profile_authorization(account, actor_id=None):
    from .accounts import _context
    from .prime_diagnostics import exception_details
    g, epoch, report = _context()
    try:
        return renew_device_token(account, g=g, epoch=epoch, observer=report, actor_id=actor_id)
    except Exception as error:
        report({'event':'token_renewal_failed','stage':'token_renewal',
                'exception':exception_details(error),'result':'failure'})
        return False


def credentials(prefer_token=False):
    """Read current credentials only; background traffic never launches sign-in."""
    from .accounts import _context, loadUser
    from .session_store import requests_session_cookies
    from .prime_diagnostics import exception_details
    g, epoch, report = _context()
    if not current_account(g.DATA_PATH, epoch):
        return False
    if prefer_token:
        account = loadUser()
        token = account.get('token')
        if isinstance(token, dict) and token.get('access'):
            try:
                if float(token.get('expires') or 0) <= time()+60:
                    token = renew_device_token(account, g=g, epoch=epoch, observer=report)
                if token and current_account(g.DATA_PATH, epoch):
                    return {'Authorization': 'Bearer '+token['access']}
            except Exception as error:
                report({'event': 'credential_renewal_failed', 'stage': 'credentials',
                        'exception': exception_details(error), 'result': 'failure'})
                return False
        if g.data_source == 1:
            report({'event': 'account_device_authorization_required', 'stage': 'credentials', 'result': 'failure'})
            return False
    jar = requests_session_cookies(g.DATA_PATH)
    if jar is not None and current_account(g.DATA_PATH, epoch):
        return jar
    if jar is not None:
        jar.clear()
    report({'event': 'account_credentials_required', 'stage': 'credentials',
            'token_preferred': bool(prefer_token), 'result': 'failure'})
    return False
