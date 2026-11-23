"""OS-managed credential records. Native APIs execute in the vault service worker.

No subprocesses, bundled keys, plaintext file keyrings or deprecated macOS
SecKeychain password functions. Android without a verified bridge is unavailable.
"""
import base64
import ctypes as C
from ctypes.util import find_library
import os
import sys
from threading import Timer
from .vault_errors import VaultUnavailable,VaultMissing

SERVICE=b'org.smoothprime.credentials'


def platform_name():
    if hasattr(sys,'getandroidapilevel') or os.environ.get('KODI_ANDROID_APK'):return 'android'
    return 'macos' if sys.platform=='darwin' else 'windows' if os.name=='nt' else 'linux'


class MacKeychain:
    name='macos_keychain'
    def __init__(self):
        self.sec=C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        C.CDLL('/System/Library/Frameworks/LocalAuthentication.framework/LocalAuthentication')
        self.objc=C.CDLL('/usr/lib/libobjc.A.dylib')
        self.objc.objc_getClass.argtypes=[C.c_char_p];self.objc.objc_getClass.restype=C.c_void_p
        self.objc.sel_registerName.argtypes=[C.c_char_p];self.objc.sel_registerName.restype=C.c_void_p
        self.send=C.CFUNCTYPE(C.c_void_p,C.c_void_p,C.c_void_p)(('objc_msgSend',self.objc))
        self.send_bool=C.CFUNCTYPE(None,C.c_void_p,C.c_void_p,C.c_bool)(('objc_msgSend',self.objc))
        self.cf.CFRelease.argtypes=[C.c_void_p];self.cf.CFRelease.restype=None
        self.cf.CFStringCreateWithCString.argtypes=[C.c_void_p,C.c_char_p,C.c_uint32];self.cf.CFStringCreateWithCString.restype=C.c_void_p
        self.cf.CFDataCreate.argtypes=[C.c_void_p,C.c_void_p,C.c_long];self.cf.CFDataCreate.restype=C.c_void_p
        self.cf.CFDataGetLength.argtypes=[C.c_void_p];self.cf.CFDataGetLength.restype=C.c_long
        self.cf.CFDataGetBytePtr.argtypes=[C.c_void_p];self.cf.CFDataGetBytePtr.restype=C.c_void_p
        self.cf.CFGetTypeID.argtypes=[C.c_void_p];self.cf.CFGetTypeID.restype=C.c_ulong
        self.cf.CFDataGetTypeID.argtypes=[];self.cf.CFDataGetTypeID.restype=C.c_ulong
        self.cf.CFDictionaryCreate.argtypes=[C.c_void_p,C.POINTER(C.c_void_p),C.POINTER(C.c_void_p),C.c_long,C.c_void_p,C.c_void_p]
        self.cf.CFDictionaryCreate.restype=C.c_void_p
        self.sec.SecItemCopyMatching.argtypes=[C.c_void_p,C.POINTER(C.c_void_p)];self.sec.SecItemCopyMatching.restype=C.c_int32
        self.sec.SecItemAdd.argtypes=[C.c_void_p,C.c_void_p];self.sec.SecItemAdd.restype=C.c_int32
        self.sec.SecItemUpdate.argtypes=[C.c_void_p,C.c_void_p];self.sec.SecItemUpdate.restype=C.c_int32
        self.sec.SecItemDelete.argtypes=[C.c_void_p];self.sec.SecItemDelete.restype=C.c_int32
        self.keys=C.addressof(C.c_byte.in_dll(self.cf,'kCFTypeDictionaryKeyCallBacks'))
        self.values=C.addressof(C.c_byte.in_dll(self.cf,'kCFTypeDictionaryValueCallBacks'))
    def constant(self,name):return C.c_void_p.in_dll(self.sec,name).value
    def string(self,value):return self.cf.CFStringCreateWithCString(None,value.encode('utf-8'),0x08000100)
    def dictionary(self,pairs):
        keys=(C.c_void_p*len(pairs))(*(self.constant(k) for k,_ in pairs))
        values=(C.c_void_p*len(pairs))(*(v for _,v in pairs))
        return self.cf.CFDictionaryCreate(None,keys,values,len(pairs),self.keys,self.values)
    def query(self,namespace,entry,extra=()):
        service=self.string(SERVICE.decode());account=self.string(namespace+':'+entry)
        context=self.send(self.objc.objc_getClass(b'LAContext'),self.objc.sel_registerName(b'new'))
        if not context:
            self.cf.CFRelease(service);self.cf.CFRelease(account);raise VaultUnavailable('keychain_context_unavailable')
        self.send_bool(context,self.objc.sel_registerName(b'setInteractionNotAllowed:'),True)
        pairs=[('kSecClass',self.constant('kSecClassGenericPassword')),('kSecAttrService',service),
            ('kSecAttrAccount',account),('kSecAttrSynchronizable',C.c_void_p.in_dll(self.cf,'kCFBooleanFalse').value),
            ('kSecUseAuthenticationContext',context)]+list(extra)
        try:return self.dictionary(pairs)
        finally:
            self.cf.CFRelease(service);self.cf.CFRelease(account)
            self.send(context,self.objc.sel_registerName(b'release'))
    def get(self,namespace,entry):
        query=self.query(namespace,entry,[('kSecReturnData',C.c_void_p.in_dll(self.cf,'kCFBooleanTrue').value),
            ('kSecMatchLimit',self.constant('kSecMatchLimitOne'))]);result=C.c_void_p()
        try:
            status=self.sec.SecItemCopyMatching(query,C.byref(result))
            if status==-25300:raise VaultMissing('keychain_item_missing',status)
            if status:raise VaultUnavailable('keychain_read_refused',status)
            if self.cf.CFGetTypeID(result)!=self.cf.CFDataGetTypeID():raise VaultUnavailable('keychain_invalid_result')
            size=self.cf.CFDataGetLength(result)
            if not 0<=size<=2*1024*1024:raise VaultUnavailable('keychain_size_limit')
            return C.string_at(self.cf.CFDataGetBytePtr(result),size)
        finally:
            if result:self.cf.CFRelease(result)
            self.cf.CFRelease(query)
    def put(self,namespace,entry,value):
        buffer=C.create_string_buffer(value)
        try:data=self.cf.CFDataCreate(None,C.cast(buffer,C.c_void_p),len(value))
        finally:C.memset(buffer,0,len(buffer))
        query=self.query(namespace,entry)
        attributes=self.dictionary([('kSecValueData',data)])
        try:
            status=self.sec.SecItemUpdate(query,attributes)
            if status==-25300:
                add=self.query(namespace,entry,[('kSecValueData',data)])
                try:status=self.sec.SecItemAdd(add,None)
                finally:self.cf.CFRelease(add)
            if status:raise VaultUnavailable('keychain_write_refused',status)
        finally:
            self.cf.CFRelease(attributes);self.cf.CFRelease(query);self.cf.CFRelease(data)
    def delete(self,namespace,entry):
        query=self.query(namespace,entry)
        try:
            status=self.sec.SecItemDelete(query)
            if status not in (0,-25300):raise VaultUnavailable('keychain_delete_refused',status)
        finally:self.cf.CFRelease(query)
        try:self.get(namespace,entry)
        except VaultMissing:return
        raise VaultUnavailable('keychain_delete_not_confirmed')
    def probe(self):
        try:self.get('capability','probe-no-credential')
        except VaultMissing:return


class _GError(C.Structure):
    _fields_=[('domain',C.c_uint32),('code',C.c_int),('message',C.c_char_p)]


class SecretService:
    name='linux_secret_service'
    def __init__(self):
        if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):raise VaultUnavailable('desktop_keyring_session_missing')
        names=[find_library(name) for name in ('secret-1','gio-2.0','gobject-2.0','glib-2.0')]
        if not all(names):raise VaultUnavailable('libsecret_unavailable')
        self.lib,self.gio,self.obj,self.glib=[C.CDLL(name) for name in names]
        self.error_type=C.POINTER(_GError)
        self.glib.g_error_free.argtypes=[self.error_type];self.glib.g_error_free.restype=None
        self.obj.g_object_unref.argtypes=[C.c_void_p];self.obj.g_object_unref.restype=None
        self.gio.g_cancellable_new.argtypes=[];self.gio.g_cancellable_new.restype=C.c_void_p
        self.gio.g_cancellable_cancel.argtypes=[C.c_void_p];self.gio.g_cancellable_cancel.restype=None
        error=C.POINTER(self.error_type)
        self.lib.secret_collection_for_alias_sync.argtypes=[C.c_void_p,C.c_char_p,C.c_int,C.c_void_p,error]
        self.lib.secret_collection_for_alias_sync.restype=C.c_void_p
        self.lib.secret_collection_get_locked.argtypes=[C.c_void_p];self.lib.secret_collection_get_locked.restype=C.c_int
        self.lib.secret_password_store_sync.argtypes=[C.c_void_p,C.c_char_p,C.c_char_p,C.c_char_p,C.c_void_p,error]
        self.lib.secret_password_store_sync.restype=C.c_int
        self.lib.secret_password_lookup_sync.argtypes=[C.c_void_p,C.c_void_p,error];self.lib.secret_password_lookup_sync.restype=C.c_void_p
        self.lib.secret_password_clear_sync.argtypes=[C.c_void_p,C.c_void_p,error];self.lib.secret_password_clear_sync.restype=C.c_int
        self.lib.secret_password_free.argtypes=[C.c_void_p];self.lib.secret_password_free.restype=None
        # Stable public schema with explicit string attributes; never use an
        # automatic Python keyring backend that might select plaintext storage.
        self.lib.secret_schema_new.argtypes=[C.c_char_p,C.c_int];self.lib.secret_schema_new.restype=C.c_void_p
        self.lib.secret_schema_unref.argtypes=[C.c_void_p];self.lib.secret_schema_unref.restype=None
        self.schema=self.lib.secret_schema_new(SERVICE,0,C.c_char_p(b'namespace'),C.c_int(0),C.c_char_p(b'entry'),C.c_int(0),C.c_void_p())
        if not self.schema:raise VaultUnavailable('secret_schema_unavailable')
    def call(self,function,*args):
        cancel=self.gio.g_cancellable_new();error=self.error_type();timer=Timer(5,self.gio.g_cancellable_cancel,args=(cancel,));timer.daemon=True;timer.start()
        try:
            result=function(*args,cancel,C.byref(error))
            if error:
                code=error.contents.code;self.glib.g_error_free(error)
                raise VaultUnavailable('secret_service_refused',code)
            return result
        finally:
            timer.cancel();timer.join();self.obj.g_object_unref(cancel)
    def probe(self):
        collection=self.call(self.lib.secret_collection_for_alias_sync,None,b'default',0)
        if not collection:raise VaultUnavailable('desktop_keyring_not_configured')
        try:
            if self.lib.secret_collection_get_locked(collection):raise VaultUnavailable('desktop_keyring_locked')
        finally:self.obj.g_object_unref(collection)
    def attributes(self,namespace,entry):return (C.c_char_p(b'namespace'),C.c_char_p(namespace.encode()),C.c_char_p(b'entry'),C.c_char_p(entry.encode()),C.c_void_p())
    def password_call(self,operation,namespace,entry,value=None):
        self.probe();cancel=self.gio.g_cancellable_new();error=self.error_type()
        timer=Timer(5,self.gio.g_cancellable_cancel,args=(cancel,));timer.daemon=True;timer.start()
        try:
            args=(self.schema,b'default',b'Smooth Prime credential',base64.b64encode(value),cancel,C.byref(error)) if operation=='put' else (self.schema,cancel,C.byref(error))
            function={'put':self.lib.secret_password_store_sync,'get':self.lib.secret_password_lookup_sync,'delete':self.lib.secret_password_clear_sync}[operation]
            result=function(*args,*self.attributes(namespace,entry))
            if error:
                code=error.contents.code;self.glib.g_error_free(error);raise VaultUnavailable('secret_service_'+operation+'_refused',code)
            if operation=='get':
                if not result:raise VaultMissing('secret_service_item_missing')
                try:
                    encoded=C.string_at(result)
                    if len(encoded)>3*1024*1024:raise VaultUnavailable('secret_service_size_limit')
                    return base64.b64decode(encoded,validate=True)
                finally:self.lib.secret_password_free(result)
            if operation=='put' and not result:raise VaultUnavailable('secret_service_write_unconfirmed')
        finally:
            timer.cancel();timer.join();self.obj.g_object_unref(cancel)
    def get(self,namespace,entry):return self.password_call('get',namespace,entry)
    def close(self):
        if self.schema:self.lib.secret_schema_unref(self.schema);self.schema=None
    def put(self,namespace,entry,value):self.password_call('put',namespace,entry,value)
    def delete(self,namespace,entry):
        self.password_call('delete',namespace,entry)
        try:self.get(namespace,entry)
        except VaultMissing:return
        raise VaultUnavailable('secret_service_delete_not_confirmed')


def native_backend():
    platform=platform_name()
    if platform=='android':raise VaultUnavailable('android_keystore_bridge_unavailable')
    backend=MacKeychain() if platform=='macos' else SecretService() if platform=='linux' else None
    if backend is None:raise VaultUnavailable('native_backend_not_required')
    try:backend.probe()
    except Exception:
        if hasattr(backend,'close'):backend.close()
        raise
    return backend
