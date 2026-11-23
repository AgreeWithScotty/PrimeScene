"""Single-account repository shared by native sign-in and backend callers.

The on-disk metadata filename remains compatible with existing installations;
website credentials are read/written exclusively through session_store.
"""
from copy import deepcopy
import json
from pathlib import Path
from threading import local

from .account_state import account_epoch, current_account, persist_if_current
from .prime_diagnostics import DiagnosticLog, exception_details

EMPTY = dict(name='', atvurl='', baseurl='', pv=False, mid='', cookie={}, token='',
             deviceid='', sidomain='', locale='', lang='')
_threads = local()


def reset_runtime(profile):
    getattr(_threads, 'epochs', {}).pop(str(profile), None)


def _context():
    from .account_runtime import AccountRuntime
    if not hasattr(_threads, 'runtime'):
        _threads.runtime = AccountRuntime()
    g = _threads.runtime
    path = str(g.DATA_PATH)
    if not hasattr(_threads, 'epochs'):
        _threads.epochs = {}
    if path not in _threads.epochs:
        _threads.epochs[path] = account_epoch(path)
    epoch = _threads.epochs[path]
    report = DiagnosticLog(path, g.addon.getAddonInfo('version'),
        active=lambda: current_account(path, epoch), operation='account_repository')
    return g, epoch, report


def _read(path, epoch):
    if not current_account(path, epoch):
        return []
    filename = Path(path)/'config/accounts.lst'
    try:
        if filename.stat().st_size > 512*1024:
            raise ValueError('Account metadata exceeds its size limit')
        data = json.loads(filename.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    if not isinstance(data, list) or len(data) > 10:
        raise ValueError('Account metadata has an invalid structure')
    records = []
    for value in data:
        if not isinstance(value, dict) or not isinstance(value.get('name'), str) or not value['name']:
            raise ValueError('Account metadata omitted its display name')
        record = deepcopy(EMPTY)
        record.update({key: value[key] for key in EMPTY if key in value and key != 'cookie'})
        # Never expose or reuse the former unscoped name/value cookie copy.
        record['cookie'] = {}
        records.append(record)
    return records if current_account(path, epoch) else []


def loadUsers():
    g, epoch, report = _context()
    try:
        records = _read(g.DATA_PATH, epoch)
        selected = g.addon.getSetting('login_acc')
        return [record for record in records if record['name'] == selected]
    except Exception as error:
        report({'event': 'account_read_failed', 'stage': 'metadata_read',
                'exception': exception_details(error), 'result': 'failure'})
        raise


def loadUser(key='', empty=False, cachedUsers=None):
    g, epoch, _ = _context()
    record = deepcopy(EMPTY)
    if not empty and current_account(g.DATA_PATH, epoch):
        records = loadUsers() if cachedUsers is None else cachedUsers
        selected = g.addon.getSetting('login_acc')
        active = next((r for r in records if r.get('name') == selected), None)
        if active:
            record.update(deepcopy(active))
            record['cookie'] = {}
    return record if not key else record.get(key, '')


def _write(record, g, epoch, report, operation):
    if not isinstance(record.get('name'), str) or not record['name']:
        raise ValueError('An account display name is required')
    clean = {key: deepcopy(record.get(key, value)) for key, value in EMPTY.items()}
    clean['cookie'] = {}
    def commit():
        directory = Path(g.DATA_PATH)/'config'
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory/'accounts.new'
        temporary.write_text(json.dumps([clean], ensure_ascii=True), encoding='utf-8')
        temporary.replace(directory/'accounts.lst')
        g.addon.setSetting('login_acc', clean['name'])
    stored = persist_if_current(g.DATA_PATH, epoch, commit)
    report({'event': 'account_metadata_updated', 'stage': operation,
            'result': 'success' if stored else 'cancelled', 'flat_cookie_storage': False})
    return stored


def updateUser(key, value, expected_epoch=None):
    if key not in EMPTY or key == 'cookie':
        raise ValueError('This account field cannot be updated')
    g, epoch, report = _context()
    if expected_epoch is not None and expected_epoch != epoch:
        return False
    if not current_account(g.DATA_PATH, epoch):
        return False
    record = loadUser()
    if not record['name']:
        return False
    record[key] = deepcopy(value)
    return _write(record, g, epoch, report, 'update_'+key)


def saveUserCookies(cookieJar, cachedUsers=None, request_epoch=None):
    from .session_store import persist_session
    g, epoch, report = _context()
    expected = request_epoch if request_epoch is not None else epoch
    if expected != epoch or not current_account(g.DATA_PATH, expected):
        return False
    stored = persist_session(g.DATA_PATH, expected, cookieJar)
    report({'event': 'account_session_refreshed', 'stage': 'canonical_session',
            'cookie_objects': len(cookieJar), 'result': 'success' if stored else 'cancelled'})
    return stored


def removeUser():
    from .account_state import sign_out
    g, _, _ = _context()
    return sign_out(g)


def renameUser():
    g, epoch, report = _context()
    record = loadUser()
    if not record['name']:
        return False
    chosen = g.dialog.input('Account display name', record['name'])
    if not chosen:
        report({'event': 'account_rename_cancelled', 'stage': 'rename', 'result': 'cancelled'})
        return False
    record['name'] = chosen.strip()
    return _write(record, g, epoch, report, 'rename')


def switchUser(sel=-1):
    # Internal index-zero calls only select the already active account. An
    # explicit switch starts a fresh native sign-in after the complete purge.
    if sel >= 0:
        return False
    g, _, report = _context()
    choice = g.dialog.select('Switch Amazon account', ['Sign out and sign in', 'Cancel'])
    if choice != 0:
        report({'event': 'account_switch_cancelled', 'stage': 'switch', 'result': 'cancelled'})
        return False
    from .account_state import sign_out
    from .prime_login import LogIn
    sign_out(g)
    return LogIn() is not False
