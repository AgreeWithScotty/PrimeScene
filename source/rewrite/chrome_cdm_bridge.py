"""Scoped standard CDM adapter for stock Windows Kodi; no player binary edits."""
import hashlib
import json
import os
from pathlib import Path
import struct
from time import monotonic
import uuid
import xbmcaddon
import xbmcvfs
from .prime_diagnostics import exception_details

JOURNAL='chrome-cdm-path-lease.json'

def _directory(g):
    return Path(xbmcvfs.translatePath(g.addon.getAddonInfo('path')))/'resources/native/windows-x64/chrome-cdm'

def available(g,report):
    started=monotonic()
    try:
        if os.name!='nt' or struct.calcsize('P')!=8:return False
        root=_directory(g);manifest=json.loads((root/'bridge.json').read_text(encoding='utf-8'))
        from .widevine_diagnostics import windows_binary_version
        chrome=Path(manifest['chrome_executable'])
        actual=windows_binary_version(chrome)
        expected=manifest['chrome_abi_fixture']
        # This is a tested installed-binary ABI fixture, not a claim that an old
        # browser release is current. Unsupported versions keep normal playback.
        if actual!=expected:
            report({'event':'chrome_cdm_bridge_available','stage':'device_capability','chrome_version':actual,
                'supported_abi_fixture':expected,'reason':'abi_not_verified','result':'unavailable'})
            return False
        for name,digest in manifest['artifacts'].items():
            if name not in ('widevinecdm.dll','transport_probe.exe'):raise ValueError('Invalid native artifact name')
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:raise ValueError('Native adapter artifact checksum mismatch')
        cdm=chrome.parent/expected/'WidevineCdm/_platform_specific/win_x64/widevinecdm.dll'
        if not cdm.is_file() or not cdm.with_suffix('.dll.sig').is_file():raise ValueError('Installed Chrome Widevine component is missing')
        report({'event':'chrome_cdm_bridge_available','stage':'device_capability',
            'chrome_version':actual,'cdm_abi':10,'native_player':'Kodi','player_binary_modified':False,
            'elapsed_ms':round((monotonic()-started)*1000),'result':'available'})
        return True
    except FileNotFoundError:
        report({'event':'chrome_cdm_bridge_available','stage':'device_capability','reason':'component_absent','result':'unavailable'})
        return False
    except Exception as error:
        report({'event':'chrome_cdm_bridge_available','stage':'device_capability',
            'exception':exception_details(error),'elapsed_ms':round((monotonic()-started)*1000),'result':'unavailable'})
        return False

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
        bridge=str(_directory(g))
        if value.get('bridge')!=bridge or not isinstance(value.get('previous'),str):raise ValueError('Invalid CDM path recovery journal')
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
    def activate(self):
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
        self.report({'event':'chrome_cdm_bridge_path_restored','stage':'native_cdm_path','reason':reason,
            'setting_changed_externally':not unchanged,'result':'success'})
    def close(self):
        self.restore('playback_cleanup')
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
