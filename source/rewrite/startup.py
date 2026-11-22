#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os.path

import xbmc, xbmcvfs

from .common import Globals, Settings
from .network import getUA
from .accounts import loadUser, loadUsers, switchUser
from .configs import getConfig
from .logging import Log
from .l10n import getString

_g = Globals()
_s = Settings()


def EntryPoint(argv):
    """ Main entry point of the Amazon VOD addon """

    # Initialise globals and settings before doing anything else

    if not xbmcvfs.exists(os.path.join(_g.DATA_PATH, 'settings.xml')):
        _g.addon.setSetting('data_source', '0')
        _g.addon.setSetting('playmethod', '3')

    from socket import setdefaulttimeout
    setdefaulttimeout(30)

    # import requests, warnings
    # warnings.simplefilter('error', requests.packages.urllib3.exceptions.SNIMissingWarning)
    # warnings.simplefilter('error', requests.packages.urllib3.exceptions.InsecurePlatformWarning)
    from urllib.parse import urlparse, parse_qsl

    args = dict(parse_qsl(urlparse(argv[2]).query))
    path = urlparse(argv[0]).path

    Log(f'Requested {path if 1 < len(path) else args}', Log.DEBUG)
    mode = args.get('mode', None)

    if mode == 'CheckUpdates':
        import xbmcplugin
        if _g.pluginhandle >= 0:
            xbmcplugin.endOfDirectory(_g.pluginhandle, cacheToDisc=False)
        xbmc.executebuiltin('RunScript("' + os.path.join(_g.PLUGIN_PATH, 'check_updates.py') + '")')
        return

    users = loadUsers()
    if not users and mode is None:
        # First launch is an explicit Prime Scene login, before background requests.
        from .account_state import is_signed_out
        if not is_signed_out(_g.DATA_PATH) and _g.dialog.yesno('Smooth Prime',
                               'Sign in to Amazon Prime Video to start watching.',
                               nolabel='Cancel', yeslabel='Sign in'):
            if not getConfig('UserAgent'):
                getUA()
            from .prime_login import LogIn
            LogIn()
        users = loadUsers()
    if users and not getConfig('UserAgent'):
        getUA()

    if mode is None and path in ('', '/'):
        # Queue the GUI script without waiting for it. Release the directory
        # request so Kodi can activate the custom window on its main thread.
        import xbmcplugin
        if _g.pluginhandle >= 0:
            xbmcplugin.endOfDirectory(_g.pluginhandle, succeeded=False, cacheToDisc=False)
        xbmc.executebuiltin('RunScript("' + os.path.join(_g.PLUGIN_PATH, 'gui.py') + '")')
        return

    if users:
        if not loadUser('mid', cachedUsers=users):
            switchUser(0)

        # Set marketplace, base and atv urls, prime video usage and
        # initialise either AmazonTLD or PrimeVideo
        _g.InitialiseProvider(loadUser('mid', cachedUsers=users), loadUser('baseurl', cachedUsers=users),
                              loadUser('atvurl', cachedUsers=users), loadUser('pv', cachedUsers=users), loadUser('deviceid', cachedUsers=users))
    elif mode != 'LogIn':
        _g.dialog.notification(getString(30200), getString(30216))
        xbmc.executebuiltin(f"Addon.OpenSettings({_g.addon.getAddonInfo('id')})")
        exit()

    if path.startswith('/pv/'):
        path = path[4:]
        verb, path = path.split('/', 1)
        _g.pv.Route(verb, path)
    elif mode == 'ClassicUI':
        _g.pv.BrowseRoot()
    elif mode == 'PlayVideo':
        from .playback import PlayVideo
        PlayVideo(args.get('name', ''), args.get('asin'), args.get('adult', '0'), int(args.get('trailer', '0')), int(args.get('selbitrate', '0')))
    elif mode == 'openSettings':
        aid = args.get('url')
        aid = _g.is_addon if aid == 'is' else aid
        import xbmcaddon
        xbmcaddon.Addon(aid).openSettings()
    elif mode == 'exportWatchlist':
        if hasattr(_g.pv, 'getListMenu'):
            _g.pv.getListMenu(_g.watchlist, export=2)
        elif hasattr(_g.pv, 'Browse'):
            _g.pv.Browse('root/Watchlist/watchlist', export=5)
        elif hasattr(_g.pv, 'getPage'):
            _g.pv.getPage(_g.watchlist, export=2)
    elif mode == 'langSettings':
        from .configs import langSettings
        langSettings(args.get('url'))
    elif mode == 'Search':
        Search(args.get('searchstring'))
    elif mode in ['LogIn', 'remLoginData', 'removeUser', 'renameUser', 'switchUser', 'createZIP', 'removeLogs']:
        from .prime_login import LogIn
        from .account_state import remLoginData
        from .accounts import removeUser, renameUser
        from .logging import createZIP, removeLogs
        exec(f'{mode}()')
    else:
        _g.pv.Route(mode, args)

def Search(searchString):
    if searchString is None:
        from .dialogs import SearchDialog
        searchString = SearchDialog().value if _s.search_history else _g.dialog.input(getString(24121)).strip(' \t\n\r')
    if 0 == len(searchString):
        exit()
    Log(f'Searching "{searchString}"…', Log.INFO)
    _g.pv.Search(searchString)
