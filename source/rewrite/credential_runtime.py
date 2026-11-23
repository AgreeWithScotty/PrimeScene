"""Kodi-only discovery for the lifetime credential worker; no disk capability."""
import json
import os
from pathlib import Path
from time import monotonic
from .credential_service import CredentialService,REGISTRY,kodi_discovery,request
from .vault_errors import VaultUnavailable


def start_service(g,report):
    if os.name=='nt':return None
    import xbmcgui
    service=CredentialService(report)
    xbmcgui.Window(10000).setProperty(REGISTRY,json.dumps(service.discovery(),separators=(',',':')))
    report({'event':'credential_service_started','stage':'vault_startup','capability_on_disk':False,'result':'success'})
    return service


def stop_service(service):
    if service is None:return
    import xbmcgui
    registry=xbmcgui.Window(10000)
    try:
        if json.loads(registry.getProperty(REGISTRY) or '{}').get('instance')==service.instance:registry.clearProperty(REGISTRY)
    finally:service.close()


def ensure_service(g):
    if os.name=='nt':return
    import xbmc
    try:
        discovery=kodi_discovery();result=request(discovery,{'action':'ping'})
        if result.get('instance')==discovery['instance']:return
    except (VaultUnavailable,OSError,ValueError):pass
    # Start the existing background service directly; this never opens a Kodi
    # directory/window. Its independent SQLite lock rejects duplicate instances.
    xbmc.executebuiltin('RunScript(%s)'%json.dumps(str(Path(g.PLUGIN_PATH)/'service.py')))
    monitor=xbmc.Monitor();deadline=monotonic()+5
    while not monitor.abortRequested() and monotonic()<deadline:
        try:
            discovery=kodi_discovery()
            if request(discovery,{'action':'ping'}).get('instance')==discovery['instance']:return
        except (VaultUnavailable,OSError,ValueError):pass
        if monitor.waitForAbort(.05):break
    raise VaultUnavailable('credential_service_not_running')
