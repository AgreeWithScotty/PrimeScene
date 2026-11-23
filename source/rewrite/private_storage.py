"""Atomic private files; Windows credentials use current-user DPAPI.

No application encryption key is saved or bundled. POSIX credentials rely on
owner-only permissions; this module does not claim encryption on those systems.
"""
import ctypes
from io import StringIO
import os
from pathlib import Path
import stat
import tempfile

MAGIC=b'SMOOTH-PRIME-DPAPI-1\n'
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
    if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&1024:
        raise ValueError('Private storage requires a regular file')


def private_directory(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&1024:
        raise ValueError('Private storage requires a real directory')
    if os.name!='nt':os.chmod(path,0o700)


def write_secret(path,content):
    path=Path(path)
    if not isinstance(content,bytes) or len(content)>MAX_SECRET:raise ValueError('Invalid private file size')
    encoded=MAGIC+_dpapi(content,True) if os.name=='nt' else content
    write_private(path,encoded)


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
    path=Path(path);_regular(path)
    with path.open('rb') as source:raw=source.read(limit+65537)
    if len(raw)>limit+65536:raise ValueError('Private storage exceeds its size limit')
    if raw.startswith(MAGIC):
        if os.name!='nt':raise ValueError('This saved Windows session must be signed in again on this platform')
        content=_dpapi(raw[len(MAGIC):],False)
    else:
        if len(raw)>limit:raise ValueError('Private storage exceeds its size limit')
        content=raw
        if os.name=='nt':write_secret(path,content)
    if len(content)>limit:raise ValueError('Decoded private storage exceeds its size limit')
    if os.name!='nt':os.chmod(path,0o600)
    return content


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
            with path.open('rb') as source:
                source.seek(max(0,path.stat().st_size-limit//2));tail=source.read(limit//2)
            tail=tail.partition(b'\n')[2]
            write_private(path,b'{"event":"diagnostic_history_trimmed","result":"retention_limit"}\n'+tail)
    flags=os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0)
    descriptor=os.open(str(path),flags,0o600)
    with os.fdopen(descriptor,'a',encoding='utf-8') as output:
        if os.name!='nt':os.fchmod(output.fileno(),0o600)
        output.write(line);output.flush()
