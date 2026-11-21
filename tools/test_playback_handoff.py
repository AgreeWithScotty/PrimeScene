#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless check that starting a title does not open a custom player window.

Stubs the Kodi modules, unpacks the current Smooth Prime release, and calls
the same play_row path the catalog uses. Kodi's own player receives the
PlayVideo URL. No add-on window, dialog, or playback monitor is constructed,
and nothing asks Kodi to close its OSD or seek bar.
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / 'packages' / 'plugin.video.primeinterface' / 'plugin.video.primeinterface-0.2.17.zip'
PROFILE = tempfile.mkdtemp(prefix='primescene-playback-')
FILM_URL = ('plugin://plugin.video.primeinterface/?mode=PlayVideo&asin=B001'
            '&name=Harbor+Lights&adult=0&trailer=0&selbitrate=0')


class ListItem(object):
    def __init__(self, label='', path=''):
        self.label = label
        self.path = path
        self.properties = {}

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, '')


class Dialog(object):
    def ok(self, heading, message):
        pass

    def select(self, heading, options):
        return -1


class Addon(object):
    def __init__(self, addon_id=None):
        self.id = addon_id

    def getAddonInfo(self, key):
        return {
            'id': 'plugin.video.primeinterface',
            'path': str(ADDON),
            'profile': PROFILE,
            'name': 'Smooth Prime',
            'version': '0.2.17',
            'icon': '',
            'fanart': '',
        }[key]


class Window(object):
    def __init__(self, window_id):
        self.window_id = window_id
        self.properties = {}

    def getProperty(self, key):
        return self.properties.get(key, '')

    def setProperty(self, key, value):
        self.properties[key] = value

    def clearProperty(self, key):
        self.properties.pop(key, None)


class WindowXML(object):
    created = []

    def __init__(self, *args, **kwargs):
        WindowXML.created.append((type(self).__name__, args[:1]))
        raise AssertionError('starting playback constructed a custom window %s' % (args[:1],))


class WindowXMLDialog(WindowXML):
    pass


class Monitor(object):
    created = []

    def __init__(self):
        Monitor.created.append(type(self).__name__)

    def abortRequested(self):
        return False

    def waitForAbort(self, seconds=0):
        return True


class Player(object):
    created = []
    start_playing = False

    def __init__(self):
        Player.created.append(self)
        self.played = []
        self.playing = Player.start_playing

    def play(self, url):
        self.played.append(url)
        self.playing = True

    def isPlaying(self):
        return self.playing

    def stop(self):
        self.playing = False


def install_stubs():
    xbmc = type(sys)('xbmc')
    xbmc.LOGINFO = 1
    xbmc.LOGERROR = 4
    xbmc.log = lambda *args, **kwargs: None
    xbmc.builtins = []
    xbmc.executebuiltin = lambda builtin: xbmc.builtins.append(builtin)
    xbmc.getInfoLabel = lambda key: '21.3 (21.3.0)'
    xbmc.getCondVisibility = lambda key: False
    xbmc.Monitor = Monitor
    xbmc.Player = Player
    xbmc.sleep = lambda ms: None

    gui = type(sys)('xbmcgui')
    gui.ListItem = ListItem
    gui.Dialog = Dialog
    gui.Window = Window
    gui.WindowXML = WindowXML
    gui.WindowXMLDialog = WindowXMLDialog
    gui.WindowDialog = WindowXML
    gui.getCurrentWindowId = lambda: 12005
    gui.NOTIFICATION_INFO = 'info'
    gui.NOTIFICATION_ERROR = 'error'
    gui.ControlButton = lambda *args, **kwargs: None

    plugin = type(sys)('xbmcplugin')
    plugin.SORT_METHOD_NONE = 0
    plugin.endOfDirectory = lambda *args, **kwargs: None
    plugin.setResolvedUrl = lambda *args, **kwargs: None

    addon = type(sys)('xbmcaddon')
    addon.Addon = Addon

    vfs = type(sys)('xbmcvfs')
    vfs.translatePath = lambda path: path
    vfs.exists = os.path.exists
    vfs.mkdirs = lambda path: os.makedirs(path, exist_ok=True) or True
    vfs.File = lambda *args, **kwargs: None

    sys.modules['xbmc'] = xbmc
    sys.modules['xbmcgui'] = gui
    sys.modules['xbmcplugin'] = plugin
    sys.modules['xbmcaddon'] = addon
    sys.modules['xbmcvfs'] = vfs
    return xbmc


def unpack_addon():
    if not ZIP_PATH.is_file():
        raise SystemExit('missing %s' % ZIP_PATH)
    dest = Path(tempfile.mkdtemp(prefix='primescene-playback-addon-'))
    with zipfile.ZipFile(ZIP_PATH) as archive:
        archive.extractall(dest)
        names = archive.namelist()
    return dest / 'plugin.video.primeinterface', names


def main():
    global ADDON
    ADDON, names = unpack_addon()
    removed_window = 'PrimeScene' + 'Player.xml'
    removed_class = 'class ' + 'Player' + 'Overlay'
    print('unpacked %s' % ZIP_PATH.name)
    if any(removed_window in name for name in names):
        print('FAIL release still packages the custom player window')
        return 1
    print('ok release does not package the custom player window')
    aurora_text = (ADDON / 'resources' / 'lib' / 'aurora.py').read_text(encoding='utf-8')
    service_text = (ADDON / 'resources' / 'lib' / 'service.py').read_text(encoding='utf-8')
    playback_text = (ADDON / 'resources' / 'lib' / 'playback.py').read_text(encoding='utf-8')
    if removed_class in aurora_text or 'Dialog.Close' in aurora_text or 'videoosd' in aurora_text:
        print('FAIL packaged catalog code still suppresses Kodi player dialogs')
        return 1
    print('ok packaged catalog code does not close Kodi player dialogs')
    if 'setResolvedUrl' not in playback_text or 'def PlayVideo' not in playback_text:
        print('FAIL PlayVideo no longer resolves through Kodi')
        return 1
    print('ok PlayVideo still resolves the stream with setResolvedUrl')

    xbmc = install_stubs()
    sys.path.insert(0, str(ADDON))
    from resources.lib.aurora import play_row

    WindowXML.created = []
    Monitor.created = []
    Player.created = []
    xbmc.builtins = []
    play_row({
        'title': 'Harbor Lights',
        'url': FILM_URL,
        'info': {'mediatype': 'movie', 'year': 2021},
    }, PROFILE)
    print('played %s' % [player.played for player in Player.created])
    print('windows %s' % WindowXML.created)
    print('monitors during play_row %s' % Monitor.created)
    print('player types %s' % [type(player).__name__ for player in Player.created])
    print('builtins %s' % xbmc.builtins)
    failed = False
    if [type(player) for player in Player.created] != [Player] or Player.created[0].played != [FILM_URL]:
        print('FAIL play_row did not hand the PlayVideo URL to Kodi\'s player')
        failed = True
    else:
        print('ok play_row hands the PlayVideo URL to Kodi\'s own player')
    if WindowXML.created or Monitor.created:
        print('FAIL starting playback instantiated a custom window or player monitor')
        failed = True
    else:
        print('ok starting playback instantiates no custom window or player monitor')
    if any('Dialog.Close' in item or 'videoosd' in item or 'seekbar' in item for item in xbmc.builtins):
        print('FAIL starting playback closed a Kodi player dialog')
        failed = True
    else:
        print('ok starting playback does not close the OSD or seek bar')

    # The service watcher records when Kodi playback ends so the catalog can
    # return to the same card. It must not open a window while the title plays.
    from resources.lib.aurora import watch_playback
    Player.start_playing = True
    WindowXML.created = []
    xbmc.builtins = []
    before_players = len(Player.created)
    watch_playback(PROFILE)
    print('service watcher windows %s' % WindowXML.created)
    print('service watcher builtins %s' % xbmc.builtins)
    print('service watcher new player types %s' % [type(player).__name__ for player in Player.created[before_players:]])
    if WindowXML.created or any('Dialog.Close' in item for item in xbmc.builtins):
        print('FAIL the playback watcher opened a window or closed a Kodi dialog')
        failed = True
    else:
        print('ok the playback watcher does not open a window or close Kodi dialogs')
    if failed:
        return 1
    print('all playback handoff checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
