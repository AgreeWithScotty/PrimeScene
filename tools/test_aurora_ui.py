#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless Prime Scene run with stubbed Kodi modules.

Checks the Aurora Cinema window XML, textures, routing, focus tint,
detail view and playback handoff. The catalogue bridge is faked; no
account and no network are used.
"""
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / 'packages' / 'plugin.video.primeinterface' / 'plugin.video.primeinterface-0.2.0.zip'
PROFILE = tempfile.mkdtemp(prefix='primescene-aurora-')

FILM_URL = ('plugin://plugin.video.primeinterface/?mode=PlayVideo&asin=B001'
            '&name=Harbor+Lights&adult=0&trailer=0&selbitrate=0')
SHOW_URL = ('plugin://plugin.video.primeinterface/?mode=PlayVideo&asin=B002'
            '&name=The+Midnight+Library&adult=0&trailer=0&selbitrate=0')
FILM = {
    'title': 'Harbor Lights',
    'url': FILM_URL,
    'video': True,
    'info': {
        'plot': 'A coastal town keeps a secret schedule.',
        'year': 2021,
        'mediatype': 'movie',
        'fanart': 'https://example.com/art/harbor-fan.jpg',
        'poster': 'https://example.com/art/harbor-poster.jpg',
        'thumb': 'https://example.com/art/harbor-poster.jpg',
    },
}
SHOW = {
    'title': 'The Midnight Library',
    'url': SHOW_URL,
    'video': True,
    'info': {
        'plot': 'A librarian walks through the lives she did not live.',
        'year': 2024,
        'mediatype': 'movie',
        'fanart': 'https://example.com/art/midnight.jpg',
        'thumb': 'https://example.com/art/midnight.jpg',
        'progress': 32,
    },
}
MOVIES = {
    'title': 'Movies',
    'url': 'plugin://plugin.video.primeinterface/pv/browse/root/movies',
    'video': False,
    'info': {'thumb': 'https://example.com/art/movies.jpg', 'plot': 'Films included with Prime.'},
}
TV = {
    'title': 'TV',
    'url': 'plugin://plugin.video.primeinterface/pv/browse/root/tv',
    'video': False,
    'info': {'thumb': 'https://example.com/art/tv.jpg', 'plot': 'Series included with Prime.'},
}


class InfoTag(object):
    def __init__(self):
        self.data = {}

    def setResumePoint(self, position, total=0):
        self.data['resume'] = (position, total)


class ListItem(object):
    def __init__(self, label='', label2='', path='', offscreen=False):
        self.label = label
        self.path = path
        self.art = {}
        self.properties = {}
        self.info = InfoTag()

    def setArt(self, art):
        self.art.update(art)

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, '')

    def getLabel(self):
        return self.label

    def getVideoInfoTag(self):
        return self.info


class Dialog(object):
    def __init__(self):
        self.inputs = []
        self.messages = []
        self.settings = 0

    def ok(self, heading, message):
        self.messages.append((heading, message))

    def input(self, heading, defaultt=''):
        return self.inputs.pop(0) if self.inputs else ''

    def yesno(self, heading, message, nolabel='', yeslabel=''):
        return False

    def select(self, heading, options):
        return -1


class Addon(object):
    def __init__(self, addon_id=None):
        self.id = addon_id or 'plugin.video.primeinterface'
        self.settings = {}

    def getAddonInfo(self, key):
        return {
            'id': 'plugin.video.primeinterface',
            'path': str(ADDON),
            'profile': PROFILE,
            'name': 'Prime Scene',
            'version': '0.2.0',
            'icon': str(ADDON / 'icon.png'),
            'fanart': str(ADDON / 'fanart.png'),
        }[key]

    def getSetting(self, key):
        return self.settings.get(key, '')

    def setSetting(self, key, value):
        self.settings[key] = value

    def openSettings(self):
        self.opened = getattr(self, 'opened', 0) + 1


class FakeControl(object):
    def __init__(self):
        self.items = []
        self.percent = 0
        self.color = ''
        self.position = 0

    def reset(self):
        self.items = []
        self.position = 0

    def addItem(self, item):
        self.items.append(item)

    def size(self):
        return len(self.items)

    def getSelectedPosition(self):
        return self.position

    def selectItem(self, position):
        self.position = position

    def setPercent(self, value):
        self.percent = value

    def setColorDiffuse(self, color):
        self.color = color


class WindowXML(object):
    opened = []
    shown = []

    def __init__(self, xml, path, skin, res):
        self.xml = xml
        self.properties = {}
        self.controls = {}

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, '')

    def getControl(self, control_id):
        return self.controls.setdefault(control_id, FakeControl())

    def setFocusId(self, control_id):
        self.focus = control_id

    def doModal(self):
        WindowXML.opened.append(self)
        self.onInit()

    def show(self):
        self.shown_flag = True
        WindowXML.shown.append(self)

    def close(self):
        self.closed = True


class WindowXMLDialog(WindowXML):
    pass


class Action(object):
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id


class Monitor(object):
    waits = 0
    limit = 1000

    def abortRequested(self):
        return False

    def waitForAbort(self, seconds=0):
        Monitor.waits += 1
        return Monitor.waits >= Monitor.limit


class Player(object):
    shared = None

    def __new__(cls):
        if cls.shared is None:
            cls.shared = object.__new__(cls)
            cls.shared.played = []
            cls.shared.playing = False
            cls.shared.time = 754
            cls.shared.total = 9360
        return cls.shared

    def play(self, url):
        self.played.append(url)
        self.playing = True

    def isPlaying(self):
        return self.playing

    def getTime(self):
        return self.time

    def getTotalTime(self):
        return self.total

    def getPlayingFile(self):
        return self.played[-1] if self.played else ''


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

    gui = type(sys)('xbmcgui')
    gui.ListItem = ListItem
    gui.Dialog = Dialog
    gui.WindowXML = WindowXML
    gui.WindowXMLDialog = WindowXMLDialog
    gui.getCurrentWindowId = lambda: 10025
    gui.NOTIFICATION_INFO = 'info'

    plugin = type(sys)('xbmcplugin')
    plugin.ended = []
    plugin.endOfDirectory = lambda *args, **kwargs: plugin.ended.append(args)

    addon = type(sys)('xbmcaddon')
    addon.Addon = Addon

    vfs = type(sys)('xbmcvfs')
    vfs.translatePath = lambda path: path
    vfs.exists = os.path.exists
    vfs.mkdirs = lambda path: os.makedirs(path, exist_ok=True) or True

    sys.modules['xbmc'] = xbmc
    sys.modules['xbmcgui'] = gui
    sys.modules['xbmcplugin'] = plugin
    sys.modules['xbmcaddon'] = addon
    sys.modules['xbmcvfs'] = vfs
    return xbmc


def unpack_addon():
    if not ZIP_PATH.is_file():
        raise SystemExit('missing %s' % ZIP_PATH)
    dest = Path(tempfile.mkdtemp(prefix='primescene-addon-'))
    with zipfile.ZipFile(ZIP_PATH) as archive:
        archive.extractall(dest)
        names = archive.namelist()
    addon = dest / 'plugin.video.primeinterface'
    if not (addon / 'addon.xml').is_file():
        raise SystemExit('zip has no addon.xml')
    return addon, names


def referenced_files(addon):
    skin = addon / 'resources' / 'skins' / 'Default'
    media = skin / 'media'
    fonts = skin / 'fonts'
    missing = []
    for name in ('Font.xml', 'Home.xml', 'Title.xml', 'Player.xml'):
        root = ET.parse(skin / '1080i' / name).getroot()
        print('parsed %s' % name)
        for node in root.iter():
            if node.tag == 'filename' and node.text:
                path = fonts / node.text.strip()
                if not path.is_file():
                    missing.append(str(path))
            if node.tag in ('texture', 'texturefocus', 'texturenofocus', 'bordertexture', 'texturebg', 'midtexture'):
                text = (node.text or '').strip()
                if not text or text.startswith('$INFO'):
                    continue
                path = media / text
                if not path.is_file():
                    missing.append('%s -> %s' % (name, text))
            if node.tag == 'colordiffuse' and node.text:
                color = node.text.strip()
                if len(color) != 8 or any(char not in '0123456789abcdefABCDEF' for char in color):
                    missing.append('bad color %s in %s' % (color, name))
    return missing


def wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class FakeBridge(object):
    calls = []

    def __init__(self, provider, g):
        self.provider = provider

    def browse_progressive(self, path, publish, active, query=None):
        FakeBridge.calls.append((path, query))
        if not active():
            return
        if query is not None:
            publish({'rows': [SHOW], 'next': None})
            return
        if path == 'root':
            publish({'rows': [MOVIES, TV, FILM], 'next': None})
        elif path == 'root/movies':
            publish({'rows': [FILM, SHOW], 'next': 'root/movies/nextPage'})
        elif path == 'root/tv':
            publish({'rows': [SHOW], 'next': None})
        elif str(path).endswith('/nextPage'):
            publish({'rows': [], 'next': None})
        else:
            publish({'rows': [SHOW], 'next': None})


def main():
    global ADDON
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)
            print('FAIL %s' % message)
        else:
            print('ok %s' % message)

    ADDON, names = unpack_addon()
    print('unpacked %s' % ZIP_PATH.name)
    missing = referenced_files(ADDON)
    check(not missing, 'every Aurora texture and font is in the add-on (%s)' % missing)
    check('plugin.video.primeinterface/resources/skins/Default/1080i/PrimeStreaming.xml' not in names,
          'old sidebar window is not packaged')
    check(not any('prime-nav-' in name or 'prime-card-gold' in name for name in names),
          'old sidebar textures are not packaged')
    addon_xml = (ADDON / 'addon.xml').read_text(encoding='utf-8')
    check('version="0.2.0"' in addon_xml, 'addon.xml version is 0.2.0')
    check('Aurora Cinema' in addon_xml, 'addon.xml describes Aurora Cinema')
    source = (ROOT / 'source' / 'rewrite' / 'aurora.py').read_bytes()
    packaged = (ADDON / 'resources' / 'lib' / 'aurora.py').read_bytes()
    check(source == packaged, 'packaged aurora.py matches source/rewrite/aurora.py')
    playback = (ADDON / 'resources' / 'lib' / 'playback.py').read_text(encoding='utf-8')
    service = (ADDON / 'resources' / 'lib' / 'service.py').read_text(encoding='utf-8')
    check('note_playback' in playback, 'playback hands the title to Aurora without replacing PlayVideo')
    check('watch_playback' in service, 'service shows the Aurora player overlay')
    check('from resources.lib.aurora import open_interface' in (ADDON / 'gui.py').read_text(encoding='utf-8'),
          'application entry opens Aurora')

    xbmc = install_stubs()
    sys.path.insert(0, str(ADDON))
    from resources.lib.aurora import (AuroraHome, PlayerOverlay, _TINTS, note_playback,
                                      open_interface, overlay_due, watch_playback)

    import resources.lib.aurora as aurora
    aurora.ProviderBridge = FakeBridge

    dialog = Dialog()
    addon = Addon()

    class Globals(object):
        pass

    g = Globals()
    g.pluginid = 'plugin://plugin.video.primeinterface/'
    g.PLUGIN_PATH = str(ADDON)
    g.DATA_PATH = PROFILE
    g.pv = object()
    g.dialog = dialog
    g.addon = addon

    class Settings(object):
        data_source = 0

    started = []

    def account_name():
        return 'Ada'

    def account_action(pause):
        started.append(pause)
        return False

    WindowXML.opened = []
    home = AuroraHome('Home.xml', str(ADDON), 'Default', '1080i',
                      globals_instance=g, initialize_provider=lambda: started.append('provider'),
                      account_name=account_name, account_action=account_action)
    home.doModal()
    ready = wait_until(lambda: len(home.rows) == 3 and home.getProperty('Section') == 'HOME')
    print('')
    print('=== aurora home ===')
    print('xml %s' % home.xml)
    print('section %s' % home.getProperty('Section'))
    print('nav %s' % home.getProperty('Nav'))
    print('title %s' % home.getProperty('HeroTitle'))
    print('tint %s' % home.getControl(101).color)
    print('cards %s' % home.getControl(200).size())
    check(ready, 'home row finished loading')
    check(home.xml == 'Home.xml', 'home loads Home.xml')
    check(home.getProperty('Nav') == 'home', 'home nav underline')
    check(home.getControl(200).size() == 3, 'home row lists categories and a title')
    check(home.getProperty('HeroTitle') == 'MOVIES', 'first card is the hero')
    check(home.getControl(101).color == _TINTS[0], 'initial tint is the first charcoal wash')
    check('provider' in started, 'provider initialises before the catalogue request')

    home.getControl(200).position = 2
    home.onFocus(200)
    print('focused %s tint %s' % (home.getProperty('HeroTitle'), home.getControl(101).color))
    check(home.getProperty('HeroTitle') == 'HARBOR LIGHTS', 'focused card becomes the hero')
    check(home.getControl(101).color == _TINTS[2], 'focus moves the ambient tint')
    check('SECRET SCHEDULE' in home.getProperty('HeroTag'), 'hero tag uses the synopsis')

    home.getControl(200).position = 0
    home.onAction(Action(2))
    print('stepped %s tint %s' % (home.getProperty('HeroTitle'), home.getControl(101).color))
    check(home.getProperty('HeroTitle') == 'TV', 'right moves the hero to the next card')
    check(home.getControl(101).color == _TINTS[1], 'right updates the ambient tint')

    home.onClick(302)
    ready = wait_until(lambda: home.path == 'root/movies' and home.getControl(200).size() >= 2)
    print('')
    print('=== movies ===')
    print('path %s section %s nav %s' % (home.path, home.getProperty('Section'), home.getProperty('Nav')))
    check(ready, 'movies shortcut opens that category')
    check(home.getProperty('Nav') == 'movies', 'movies underline follows the shortcut')
    check(any(item.getLabel() == 'HARBOR LIGHTS' for item in home.getControl(200).items), 'movies row lists the film')

    WindowXML.opened = []
    home.getControl(200).position = 0
    home.onClick(200)
    title = WindowXML.opened[-1]
    print('')
    print('=== title ===')
    print('xml %s kicker %s title %s' % (title.xml, title.getProperty('Kicker'), title.getProperty('HeroTitle')))
    print('synopsis %s related %s' % (title.getProperty('Synopsis'), title.getControl(210).size()))
    check(title.xml == 'Title.xml', 'detail view loads Title.xml')
    check(title.getProperty('Kicker') == 'A FILM', 'film kicker')
    check(title.getProperty('HeroTitle') == 'HARBOR LIGHTS', 'detail title')
    check('secret schedule' in title.getProperty('Synopsis'), 'synopsis is on the detail view')
    check(title.getControl(210).size() == 1, 'detail row lists the other title')

    title.onClick(310)
    print('play %s' % Player.shared.played)
    check(Player.shared.played == [FILM_URL], 'watch now hands the existing playback URL to the player')
    note = (Path(PROFILE) / 'aurora-playback.json').read_text(encoding='utf-8')
    check('Harbor Lights' in note, 'playback note carries the title for the overlay')

    title.onClick(210)
    check(Player.shared.played[-1] == SHOW_URL, 'a related card uses the same playback handoff')

    home.onAction(Action(10))
    ready = wait_until(lambda: home.path == 'root' and home.getProperty('Section') == 'HOME')
    check(ready, 'back returns from the category to home')

    dialog.inputs = ['Harbor']
    home.onClick(304)
    ready = wait_until(lambda: any(call[1] == 'Harbor' for call in FakeBridge.calls)
                       and home.getProperty('Section') == 'SEARCH')
    print('search calls %s' % [call for call in FakeBridge.calls if call[1]])
    check(ready, 'search sends the typed phrase to the catalogue')
    check(any(item.getLabel() == 'THE MIDNIGHT LIBRARY' for item in home.getControl(200).items),
          'search opens a result row')

    home.onClick(305)
    check(getattr(addon, 'opened', 0) == 1, 'settings opens the add-on settings')
    before = len(started)
    home.onClick(306)
    check(len(started) > before, 'account button reaches the sign-in and sign-out action')

    home.onClick(303)
    ready = wait_until(lambda: home.path == 'root/tv')
    check(ready, 'tv shortcut opens that category')

    signed = AuroraHome('Home.xml', str(ADDON), 'Default', '1080i',
                        globals_instance=g, account_name=lambda: '')
    signed.doModal()
    print('signed out focus %s tag %s' % (getattr(signed, 'focus', None), signed.getProperty('HeroTag')))
    check(getattr(signed, 'focus', None) == 306, 'signed-out screen focuses sign in')
    check('SIGN IN' in signed.getProperty('HeroTitle'), 'signed-out hero asks for sign in')
    signed.stop.set()

    settings = Settings()
    settings.data_source = 1
    check(open_interface(g, settings) == 'classic', 'a non-website source still opens the classic list')

    overlay = PlayerOverlay('Player.xml', str(ADDON), 'Default', '1080i')
    overlay.info = {'title': 'Harbor Lights', 'meta': '2021  •  FILM', 'position': 754, 'duration': 9360, 'volume': 35}
    overlay.doModal()
    print('')
    print('=== player ===')
    print('elapsed %s total %s progress %s' % (
        overlay.getProperty('Elapsed'), overlay.getProperty('Total'), overlay.getControl(400).percent))
    check(overlay.xml == 'Player.xml', 'player overlay loads Player.xml')
    check(overlay.getProperty('Title') == 'HARBOR LIGHTS', 'player title')
    check(overlay.getProperty('Elapsed') == '12:34', 'elapsed time')
    check(overlay.getProperty('Total') == '2:36:00', 'total time')
    check(overlay.getControl(400).percent == 8, 'progress percent')
    overlay.onClick(501)
    check('PlayerControl(Play)' in xbmc.builtins, 'pause button uses Kodi player controls')
    check(overlay_due(True, {'armed': time.time()}, time.time(), False), 'fresh playback may show the overlay')
    check(not overlay_due(True, {'armed': time.time() - 500}, time.time(), False),
          'an old note does not cover someone else playing')
    check(overlay_due(True, {'armed': time.time() - 500}, time.time(), True), 'overlay stays for the title already shown')
    check(not overlay_due(False, {'armed': time.time()}, time.time(), True), 'overlay leaves when playback stops')

    note_playback(PROFILE, 'Harbor Lights', '2021  •  FILM')
    Player.shared.playing = True
    Monitor.waits = 0
    Monitor.limit = 3
    watch_playback(PROFILE)
    shown = [window for window in WindowXML.shown if getattr(window, 'xml', '') == 'Player.xml']
    check(bool(shown), 'service overlay opens while the noted title is playing')
    if shown:
        print('service overlay %s %s' % (shown[-1].getProperty('Title'), shown[-1].getProperty('Elapsed')))
        check(shown[-1].getProperty('Title') == 'HARBOR LIGHTS', 'service overlay uses the noted title')

    startup_calls = []
    gui_calls = []
    startup = type(sys)('resources.lib.startup')
    startup.EntryPoint = lambda argv: startup_calls.append(list(argv))
    sys.modules['resources.lib.startup'] = startup
    gui = type(sys)('gui')
    gui.main = lambda **kwargs: gui_calls.append(kwargs)
    sys.modules['gui'] = gui
    import default
    sys.argv = ['plugin://plugin.video.primeinterface/?mode=PlayVideo&asin=B001', '7',
                '?mode=PlayVideo&asin=B001']
    default.main()
    print('')
    print('=== routing ===')
    print('entrypoint %s gui %s' % (len(startup_calls), len(gui_calls)))
    check(len(startup_calls) == 1 and not gui_calls, 'a playback URL keeps the resolver and does not open the window')
    check('mode=PlayVideo' in startup_calls[0][0], 'resolver arguments reach the existing entry point')

    gui_calls.clear()
    startup_calls.clear()
    sys.argv = [str(ADDON / 'default.py')]
    default.main()
    check(len(gui_calls) == 1 and not startup_calls, 'opening the add-on starts the Aurora window')

    print('')
    if failures:
        print('%s check(s) failed' % len(failures))
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
