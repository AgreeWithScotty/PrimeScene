"""Native Kodi prompts and persistence for Prime Scene account authorization."""
import json
from datetime import datetime, timezone
import secrets
from http.cookiejar import CookieJar
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
import xbmc
import xbmcgui

from .common import Globals, Settings
from .prime_device import DeviceAuthorization
from .prime_signin import SignInError, authenticate
from .account_state import account_epoch, clear_runtime, current_account, store_account


TITLE = 'Prime Scene'


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
    from .login import getTerritory
    from .users import loadUser

    g, settings = Globals(), Settings()
    profile = Path(g.DATA_PATH)
    profile.mkdir(parents=True, exist_ok=True)
    generation = account_epoch(profile)
    attempt = secrets.token_hex(8)
    # An OS-backed exclusive lock cannot survive the process holding it.
    # Kodi properties would not provide an atomic claim across script invocations.
    import sqlite3
    lock = sqlite3.connect(str(profile / 'signin-lock.db'), timeout=0)
    try:
        try:
            lock.execute('BEGIN EXCLUSIVE')
        except sqlite3.OperationalError:
            g.dialog.ok(TITLE, 'Another Prime Scene sign-in is already open.')
            return False

        def observe(event):
            # The transport emits booleans, counts and hostnames, never page bodies.
            try:
                if not current_account(profile, generation, allow_signed_out=True):
                    return
                with (profile / 'prime-scene-signin.jsonl').open('a', encoding='utf-8') as report:
                    report.write(json.dumps(dict(event, attempt=attempt,
                                                 time=datetime.now(timezone.utc).isoformat())) + '\n')
            except OSError:
                pass

        observe({'event': 'signin_started'})
        account, valid = getTerritory(dict(loadUser(empty=True)))
        if not valid:
            raise SignInError('Amazon could not determine the account region. Please retry.')
        authorization = DeviceAuthorization(account, g.dtid_android, g.headers_android)
        cookies = CookieJar()
        success = authenticate(
            cookies, ask, observer=observe, entry_url=authorization.entry_url(),
            completion=authorization.complete, headers=g.headers_android,
            approve=lambda message: g.dialog.yesno(TITLE, message, yeslabel='Continue', nolabel='Cancel'),
            choose=lambda message, options: g.dialog.select(message, options),
            captcha=lambda content, extension: show_security_image(content, extension, profile))
        if not success:
            observe({'event': 'signin_cancelled'})
            return False
        if authorization.registered is None:
            raise SignInError('Amazon has not completed playback authorization. Your saved account was kept.')
        account = authorization.registered
        if settings.multiuser:
            name = g.dialog.input('Name this Prime Scene account', account['name'])
            if not name:
                return False
            account['name'] = name
        # Persist only after all requested verification and registration succeeds.
        saved_generation = store_account(profile, generation, account, g.addon)
        if not saved_generation:
            raise SignInError('This sign-in was canceled by signing out. Start a new sign-in.')
        clear_runtime(g)
        generation = saved_generation
        observe({'event': 'signin_complete', 'device_registered': True})
        if retToken:
            return {'Authorization': 'Bearer ' + account['token']['access']}
        jar = requests.cookies.RequestsCookieJar()
        jar.update(account['cookie'])
        return jar
    except Exception as error:
        # HTTP errors can embed secret URL parameters; only our own messages are shown.
        if hasattr(error, 'status') and hasattr(error, 'stage'):
            observe({'event': 'http_error', 'status': error.status, 'stage': error.stage})
        message = str(error) if isinstance(error, SignInError) else (
            'Amazon sign-in could not finish (' + type(error).__name__ + '). Your saved account was kept. Please retry.')
        g.dialog.ok(TITLE, message)
        return False
    finally:
        if 'cookies' in locals():
            cookies.clear()
        lock.close()
