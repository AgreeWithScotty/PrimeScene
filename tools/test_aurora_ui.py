#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless Prime Scene run with stubbed Kodi modules.

Checks the Aurora Cinema window XML, textures, routing, focus tint,
detail view and playback handoff. The catalogue bridge is faked; no
account and no network are used.
"""
import ast
import os
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / 'packages' / 'plugin.video.primeinterface' / 'plugin.video.primeinterface-0.2.7.zip'
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
        'duration': 6120,
        'mpaa': '16+',
        'genre': ['Drama'],
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
        'duration': 2700,
        'mpaa': 'TV-MA',
        'genre': 'Mystery',
        'mediatype': 'tvshow',
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
EPISODE_A_URL = ('plugin://plugin.video.primeinterface/?mode=PlayVideo&asin=B003E1'
                 '&name=The+Breaking&adult=0&trailer=0&selbitrate=0')
EPISODE_B_URL = ('plugin://plugin.video.primeinterface/?mode=PlayVideo&asin=B003E2'
                 '&name=To+the+Edge&adult=0&trailer=0&selbitrate=0')
EPISODE_A = {
    'title': 'The Breaking',
    'url': EPISODE_A_URL,
    'video': True,
    'info': {'mediatype': 'episode', 'season': 1, 'episode': 3, 'plot': 'The ice opens.', 'year': 2021},
}
EPISODE_B = {
    'title': 'To the Edge',
    'url': EPISODE_B_URL,
    'video': True,
    'info': {'mediatype': 'episode', 'season': 1, 'episode': 4, 'plot': 'The ship turns.', 'year': 2021},
}
SERIES_LIST = {
    'title': 'The North Water',
    'url': 'plugin://plugin.video.primeinterface/pv/browse/__storefront__/B003SERIES',
    'video': False,
    'info': {
        'mediatype': 'tvshow',
        'plot': 'A man can disappear up here.',
        'year': 2021,
        'seasons': '1',
        'genre': ['Drama'],
    },
}
SERIES_NEXT = {
    'title': 'Succession',
    'url': 'plugin://plugin.video.primeinterface/pv/browse/__storefront__/B004NEXT',
    'video': False,
    'info': {
        'mediatype': 'tvshow',
        'year': 2023,
        'seasons': '4',
        'plot': 'A family decides.',
        'next': {
            'title': 'America Decides',
            'url': EPISODE_A_URL,
            'video': True,
            'info': {'mediatype': 'episode', 'season': 4, 'episode': 8},
        },
    },
}
STORE_MOVIE = {
    'title': 'Blade Runner 2049',
    'url': 'plugin://plugin.video.primeinterface/pv/browse/__storefront__/B009MOVIE',
    'video': False,
    'info': {'mediatype': 'movie', 'plot': 'A new blade runner.', 'year': 2017},
}
MADEA_URL = ('plugin://plugin.video.primeinterface/?mode=PlayVideo&asin=B010ASIN'
             '&name=Tyler+Perry%27s+A+Madea+Christmas&adult=0&trailer=0&selbitrate=0')
BARE = {
    'title': "Tyler Perry's A Madea Christmas",
    'url': 'plugin://plugin.video.primeinterface/pv/browse/__storefront__/B010MADEA',
    'video': False,
    'identity': 'B010MADEA',
    'info': {'thumb': 'https://example.com/art/madea.jpg', 'mediatype': 'movie'},
}
MADEA = {
    'title': "Tyler Perry's A Madea Christmas",
    'url': MADEA_URL,
    'video': True,
    'info': {
        'plot': 'Madea (Tyler Perry) dishes out her own brand of Christmas spirit in a small, rural town.',
        'year': 2013,
        'duration': 5940,
        'mpaa': 'PG-13',
        'genre': ['Comedy', 'Drama', 'Romance'],
        'mediatype': 'movie',
        'fanart': 'https://example.com/art/madea-fan.jpg',
        'thumb': 'https://example.com/art/madea.jpg',
    },
}
SEASON_ROW = {
    'title': 'Season 1',
    'url': 'plugin://plugin.video.primeinterface/pv/browse/__storefront__/B007SEASONS/s1',
    'video': False,
    'info': {'mediatype': 'season', 'season': 1, 'plot': 'The first winter.'},
}
SERIES_SEASONS = {
    'title': 'Unpicked',
    'url': 'plugin://plugin.video.primeinterface/pv/browse/__storefront__/B007SEASONS',
    'video': False,
    'info': {'mediatype': 'tvshow', 'plot': 'Several seasons, no episode yet.', 'year': 2019, 'seasons': '2'},
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
    choices = []
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
        Dialog.choices.append((heading, list(options)))
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
            'name': 'Smooth Prime',
            'version': '0.2.7',
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

    def getListItem(self, index):
        return self.items[index]

    def getPercent(self):
        return self.percent

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

    def getFocusId(self):
        return getattr(self, 'focus', 0)

    def getCurrentContainerId(self):
        return 200

    def clearList(self):
        self.getControl(200).reset()

    def addItems(self, items):
        control = self.getControl(200)
        for item in items:
            control.addItem(item)

    def getCurrentListPosition(self):
        return self.getControl(200).getSelectedPosition()

    def setCurrentListPosition(self, position):
        self.getControl(200).selectItem(position)

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

    def seekTime(self, seconds):
        self.time = float(seconds)

    def stop(self):
        self.playing = False

    def pause(self):
        self.paused = not getattr(self, 'paused', False)

    def getAvailableSubtitleStreams(self):
        return list(getattr(self, 'subs', []) or [])

    def getAvailableAudioStreams(self):
        return list(getattr(self, 'audios', []) or [])

    def setSubtitleStream(self, index):
        self.subtitle_stream = index

    def setAudioStream(self, index):
        self.audio_stream = index

    def showSubtitles(self, enabled):
        self.subtitles_on = bool(enabled)


def install_stubs():
    xbmc = type(sys)('xbmc')
    xbmc.LOGINFO = 1
    xbmc.LOGERROR = 4
    xbmc.log = lambda *args, **kwargs: None
    xbmc.builtins = []
    xbmc.executebuiltin = lambda builtin: xbmc.builtins.append(builtin)
    xbmc.getInfoLabel = lambda key: '21.3 (21.3.0)'
    xbmc.conditions = {}
    xbmc.getCondVisibility = lambda key: bool(xbmc.conditions.get(key, False))
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
    for name in ('Font.xml', 'PrimeSceneHome.xml', 'PrimeScenePlayer.xml'):
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

    def __init__(self, provider, g, initialize_provider=None):
        self.provider = provider
        self.initialize_provider = initialize_provider
        self.details = {}
        self.continuations = {}

    def browse_progressive(self, path, publish, active, query=None):
        FakeBridge.calls.append((path, query))
        if not active():
            return
        if query is not None:
            publish({'rows': [SHOW], 'next': None})
            return
        if path == 'root':
            publish({'rows': [FILM, SHOW, BARE], 'next': None})
        elif path in ('__prime_movies__', 'root/movies'):
            publish({'rows': [FILM, SHOW, BARE], 'next': 'root/movies/nextPage'})
        elif path in ('__prime_tv__', 'root/tv'):
            publish({'rows': [SHOW, BARE], 'next': None})
        elif str(path).endswith('B003SERIES'):
            publish({'rows': [EPISODE_A, EPISODE_B], 'next': None})
        elif str(path).endswith('B009MOVIE'):
            publish({'rows': [FILM], 'next': None})
        elif str(path).endswith('B010MADEA'):
            publish({'rows': [MADEA], 'next': None})
        elif str(path).endswith('B007SEASONS/s1'):
            publish({'rows': [], 'next': None})
        elif str(path).endswith('B007SEASONS'):
            publish({'rows': [SEASON_ROW], 'next': None})
        elif str(path).endswith('/nextPage'):
            publish({'rows': [], 'next': None})
        else:
            publish({'rows': [SHOW], 'next': None})


# skin.estuary xml/ on Kodi 21.2 Omega. WindowXML uses one of these names
# from the active skin and does not open the add-on copy.
_OMEGA_ESTUARY_WINDOWS = frozenset((
    'AddonBrowser.xml', 'Custom_1100_AddonLauncher.xml', 'Custom_1101_SettingsList.xml',
    'Custom_1102_TextViewer.xml', 'Custom_1103_VolumeSlider.xml', 'Custom_1104_Fanart.xml',
    'Custom_1105_MusicOSDSettings.xml', 'Custom_1107_SearchDialog.xml',
    'Custom_1109_TopBarOverlay.xml', 'Custom_1110_TempoControl.xml', 'Defaults.xml',
    'DialogAddonInfo.xml', 'DialogAddonSettings.xml', 'DialogBusy.xml', 'DialogButtonMenu.xml',
    'DialogColorPicker.xml', 'DialogConfirm.xml', 'DialogContextMenu.xml',
    'DialogExtendedProgressBar.xml', 'DialogFullScreenInfo.xml', 'DialogGameControllers.xml',
    'DialogKeyboard.xml', 'DialogMediaSource.xml', 'DialogMusicInfo.xml', 'DialogNotification.xml',
    'DialogNumeric.xml', 'DialogPVRChannelGuide.xml', 'DialogPVRChannelManager.xml',
    'DialogPVRChannelsOSD.xml', 'DialogPVRGroupManager.xml', 'DialogPVRGuideControls.xml',
    'DialogPVRGuideSearch.xml', 'DialogPVRInfo.xml', 'DialogPVRRadioRDSInfo.xml',
    'DialogPictureInfo.xml', 'DialogPlayerProcessInfo.xml', 'DialogSeekBar.xml',
    'DialogSelect.xml', 'DialogSettings.xml', 'DialogSlider.xml', 'DialogSubtitles.xml',
    'DialogTextViewer.xml', 'DialogVideoInfo.xml', 'DialogVideoManager.xml', 'DialogVolumeBar.xml',
    'EventLog.xml', 'FileBrowser.xml', 'FileManager.xml', 'Font.xml', 'GameOSD.xml', 'Home.xml',
    'Includes.xml', 'Includes_Animations.xml', 'Includes_Buttons.xml', 'Includes_DialogSelect.xml',
    'Includes_Games.xml', 'Includes_Home.xml', 'Includes_MediaMenu.xml', 'Includes_MusicInfo.xml',
    'Includes_PVR.xml', 'LoginScreen.xml', 'MusicOSD.xml', 'MusicVisualisation.xml',
    'MyFavourites.xml', 'MyGames.xml', 'MyMusicNav.xml', 'MyMusicPlaylistEditor.xml',
    'MyPVRChannels.xml', 'MyPVRGuide.xml', 'MyPVRRecordings.xml', 'MyPVRSearch.xml',
    'MyPVRTimers.xml', 'MyPics.xml', 'MyPlaylist.xml', 'MyPrograms.xml', 'MyVideoNav.xml',
    'MyWeather.xml', 'PlayerControls.xml', 'Pointer.xml', 'Settings.xml', 'SettingsCategory.xml',
    'SettingsProfile.xml', 'SettingsScreenCalibration.xml', 'SettingsSystemInfo.xml',
    'SkinSettings.xml', 'SlideShow.xml', 'SmartPlaylistEditor.xml', 'SmartPlaylistRule.xml',
    'Startup.xml', 'Timers.xml', 'Variables.xml', 'VideoFullScreen.xml', 'VideoOSD.xml',
    'VideoOSDBookmarks.xml', 'View_500_Wall.xml', 'View_501_Banner.xml', 'View_502_FanArt.xml',
    'View_503_NowPlaying.xml', 'View_50_List.xml', 'View_51_Poster.xml', 'View_52_IconWall.xml',
    'View_53_Shift.xml', 'View_54_InfoWall.xml', 'View_55_WideList.xml',
))
_CONTAINER_TYPES = frozenset(('fixedlist', 'list', 'wraplist', 'panel'))
_CONTROL_METHODS = {
    'reset': _CONTAINER_TYPES,
    'addItem': _CONTAINER_TYPES,
    'addItems': _CONTAINER_TYPES,
    'getSelectedPosition': _CONTAINER_TYPES,
    'selectItem': _CONTAINER_TYPES,
    'size': _CONTAINER_TYPES,
    'getListItem': _CONTAINER_TYPES,
    'setColorDiffuse': frozenset(('image',)),
    'setImage': frozenset(('image',)),
    'setPercent': frozenset(('progress', 'slider')),
}


def _module_strings(tree):
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values[node.targets[0].id] = value.value
    return values


def _string_value(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _getcontrol_id(node):
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != 'getControl' or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and type(arg.value) is int:
        return arg.value
    return 'dynamic'


def _control_uses(class_node):
    """Map getControl ids in one window class to the methods called on them."""
    uses = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.bound = {}

        def visit_FunctionDef(self, node):
            saved = self.bound
            self.bound = {}
            for stmt in node.body:
                self.visit(stmt)
            self.bound = saved

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            control_id = _getcontrol_id(node.value)
            if (control_id not in (None, 'dynamic') and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                self.bound[node.targets[0].id] = control_id
                uses.setdefault(control_id, set())
            elif control_id == 'dynamic':
                uses.setdefault('dynamic', set())
            self.generic_visit(node)

        def visit_Call(self, node):
            control_id = _getcontrol_id(node)
            if control_id == 'dynamic':
                uses.setdefault('dynamic', set())
            elif control_id is not None:
                uses.setdefault(control_id, set())
            func = node.func
            if isinstance(func, ast.Attribute):
                target = None
                if isinstance(func.value, ast.Name):
                    target = self.bound.get(func.value.id)
                if target is None:
                    target = _getcontrol_id(func.value)
                if target not in (None, 'dynamic'):
                    uses.setdefault(target, set()).add(func.attr)
            self.generic_visit(node)

    Visitor().visit(class_node)
    return uses


def _xml_controls(path):
    found = {}
    duplicates = []
    for node in ET.parse(path).getroot().iter('control'):
        raw = node.attrib.get('id')
        if not raw:
            continue
        control_id = int(raw)
        if control_id in found:
            duplicates.append(control_id)
        found[control_id] = (node.attrib.get('type') or '').strip().lower()
    return found, duplicates


def _path_mentions_resources(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            text = child.value.replace('\\', '/')
            if 'resources/' in text or text.endswith('.xml'):
                return True
    return False


def windowxml_contract(addon, packaged_source, zip_names):
    """Kodi loads <addon>/resources/skins/<skin>/<res>/<xmlFilename> only when the active skin has no such file."""
    problems = []
    tree = ast.parse(packaged_source)
    constants = _module_strings(tree)
    classes = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [base.attr if isinstance(base, ast.Attribute) else getattr(base, 'id', '') for base in node.bases]
        if 'WindowXML' in bases or 'WindowXMLDialog' in bases:
            classes[node.name] = node
    windows = {}

    class Calls(ast.NodeVisitor):
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id in classes and len(node.args) >= 4:
                xml_name = _string_value(node.args[0], constants)
                skin = _string_value(node.args[2], constants)
                res = _string_value(node.args[3], constants)
                if not xml_name or not skin or not res:
                    problems.append('%s WindowXML arguments are not literal xml, skin and resolution' % node.func.id)
                else:
                    windows.setdefault(node.func.id, set()).add((xml_name, skin, res))
                if _path_mentions_resources(node.args[1]):
                    problems.append('%s scriptPath includes a resources path; it must be the add-on root' % node.func.id)
            self.generic_visit(node)

    Calls().visit(tree)
    for name, node in classes.items():
        if name not in windows:
            problems.append('%s is never constructed with an xml filename' % name)
            continue
        if len(windows[name]) != 1:
            problems.append('%s is constructed with more than one window file: %s' % (name, sorted(windows[name])))
            continue
        xml_name, skin, res = next(iter(windows[name]))
        relative = 'resources/skins/%s/%s/%s' % (skin, res, xml_name)
        archived = 'plugin.video.primeinterface/' + relative
        if xml_name in _OMEGA_ESTUARY_WINDOWS:
            problems.append(
                '%s collides with skin.estuary on Kodi 21, so WindowXML never opens %s'
                % (xml_name, archived))
        if archived not in zip_names:
            problems.append('packaged zip is missing %s' % archived)
        xml_path = addon / relative
        if not xml_path.is_file():
            problems.append('unpacked add-on is missing %s' % relative)
            continue
        controls, duplicates = _xml_controls(xml_path)
        if duplicates:
            problems.append('%s repeats control ids %s' % (xml_name, sorted(set(duplicates))))
        uses = _control_uses(node)
        if 'dynamic' in uses:
            problems.append('%s calls getControl with a non-literal id' % name)
        for control_id, methods in sorted((key, value) for key, value in uses.items() if key != 'dynamic'):
            if control_id not in controls:
                problems.append('%s getControl(%s) is not a control in %s' % (name, control_id, xml_name))
                continue
            allowed = None
            for method in methods:
                expected = _CONTROL_METHODS.get(method)
                if expected is None:
                    continue
                allowed = expected if allowed is None else allowed & expected
            actual = controls[control_id]
            if allowed is not None and actual not in allowed:
                problems.append(
                    '%s getControl(%s) is <%s> in %s; the calls need %s'
                    % (name, control_id, actual or 'unknown', xml_name, ', '.join(sorted(allowed))))
    return problems


def _lateral_work(source):
    """AuroraHome must not touch the window or the list while left and right move."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != 'AuroraHome':
            continue
        methods = {item.name: item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
        problems = []
        if 'onFocus' in methods:
            problems.append('AuroraHome.onFocus still runs on focus changes')
        action = methods.get('onAction')
        if action is None:
            return problems + ['AuroraHome.onAction is missing']
        names = set()
        for child in ast.walk(action):
            if isinstance(child, ast.Attribute):
                names.add(child.attr)
            elif isinstance(child, ast.Name):
                names.add(child.id)
        blocked = names & {'setProperty', 'getControl', '_apply_hero', '_hero_from_list', '_selected_index', '_LATERAL'}
        if blocked:
            problems.append('onAction references %s' % ', '.join(sorted(blocked)))
        return problems
    return ['AuroraHome is missing']


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
    check('version="0.2.7"' in addon_xml, 'addon.xml version is 0.2.7')
    check('name="Smooth Prime"' in addon_xml, 'addon.xml name is Smooth Prime')
    check('Aurora Cinema' in addon_xml, 'addon.xml describes Aurora Cinema')
    source = (ROOT / 'source' / 'rewrite' / 'aurora.py').read_bytes()
    packaged = (ADDON / 'resources' / 'lib' / 'aurora.py').read_bytes()
    check(source == packaged, 'packaged aurora.py matches source/rewrite/aurora.py')
    check((ROOT / 'source' / 'rewrite' / 'gui.py').read_bytes() == (ADDON / 'gui.py').read_bytes(),
          'packaged gui.py matches source/rewrite/gui.py')
    for packaged_name, source_name in (
            ('resources/lib/public_catalog.py', 'public_catalog.py'),
            ('resources/lib/dedicated_bridge.py', 'dedicated_bridge.py'),
            ('resources/skins/Default/1080i/PrimeSceneHome.xml', 'skins/Default/1080i/PrimeSceneHome.xml'),
            ('resources/skins/Default/1080i/PrimeScenePlayer.xml', 'skins/Default/1080i/PrimeScenePlayer.xml'),
            ('resources/skins/Default/1080i/Font.xml', 'skins/Default/1080i/Font.xml'),
            ('resources/lib/title_art.py', 'title_art.py'),
            ('resources/lib/title_glyphs.json', 'title_glyphs.json'),
            ('resources/lib/title_glyphs.py', 'title_glyphs.py')):
        check((ROOT / 'source' / 'rewrite' / source_name).read_bytes() == (ADDON / packaged_name).read_bytes(),
              'packaged %s matches source' % source_name)
    check('PrimeSceneTitle.xml' not in ''.join(names), 'the title window is not packaged')
    home_xml = (ADDON / 'resources/skins/Default/1080i/PrimeSceneHome.xml').read_text(encoding='utf-8')
    aurora_text = packaged.decode('utf-8')
    check('EXPLORE' not in aurora_text and 'EXPLORE' not in home_xml,
          'EXPLORE filler is gone from the window and the catalogue labels')
    check('category-cinema' not in aurora_text and 'category-' not in home_xml and 'fanart.jpg' not in home_xml,
          'home does not use the gold theater art or category icons')
    for binding in (
            'Container(200).ListItem.Art(fanart)',
            'Container(200).ListItem.Art(thumb)',
            'Container(200).ListItem.Property(TitleArt)',
            'Container(200).ListItem.Property(Meta)',
            'Container(200).ListItem.Property(Plot)'):
        check(binding in home_xml, 'hero binds %s' % binding)
    home_root = ET.parse(ADDON / 'resources/skins/Default/1080i/PrimeSceneHome.xml').getroot()
    buttons = [node.attrib.get('id') for node in home_root.iter('control')
               if (node.attrib.get('type') or '').lower() == 'button']
    check(set(buttons) <= {'301', '302', '303', '304', '305', '306'},
          'home buttons are the navigation row (%s)' % buttons)
    check('WATCH NOW' not in home_xml and '>WATCH<' not in home_xml and '>DETAILS<' not in home_xml,
          'the hero has no watch or details button')
    boxes = [node for node in home_root.iter('control') if (node.attrib.get('type') or '').lower() == 'textbox']
    check(len(boxes) == 1, 'the synopsis is one textbox')
    if boxes:
        synopsis = boxes[0].findtext('label') or ''
        autoscroll = boxes[0].find('autoscroll')
        check(synopsis == '$INFO[Container(200).ListItem.Property(Plot)]',
              'synopsis textbox is bound to the focused card')
        check(autoscroll is not None and (autoscroll.text or '').strip() == 'true'
              and autoscroll.attrib.get('delay') and autoscroll.attrib.get('time'),
              'synopsis textbox autoscrolls')
    check('bordersize>4<' in home_xml, 'focused card has a 4px border')
    check('effect="zoom"' not in home_xml and 'effect="slide"' not in home_xml,
          'focus does not zoom or slide the card')
    check('<scrolltime>0</scrolltime>' in home_xml, 'the row has no scroll delay')
    check('<fadetime>0</fadetime>' in home_xml, 'hero art swaps without a fade')
    check('focus-pill.png' in home_xml, 'navigation uses a high-contrast focus texture')
    check('button-focus.png' not in home_xml, 'hero buttons are gone')
    check('<font>Hero</font>' not in home_xml, 'the hero title is not the skin font13 fallback label')
    title_art = [node for node in home_root.iter('control')
                 if node.attrib.get('id') in ('110', '111')]
    check(len(title_art) == 2, 'home has the large title plate and the empty-state plate')
    for node in title_art:
        check(node.attrib.get('type') == 'image', 'title plate is an image')
        check(int(node.findtext('width') or 0) >= 1000 and int(node.findtext('height') or 0) >= 240,
              'title plate is the large hero box')
        check((node.findtext('aspectratio') or '').strip() == 'center', 'title plate is not scaled away from its pixels')
    check('<colordiffuse>99141A20</colordiffuse>' in home_xml, 'hero scrim is the matte slate wash')
    lateral = _lateral_work(aurora_text)
    for problem in lateral:
        check(False, problem)
    check(not lateral, 'no Python handler does work on plain left/right moves')
    bridge_text = (ADDON / 'resources/lib/dedicated_bridge.py').read_text(encoding='utf-8')
    home_branch = bridge_text.split('def _browse_progressive', 1)[1].split('\n    def browse', 1)[0]
    check("public_packet('home'" in home_branch, 'home loads the signed-in storefront')
    check('dashboard_packet' not in home_branch, 'home does not open the category-tile dashboard')
    contract = windowxml_contract(ADDON, packaged.decode('utf-8'), names)
    for problem in contract:
        check(False, problem)
    check(not contract, 'WindowXML path contract and getControl types')
    playback = (ADDON / 'resources' / 'lib' / 'playback.py').read_text(encoding='utf-8')
    service = (ADDON / 'resources' / 'lib' / 'service.py').read_text(encoding='utf-8')
    check('note_playback' in playback, 'playback hands the title to Aurora without replacing PlayVideo')
    check('watch_playback' in service, 'service shows the Aurora player overlay')
    gui_source = (ADDON / 'gui.py').read_text(encoding='utf-8')
    check('from resources.lib.aurora import' in gui_source and 'open_interface' in gui_source,
          'application entry opens Aurora')

    xbmc = install_stubs()
    sys.path.insert(0, str(ADDON))
    from resources.lib.aurora import (AuroraHome, PlayerOverlay, _SCRIM, _fit_art, _item, _meta,
                                      choose_playback, note_playback, open_interface, overlay_due,
                                      playback_row, watch_playback)
    from resources.lib.public_catalog import parse_cards

    import resources.lib.aurora as aurora
    import resources.lib.navigation as navigation
    # Home, Movies and TV go through StorefrontLoader, which constructs its own bridge.
    navigation.ProviderBridge = FakeBridge
    aurora.ProviderBridge = FakeBridge
    PlayerOverlay.automatic = False

    def png_size(path):
        data = Path(path).read_bytes()
        assert data[12:16] == b'IHDR'
        return struct.unpack('>II', data[16:24])

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
    home = AuroraHome('PrimeSceneHome.xml', str(ADDON), 'Default', '1080i',
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
    print('meta %s' % home.getProperty('HeroMeta'))
    print('tint %s' % home.getControl(101).color)
    print('cards %s' % home.getControl(200).size())
    check(ready, 'home row finished loading')
    check(home.xml == 'PrimeSceneHome.xml', 'home loads PrimeSceneHome.xml')
    check(home.getProperty('Nav') == 'home', 'home nav underline')
    check(home.getControl(200).size() == 3, 'home row lists real titles')
    check(_SCRIM == '99141A20', 'the scrim constant matches the skin')
    first = home.getControl(200).items[0]
    print('card meta %s art %s' % (first.properties.get('Meta'), sorted(first.art)))
    check(first.getLabel() == 'HARBOR LIGHTS', 'first card carries the hero title')
    check('2021' in first.properties.get('Meta', '') and '1H 42M' in first.properties.get('Meta', '')
          and '16+' in first.properties.get('Meta', '') and 'DRAMA' in first.properties.get('Meta', ''),
          'card sublabel uses year, runtime, rating and genre')
    check(not any('EXPLORE' in (item.getLabel() + item.properties.get('Meta', ''))
                  for item in home.getControl(200).items),
          'no card label is EXPLORE')
    check(first.art.get('fanart') == FILM['info']['fanart'] and first.art.get('thumb') == FILM['info']['thumb']
          and first.art.get('poster') == FILM['info']['poster'],
          'each title sets fanart, thumb and poster')
    check(first.properties.get('Plot', '').startswith('A coastal town'), 'card carries the synopsis')
    check(first.properties.get('Plot') == FILM['info']['plot'], 'the synopsis is the full plot')
    check(_meta({'title': 'Shelf', 'video': False, 'info': {}}) == '', 'a missing field stays blank')
    series_meta = _meta({'title': 'Shelf', 'video': False,
                         'info': {'mediatype': 'tvshow', 'year': 2021, 'seasons': '2', 'duration': 3600,
                                  'genre': ['Drama', 'Mystery']}})
    check('2021' in series_meta and '2 SEASONS' in series_meta and 'DRAMA' in series_meta
          and 'MYSTERY' in series_meta and '1H' not in series_meta,
          'a series line shows year, season count and genres')
    check('2024' in home.getControl(200).items[1].properties.get('Meta', '')
          and 'TV-MA' in home.getControl(200).items[1].properties.get('Meta', '')
          and 'MYSTERY' in home.getControl(200).items[1].properties.get('Meta', ''),
          'a series card shows its own metadata')
    long_plot = ('A man can disappear up here. ' * 40).strip()
    sized = _item({'title': 'The North Water', 'url': 'x', 'video': False, 'info': {
        'plot': long_plot, 'mediatype': 'tvshow', 'year': 2021, 'seasons': '1',
        'fanart': 'https://m.media-amazon.com/images/I/north._UR1920,1080_SX3000_.jpg',
        'thumb': 'https://m.media-amazon.com/images/I/north-card.jpg'}})
    check(sized.properties.get('Plot') == long_plot and '…' not in sized.properties.get('Plot', ''),
          'a long synopsis is stored whole for the scrolling textbox')
    check(sized.art.get('fanart', '').endswith('/north._SX1280_.jpg'), 'hero art requests a 1280-wide cache file')
    check(sized.art.get('thumb', '').endswith('/north-card._SX720_.jpg'), 'card art requests a 720-wide cache file')
    check(_fit_art('https://example.com/art/harbor-fan.jpg', 1280) == 'https://example.com/art/harbor-fan.jpg',
          'non-Amazon artwork is left unchanged')
    check(playback_row(FILM)['url'] == FILM_URL, 'a movie card is its PlayVideo url')
    check(playback_row(SERIES_NEXT)['url'] == EPISODE_A_URL, 'a series plays the next episode when the card has one')
    check(playback_row(SERIES_LIST) is None, 'a series with only a detail link is not a single playable item')
    check('provider' not in started, 'the home screen does not start the playback provider')

    writes = []
    gets = []
    original_set = home.setProperty
    original_get = home.getControl

    def spy_set(key, value):
        writes.append(key)
        return original_set(key, value)

    def spy_get(control_id):
        gets.append(control_id)
        return original_get(control_id)

    home.setProperty = spy_set
    home.getControl = spy_get
    home.onAction(Action(1))
    home.onAction(Action(2))
    home.setProperty = original_set
    home.getControl = original_get
    print('lateral writes %s gets %s' % (writes, gets))
    check(writes == [] and gets == [], 'left and right do no window or control work')

    def bare_item(window):
        for item in window.getControl(200).items:
            if item.getLabel().startswith('TYLER PERRY'):
                return item
        return None

    def bare_filled(window):
        item = bare_item(window)
        meta = item.properties.get('Meta', '') if item is not None else ''
        plot = item.properties.get('Plot', '') if item is not None else ''
        return '2013' in meta and '1H 39M' in meta and 'PG-13' in meta and 'COMEDY' in meta and plot.startswith('Madea')

    home.getControl(200).position = 2
    check(wait_until(lambda: bare_filled(home)), 'home fills metadata and synopsis for a card that had neither')
    check(wait_until(lambda: bool((bare_item(home).properties.get('TitleArt') if bare_item(home) else ''))),
          'the focused long title is drawn')
    madea_plate = png_size(bare_item(home).properties.get('TitleArt', ''))
    home.getControl(200).position = 0
    check(wait_until(lambda: bool(home.getControl(200).items[0].properties.get('TitleArt'))),
          'the focused one-line title is drawn')
    harbor_plate = png_size(home.getControl(200).items[0].properties.get('TitleArt', ''))
    home.getControl(200).position = 2
    print('plates harbor %s madea %s' % (harbor_plate, madea_plate))
    check(harbor_plate[1] >= 120 and harbor_plate[0] >= 400, 'a one-line title is the large Bebas plate')
    check(madea_plate[1] > harbor_plate[1] and madea_plate[1] >= 200,
          'a long title uses the second line of the large plate')
    home_path = home.path
    WindowXML.opened = []
    Player().played.clear()
    home.onClick(200)
    ready = wait_until(lambda: Player.shared.played[-1:] == [MADEA_URL] and home.path == home_path and not home.busy)
    print('bare play %s path %s opened %s' % (Player.shared.played, home.path, WindowXML.opened))
    check(ready, 'a card with no ASIN plays through PlayVideo')
    check(not WindowXML.opened and home.path == home_path, 'that play does not open another directory or window')

    home.onClick(302)
    ready = wait_until(lambda: home.path == '__prime_movies__' and home.getControl(200).size() >= 2)
    print('')
    print('=== movies ===')
    print('path %s section %s nav %s' % (home.path, home.getProperty('Section'), home.getProperty('Nav')))
    check(ready, 'movies shortcut opens that category')
    check(home.getProperty('Nav') == 'movies', 'movies underline follows the shortcut')
    check(any(item.getLabel() == 'HARBOR LIGHTS' for item in home.getControl(200).items), 'movies row lists the film')
    home.getControl(200).position = next(index for index, item in enumerate(home.getControl(200).items)
                                         if item.getLabel().startswith('TYLER PERRY'))
    check(wait_until(lambda: bare_filled(home)), 'movies fills metadata and synopsis for a card that had neither')
    check(png_size(bare_item(home).properties.get('TitleArt', ''))[1] >= 200,
          'movies uses the large title plate')

    WindowXML.opened = []
    Player().played.clear()
    home.getControl(200).position = 0
    home.onClick(200)
    print('')
    print('=== play ===')
    print('played %s opened %s' % (Player.shared.played, [getattr(window, 'xml', '') for window in WindowXML.opened]))
    check(Player.shared.played == [FILM_URL], 'card click hands the existing playback URL to the player')
    check(not WindowXML.opened, 'card click does not open another screen')
    note = (Path(PROFILE) / 'aurora-playback.json').read_text(encoding='utf-8')
    check('Harbor Lights' in note, 'playback note carries the title for the overlay')

    home.onAction(Action(10))
    ready = wait_until(lambda: home.path == 'root' and not home.busy and home.getControl(200).size() == 3
                       and any(item.getLabel() == 'HARBOR LIGHTS' for item in home.getControl(200).items))
    check(ready, 'back returns from the category to home')

    def stage_row(window, row):
        item = ListItem(label=(row.get('title') or '').upper())
        with window.lock:
            window.rows.append(dict(row))
            window.items.append(item)
        window.getControl(200).addItem(item)
        window.getControl(200).position = len(window.rows) - 1

    stage_row(home, SERIES_NEXT)
    path_before = home.path
    home.onClick(200)
    print('next episode %s path %s' % (Player.shared.played[-1], home.path))
    check(Player.shared.played[-1] == EPISODE_A_URL, 'a series card plays its next episode')
    check(home.path == path_before, 'playing the next episode stays on this row')

    stage_row(home, SERIES_LIST)
    path_before = home.path
    played_before = len(Player.shared.played)
    WindowXML.opened = []
    home.onClick(200)
    ready = wait_until(lambda: len(Player.shared.played) > played_before and home.path == path_before and not home.busy)
    print('series path %s played %s opened %s' % (home.path, Player.shared.played[-1:], WindowXML.opened))
    check(ready and Player.shared.played[-1] == EPISODE_A_URL,
          'a series with episodes plays the earliest one and does not open a list')
    check(not WindowXML.opened, 'that episode does not open another window')

    resumed, why = choose_playback([
        {'title': 'Later', 'url': EPISODE_B_URL, 'video': True,
         'info': {'mediatype': 'episode', 'season': 1, 'episode': 4, 'progress': 10}},
        {'title': 'Earlier', 'url': EPISODE_A_URL, 'video': True,
         'info': {'mediatype': 'episode', 'season': 1, 'episode': 1}},
    ])
    check(why == 'resume' and resumed['url'] == EPISODE_B_URL, 'an in-progress episode is the one that plays')

    stage_row(home, STORE_MOVIE)
    path_before = home.path
    home.onClick(200)
    ready = wait_until(lambda: home.path == path_before and Player.shared.played[-1] == FILM_URL and not home.getProperty('Busy'))
    print('movie detail %s played %s busy %s' % (home.path, Player.shared.played[-1], [item for item in xbmc.builtins if 'busy' in item.lower()]))
    check(ready, 'a movie detail with one playable item starts that PlayVideo url')
    check(any(item == 'ActivateWindow(busydialognocancel)' for item in xbmc.builtins),
          'resolving an ASIN shows a busy indicator')
    check(home.path == path_before, 'the movie never opens an intermediate listing')

    stage_row(home, SERIES_SEASONS)
    played_before = len(Player.shared.played)
    home.onClick(200)
    ready = wait_until(lambda: home.path.endswith('B007SEASONS') and not home.busy
                       and any(item.getLabel() == 'SEASON 1' for item in home.getControl(200).items))
    print('picker %s played %s' % (home.path, len(Player.shared.played)))
    check(ready, 'a series with no playable episode opens the season list')
    check(len(Player.shared.played) == played_before, 'the season list does not start playback')

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
    ready = wait_until(lambda: home.path == '__prime_tv__' and any(
        item.getLabel().startswith('TYLER PERRY') for item in home.getControl(200).items))
    check(ready, 'tv shortcut opens that category')
    home.getControl(200).position = next(index for index, item in enumerate(home.getControl(200).items)
                                         if item.getLabel().startswith('TYLER PERRY'))
    check(wait_until(lambda: bare_filled(home)), 'tv fills metadata and synopsis for a card that had neither')
    check(png_size(bare_item(home).properties.get('TitleArt', ''))[1] >= 200, 'tv uses the large title plate')

    signed = AuroraHome('PrimeSceneHome.xml', str(ADDON), 'Default', '1080i',
                        globals_instance=g, account_name=lambda: '')
    signed.doModal()
    print('signed out focus %s tag %s' % (getattr(signed, 'focus', None), signed.getProperty('HeroTag')))
    check(getattr(signed, 'focus', None) == 306, 'signed-out screen focuses sign in')
    check('SIGN IN' in signed.getProperty('HeroTitle'), 'signed-out hero asks for sign in')
    check('EXPLORE' not in signed.getProperty('HeroTitle') and 'EXPLORE' not in signed.getProperty('HeroMeta'),
          'signed-out hero has no filler label')
    check(signed.getProperty('HeroFanart') == '', 'signed-out hero has no theater backdrop')
    signed.stop.set()

    catalog_html = (
        '<script type="application/json">'
        '{"title":"Harbor Lights","detailUrl":"https://www.primevideo.com/detail/B001TEST",'
        '"synopsis":"A coastal town keeps a secret schedule.","releaseYear":2021,"duration":6120,'
        '"titleType":"movie","ratingBadge":{"displayText":"16+"},'
        '"genres":[{"text":"Drama"},{"text":"Mystery"}],'
        '"images":{"packshot":"https://m.media-amazon.com/images/I/pack.jpg",'
        '"heroshot":"https://m.media-amazon.com/images/I/hero.jpg",'
        '"titleshot":"https://m.media-amazon.com/images/I/poster.jpg"}}'
        '</script>'
        '<article data-testid="card"><a href="/detail/B002TEST" aria-label="Quiet Title">'
        '<img src="https://m.media-amazon.com/images/I/quiet.jpg" alt="Quiet Title"></a></article>'
    )
    parsed = {card.title: card for card in parse_cards(catalog_html)}
    harbor = parsed.get('Harbor Lights')
    quiet = parsed.get('Quiet Title')
    check(harbor is not None and quiet is not None, 'storefront reader keeps title cards')
    if harbor is not None:
        print('parsed %s %s %s %s' % (harbor.year, harbor.duration, harbor.rating, harbor.mediatype))
        check(harbor.artwork_url.endswith('/pack.jpg') and harbor.fanart_url.endswith('/hero.jpg')
              and harbor.poster_url.endswith('/poster.jpg'),
              'storefront card keeps landscape, hero and poster art')
        check(harbor.year == '2021' and harbor.duration == '6120' and harbor.rating == '16+'
              and harbor.mediatype == 'movie' and 'Drama' in harbor.genres
              and harbor.plot.startswith('A coastal town'),
              'storefront card keeps year, runtime, rating, genre and synopsis')
        check(harbor.seasons == '', 'a movie card leaves the season count blank')
    if quiet is not None:
        check(quiet.year == '' and quiet.plot == '' and quiet.rating == '' and quiet.fanart_url == '',
              'a card without those fields leaves them blank')
    series_cards = {card.title: card for card in parse_cards(
        '<script type="application/json">'
        '{"title":"The North Water","detailUrl":"https://www.primevideo.com/detail/B003TEST",'
        '"titleType":"tv show","seasonCount":1,"releaseYear":2021,'
        '"images":{"packshot":"https://m.media-amazon.com/images/I/north.jpg"}}'
        '</script>')}
    north = series_cards.get('The North Water')
    check(north is not None and north.seasons == '1' and north.mediatype == 'tvshow',
          'storefront reader keeps a series season count')

    settings = Settings()
    settings.data_source = 1
    check(open_interface(g, settings) is False, 'a non-website source asks for the website data source')
    check(any('Website' in message for _heading, message in dialog.messages),
          'the website data source message is shown')

    overlay = PlayerOverlay('PrimeScenePlayer.xml', str(ADDON), 'Default', '1080i')
    overlay.player = Player()
    overlay.info = {'title': 'Harbor Lights', 'meta': '2021  •  FILM', 'position': 754, 'duration': 9360, 'volume': 35}
    overlay.doModal()
    print('')
    print('=== player ===')
    print('elapsed %s total %s progress %s chrome %s' % (
        overlay.getProperty('Elapsed'), overlay.getProperty('Total'), overlay.getControl(400).percent,
        overlay.getProperty('Chrome')))
    player_xml = (ADDON / 'resources/skins/Default/1080i/PrimeScenePlayer.xml').read_text(encoding='utf-8')
    check(overlay.xml == 'PrimeScenePlayer.xml', 'player overlay loads PrimeScenePlayer.xml')
    check('Window.Property(Chrome)' in player_xml and 'stop.png' in player_xml,
          'player chrome is one Aurora group with a stop control')
    check(overlay.getProperty('Title') == 'HARBOR LIGHTS', 'player title')
    check(overlay.getProperty('Chrome') == '1', 'player chrome starts visible')
    check(overlay.getProperty('Elapsed') == '12:34', 'elapsed time')
    check(overlay.getProperty('Total') == '2:36:00', 'total time')
    check(overlay.getControl(400).percent == 8, 'progress percent')
    xbmc.builtins.clear()
    overlay.onClick(501)
    check('PlayerControl(Play)' in xbmc.builtins, 'pause button uses Kodi player controls')
    overlay.onAction(Action(10))
    check(overlay.getProperty('Chrome') == '' and not getattr(overlay, 'closed', False),
          'back hides the chrome and leaves the controls alive')
    overlay.onAction(Action(12))
    check(overlay.getProperty('Chrome') == '1', 'pause brings the same chrome back')
    overlay.hide_chrome()
    overlay.onAction(Action(11))
    check(overlay.getProperty('Chrome') == '1', 'info brings the same chrome back')
    overlay.hide_chrome()
    overlay.onAction(Action(24))
    check(overlay.getProperty('Chrome') == '1', 'the OSD key brings the same chrome back')
    overlay.hide_chrome()
    overlay.onAction(Action(7))
    check(overlay.getProperty('Chrome') == '1', 'OK brings the same chrome back')
    overlay.hide_chrome()
    Player.shared.time = 100
    Player.shared.total = 9360
    overlay.onAction(Action(2))
    check(Player.shared.time == 110 and overlay.getProperty('Chrome') == '1',
          'a hidden right press seeks and shows the time bar')
    overlay.onAction(Action(20))
    check(Player.shared.time == 120, 'step forward seeks while the chrome is up')
    xbmc.builtins.clear()
    overlay.onAction(Action(13))
    check('PlayerControl(Stop)' in xbmc.builtins and not Player.shared.playing and getattr(overlay, 'closed', False),
          'stop ends playback and closes the chrome')
    check(overlay_due(True, {'armed': time.time()}, time.time(), False), 'fresh playback may show the overlay')
    check(not overlay_due(True, {'armed': time.time() - 500}, time.time(), False),
          'an old note does not cover someone else playing')
    check(overlay_due(True, {'armed': time.time() - 500}, time.time(), True), 'overlay stays for the title already shown')
    check(not overlay_due(False, {'armed': time.time()}, time.time(), True), 'overlay leaves when playback stops')

    again = PlayerOverlay('PrimeScenePlayer.xml', str(ADDON), 'Default', '1080i')
    again.player = Player()
    Player.shared.playing = True
    Player.shared.subs = ['English']
    Player.shared.audios = ['English', 'Spanish']
    again.info = {'title': 'Harbor Lights', 'meta': '2021  •  FILM', 'position': 10, 'duration': 100, 'volume': 40}
    again.doModal()
    xbmc.conditions['Window.IsActive(videoosd)'] = True
    xbmc.conditions['Window.IsActive(seekbar)'] = True
    xbmc.builtins.clear()
    check(again.reclaim_skin_osd(), 'a skin OSD that appears during Prime Scene playback is closed')
    check('Dialog.Close(videoosd,true)' in xbmc.builtins and 'Dialog.Close(seekbar,true)' in xbmc.builtins,
          'Estuary video OSD and seek bar are dismissed')
    check(again.getProperty('Chrome') == '1', 'dismissing the skin OSD shows the Aurora chrome')
    again.refresh_streams()
    check(again.getProperty('HasSubtitles') == '1' and again.getProperty('HasAudio') == '1',
          'subtitle and audio controls follow the streams Prime provides')
    Dialog.choices = []
    again.onClick(504)
    again.onClick(505)
    check([item[0] for item in Dialog.choices] == ['Subtitles', 'Audio'],
          'subtitle and audio choices use the Aurora dialog')
    xbmc.conditions.clear()
    again._chrome_at = 0
    xbmc.conditions['Player.Paused'] = False
    again.maintain()
    check(again.getProperty('Chrome') == '' and not getattr(again, 'closed', False),
          'the chrome hides on its own while playback continues')
    xbmc.conditions['Player.Paused'] = True
    again.reveal()
    again._chrome_at = 0
    again.maintain()
    check(again.getProperty('Chrome') == '1', 'pause keeps the chrome on screen')
    xbmc.conditions['Player.Paused'] = False
    Player.shared.playing = False
    again.maintain()
    check(getattr(again, 'closed', False), 'the chrome closes when playback ends')

    note_playback(PROFILE, 'Harbor Lights', '2021  •  FILM')
    Player.shared.playing = True
    WindowXML.opened = []
    Monitor.waits = 0
    Monitor.limit = 4
    watch_playback(PROFILE)
    opened = [window for window in WindowXML.opened if getattr(window, 'xml', '') == 'PrimeScenePlayer.xml']
    print('service overlays %s' % len(opened))
    check(len(opened) >= 2, 'the overlay is shown again for the whole playback after it closes')
    check(all(window.getProperty('Title') == 'HARBOR LIGHTS' and window.getProperty('Chrome') == '1'
              for window in opened), 'each return uses the Aurora title and chrome')
    WindowXML.opened = []
    (Path(PROFILE) / 'aurora-playback.json').unlink()
    Monitor.waits = 0
    Monitor.limit = 2
    watch_playback(PROFILE)
    check(not any(getattr(window, 'xml', '') == 'PrimeScenePlayer.xml' for window in WindowXML.opened),
          'another video does not get the Aurora controls')
    note_playback(PROFILE, 'Harbor Lights', '2021  •  FILM')
    Player.shared.playing = False
    Monitor.waits = 0
    Monitor.limit = 2
    watch_playback(PROFILE)
    check(not (Path(PROFILE) / 'aurora-playback.json').exists(), 'ending playback clears the Aurora note')

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

    logs = []
    xbmc.log = lambda *args, **kwargs: logs.append(args)

    class MissingControl(AuroraHome):
        def getControl(self, control_id):
            raise RuntimeError('Non-Existent Control %s' % control_id)

    aurora.AuroraHome = MissingControl
    dialog.messages.clear()
    failed = open_interface(g, Settings(), account_name=lambda: 'Ada')
    aurora.AuroraHome = AuroraHome
    if dialog.messages:
        print('init dialog %s' % dialog.messages[-1][1])
    check(failed is False, 'a missing control aborts initialization')
    check(any('could not initialize (RuntimeError: Non-Existent Control 200)' in message
              for _heading, message in dialog.messages),
          'initialization dialog includes the exception detail')
    logged = '\n'.join(str(part) for entry in logs for part in entry)
    check('Traceback (most recent call last)' in logged and 'Non-Existent Control 200' in logged,
          'initialization logs traceback.format_exc()')

    print('')
    if failures:
        print('%s check(s) failed' % len(failures))
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
