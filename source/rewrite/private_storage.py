"""Atomic credentials: Windows DPAPI, native vault references or session-only RAM."""
import base64
import ctypes
import hashlib
import hmac
from io import StringIO
import json
import os
from pathlib import Path
from secrets import token_hex
import stat
import tempfile
from .vault_errors import VaultError,VaultMissing,VaultCleanupRequired,VaultUnavailable

MAGIC=b'SMOOTH-PRIME-DPAPI-1\n'
REFERENCE=b'SMOOTH-PRIME-VAULT-1\n'
REGISTRY='credential-vault.json'
MAX_SECRET=2*1024*1024


def _dpapi(content,protect):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(content)
    source=Blob(len(content),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    function=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,
        ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    # UI_FORBIDDEN only: never use LOCAL_MACHINE (which permits other users).
    try:
        if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
            raise OSError(ctypes.get_last_error(),'Windows credential protection failed')
        return ctypes.string_at(target.data,target.size)
    finally:
        if target.data:
            ctypes.memset(target.data,0,target.size)
            kernel.LocalFree(target.data)
        ctypes.memset(buffer,0,len(buffer))


def _regular(path):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&1024
        or info.st_nlink!=1 or os.name!='nt' and info.st_uid!=os.geteuid()):
        raise ValueError('Private storage requires a regular file')
    return info


def private_directory(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    info=path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&1024
        or os.name!='nt' and info.st_uid!=os.geteuid()):
        raise ValueError('Private storage requires a real directory')
    if os.name!='nt':os.chmod(path,0o700)


def _open_private(path):
    """Read the checked file itself; never follow a substituted link/FIFO."""
    path=Path(path);private_directory(path.parent);expected=_regular(path)
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0)
    descriptor=os.open(str(path),flags)
    try:
        actual=os.fstat(descriptor)
        if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink!=1
            or (actual.st_dev,actual.st_ino)!=(expected.st_dev,expected.st_ino)
            or os.name!='nt' and actual.st_uid!=os.geteuid()):
            raise ValueError('Private file changed while opening')
        if os.name!='nt':os.fchmod(descriptor,0o600)
        result=os.fdopen(descriptor,'rb');descriptor=None
        return result
    finally:
        if descriptor is not None:os.close(descriptor)


def write_secret(path,content):
    path=Path(path)
    if not isinstance(content,bytes) or len(content)>MAX_SECRET:raise ValueError('Invalid private file size')
    if os.name=='nt':write_private(path,MAGIC+_dpapi(content,True));return
    from .credential_service import kodi_request,kodi_discovery
    _vault_write(path,content,kodi_request,kodi_discovery()['instance'])


def _vault_write(path,content,call,instance):
    """Portable reference/journal path, also exercised against the real worker."""
    path=Path(path)
    if not isinstance(content,bytes) or len(content)>MAX_SECRET:raise ValueError('Invalid private file size')
    root,slot=_credential_path(path);scope=_scope(root)
    previous=None
    if path.exists():
        with _open_private(path) as source:old=source.read(MAX_SECRET+65537)
        if old.startswith(REFERENCE):previous=_reference(old,scope,slot)
        elif old.startswith(MAGIC):raise VaultUnavailable('windows_session_requires_new_signin')
    selected=previous or call({'action':'choose','namespace':scope})
    ref=previous or {'backend':selected['backend'],'entry':token_hex(16),'scope':scope,'slot':slot,
        'instance':selected['instance'] if selected['backend']=='session' else ''}
    if ref['backend']=='session' and ref['instance']!=instance:
        raise VaultMissing('session_credentials_expired')
    index=_index(root);key=ref['entry'];index[key]=dict(ref,pending=True)
    _save_index(root,index)
    try:
        result=call({'action':'put','namespace':scope,'backend':ref['backend'],'entry':key,
            'value':base64.b64encode(content).decode('ascii')})
        if not hmac.compare_digest(result['sha256'],hashlib.sha256(content).hexdigest()):
            raise VaultUnavailable('vault_write_verification_failed')
        if ref['backend']=='session' and result['instance']!=ref['instance']:raise VaultMissing('session_credentials_expired')
        write_private(path,REFERENCE+json.dumps(ref,separators=(',',':')).encode('ascii'))
        index[key]=dict(ref,pending=False);_save_index(root,index)
    except Exception:
        # An existing value may still be valid. Keep its journal/reference so
        # sign-out can retry deletion; never turn it into an untracked OS item.
        raise


def write_private(path,encoded):
    path=Path(path);private_directory(path.parent)
    if path.exists() or path.is_symlink():_regular(path)
    descriptor,temporary=tempfile.mkstemp(prefix='.private-',dir=str(path.parent))
    try:
        with os.fdopen(descriptor,'wb') as output:
            if os.name!='nt':os.fchmod(output.fileno(),0o600)
            output.write(encoded);output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        try:os.unlink(temporary)
        except FileNotFoundError:pass


def read_secret(path,limit=MAX_SECRET):
    path=Path(path)
    with _open_private(path) as source:raw=source.read(limit+65537)
    if len(raw)>limit and not raw.startswith((MAGIC,REFERENCE)):
        _discard_unprotected(path,ValueError('Invalid legacy credential size'))
    if len(raw)>limit+65536:raise ValueError('Private storage exceeds its size limit')
    if raw.startswith(REFERENCE):
        if os.name=='nt':raise VaultMissing('platform_session_requires_new_signin')
        from .credential_service import kodi_request,kodi_discovery
        content=_vault_read(path,raw,kodi_request,kodi_discovery()['instance'])
    elif raw.startswith(MAGIC):
        if os.name!='nt':raise VaultMissing('windows_session_requires_new_signin')
        content=_dpapi(raw[len(MAGIC):],False)
    else:
        if len(raw)>limit:raise ValueError('Private storage exceeds its size limit')
        content=raw
        # An unsuccessful upgrade must not leave a plaintext credential behind.
        # Native deletion journals remain available for complete sign-out/retry.
        try:write_secret(path,content)
        except Exception as error:
            _discard_unprotected(path,error)
    if len(content)>limit:raise ValueError('Decoded private storage exceeds its size limit')
    if os.name!='nt':os.chmod(path,0o600)
    return content


def _discard_unprotected(path,error):
    try:
        with _open_private(path) as source:prefix=source.read(max(len(MAGIC),len(REFERENCE)))
        if not prefix.startswith((MAGIC,REFERENCE)):path.unlink()
    except FileNotFoundError:pass
    except Exception as cleanup:
        raise VaultCleanupRequired('legacy_plaintext_cleanup_failed') from cleanup
    raise VaultMissing('legacy_credential_protection_failed_new_signin') from error


def _vault_read(path,raw,call,instance):
    root,slot=_credential_path(path);scope=_scope(root);ref=_reference(raw,scope,slot)
    index=_index(root)
    if ref['entry'] not in index:
        index[ref['entry']]=dict(ref,pending=False);_save_index(root,index)
    if index[ref['entry']]['pending']:raise VaultCleanupRequired('vault_cleanup_incomplete')
    if ref['backend']=='session' and ref['instance']!=instance:raise VaultMissing('session_credentials_expired')
    result=call({'action':'get','namespace':scope,'backend':ref['backend'],'entry':ref['entry']})
    return base64.b64decode(result['value'],validate=True)


def _credential_path(path):
    path=Path(path)
    if path.name=='accounts.lst' and path.parent.name=='config':return path.parent.parent,'account'
    if path.name=='prime-video-session.cookies':return path.parent,'cookies'
    raise VaultUnavailable('unsupported_credential_path')


def _scope(root):return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode('utf-8')).hexdigest()


def _reference(raw,scope,slot):
    if len(raw)>4096:raise VaultUnavailable('vault_reference_invalid')
    try:ref=json.loads(raw[len(REFERENCE):])
    except ValueError as error:raise VaultUnavailable('vault_reference_invalid') from error
    import re
    if (not isinstance(ref,dict) or set(ref)!={'backend','entry','scope','slot','instance'}
        or ref['backend'] not in ('session','macos_keychain','linux_secret_service')
        or not isinstance(ref['entry'],str) or not re.fullmatch('[a-f0-9]{32}',ref['entry'])
        or not isinstance(ref['scope'],str) or not re.fullmatch('[a-f0-9]{64}',ref['scope']) or ref['slot']!=slot
        or (ref['backend']=='session' and not re.fullmatch('[a-f0-9]{32}',str(ref['instance'])))
        or (ref['backend']!='session' and ref['instance']!='')):
        raise VaultUnavailable('vault_reference_invalid')
    if ref['scope']!=scope:raise VaultMissing('credential_profile_changed')
    return ref


def _index(root):
    path=Path(root)/REGISTRY
    if not path.exists():return {}
    with _open_private(path) as source:raw=source.read(32769)
    if len(raw)>32768:raise VaultUnavailable('vault_journal_limit')
    try:index=json.loads(raw)
    except ValueError as error:raise VaultUnavailable('vault_journal_invalid') from error
    if not isinstance(index,dict) or len(index)>64:raise VaultUnavailable('vault_journal_limit')
    for key,item in index.items():
        if not isinstance(item,dict) or set(item)!={'backend','entry','scope','slot','instance','pending'}:
            raise VaultUnavailable('vault_journal_invalid')
        _reference(REFERENCE+json.dumps({k:v for k,v in item.items() if k!='pending'}).encode(),item['scope'],item['slot'])
        if key!=item['entry'] or item['slot'] not in ('account','cookies') or type(item['pending']) is not bool:
            raise VaultUnavailable('vault_journal_invalid')
    return index


def _save_index(root,index):
    if len(index)>64:raise VaultUnavailable('vault_journal_limit')
    write_private(Path(root)/REGISTRY,json.dumps(index,separators=(',',':')).encode('ascii'))


def pending_cleanup(root):return any(item['pending'] for item in _index(root).values())


def storage_status(root):
    if os.name=='nt':return {'backends':['windows_current_user_dpapi'],'persistent_credentials':True}
    names=sorted({item['backend'] for item in _index(root).values()})
    return {'backends':names,'persistent_credentials':bool(names) and 'session' not in names}


def legacy_credentials_remaining(root):
    count=0
    for relative in ('config/accounts.lst','prime-video-session.cookies'):
        path=Path(root)/relative
        if path.is_file():
            with _open_private(path) as source:prefix=source.read(64)
            count+=not prefix.startswith((MAGIC,REFERENCE))
    return count


def purge_credentials(root,observer=None):
    """Keep failed deletions in a neutral retry journal until verified removed."""
    if not _index(root):return
    if os.name=='nt':
        # Imported non-Windows references cannot refer to a Windows DPAPI item.
        (Path(root)/REGISTRY).unlink(missing_ok=True)
        if observer:observer({'event':'foreign_vault_references_discarded','stage':'vault_cleanup','result':'success'})
        return
    from .credential_service import kodi_request,kodi_discovery
    _purge_vault(root,kodi_request,kodi_discovery()['instance'],observer)


def _purge_vault(root,call,instance,observer=None):
    index=_index(root);failures=[]
    for key,item in list(index.items()):
        try:
            if item['backend']!='session' or item['instance']==instance:
                call({'action':'delete','namespace':item['scope'],'backend':item['backend'],'entry':key})
            index.pop(key);_save_index(root,index)
            if observer:observer({'event':'credential_vault_item_deleted','stage':'vault_cleanup',
                'backend':item['backend'],'result':'success'})
        except Exception as error:
            item['pending']=True;failures.append(item['backend'])
            if observer:
                from .prime_diagnostics import exception_details
                observer({'event':'credential_vault_delete_failed','stage':'vault_cleanup','backend':item['backend'],
                    'exception':exception_details(error),'result':'failure'})
    _save_index(root,index)
    if failures:raise VaultCleanupRequired('vault_cleanup_incomplete')
    call({'action':'clear','namespace':_scope(root)})
    (Path(root)/REGISTRY).unlink(missing_ok=True)


def load_cookie_text(jar,path):
    """Parse LWP in memory: no decrypted temporary cookie file is created."""
    content=read_secret(path).decode('utf-8')
    # FileCookieJar.load delegates to this same standard-library LWP parser.
    jar._really_load(StringIO(content),str(path),True,False)


def append_report(path,line,limit=8*1024*1024):
    """Bound diagnostics without creating account-bearing rotated archives."""
    path=Path(path);private_directory(path.parent)
    if path.exists():
        _regular(path)
        if path.stat().st_size>limit:
            with _open_private(path) as source:
                source.seek(max(0,path.stat().st_size-limit//2));tail=source.read(limit//2)
            tail=tail.partition(b'\n')[2]
            write_private(path,b'{"event":"diagnostic_history_trimmed","result":"retention_limit"}\n'+tail)
    flags=os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0)
    descriptor=os.open(str(path),flags,0o600)
    with os.fdopen(descriptor,'a',encoding='utf-8') as output:
        if os.name!='nt':os.fchmod(output.fileno(),0o600)
        output.write(line);output.flush()
