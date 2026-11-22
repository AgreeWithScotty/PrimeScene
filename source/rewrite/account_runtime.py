"""Small native Kodi boundary for account operations.

Authentication does not initialize the catalog/player Globals singleton.
"""
from pathlib import Path
import xbmcaddon
import xbmcgui
import xbmcvfs

ID = 'plugin.video.primeinterface'
# These identify the already registered Amazon device protocol, not add-on
# dependency versions or a request to install Android software.
DEVICE_TYPE = 'A43PXU4ZN2AL1'
DEVICE_HEADERS = {'Accept-Charset': 'utf-8',
    'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 11; SHIELD Android TV RQ1A.210105.003)',
    'X-Requested-With': 'com.amazon.avod.thirdpartyclient', 'x-gasc-enabled': 'true'}


class AccountRuntime:
    def __init__(self):
        self.addon = xbmcaddon.Addon(ID)
        self.dialog = xbmcgui.Dialog()
        self.DATA_PATH = xbmcvfs.translatePath(self.addon.getAddonInfo('profile'))
        self.PLUGIN_PATH = self.addon.getAddonInfo('path')
        self.dtid_android = DEVICE_TYPE
        self.headers_android = dict(DEVICE_HEADERS)

    def _number(self, name):
        value = self.addon.getSetting(name)
        return int(value) if value else 0

    @property
    def data_source(self):
        return self._number('data_source')

    @property
    def region(self):
        return self._number('region')

    @property
    def multiuser(self):
        return self.addon.getSetting('multiuser').lower() == 'true'

    def configured_user_agent(self):
        filename = Path(self.DATA_PATH)/'config/UserAgent'
        try:
            if filename.stat().st_size > 4096:
                raise ValueError('The saved browser identity exceeds its size limit')
            value = filename.read_text(encoding='utf-8')
        except FileNotFoundError:
            from .web_identity import USER_AGENT
            return USER_AGENT
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError('The saved browser identity contains invalid characters')
        return value
