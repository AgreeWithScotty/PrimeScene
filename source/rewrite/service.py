#!/usr/bin/env python
# -*- coding: utf-8 -*-
import threading

import xbmc, xbmcgui

from .logging import Log
from .configs import getConfig, writeConfig
from .common import Globals, Settings
from .proxy import ProxyTCPD


class BackgroundService(xbmc.Monitor):
    def __init__(self):
        super(BackgroundService, self).__init__()
        self._s = Settings()
        from .account_state import account_epoch
        self._prime_scene_epoch = account_epoch(Globals().DATA_PATH)
        self.freqCheck = 60
        self.freqExport = 86400  # 24 * 60 * 60 seconds
        self.lastCheck = 0
        self.lastExport = float(getConfig('last_wl_export', '0'))
        self.wl_export = self._s.wl_export
        from .account_state import refresh_service_session
        refresh_service_session(Globals(), self)
        self.proxy = ProxyTCPD(self._s)
        writeConfig('loginlock', '')
        writeConfig('proxyaddress', f'127.0.0.1:{self.proxy.port}')
        Log(f'Service: Proxy bound to {self._s.proxyaddress}')
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever)

    def onSettingsChanged(self):
        super(BackgroundService, self).__init__()
        xbmc.sleep(500)
        self.wl_export = self._s.wl_export
        from .account_state import refresh_service_session
        refresh_service_session(Globals(), self)

    def onNotification(self, sender, method, data):
        if sender == 'plugin.video.primeinterface' and method.rsplit('.', 1)[-1] == 'account_invalidated':
            from .account_state import refresh_service_session
            refresh_service_session(Globals(), self)

    def start(self):
        self.proxy.server_activate()
        self.proxy.timeout = 1
        self.proxy_thread.start()
        Log('Service: Proxy server started')

        def _playback_return():
            try:
                from .aurora import watch_playback
                watch_playback(Globals().DATA_PATH)
            except Exception as error:
                Log('Service: playback return stopped (' + type(error).__name__ + ')')

        threading.Thread(target=_playback_return, name='PlaybackReturn', daemon=True).start()

        from time import time
        Log('Service started')

        while not self.abortRequested():
            from .account_state import refresh_service_session
            refresh_service_session(Globals(), self)
            ct = time()
            if (ct >= (self.lastCheck + self.freqCheck)) and self.wl_export:
                self.lastCheck = ct
                self.export_watchlist(ct)

            if self.waitForAbort(5):
                break

        Log('Service stopped')
        self.stop()

    def stop(self):
        self.proxy.server_close()
        self.proxy.shutdown()
        self.proxy_thread.join()
        Log('Service: Proxy server stopped')

    def export_watchlist(self, cur_time=0):
        """ Export the watchlist every self.freqExport seconds """
        if cur_time >= (self.freqExport + self.lastExport):
            Log('Service: Exporting the Watchlist')
            self.lastExport = cur_time
            xbmc.executebuiltin('RunScript(plugin.video.primeinterface,plugin://plugin.video.primeinterface/?mode=exportWatchlist)')


class SettingsMonitor(xbmc.Monitor):
    def __init__(self):
        super(SettingsMonitor, self).__init__()
        self.mon_prop = 'primeinterface_settingsmonitor_state'
        self.window = xbmcgui.Window(10000)
        self._abort = False

    def onSettingsChanged(self):
        super(SettingsMonitor, self).__init__()
        Log('Service: Settings changed', Log.DEBUG)
        xbmc.sleep(500)
        self._abort = True

    def start(self):
        if self.window.getProperty(self.mon_prop) == '1':
            return

        self.window.setProperty(self.mon_prop, '1')
        Log('Service: Settings Monitor started ', Log.DEBUG)
        while not self.abortRequested() and not self._abort:
            if self.waitForAbort(2):
                break

        self.window.setProperty(self.mon_prop, '')
        Log('Service: Settings Monitor stopped ', Log.DEBUG)
        exit()
