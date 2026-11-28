"""Scoped standard CDM adapter for stock Windows Kodi; no player binary edits."""
import json
import os
from pathlib import Path
import struct
from time import monotonic
import uuid
import xbmcaddon
import xbmcvfs
from .prime_diagnostics import exception_details
from .chrome_runtime import PreparationCancelled

JOURNAL='chrome-cdm-path-lease.json'

def _source_directory(g):
    return Path(xbmcvfs.translatePath(g.addon.getAddonInfo('path')))/'resources/native/windows-x64/chrome-cdm'


def _directory(g):
    from .chrome_storage import layout
    manifest=json.loads((_source_directory(g)/'bridge.json').read_text(encoding='utf-8'))
    return layout(g.DATA_PATH,manifest)[1]

def available(g,report):
    started=monotonic()
    dialog=None
    try:
        if os.name!='nt' or struct.calcsize('P')!=8:return False
        source=_source_directory(g);manifest=json.loads((source/'bridge.json').read_text(encoding='utf-8'))
        from .chrome_storage import layout,prepare_adapter
        root,_=layout(g.DATA_PATH,manifest)
        from .widevine_diagnostics import windows_binary_version
        from .chrome_runtime import ensure
        import xbmc,xbmcgui
        monitor=xbmc.Monitor()
        def progress(percent,message):
            nonlocal dialog
            if dialog is None:
                dialog=xbmcgui.DialogProgress()
                dialog.create('Preparing HD playback',message)
            dialog.update(percent,message)
            return dialog.iscanceled()
        def cancelled():
            return monitor.abortRequested() or dialog is not None and dialog.iscanceled()
        runtime=ensure(root,manifest,report,cancel=cancelled,progress=progress,
            assets=source,legacy_root=source)
        chrome=runtime/'chrome.exe'
        actual=windows_binary_version(chrome)
        expected=manifest['chrome_abi_fixture']
        # Only the private hash-pinned runtime participates in this adapter.
        # Installing/updating a system browser never changes this selection.
        if actual!=expected:
            report({'event':'chrome_cdm_bridge_available','stage':'device_capability','chrome_version':actual,
                'supported_abi_fixture':expected,'reason':'abi_not_verified','result':'unavailable'})
            return False
        prepare_adapter(source,root,manifest,report)
        cdm=runtime/expected/'WidevineCdm/_platform_specific/win_x64/widevinecdm.dll'
        if not cdm.is_file() or not cdm.with_suffix('.dll.sig').is_file():raise ValueError('Bundled Chrome Widevine component is missing')
        report({'event':'chrome_cdm_bridge_available','stage':'device_capability',
            'chrome_version':actual,'cdm_abi':10,'native_player':'Kodi','player_binary_modified':False,
            'runtime_source':'google_direct_download','system_chrome_required':False,'automatic_updates':False,
            'runtime_storage':'addon_data','survives_addon_updates':True,
            'adapter_revision':manifest.get('performance_revision','baseline'),
            'elapsed_ms':round((monotonic()-started)*1000),'result':'available'})
        return True
    except PreparationCancelled:
        report({'event':'chrome_cdm_bridge_available','stage':'device_capability','result':'cancelled'})
        raise
    except FileNotFoundError:
        report({'event':'chrome_cdm_bridge_available','stage':'device_capability','reason':'component_absent','result':'unavailable'})
        return False
    except Exception as error:
        report({'event':'chrome_cdm_bridge_available','stage':'device_capability',
            'exception':exception_details(error),'elapsed_ms':round((monotonic()-started)*1000),'result':'unavailable'})
        return False
    finally:
        if dialog is not None:dialog.close()

def recover(g,report):
    path=Path(g.DATA_PATH)/JOURNAL
    if not path.exists():return
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('pid')==os.getpid():return
        # Respect another live Kodi process sharing the profile.
        if os.name=='nt':
            import ctypes as C
            from ctypes import wintypes as W
            api=C.WinDLL('kernel32.dll',winmode=0x800)
            api.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];api.OpenProcess.restype=W.HANDLE
            api.CloseHandle.argtypes=[W.HANDLE]
            handle=api.OpenProcess(0x1000,False,int(value.get('pid',0)))
            if handle:api.CloseHandle(handle);return
        from .chrome_storage import owned_directory
        bridge=value.get('bridge')
        if (not isinstance(bridge,str) or not isinstance(value.get('previous'),str) or
                not (bridge==str(_source_directory(g)) or owned_directory(g.DATA_PATH,bridge))):
            raise ValueError('Invalid CDM path recovery journal')
        adaptive=xbmcaddon.Addon('inputstream.adaptive')
        current=adaptive.getSetting('DECRYPTERPATH')
        if current==bridge:
            adaptive.setSetting('DECRYPTERPATH',value['previous'])
            if adaptive.getSetting('DECRYPTERPATH')!=value['previous']:raise RuntimeError('CDM path recovery did not persist')
        path.unlink()
        report({'event':'chrome_cdm_bridge_path_recovered','stage':'native_cdm_path','result':'success'})
    except Exception as error:
        report({'event':'chrome_cdm_bridge_path_recovery_failed','stage':'native_cdm_path','exception':exception_details(error),'result':'failure'})
        raise

class Lease:
    def __init__(self,g,adaptive,report):
        self.g=g;self.adaptive=adaptive;self.report=report;self.root=_directory(g)
        self.path=Path(g.DATA_PATH)/JOURNAL;self.token=uuid.uuid4().hex;self.active=False
        self.log=self.root/'prime-cdm-proxy-diagnostics.jsonl'
        self.log_start=self.log.stat().st_size if self.log.exists() else 0
        self._output_policy=None;self._output_policy_registered=False
        self._output_token=int(self.token[:16],16) or 1
    def _configure_output_policy(self,preparation_failed=True):
        import ctypes
        self._output_policy=ctypes.WinDLL(str(self.root/'widevinecdm.dll'),winmode=0x800,use_last_error=True)
        configure=self._output_policy.SmoothPrimeConfigureHdcpPreflight
        configure.argtypes=[ctypes.c_uint64,ctypes.c_uint32];configure.restype=ctypes.c_uint32
        if not configure(self._output_token,1):raise RuntimeError('Native output preflight ownership is unavailable')
        self._output_policy_registered=True
        self.report({'event':'chrome_cdm_output_preflight_selected','stage':'native_cdm_path',
            'preparation_failed':preparation_failed,'preparation_deferred':not preparation_failed,
            'query_can_enable_protection':False,
            'explicit_CDM_protection_requests_preserved':True,'protection_claim':'unknown','result':'success'})
    def _release_output_policy(self):
        if self._output_policy is None:return
        import ctypes
        from _ctypes import FreeLibrary
        module=self._output_policy
        try:
            if self._output_policy_registered:
                clear=module.SmoothPrimeClearHdcpPreflight
                clear.argtypes=[ctypes.c_uint64];clear.restype=ctypes.c_uint32
                if not clear(self._output_token):raise RuntimeError('Native output preflight ownership changed')
        finally:
            self._output_policy=None;self._output_policy_registered=False
            FreeLibrary(module._handle);module._handle=0
    def activate(self,preflight_unavailable=False,query_only=False):
        recover(self.g,self.report)
        if self.path.exists():raise RuntimeError('Another playback is preparing its native CDM path')
        self.previous=self.adaptive.getSetting('DECRYPTERPATH')
        value={'schema':1,'token':self.token,'pid':os.getpid(),'bridge':str(self.root),'previous':self.previous}
        # Exclusive creation prevents separate Kodi Python interpreters from
        # claiming the same setting while another playback is still opening.
        with self.path.open('x',encoding='utf-8') as stream:json.dump(value,stream)
        self.active=True
        try:
            self.adaptive.setSetting('DECRYPTERPATH',str(self.root))
            if self.adaptive.getSetting('DECRYPTERPATH')!=str(self.root):raise RuntimeError('Native CDM path did not persist')
            if preflight_unavailable or query_only:self._configure_output_policy(preflight_unavailable)
        except Exception:
            self.restore('activation_failed');raise
        self.report({'event':'chrome_cdm_bridge_path_selected','stage':'native_cdm_path','cdm_abi':10,
            'scope':'selected_playback','crash_recovery_journal':True,'result':'success'})
    def restore(self,reason):
        if not self.active:return
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if value.get('token')!=self.token:raise RuntimeError('Native CDM path ownership changed')
        unchanged=self.adaptive.getSetting('DECRYPTERPATH')==str(self.root)
        if unchanged:
            self.adaptive.setSetting('DECRYPTERPATH',self.previous)
            if self.adaptive.getSetting('DECRYPTERPATH')!=self.previous:raise RuntimeError('Native CDM path was not restored')
        self.path.unlink();self.active=False
        self._release_output_policy()
        self.report({'event':'chrome_cdm_bridge_path_restored','stage':'native_cdm_path','reason':reason,
            'setting_changed_externally':not unchanged,'result':'success'})
    def close(self):
        try:self.restore('playback_cleanup')
        finally:self._release_output_policy()
        if not self.log.exists():return
        with self.log.open('rb') as source:source.seek(self.log_start);data=source.read(4*1024*1024+1)
        truncated=len(data)>4*1024*1024;invalid=0;records=0
        for line in data[:4*1024*1024].splitlines():
            try:
                row=json.loads(line)
                # Native diagnostics are metadata only. Keep an explicit
                # allowlist; private CDM messages never enter the report.
                safe={key:row[key] for key in ('stage','code','process','tick_ms','cdm_abi','chrome_abi_fixture') if key in row}
                self.report(dict(safe,event='chrome_cdm_native_diagnostic',native_stage=safe.pop('stage',None),
                    stage='native_cdm_adapter',result='observed'));records+=1
            except (ValueError,TypeError):invalid+=1
        self.report({'event':'chrome_cdm_native_diagnostics_collected','stage':'native_cdm_adapter',
            'records':records,'invalid_records':invalid,'truncated':truncated,
            'result':'partial' if truncated or invalid else 'success'})
