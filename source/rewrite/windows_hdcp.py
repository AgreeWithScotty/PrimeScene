"""Scoped Windows output protection through the published WinRT HDCP API.

No CDM, Kodi binary or security-level override. Close releases this session.
ABI: Microsoft Windows.Media.Protection.IHdcpSession metadata.
"""
import ctypes as C
from ctypes import wintypes as W
from time import monotonic,sleep
from threading import RLock
import uuid


class NativeFailure(RuntimeError):
    def __init__(self,stage,hr):
        self.stage=stage;self.code=hr&0xffffffff
        super().__init__(stage+' failed 0x%08x'%self.code)


def checked(hr,stage):
    if hr<0:raise NativeFailure(stage,hr)


class Interface:
    def __init__(self,pointer):
        if not pointer:raise ValueError('Windows returned no COM interface')
        self.pointer=pointer
    def call(self,index,argtypes,*args,result=C.c_long):
        if not self.pointer:raise RuntimeError('Windows HDCP interface is closed')
        table=C.cast(self.pointer,C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(result,C.c_void_p,*argtypes)(table[index])(self.pointer,*args)
    def query(self,iid):
        identifier=(C.c_ubyte*16).from_buffer_copy(uuid.UUID(iid).bytes_le)
        output=C.c_void_p()
        checked(self.call(0,[C.c_void_p,C.POINTER(C.c_void_p)],C.byref(identifier),C.byref(output)),'query_interface')
        return Interface(output)
    def release(self):
        if self.pointer:
            self.call(2,[],result=W.ULONG);self.pointer=None


class HdcpSession:
    def __init__(self):
        self._lock=RLock()
        self.api=C.WinDLL('combase.dll',winmode=0x800,use_last_error=True)
        self.api.RoInitialize.argtypes=[W.UINT];self.api.RoInitialize.restype=C.c_long
        self.api.RoUninitialize.argtypes=[];self.api.RoUninitialize.restype=None
        self.api.WindowsCreateString.argtypes=[W.LPCWSTR,W.UINT,C.POINTER(C.c_void_p)]
        self.api.WindowsCreateString.restype=C.c_long
        self.api.WindowsDeleteString.argtypes=[C.c_void_p];self.api.WindowsDeleteString.restype=C.c_long
        self.api.RoActivateInstance.argtypes=[C.c_void_p,C.POINTER(C.c_void_p)]
        self.api.RoActivateInstance.restype=C.c_long
        self.session=None;self.uninitialize=False
        hr=self.api.RoInitialize(1)
        if hr>=0:self.uninitialize=True
        elif hr&0xffffffff!=0x80010106:raise NativeFailure('initialize_winrt',hr)
        name='Windows.Media.Protection.HdcpSession';string=C.c_void_p();instance=None
        try:
            checked(self.api.WindowsCreateString(name,len(name),C.byref(string)),'create_class_name')
            pointer=C.c_void_p()
            checked(self.api.RoActivateInstance(string,C.byref(pointer)),'activate_hdcp_session')
            instance=Interface(pointer)
            self.session=instance.query('718845e9-64d7-426d-809b-1be461941a2a')
        except Exception:
            self.close();raise
        finally:
            if instance:instance.release()
            if string: self.api.WindowsDeleteString(string)
    def status(self):
        output=C.c_void_p()
        checked(self.session.call(7,[C.POINTER(C.c_void_p)],C.byref(output)),'effective_protection')
        value=None
        if output:
            reference=Interface(output)
            try:
                number=C.c_int()
                checked(reference.call(6,[C.POINTER(C.c_int)],C.byref(number)),'protection_value')
                value=number.value
            finally:reference.release()
        on=C.c_ubyte();typed=C.c_ubyte()
        checked(self.session.call(6,[C.c_int,C.POINTER(C.c_ubyte)],1,C.byref(on)),'is_protected')
        checked(self.session.call(6,[C.c_int,C.POINTER(C.c_ubyte)],2,C.byref(typed)),'is_type_enforced')
        return {'effective_protection':value,'hdcp_on':bool(on.value),'type_enforcement':bool(typed.value)}
    def request(self,level,wait=None):
        operation=None;info=None;output=C.c_void_p();started=monotonic()
        checked(self.session.call(8,[C.c_int,C.POINTER(C.c_void_p)],level,C.byref(output)),'set_protection')
        operation=Interface(output)
        try:
            info=operation.query('00000036-0000-0000-c000-000000000046')
            status=C.c_int()
            cancelled=False
            while monotonic()-started<8:
                checked(info.call(7,[C.POINTER(C.c_int)],C.byref(status)),'protection_async_status')
                if status.value!=0:break
                if wait is not None:
                    if wait(.05):cancelled=True;break
                else:sleep(.05)
            if status.value==0:
                info.call(9,[])
                return {'operation_status':'cancelled' if cancelled else 'timeout','elapsed_ms':round((monotonic()-started)*1000)}
            result=C.c_int()
            checked(operation.call(8,[C.POINTER(C.c_int)],C.byref(result)),'protection_result')
            return {'operation_status':status.value,'result_code':result.value,
                    'elapsed_ms':round((monotonic()-started)*1000),**self.status()}
        finally:
            if info:
                if status.value!=0:info.call(10,[])
                info.release()
            if operation:operation.release()
    def current_status(self):
        # License requests run on a separate Python proxy thread. The WinRT
        # object is agile; initialize that calling apartment before querying it.
        with self._lock:
            hr=self.api.RoInitialize(1)
            owned=hr>=0
            if not owned and hr&0xffffffff!=0x80010106:raise NativeFailure('initialize_query_apartment',hr)
            try:return self.status()
            finally:
                if owned:self.api.RoUninitialize()
    def close(self):
        with self._lock:self._close()
    def _close(self):
        try:
            if self.session:
                closable=self.session.query('30d5a829-7fa4-4026-83bb-d75bae4ea99e')
                try:checked(closable.call(6,[]),'close_hdcp_session')
                finally:closable.release()
        finally:
            if self.session:self.session.release();self.session=None
            if self.uninitialize:self.api.RoUninitialize();self.uninitialize=False


