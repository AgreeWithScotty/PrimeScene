"""Suppress redundant Windows Stop mode resets with an owned, restored setting lease."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from secrets import token_hex
import sys
from time import monotonic,time

import xbmc
import xbmcgui
from .prime_diagnostics import exception_details

SETTING='videoplayer.adjustrefreshrate'
JOURNAL='display-continuity-lease.json'

def _rpc(method,params):
    response=json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params})))
    if 'error' in response:raise RuntimeError('Display setting operation failed: %s'%response['error'].get('code'))
    return response.get('result')

def _get():return (_rpc('Settings.GetSettingValue',{'setting':SETTING}) or {}).get('value')

def _set(value):
    if _rpc('Settings.SetSettingValue',{'setting':SETTING,'value':value}) is not True or _get()!=value:
        raise RuntimeError('Display setting did not persist')

@contextmanager
def _lock(profile):
    import ctypes as C
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[C.c_void_p,C.c_int,C.c_wchar_p];kernel.CreateMutexW.restype=C.c_void_p
    kernel.WaitForSingleObject.argtypes=[C.c_void_p,C.c_uint32]
    kernel.ReleaseMutex.argtypes=[C.c_void_p];kernel.CloseHandle.argtypes=[C.c_void_p]
    name='Local\\SmoothPrimeDisplayPolicy_'+sha256(str(Path(profile).resolve()).casefold().encode()).hexdigest()[:24]
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:raise C.WinError(C.get_last_error())
    owned=False
    try:
        status=kernel.WaitForSingleObject(handle,250)
        if status not in (0,0x80):raise RuntimeError('Display policy lease is busy')
        owned=True;yield
    finally:
        if owned:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

def _read(path):
    if path.stat().st_size>4096:raise ValueError('Display lease journal exceeds its limit')
    value=json.loads(path.read_text(encoding='utf-8'))
    if (value.get('schema')!=1 or value.get('previous')!=2 or value.get('temporary')!=3
            or not re.fullmatch('[a-f0-9]{16}',str(value.get('owner') or ''))):
        raise ValueError('Invalid display lease journal')
    return value

def _restore(path,value,report,reason):
    if not path.exists() or _read(path)['owner']!=value['owner']:return False
    current=_get();restored=False
    if current==value['temporary']:_set(value['previous']);restored=True
    path.unlink()
    report({'event':'display_continuity_restored','stage':'display_mode_cleanup','reason':reason,
        'previous_policy':value['previous'],'observed_policy':current,'restored':restored,
        'user_change_preserved':current!=value['temporary'],'result':'success'})
    return True

def recover(profile,report):
    """Restore after interrupted cleanup, once native video/fullscreen has ended."""
    if sys.platform!='win32':return False
    path=Path(profile)/JOURNAL
    if not path.exists():return False
    try:
        if xbmc.Player().isPlayingVideo() or xbmcgui.getCurrentWindowId()==12005:return False
        with _lock(profile):
            if not path.exists():return False
            value=_read(path)
            # An idle observer must not race a live playback thread's cleanup.
            if (value.get('process')==os.getpid() and not value.get('recoverable')
                    and 0<=time()-value.get('heartbeat',value.get('created',0))<5):return False
            return _restore(path,value,report,'interrupted_session_recovery')
    except Exception as error:
        report({'event':'display_continuity_recovery_failed','stage':'display_mode_cleanup',
            'exception':exception_details(error),'result':'failure'})
        return False

class DisplayContinuity:
    def __init__(self,g,report):
        self.g,self.report=g,report;self.path=Path(g.DATA_PATH)/JOURNAL
        self.owner=token_hex(8);self.active=False;self.enabled=False;self.fullscreen_since=None
        self.last_heartbeat=0
        self.initial=''
        if sys.platform!='win32':return
        try:
            self.initial=xbmc.getInfoLabel('System.ScreenMode') or ''
            recover(g.DATA_PATH,report)
            policy=_get()
            with _lock(g.DATA_PATH):
                inherited=policy==3 and self.path.exists() and _read(self.path).get('previous')==2
            # 2 and 3 differ only in the native Stop reset. Preserve Off/Always
            # and any manually chosen policy; do not change playback cadence.
            self.enabled=(policy==2 or inherited) and bool(re.search(r'\d+x\d+\s*@\s*\d',self.initial))
            report({'event':'display_continuity_prepared','stage':'display_mode_prepare',
                'original_policy':2 if inherited else policy,'observed_policy':policy,
                'earlier_session_cleanup_pending':inherited,'menu_screen_mode':self.initial,'eligible':self.enabled,
                'scope':'same_mode_Windows_playback_only','result':'success' if self.enabled else 'not_needed'})
        except Exception as error:
            report({'event':'display_continuity_prepare_failed','stage':'display_mode_prepare',
                'exception':exception_details(error),'result':'failure'})

    def update(self):
        if not self.enabled:return
        try:
            current=xbmc.getInfoLabel('System.ScreenMode') or ''
            if not current:return
            if current!=self.initial:
                if self.active:self._release('playback_mode_changed')
                self.enabled=False
                self.report({'event':'display_continuity_not_applied','stage':'display_mode_prepare',
                    'reason':'playback_uses_different_mode','menu_screen_mode':self.initial,
                    'playback_screen_mode':current,'normal_Stop_reset_preserved':True,'result':'not_needed'})
                return
            if self.active:
                if monotonic()-self.last_heartbeat>=2:
                    with _lock(self.g.DATA_PATH):
                        if self.path.exists():
                            value=_read(self.path)
                            if value['owner']==self.owner:
                                value['heartbeat']=time()
                                self.path.write_text(json.dumps(value),encoding='utf-8')
                    self.last_heartbeat=monotonic()
                return
            if not xbmc.getCondVisibility('Window.IsVisible(fullscreenvideo)'):return
            if self.fullscreen_since is None:self.fullscreen_since=monotonic();return
            if monotonic()-self.fullscreen_since<.5:return
            with _lock(self.g.DATA_PATH):
                if self.path.exists():return
                if _get()!=2:
                    self.enabled=False
                    self.report({'event':'display_continuity_not_applied','stage':'display_mode_prepare',
                        'reason':'policy_owned_or_changed','result':'cancelled'});return
                value={'schema':1,'owner':self.owner,'previous':2,'temporary':3,
                    'process':os.getpid(),'created':time(),'heartbeat':time(),'recoverable':False}
                # Journal first, so startup recovery also covers a process crash.
                with self.path.open('x',encoding='utf-8') as output:json.dump(value,output)
                try:_set(3)
                except Exception:
                    _restore(self.path,value,self.report,'activation_failure');raise
                self.active=True;self.last_heartbeat=monotonic()
            self.report({'event':'display_continuity_activated','stage':'display_mode_prepare',
                'menu_screen_mode':self.initial,'playback_screen_mode':current,
                'original_policy':2,'temporary_policy':3,'playback_cadence_unchanged':True,
                'redundant_Stop_reset_suppressed':True,'result':'success'})
        except Exception as error:
            self.enabled=False
            self.report({'event':'display_continuity_activation_failed','stage':'display_mode_prepare',
                'exception':exception_details(error),'result':'failure'})

    def _release(self,reason):
        with _lock(self.g.DATA_PATH):
            if self.path.exists():
                value=_read(self.path)
                if value['owner']==self.owner:_restore(self.path,value,self.report,reason)
        self.active=False

    def close(self):
        if not self.active:return
        started=monotonic()
        try:
            if xbmc.Player().isPlayingVideo():
                self._release('video_active_during_cleanup');return
            while xbmcgui.getCurrentWindowId()==12005 and monotonic()-started<2:
                if self.g.monitor.waitForAbort(.02):break
            if xbmcgui.getCurrentWindowId()==12005:
                with _lock(self.g.DATA_PATH):
                    if self.path.exists():
                        value=_read(self.path)
                        if value['owner']==self.owner:
                            value['recoverable']=True
                            self.path.write_text(json.dumps(value),encoding='utf-8')
                self.report({'event':'display_continuity_restore_deferred','stage':'display_mode_cleanup',
                    'reason':'native_fullscreen_still_closing','elapsed_ms':round((monotonic()-started)*1000),
                    'service_recovery_required':True,'result':'pending'});return
            self.g.monitor.waitForAbort(.1)
            self._release('native_fullscreen_closed')
        except Exception as error:
            self.report({'event':'display_continuity_restore_failed','stage':'display_mode_cleanup',
                'exception':exception_details(error),'service_recovery_required':True,'result':'failure'})
