"""Account generation and complete local sign-out for Prime Scene."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import re
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ADDON_ID = 'plugin.video.primeinterface'
STATE_FILE = 'account-state.db'
NEUTRAL_FILES = {STATE_FILE, STATE_FILE + '-journal', STATE_FILE + '-wal', STATE_FILE + '-shm',
                 'signin-lock.db', 'signin-lock.db-journal','credential-vault.json',
                 'observer-service.lock.db','observer-service.lock.db-journal'}


@contextmanager
def state_db(profile):
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(profile / STATE_FILE), timeout=20)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS session (id INTEGER PRIMARY KEY, epoch TEXT, signed_out INTEGER)')
        connection.execute('INSERT OR IGNORE INTO session (id, epoch, signed_out) VALUES (1, ?, 0)', (secrets.token_hex(16),))
        # Leave the three-column session table compatible with older invocations.
        connection.execute('CREATE TABLE IF NOT EXISTS cleanup (id INTEGER PRIMARY KEY, pending INTEGER NOT NULL)')
        connection.execute('INSERT OR IGNORE INTO cleanup VALUES (1, 0)')
        yield connection
        connection.commit()
    finally:
        connection.close()


def read_state(profile):
    """Read account guards without taking a writer lock on every UI/log event."""
    path = Path(profile) / STATE_FILE
    if path.exists():
        connection = sqlite3.connect(str(path), timeout=2)
        try:
            value = connection.execute('SELECT epoch, signed_out FROM session WHERE id=1').fetchone()
            if value is not None:
                return value
        except sqlite3.OperationalError as error:
            if 'no such table' not in str(error):
                raise
        finally:
            connection.close()
    with state_db(profile) as db:
        return db.execute('SELECT epoch, signed_out FROM session WHERE id=1').fetchone()


def account_epoch(profile):
    return read_state(profile)[0]


def is_signed_out(profile):
    return bool(read_state(profile)[1])


def needs_cleanup(profile):
    with state_db(profile) as db:
        pending=bool(db.execute('SELECT pending FROM cleanup WHERE id=1').fetchone()[0])
    from .private_storage import pending_cleanup
    return pending or pending_cleanup(profile)


def current_account(profile, expected, allow_signed_out=False):
    epoch, signed_out = read_state(profile)
    return epoch == expected and (allow_signed_out or not signed_out)


def persist_if_current(profile, expected, callback, allow_signed_out=False):
    with state_db(profile) as db:
        epoch, signed_out = db.execute('SELECT epoch, signed_out FROM session WHERE id=1').fetchone()
        if epoch != expected or (signed_out and not allow_signed_out):
            return False
        callback()
        return True


def store_account(profile, expected, account, addon, cookie_session=None, session_headers=None):
    """Commit a fully authorized account; a signed-out attempt cannot restore it."""
    with state_db(profile) as db:
        epoch = db.execute('SELECT epoch FROM session WHERE id=1').fetchone()[0]
        pending = db.execute('SELECT pending FROM cleanup WHERE id=1').fetchone()[0]
        if epoch != expected or pending:
            return False
        next_epoch = secrets.token_hex(16)
        if (Path(profile) / 'config/accounts.lst').exists():
            clear_profile(profile)
        config = Path(profile) / 'config'
        config.mkdir(parents=True, exist_ok=True)
        target = config / 'accounts.lst'
        from .private_storage import write_secret
        write_secret(target,json.dumps([account]).encode('utf-8'))
        if cookie_session is not None:
            from .session_store import COOKIE_FILE, copy_cookie_session
            from .prime_signin import save_cookies
            save_cookies(copy_cookie_session(cookie_session), Path(profile) / COOKIE_FILE)
            (Path(profile) / 'prime-video-session.headers.json').write_text(
                json.dumps(session_headers or {}), encoding='utf-8')
            agent = (session_headers or {}).get('User-Agent')
            if isinstance(agent, str) and agent:
                (config / 'UserAgent').write_text(agent, encoding='utf-8')
        addon.setSetting('login_acc', account['name'])
        db.execute('UPDATE session SET epoch=?, signed_out=0 WHERE id=1', (next_epoch,))
    return next_epoch


def _remove_child(path, root):
    # Never follow a symbolic link or Windows junction outside this add-on.
    attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    if path.is_symlink() or attributes & 1024:
        if path.is_dir() and not path.is_symlink():
            os.rmdir(str(path))
        else:
            path.unlink()
        return
    if root not in path.resolve().parents:
        raise ValueError('Refusing to remove a file outside the Smooth Prime profile')
    if path.is_dir():
        for child in path.iterdir():
            _remove_child(child, root)
        path.rmdir()
    else:
        path.unlink()


def clear_profile(profile, observer=None):
    root = Path(profile).resolve()
    if root.name != ADDON_ID or root == root.parent:
        raise ValueError('The Smooth Prime profile path is invalid')
    failures = []
    from .private_storage import purge_credentials
    try:purge_credentials(root,observer)
    except Exception as error:
        failures.append('credential_vault')
        if observer is not None:
            from .prime_diagnostics import exception_details
            observer({'event':'credential_vault_purge_failed','stage':'vault_cleanup',
                'exception':exception_details(error),'result':'failure'})
    for child in list(root.iterdir()):
        if child.name not in NEUTRAL_FILES:
            try:
                _remove_child(child, root)
            except OSError as error:
                failures.append(child.name)
                if observer is not None:
                    from .prime_diagnostics import exception_details
                    category = {'config': 'credentials_and_configuration', 'navigation-cache': 'catalog_cache',
                        'title-art': 'title_art', 'image-cache': 'artwork', 'log': 'account_logs',
                        'prime-video-session.cookies': 'website_cookies', 'settings.xml': 'account_settings'}.get(child.name, 'account_file')
                    observer({'event': 'profile_entry_purge_failed', 'stage': 'purge_account_profile',
                              'category': category, 'exception': exception_details(error), 'result': 'failure'})
    (root / 'log').mkdir(exist_ok=True)
    if failures:
        if 'credential_vault' in failures:
            raise RuntimeError('Signed out, but secure vault cleanup could not finish. Unlock your system keyring if it is locked, then use Finish sign out. Diagnostics records the failed stage.')
        raise RuntimeError('Signed out, but some local account files are still in use. Close Smooth Prime and retry cleanup.')


def _artwork_urls(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _artwork_urls(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _artwork_urls(child)
    elif isinstance(value, str) and value.startswith(('https://', 'image://')):
        yield value


def clear_runtime(g):
    repository=sys.modules.get(__package__+'.accounts')
    if repository is not None:repository.reset_runtime(g.DATA_PATH)
    sessions=sys.modules.get(__package__+'.session_store')
    if sessions is not None:sessions.clear_all_pending()
    # In-flight native network/media requests retain their captured epoch.
    # They cannot adopt the next account; each request and player tick checks it.


def refresh_service_session(g, service):
    epoch = account_epoch(g.DATA_PATH)
    if getattr(service, '_prime_scene_epoch', None) != epoch:
        clear_runtime(g)
        service._prime_scene_epoch = epoch
    import xbmcgui
    xbmcgui.Window(10000).setProperty('smoothprime.service_account_epoch', epoch)
    return epoch


def _sign_out(g, artwork=(), report=None, before_cleanup=None):
    import xbmc
    from time import monotonic
    from .prime_diagnostics import exception_details
    report = report or (lambda event: None)
    profile = Path(g.DATA_PATH)
    if profile.resolve().name != ADDON_ID:
        raise ValueError('The Smooth Prime profile path is invalid')
    # Invalidate other invocations first. Their account/config writes are guarded
    # by this generation and cannot recreate the previous account after cleanup.
    with state_db(profile) as db:
        db.execute('UPDATE session SET epoch=?, signed_out=1 WHERE id=1', (secrets.token_hex(16),))
        db.execute('UPDATE cleanup SET pending=1 WHERE id=1')
    report({'event': 'signout_stage_complete', 'stage': 'invalidate_account', 'result': 'success'})
    failures = []

    def stage(name, action):
        started = monotonic()
        try:
            result = action()
            report({'event': 'signout_stage_complete', 'stage': name,
                    'elapsed_ms': round((monotonic()-started)*1000), 'result': 'success'})
            return result
        except Exception as error:
            failures.append(name)
            report({'event': 'signout_stage_failed', 'stage': name, 'exception': exception_details(error),
                    'elapsed_ms': round((monotonic()-started)*1000), 'result': 'failure'})

    # Capture texture references before notification clears other live copies.
    urls = set(artwork)

    def notify():
        reply = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1,
            'method': 'JSONRPC.NotifyAll', 'params': {'sender': ADDON_ID, 'message': 'account_invalidated'}})))
        if 'error' in reply:
            raise RuntimeError('Kodi did not deliver account invalidation')
    stage('notify_active_invocations', notify)
    if before_cleanup is not None:
        stage('stop_interface_workers', before_cleanup)
    tracked = profile / 'account-artwork.jsonl'
    def track():
        if tracked.exists():
            for line in tracked.read_text(encoding='utf-8').splitlines():
                try:
                    value = json.loads(line)
                    if isinstance(value, str):
                        urls.add(value)
                except ValueError:
                    pass
    stage('read_artwork_inventory', track)
    def stop_player():
        player = xbmc.Player()
        if player.isPlaying():
            player.stop()
    stage('stop_playback', stop_player)
    stage('clear_runtime_credentials', lambda: clear_runtime(g))
    # Remove Kodi's cached copies of artwork used by this account as well.
    texture_error = None
    try:
        if urls:
            reply = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'Textures.GetTextures', 'params': {'properties': ['url']}})))
            if 'error' in reply:
                raise RuntimeError('Artwork cache cleanup failed')
            for texture in reply.get('result', {}).get('textures', []):
                url = texture.get('url', '')
                decoded = unquote(url[8:].rstrip('/')) if url.startswith('image://') else url
                if (url in urls or decoded in urls
                        or str(profile).replace('\\', '/').casefold() in decoded.replace('\\', '/').casefold()):
                    removed = json.loads(xbmc.executeJSONRPC(json.dumps({
                        'jsonrpc': '2.0', 'id': 1, 'method': 'Textures.RemoveTexture',
                        'params': {'textureid': texture['textureid']}})))
                    if 'error' in removed:
                        raise RuntimeError('Artwork cache cleanup failed')
    except Exception as error:
        texture_error = error
    def settings():
        schema = Path(g.PLUGIN_PATH) / 'resources/settings.xml'
        for setting in ET.parse(schema).getroot().iter('setting'):
            if setting.get('type') != 'action' and setting.get('id'):
                g.addon.setSetting(setting.get('id'), setting.findtext('default') or '')
        g.addon.setSetting('login_acc', '')
        g.addon.setSetting('data_source', '0')
        g.addon.setSetting('playmethod', '3')
    stage('clear_account_settings', settings)
    # Purge runs even when a preceding cleanup stage failed.
    stage('purge_account_profile', lambda: clear_profile(profile, report))
    (profile / 'profile-initialized').write_text('1', encoding='ascii')
    if texture_error is not None:
        failures.append('kodi_artwork')
        report({'event': 'signout_stage_failed', 'stage': 'kodi_artwork',
                'exception': exception_details(texture_error), 'result': 'failure'})
    allowed = NEUTRAL_FILES | {'profile-initialized'}
    leftovers = []
    for path in profile.iterdir():
        if path.name in allowed:
            continue
        if path.name == 'log' and path.is_dir() and not any(path.iterdir()):
            continue
        if path.name == 'prime-scene-diagnostics.jsonl' and path.is_file():
            try:
                entries = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
                if entries and all(entry.get('operation') == 'signout' and entry.get('attempt') == getattr(report, 'attempt', None)
                                   for entry in entries):
                    continue
            except (OSError, ValueError):
                pass
        leftovers.append(path)
    account_files = sum(path.is_file() for path in leftovers)
    report({'event': 'signout_purge_audit', 'stage': 'verify_local_state', 'account_files_remaining': account_files,
            'account_entries_remaining': len(leftovers), 'saved_account_setting_present': bool(g.addon.getSetting('login_acc')),
            'canonical_cookie_file_present': (profile/'prime-video-session.cookies').exists(),
            'result': 'success' if not leftovers and not g.addon.getSetting('login_acc') else 'failure'})
    import xbmcgui
    report({'event': 'signout_service_state', 'stage': 'verify_runtime_invalidation',
        'service_epoch_matches': xbmcgui.Window(10000).getProperty('smoothprime.service_account_epoch') == account_epoch(profile),
        'native_request_generation_invalidated': True})
    if leftovers or g.addon.getSetting('login_acc'):
        failures.append('verify_local_state')
    if failures:
        raise RuntimeError('Signed out, but cleanup could not finish. Use Finish sign out to retry. Diagnostics records the failed stages.')
    with state_db(profile) as db:
        db.execute('UPDATE cleanup SET pending=0 WHERE id=1')


def sign_out(g, artwork=(), before_cleanup=None):
    from .prime_diagnostics import DiagnosticLog, exception_details
    report = DiagnosticLog(g.DATA_PATH, g.addon.getAddonInfo('version'), operation='signout')
    try:
        _sign_out(g, artwork, report=report, before_cleanup=before_cleanup)
    except Exception as error:
        report.replay_current(replace=True)
        report({'event': 'signout_failed', 'exception': exception_details(error), 'result': 'failure'})
        raise
    else:
        # This records only the cleanup result after old account reports are erased.
        report.replay_current(replace=True)
        report({'event': 'signout_complete', 'account_data_cleared': True, 'result': 'success'})
        g.dialog.notification('Smooth Prime', 'Signed out. Local account data cleared.', sound=False)


def remLoginData(info=True):
    from .account_runtime import AccountRuntime
    sign_out(AccountRuntime())
