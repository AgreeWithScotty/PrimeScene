"""Native Kodi prompts and persistence for Prime Scene account authorization."""
from copy import deepcopy
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
import xbmc
import xbmcgui

from .common import Globals, Settings
from .prime_device import DeviceAuthorization
from .prime_signin import HEADERS, SignInError, authenticate
from .prime_diagnostics import DiagnosticLog, exception_details
from .account_state import account_epoch, clear_runtime, current_account, store_account, needs_cleanup, is_signed_out
from .public_catalog import ORIGIN
from .session_store import begin_signin, discard_pending, retain_challenge


TITLE = 'Smooth Prime'


def ask(label, masked):
    keyboard = xbmc.Keyboard('', label, masked)
    keyboard.doModal()
    return keyboard.getText() if keyboard.isConfirmed() else None


def show_security_image(content, extension, profile):
    with NamedTemporaryFile(dir=profile, suffix='.' + extension, delete=False) as output:
        output.write(content)
        filename = Path(output.name)
    dialog = xbmcgui.WindowDialog()
    try:
        dialog.addControl(xbmcgui.ControlImage(450, 70, 1000, 300, str(filename)))
        dialog.show()
        return ask('Type the characters from the Amazon security image', False)
    finally:
        dialog.close()
        filename.unlink(missing_ok=True)


def LogIn(retToken=False):
    from .configs import getConfig
    from .login import getTerritory
    from .users import loadUser

    g, settings = Globals(), Settings()
    profile = Path(g.DATA_PATH)
    profile.mkdir(parents=True, exist_ok=True)
    if needs_cleanup(profile):
        g.dialog.ok(TITLE, 'Finish signing out before starting another sign-in. Use the account button to retry cleanup.')
        return False
    signed_out_at_start = is_signed_out(profile)
    generation = account_epoch(profile)
    observe = DiagnosticLog(profile, g.addon.getAddonInfo('version'),
                            active=lambda: current_account(profile, generation, allow_signed_out=True))
    # An OS-backed exclusive lock cannot survive the process holding it.
    # Kodi properties would not provide an atomic claim across script invocations.
    import sqlite3
    lock = None
    claimed = False
    pending = False
    cookies = None
    signin_state = {}
    mode = 'device' if settings.data_source == 1 else 'website'
    try:
        lock = sqlite3.connect(str(profile / 'signin-lock.db'), timeout=0)
        try:
            lock.execute('BEGIN EXCLUSIVE')
            claimed = True
        except sqlite3.OperationalError:
            observe({'event': 'signin_blocked', 'reason': 'another_signin_is_open'})
            g.dialog.ok(TITLE, 'Another Smooth Prime sign-in is already open.')
            return False

        cookies, signin_state, resumed = begin_signin(profile, generation)
        if resumed and signin_state.get('_prime_scene_mode') != mode:
            discard_pending(profile)
            cookies, signin_state, resumed = begin_signin(profile, generation)
        signin_state['_prime_scene_mode'] = mode
        # The same challenge must keep its browser identity on retry. These two
        # headers are also persisted with the complete website CookieJar.
        if resumed:
            headers = signin_state['_prime_scene_headers']
        elif mode == 'device':
            headers = dict(g.headers_android)
        else:
            headers = dict(HEADERS)
            headers['User-Agent'] = getConfig('UserAgent') or HEADERS['User-Agent']
        signin_state['_prime_scene_headers'] = headers
        for cookie in cookies:
            observe.secret(cookie.value)
        observe({'event': 'signin_started', 'python': sys.version.split()[0], 'platform': sys.platform,
                 'kodi': xbmc.getInfoLabel('System.BuildVersion'), 'token_requested': bool(retToken),
                 'authorization_mode': mode, 'challenge_resumed': resumed,
                 'signed_out_at_start': signed_out_at_start, 'fresh_cookie_jar': not bool(len(cookies)),
                 'user_agent': headers.get('User-Agent'), 'initial_cookie_count': len(cookies)})
        observe({'event': 'territory_lookup_started'})
        account, valid = getTerritory(dict(loadUser(empty=True)))
        if not valid:
            observe({'event': 'territory_lookup_failed'})
            raise SignInError('Amazon could not determine the account region. Please retry.')
        observe({'event': 'territory_lookup_complete', 'domain': account.get('sidomain'),
                 'language': account.get('lang'), 'locale': account.get('locale'), 'primevideo_site': account.get('pv')})
        authorization = None
        auth_options = {}
        identity = {}

        def website_verified(url, page, jar):
            identity['verified'] = True
            name = page.profile_name.strip() if isinstance(page.profile_name, str) else ''
            if name:
                observe.secret(name)
                identity['name'] = name
            observe({'event': 'website_session_verified', 'profile_name_present': bool(name),
                     'cookie_count': len(jar)})

        if mode == 'device':
            authorization = signin_state.get('_prime_scene_authorization')
            if authorization is None:
                authorization = DeviceAuthorization(account, g.dtid_android, headers, observer=observe)
                signin_state['_prime_scene_authorization'] = authorization
            else:
                authorization.observer = observe
            auth_options.update(entry_url=authorization.entry_url(), completion=authorization.complete)
        else:
            auth_options['success'] = website_verified

        def active_prompt(label, masked):
            if not current_account(profile, generation, allow_signed_out=True):
                return None
            return ask(label, masked)

        def approve(message):
            return (current_account(profile, generation, allow_signed_out=True)
                    and g.dialog.yesno(TITLE, message, yeslabel='Continue', nolabel='Cancel'))

        def choose(message, options):
            if not current_account(profile, generation, allow_signed_out=True):
                return -1
            return g.dialog.select(message, options)

        def captcha(content, extension):
            if not current_account(profile, generation, allow_signed_out=True):
                return None
            return show_security_image(content, extension, profile)

        success = authenticate(
            cookies, active_prompt, observer=observe, headers=headers, state=signin_state,
            approve=approve, choose=choose, captcha=captcha, **auth_options)
        if not success:
            discard_pending(profile)
            observe({'event': 'signin_cancelled', 'stage': 'native_signin', 'challenge_retained': False})
            return False
        if authorization is not None:
            if authorization.registered is None:
                raise SignInError('Amazon has not completed playback authorization. Your saved account was kept.')
            account = authorization.registered
        else:
            # Website login is complete without registering an Android device.
            if not identity.get('verified'):
                raise SignInError('Amazon did not verify the website session. No account was saved.')
            # Compatibility metadata remains available to the playback paths;
            # website credentials are stored only in the full canonical jar.
            account.update(name=identity.get('name') or 'Prime Video', cookie={}, token='',
                           baseurl=ORIGIN, pv=True)
        if settings.multiuser:
            name = g.dialog.input('Name this Smooth Prime account', account['name'])
            if not name:
                discard_pending(profile)
                observe({'event': 'signin_cancelled', 'stage': 'account_name', 'challenge_retained': False})
                return False
            observe.secret(name)
            account['name'] = name
        result = requests.cookies.RequestsCookieJar()
        for cookie in cookies:
            if not cookie.is_expired():
                result.set_cookie(deepcopy(cookie))
        # Persist only after the selected website/device flow verifies success.
        saved_generation = store_account(profile, generation, account, g.addon,
                                         cookie_session=cookies, session_headers=headers)
        if not saved_generation:
            result.clear()
            raise SignInError('This sign-in was canceled by signing out. Start a new sign-in.')
        # Runtime cleanup erases pending challenge references. The Requests jar
        # is already independent, so it survives cleanup with its exact scope.
        discard_pending(profile, clear=False)
        clear_runtime(g)
        generation = saved_generation
        observe.replay_current()
        observe({'event': 'account_storage_complete', 'website_session_persisted': True})
        observe({'event': 'signin_complete', 'authorization_mode': mode,
                 'device_registered': authorization is not None, 'cookie_count': len(result),
                 'server_verification_confirmed': bool(identity.get('verified') or authorization is not None), 'result': 'success'})
        g.dialog.notification(TITLE, 'Signed in and verified by Amazon.', sound=False)
        if retToken and authorization is not None:
            result.clear()
            return {'Authorization': 'Bearer ' + account['token']['access']}
        return result
    except Exception as error:
        if claimed and cookies is not None:
            try:
                pending = retain_challenge(profile, generation, cookies, signin_state)
            except Exception as retention_error:
                observe({'event': 'signin_challenge_retention_failed',
                         'exception': exception_details(retention_error)})
        observe({'event': 'signin_failed', 'exception': exception_details(error),
                 'last_sequence': observe.sequence, 'authorization_mode': mode,
                 'challenge_retained': pending})
        # HTTP errors can embed secret URL parameters; only our own messages are shown.
        if hasattr(error, 'status') and hasattr(error, 'stage'):
            observe({'event': 'http_error', 'status': error.status, 'stage': error.stage})
        message = str(error) if isinstance(error, SignInError) else (
            'Amazon sign-in could not finish (' + type(error).__name__ + '). Your saved account was kept. Please retry.')
        g.dialog.ok(TITLE, message)
        return False
    finally:
        if claimed and not pending:
            discard_pending(profile)
            if cookies is not None:
                cookies.clear()
            signin_state.clear()
        if lock is not None:
            lock.close()
