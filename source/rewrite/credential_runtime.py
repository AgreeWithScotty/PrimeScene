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
    registry=xbmcgui.Window(10000)
    try:service=CredentialService(report)
    except Exception as error:
        from .prime_diagnostics import exception_details
        reason=error.reason if isinstance(error,VaultUnavailable) else 'private_credential_transport_start_failed'
        registry.setProperty(REGISTRY+'.failure',reason)
        report({'event':'credential_service_start_failed','stage':'vault_startup','reason':reason,
            'credential_tcp_listener':False,'exception':exception_details(error),'result':'failure'})
        raise
    registry.clearProperty(REGISTRY+'.failure')
    xbmcgui.Window(10000).setProperty(REGISTRY,json.dumps(service.discovery(),separators=(',',':')))
    report({'event':'credential_service_started','stage':'vault_startup','capability_on_disk':False,
        'transport':'owner_only_filesystem_socket','credential_tcp_listener':False,'result':'success'})
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
    import xbmcgui
    reason=xbmcgui.Window(10000).getProperty(REGISTRY+'.failure')
    allowed={'private_credential_transport_unavailable','private_credential_socket_path_too_long',
        'credential_socket_directory_not_private','credential_socket_not_private','private_credential_transport_start_failed'}
    raise VaultUnavailable(reason if reason in allowed else 'credential_service_not_running')
