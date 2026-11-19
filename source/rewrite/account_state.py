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
                 'signin-lock.db', 'signin-lock.db-journal'}


@contextmanager
def state_db(profile):
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(profile / STATE_FILE), timeout=20)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS session (id INTEGER PRIMARY KEY, epoch TEXT, signed_out INTEGER)')
        connection.execute('INSERT OR IGNORE INTO session VALUES (1, ?, 0)', (secrets.token_hex(16),))
        yield connection
        connection.commit()
    finally:
        connection.close()


def account_epoch(profile):
    with state_db(profile) as db:
        return db.execute('SELECT epoch FROM session WHERE id=1').fetchone()[0]


def is_signed_out(profile):
    with state_db(profile) as db:
        return bool(db.execute('SELECT signed_out FROM session WHERE id=1').fetchone()[0])


def current_account(profile, expected, allow_signed_out=False):
    with state_db(profile) as db:
        epoch, signed_out = db.execute('SELECT epoch, signed_out FROM session WHERE id=1').fetchone()
        return epoch == expected and (allow_signed_out or not signed_out)


def persist_if_current(profile, expected, callback, allow_signed_out=False):
    with state_db(profile) as db:
        epoch, signed_out = db.execute('SELECT epoch, signed_out FROM session WHERE id=1').fetchone()
        if epoch != expected or (signed_out and not allow_signed_out):
            return False
        callback()
        return True


def store_account(profile, expected, account, addon):
    """Commit a fully authorized account; a signed-out attempt cannot restore it."""
    with state_db(profile) as db:
        epoch = db.execute('SELECT epoch FROM session WHERE id=1').fetchone()[0]
        if epoch != expected:
            return False
        next_epoch = secrets.token_hex(16)
        if (Path(profile) / 'config/accounts.lst').exists():
            clear_profile(profile)
        config = Path(profile) / 'config'
        config.mkdir(parents=True, exist_ok=True)
        target = config / 'accounts.lst'
        temporary = config / 'accounts.new'
        temporary.write_text(json.dumps([account]), encoding='utf-8')
        temporary.replace(target)
        addon.setSetting('login_acc', account['name'])
        db.execute('UPDATE session SET epoch=?, signed_out=0 WHERE id=1', (next_epoch,))
    # Let this invocation's config helpers write in the newly authorized session.
    module = sys.modules.get(__package__ + '.configs')
    if module is not None:
        module._prime_scene_epoch = next_epoch
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
        raise ValueError('Refusing to remove a file outside the Prime Scene profile')
    if path.is_dir():
        for child in path.iterdir():
            _remove_child(child, root)
        path.rmdir()
    else:
        path.unlink()


def clear_profile(profile):
    root = Path(profile).resolve()
    if root.name != ADDON_ID or root == root.parent:
        raise ValueError('The Prime Scene profile path is invalid')
    # This loopback address belongs to the running playback service, not to an
    # Amazon account. Keep it usable after signing into a different account.
    proxy_path = root / 'config/proxyaddress'
    proxy = proxy_path.read_text(encoding='utf-8') if proxy_path.is_file() else ''
    proxy = proxy if re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}', proxy) else ''
    failures = []
    for child in root.iterdir():
        if child.name not in NEUTRAL_FILES:
            try:
                _remove_child(child, root)
            except OSError:
                failures.append(child.name)
    if proxy:
        proxy_path.parent.mkdir(parents=True, exist_ok=True)
        proxy_path.write_text(proxy, encoding='utf-8')
    if failures:
        raise RuntimeError('Signed out, but some local account files are still in use. Close Prime Scene and retry cleanup.')


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
    network = sys.modules.get(__package__ + '.network')
    if network is not None:
        session = getattr(network, '_session', None)
        if session is not None:
            session.cookies.clear()
            session.close()
        network._session = None
        network._prime_scene_epoch = None
    proxy = sys.modules.get(__package__ + '.proxy')
    if proxy is not None:
        handler = proxy.ProxyHTTPD
        for session in handler.sessions.values():
            session.cookies.clear()
            session.close()
        handler.sessions.clear()
        handler._refused_licenses.clear()
    configs = sys.modules.get(__package__ + '.configs')
    if configs is not None:
        configs._prime_scene_epoch = None
    values = getattr(g, '_globals', {})
    provider = values.get('pv')
    if provider is not None:
        for key, empty in (('_catalog', {}), ('_videodata', {'urn2gti': {}}), ('_prime_scene_details', {})):
            setattr(provider, key, empty)
            setattr(type(provider), key, empty.copy())
        instances = getattr(type(provider), '_instances', {})
        instances.pop(type(provider), None)
    for key in ('pv', 'MarketID', 'BaseUrl', 'ATVUrl', 'UsePrimeVideo', 'deviceID'):
        values.pop(key, None)


def refresh_service_session(g, service):
    epoch = account_epoch(g.DATA_PATH)
    if getattr(service, '_prime_scene_epoch', None) != epoch:
        clear_runtime(g)
        service._prime_scene_epoch = epoch
    return epoch


def sign_out(g, artwork=()):
    import xbmc
    profile = Path(g.DATA_PATH)
    if profile.resolve().name != ADDON_ID:
        raise ValueError('The Prime Scene profile path is invalid')
    # Invalidate other invocations first. Their account/config writes are guarded
    # by this generation and cannot recreate the previous account after cleanup.
    with state_db(profile) as db:
        db.execute('UPDATE session SET epoch=?, signed_out=1 WHERE id=1', (secrets.token_hex(16),))
    provider = getattr(g, '_globals', {}).get('pv')
    urls = set(artwork)
    if provider is not None:
        urls.update(_artwork_urls(getattr(provider, '_catalog', {})))
        urls.update(_artwork_urls(getattr(provider, '_videodata', {})))
    tracked = profile / 'account-artwork.jsonl'
    if tracked.exists():
        for line in tracked.read_text(encoding='utf-8').splitlines():
            try:
                urls.add(json.loads(line))
            except ValueError:
                pass
    player = xbmc.Player()
    if player.isPlaying():
        player.stop()
    clear_runtime(g)
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
                if url in urls or decoded in urls or str(profile) in decoded:
                    removed = json.loads(xbmc.executeJSONRPC(json.dumps({
                        'jsonrpc': '2.0', 'id': 1, 'method': 'Textures.RemoveTexture',
                        'params': {'textureid': texture['textureid']}})))
                    if 'error' in removed:
                        raise RuntimeError('Artwork cache cleanup failed')
    except Exception as error:
        texture_error = error
    # Account-specific settings are cleared in Kodi's in-memory settings too.
    schema = Path(g.PLUGIN_PATH) / 'resources/settings.xml'
    for setting in ET.parse(schema).getroot().iter('setting'):
        if setting.get('type') != 'action' and setting.get('id'):
            g.addon.setSetting(setting.get('id'), setting.findtext('default') or '')
    g.addon.setSetting('login_acc', '')
    g.addon.setSetting('data_source', '0')
    g.addon.setSetting('playmethod', '3')
    clear_profile(profile)
    (profile / 'profile-initialized').write_text('1', encoding='ascii')
    if texture_error is not None:
        raise RuntimeError('Signed out, but Kodi could not clear every cached artwork image.') from None


def remLoginData(info=True):
    from .common import Globals
    sign_out(Globals())
