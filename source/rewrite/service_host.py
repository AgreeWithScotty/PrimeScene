"""Native playback observer and account invalidation boundary."""
from secrets import token_hex
import xbmc
import xbmcgui
from .app_runtime import AppRuntime
from .account_state import account_epoch,clear_runtime,refresh_service_session
from .prime_diagnostics import DiagnosticLog,exception_details

class AccountMonitor(xbmc.Monitor):
    def __init__(self,g):
        super().__init__();self.g=g;self._prime_scene_epoch=account_epoch(g.DATA_PATH)
    def onNotification(self,sender,method,data):
        if sender==self.g.addon.getAddonInfo('id') and method.rsplit('.',1)[-1]=='account_invalidated':
            refresh_service_session(self.g,self)

def run():
    g=AppRuntime();monitor=AccountMonitor(g);registry=xbmcgui.Window(10000)
    token=token_hex(8);property_name='smoothprime.observer_service'
    if registry.getProperty(property_name):return
    registry.setProperty(property_name,token)
    report=DiagnosticLog(g.DATA_PATH,g.addon.getAddonInfo('version'),operation='service')
    try:
        refresh_service_session(g,monitor)
        report({'event':'service_started','stage':'observer','result':'success'})
        from .aurora import watch_playback
        watch_playback(g.DATA_PATH)
    except Exception as error:
        report({'event':'service_failed','stage':'observer','exception':exception_details(error),'result':'failure'})
    finally:
        if registry.getProperty(property_name)==token:registry.clearProperty(property_name)
        clear_runtime(g)
        report({'event':'service_stopped','stage':'observer','result':'success'})
