"""Kodi-lifetime vault worker and memory-only fallback.

An owner-only filesystem socket performs mutual HMAC authentication before any
credential is sent. No credential TCP listener or network fallback exists.
Frames are bounded/authenticated JSON bytes, never pickle. Discovery and the
capability stay in Kodi memory, never in a profile file or report.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from secrets import token_bytes,token_hex
import socket
import socketserver
import struct
import stat
import tempfile
from threading import BoundedSemaphore,RLock,Thread
from time import monotonic
from .vault_errors import VaultError,VaultUnavailable,VaultMissing

REGISTRY='smoothprime.credential_service'
MAX_FRAME=3*1024*1024


def _receive(connection,length):
    chunks=[];deadline=monotonic()+8
    while length:
        left=deadline-monotonic()
        if left<=0:raise VaultUnavailable('vault_transport_timeout')
        connection.settimeout(left);part=connection.recv(min(length,65536))
        if not part:raise VaultUnavailable('vault_transport_closed')
        chunks.append(part);length-=len(part)
    return b''.join(chunks)


def _mac(key,context,data):return hmac.new(key,context+data,hashlib.sha256).digest()


def _send_frame(connection,key,context,value):
    data=json.dumps(value,separators=(',',':')).encode('utf-8')
    if len(data)>MAX_FRAME:raise VaultUnavailable('vault_frame_limit')
    connection.sendall(struct.pack('!I',len(data))+_mac(key,context,data)+data)


def _read_frame(connection,key,context):
    length=struct.unpack('!I',_receive(connection,4))[0]
    if length>MAX_FRAME:raise VaultUnavailable('vault_frame_limit')
    expected=_receive(connection,32);data=_receive(connection,length)
    if not hmac.compare_digest(expected,_mac(key,context,data)):raise VaultUnavailable('vault_frame_authentication_failed')
    value=json.loads(data)
    if not isinstance(value,dict):raise VaultUnavailable('vault_protocol_invalid')
    return value


class _Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer if hasattr(socketserver,'UnixStreamServer') else socketserver.BaseServer):
    daemon_threads=True;allow_reuse_address=False
    def __init__(self,owner):
        self.owner=owner;self.slots=BoundedSemaphore(6)
        # The fallback base class only keeps this module importable on Windows,
        # where native DPAPI is used and this worker is never started. Refuse
        # construction if private filesystem IPC is unavailable; never bind TCP.
        if os.name=='nt' or not hasattr(socket,'AF_UNIX'):
            raise VaultUnavailable('private_credential_transport_unavailable')
        super().__init__(owner.endpoint,_Handler)
    def process_request(self,request,address):
        if not self.slots.acquire(False):self.shutdown_request(request);return
        request.settimeout(8)
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()
    def handle_error(self,*args):
        self.owner.report({'event':'vault_transport_failed','stage':'vault_socket','result':'failure'})


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        owner=self.server.owner;started=monotonic();request_id=token_hex(8);action='authenticate';backend=None
        try:
            nonce=token_bytes(32);self.request.sendall(b'SPV2'+nonce)
            answer=_receive(self.request,64);peer=answer[:32];context=b'SPV2'+nonce+peer
            if not hmac.compare_digest(answer[32:],_mac(owner.key,b'client:',context)):
                raise VaultUnavailable('vault_peer_authentication_failed')
            self.request.sendall(_mac(owner.key,b'server:',context))
            value=_read_frame(self.request,owner.key,b'request:'+context)
            action=value.get('action');backend=value.get('backend')
            result=owner.operation(value)
            _send_frame(self.request,owner.key,b'response:'+context,{'ok':True,'result':result})
            # Never include values, identifiers, discovery metadata or payloads.
            if action not in ('get','ping'):
                owner.report({'event':'credential_vault_operation','stage':'vault_worker','action':action,
                    'backend':backend,'request_id':request_id,'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
        except Exception as error:
            from .prime_diagnostics import exception_details
            reason=error.reason if isinstance(error,VaultError) else 'vault_operation_failed'
            owner.report({'event':'credential_vault_failed','stage':'vault_worker','action':action if isinstance(action,str) and action in _ACTIONS else 'invalid',
                'backend':backend if isinstance(backend,str) and backend in _BACKENDS else None,'request_id':request_id,'reason':reason,
                'status':getattr(error,'status',None),'error_type':type(error).__name__,
                'exception':exception_details(error),
                'elapsed_ms':round((monotonic()-started)*1000),'result':'failure'})
            if action!='authenticate':
                try:_send_frame(self.request,owner.key,b'response:'+context,{'ok':False,'reason':reason,
                    'missing':isinstance(error,VaultMissing),'status':getattr(error,'status',None)})
                except (OSError,VaultError):pass
        finally:
            if owner.closed:owner.discard()


_ACTIONS={'choose','put','get','delete','clear','ping'}
_BACKENDS={'session','macos_keychain','linux_secret_service'}


class CredentialService:
    def __init__(self,report,directory=None):
        self.report=report;self.key=token_bytes(32);self.instance=token_hex(16)
        self.lock=RLock();self.native=None;self.native_reason=None;self.choices={};self.records={};self.retired=set();self.closed=False
        if os.name=='nt' or not hasattr(socket,'AF_UNIX'):
            raise VaultUnavailable('private_credential_transport_unavailable')
        self.directory=Path(tempfile.mkdtemp(prefix='sp-vault-',dir=directory))
        self.endpoint=str(self.directory/'ipc')
        try:
            os.chmod(self.directory,0o700)
            if len(os.fsencode(self.endpoint))>103:raise VaultUnavailable('private_credential_socket_path_too_long')
            _private_endpoint(self.endpoint,exists=False)
            self.server=_Server(self)
            os.chmod(self.endpoint,0o600)
            _private_endpoint(self.endpoint)
            self.thread=Thread(target=self.server.serve_forever,name='PrimeVault',daemon=True)
            self.thread.start()
        except Exception:
            if hasattr(self,'server'):self.server.server_close()
            self._remove_endpoint()
            raise
    def discovery(self):return {'socket':self.endpoint,'capability':self.key.hex(),'instance':self.instance}
    def _remove_endpoint(self):
        # This directory is freshly created by this service. No recursive delete
        # or following a replaced symlink, and no endpoint paths in diagnostics.
        endpoint=Path(self.endpoint)
        if endpoint.exists() or endpoint.is_symlink():endpoint.unlink()
        self.directory.rmdir()
    def _native(self):
        if self.native is None:
            from .native_vault import native_backend
            self.native=native_backend()
        return self.native
    def _erase(self,key):
        value=self.records.pop(key,None)
        if value is not None:value[:]=b'\0'*len(value)
    def operation(self,value):
        if set(value)-{'action','namespace','entry','backend','value'}:raise VaultUnavailable('vault_protocol_invalid')
        action=value.get('action')
        if not isinstance(action,str) or action not in _ACTIONS:raise VaultUnavailable('vault_action_invalid')
        if action=='ping':return {'instance':self.instance}
        namespace=value.get('namespace','')
        if not isinstance(namespace,str) or not re.fullmatch('[a-f0-9]{64}',namespace):raise VaultUnavailable('vault_namespace_invalid')
        with self.lock:
            if self.closed:raise VaultUnavailable('vault_service_stopped')
            if action=='choose':
                if namespace not in self.choices:
                    try:
                        self.choices[namespace]=self._native().name;self.native_reason=None
                    except (VaultUnavailable,OSError,AttributeError) as error:
                        from .prime_diagnostics import exception_details
                        self.choices[namespace]='session'
                        self.native_reason=error.reason if isinstance(error,VaultError) else 'native_library_unavailable'
                        self.report({'event':'credential_storage_selected','stage':'vault_selection','backend':'session',
                            'reason':self.native_reason,'exception':exception_details(error),
                            'persistent_credentials':False,'result':'success'})
                return {'backend':self.choices[namespace],'reason':self.native_reason,'instance':self.instance}
            if action=='clear':
                for key in list(self.records):
                    if key[0]==namespace:self._erase(key)
                self.choices.pop(namespace,None)
                return True
            entry=value.get('entry','');backend=value.get('backend')
            if not isinstance(entry,str) or not re.fullmatch('[a-f0-9]{32}',entry) or not isinstance(backend,str) or backend not in _BACKENDS:raise VaultUnavailable('vault_reference_invalid')
            key=(namespace,backend,entry)
            from .native_vault import platform_name
            foreign=backend!='session' and {'macos_keychain':'macos','linux_secret_service':'linux'}[backend]!=platform_name()
            if action=='delete':
                self.retired.add(key);self._erase(key)
                if backend!='session' and not foreign:
                    native=self._native()
                    if native.name!=backend:raise VaultUnavailable('vault_platform_mismatch')
                    native.delete(namespace,entry)
                return True
            if foreign:raise VaultMissing('vault_platform_changed')
            if key in self.retired:raise VaultMissing('vault_entry_retired')
            if action=='put':
                encoded=value.get('value')
                if not isinstance(encoded,str) or len(encoded)>MAX_FRAME:raise VaultUnavailable('vault_value_limit')
                data=base64.b64decode(encoded,validate=True)
                if len(data)>2*1024*1024:raise VaultUnavailable('vault_value_limit')
                if len(self.records)>=32 and key not in self.records:raise VaultUnavailable('vault_record_limit')
                if sum(len(v) for k,v in self.records.items() if k!=key)+len(data)>8*1024*1024:
                    raise VaultUnavailable('vault_memory_limit')
                if backend!='session':
                    native=self._native()
                    if native.name!=backend:raise VaultUnavailable('vault_platform_mismatch')
                    native.put(namespace,entry,data)
                    if not hmac.compare_digest(data,native.get(namespace,entry)):raise VaultUnavailable('vault_write_verification_failed')
                if self.closed:raise VaultUnavailable('vault_service_stopped')
                self._erase(key);self.records[key]=bytearray(data)
                return {'sha256':hashlib.sha256(data).hexdigest(),'instance':self.instance}
            if key not in self.records:
                if backend=='session':raise VaultMissing('session_credentials_expired')
                native=self._native()
                if native.name!=backend:raise VaultUnavailable('vault_platform_mismatch')
                data=native.get(namespace,entry)
                if (len(self.records)>=32 or len(data)>2*1024*1024
                    or sum(len(v) for v in self.records.values())+len(data)>8*1024*1024):raise VaultUnavailable('vault_memory_limit')
                if self.closed:raise VaultUnavailable('vault_service_stopped')
                self.records[key]=bytearray(data)
            return {'value':base64.b64encode(self.records[key]).decode('ascii'),'instance':self.instance}
    def close(self):
        self.closed=True
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        try:self._remove_endpoint()
        except OSError as error:
            from .prime_diagnostics import exception_details
            self.report({'event':'credential_socket_cleanup_failed','stage':'vault_shutdown',
                'exception':exception_details(error),'result':'failure'})
        acquired=self.lock.acquire(timeout=2)
        if not acquired:
            self.report({'event':'credential_vault_shutdown_pending','stage':'vault_shutdown','reason':'native_worker_pending','result':'failure'})
            # Do not release the process-wide service lease while a native write
            # could still complete after a replacement service deletes its item.
            self.lock.acquire()
        try:
            self.discard()
        finally:self.lock.release()
        self.report({'event':'credential_vault_stopped','stage':'vault_shutdown','memory_credentials_discarded':True,'result':'success'})
    def discard(self):
        with self.lock:
            for key in list(self.records):self._erase(key)
            self.choices.clear();self.retired.clear()
            if self.native is not None and hasattr(self.native,'close'):self.native.close()
            self.native=None;self.key=b''


def _private_endpoint(address,exists=True):
    if os.name=='nt' or not hasattr(socket,'AF_UNIX'):
        raise VaultUnavailable('private_credential_transport_unavailable')
    if not isinstance(address,str) or not address or '\0' in address or len(os.fsencode(address))>103:
        raise VaultUnavailable('vault_discovery_invalid')
    path=Path(address)
    if not path.is_absolute() or path.name!='ipc' or not path.parent.name.startswith('sp-vault-'):
        raise VaultUnavailable('vault_discovery_invalid')
    parent=path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid()
        or stat.S_IMODE(parent.st_mode)!=0o700):raise VaultUnavailable('credential_socket_directory_not_private')
    if exists:
        info=path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid()
            or stat.S_IMODE(info.st_mode)!=0o600):raise VaultUnavailable('credential_socket_not_private')


def request(discovery,value):
    if (not isinstance(discovery,dict) or set(discovery)!={'socket','capability','instance'}
        or not re.fullmatch('[a-f0-9]{64}',str(discovery['capability']))
        or not re.fullmatch('[a-f0-9]{32}',str(discovery['instance']))):
        raise VaultUnavailable('vault_discovery_invalid')
    key=bytes.fromhex(discovery['capability'])
    try:
        _private_endpoint(discovery['socket'])
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(8);connection.connect(discovery['socket'])
            greeting=_receive(connection,36)
            if greeting[:4]!=b'SPV2':raise VaultUnavailable('vault_server_invalid')
            nonce=token_bytes(32);context=greeting+nonce
            connection.sendall(nonce+_mac(key,b'client:',context))
            if not hmac.compare_digest(_receive(connection,32),_mac(key,b'server:',context)):
                raise VaultUnavailable('vault_server_authentication_failed')
            # This exact authenticated socket is retained. There is no automatic
            # reconnect that could send credentials to a replacement endpoint.
            _send_frame(connection,key,b'request:'+context,value)
            reply=_read_frame(connection,key,b'response:'+context)
        if not reply.get('ok'):
            kind=VaultMissing if reply.get('missing') else VaultUnavailable
            raise kind(reply.get('reason','vault_refused'),reply.get('status'))
        return reply['result']
    except OSError as error:raise VaultUnavailable('vault_service_unreachable',getattr(error,'errno',None)) from error


def kodi_discovery():
    import xbmcgui
    raw=xbmcgui.Window(10000).getProperty(REGISTRY)
    if not raw:raise VaultUnavailable('credential_service_not_running')
    if len(raw)>1024:raise VaultUnavailable('vault_discovery_invalid')
    try:return json.loads(raw)
    except ValueError as error:raise VaultUnavailable('vault_discovery_invalid') from error


def kodi_request(value):return request(kodi_discovery(),value)
