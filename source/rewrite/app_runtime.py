"""Native Kodi context without provider initialization or process-wide credentials."""
from pathlib import Path
import xbmc
from .account_runtime import AccountRuntime


class AppRuntime(AccountRuntime):
    def __init__(self):
        super().__init__()
        self.pluginid='plugin://'+self.addon.getAddonInfo('id')+'/'
        self.monitor=xbmc.Monitor()
        Path(self.DATA_PATH).mkdir(parents=True,exist_ok=True)


class Preferences:
    def __init__(self,addon):self.addon=addon
    @property
    def data_source(self):return 0
    def flag(self,name,default=False):
        value=self.addon.getSetting(name).strip().lower()
        return default if not value else value=='true'
    def number(self,name,default=0):
        try:return int(self.addon.getSetting(name))
        except (TypeError,ValueError):return default
    def text(self,name,default=''):return self.addon.getSetting(name) or default
