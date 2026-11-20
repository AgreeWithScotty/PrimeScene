#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless Prime Scene run with stubbed Kodi modules.

Checks the Aurora Cinema window XML, textures, routing, focus tint,
detail view and playback handoff. The catalogue bridge is faked; no
account and no network are used.
"""
import ast
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / 'packages' / 'plugin.video.primeinterface' / 'plugin.video.primeinterface-0.2.2.zip'
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
            'version': '0.2.2',
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
    for name in ('Font.xml', 'PrimeSceneHome.xml', 'PrimeSceneTitle.xml', 'PrimeScenePlayer.xml'):
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

    def browse_progressive(self, path, publish, active, query=None):
        FakeBridge.calls.append((path, query))
        if not active():
            return
        if query is not None:
            publish({'rows': [SHOW], 'next': None})
            return
        if path == 'root':
            publish({'rows': [FILM, SHOW], 'next': None})
        elif path in ('__prime_movies__', 'root/movies'):
            publish({'rows': [FILM, SHOW], 'next': 'root/movies/nextPage'})
        elif path in ('__prime_tv__', 'root/tv'):
            publish({'rows': [SHOW], 'next': None})
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
    check('version="0.2.2"' in addon_xml, 'addon.xml version is 0.2.2')
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
            ('resources/skins/Default/1080i/PrimeSceneTitle.xml', 'skins/Default/1080i/PrimeSceneTitle.xml'),
            ('resources/skins/Default/1080i/PrimeScenePlayer.xml', 'skins/Default/1080i/PrimeScenePlayer.xml'),
            ('resources/skins/Default/1080i/Font.xml', 'skins/Default/1080i/Font.xml')):
        check((ROOT / 'source' / 'rewrite' / source_name).read_bytes() == (ADDON / packaged_name).read_bytes(),
              'packaged %s matches source' % source_name)
    home_xml = (ADDON / 'resources/skins/Default/1080i/PrimeSceneHome.xml').read_text(encoding='utf-8')
    title_xml = (ADDON / 'resources/skins/Default/1080i/PrimeSceneTitle.xml').read_text(encoding='utf-8')
    aurora_text = packaged.decode('utf-8')
    check('EXPLORE' not in aurora_text and 'EXPLORE' not in home_xml and 'EXPLORE' not in title_xml,
          'EXPLORE filler is gone from the window and the catalogue labels')
    check('category-cinema' not in aurora_text and 'category-' not in home_xml and 'fanart.jpg' not in home_xml,
          'home does not use the gold theater art or category icons')
    for binding in (
            'Container(200).ListItem.Art(fanart)',
            'Container(200).ListItem.Art(thumb)',
            'Container(200).ListItem.Label',
            'Container(200).ListItem.Property(Meta)',
            'Container(200).ListItem.Property(Plot)',
            'Container(200).ListItem.Property(Kicker)'):
        check(binding in home_xml, 'hero binds %s' % binding)
    check('bordersize>4<' in home_xml and 'effect="zoom"' in home_xml, 'focused card has a 4px border and a lift')
    check('focus-pill.png' in home_xml and 'button-focus.png' in home_xml,
          'nav and hero buttons use a high-contrast focus texture')
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
    from resources.lib.aurora import (AuroraHome, PlayerOverlay, _SCRIM, _meta, note_playback,
                                      open_interface, overlay_due, watch_playback)
    from resources.lib.public_catalog import parse_cards

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
    home = AuroraHome('PrimeSceneHome.xml', str(ADDON), 'Default', '1080i',
                      globals_instance=g, initialize_provider=lambda: started.append('provider'),
                      account_name=account_name, account_action=account_action)
    home.doModal()
    ready = wait_until(lambda: len(home.rows) == 2 and home.getProperty('Section') == 'HOME')
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
    check(home.getControl(200).size() == 2, 'home row lists real titles')
    check(home.getProperty('HeroTitle') == 'HARBOR LIGHTS', 'first title is the hero')
    check(home.getControl(101).color == _SCRIM, 'hero scrim is the matte slate wash')
    first = home.getControl(200).items[0]
    print('card meta %s art %s' % (first.properties.get('Meta'), sorted(first.art)))
    check('2021' in first.properties.get('Meta', '') and '1H 42M' in first.properties.get('Meta', '')
          and '16+' in first.properties.get('Meta', '') and 'DRAMA' in first.properties.get('Meta', ''),
          'card sublabel uses year, runtime, rating and genre')
    check(not any('EXPLORE' in (item.getLabel() + item.properties.get('Meta', '') + item.properties.get('Kicker', ''))
                  for item in home.getControl(200).items),
          'no card label is EXPLORE')
    check(first.art.get('fanart') == FILM['info']['fanart'] and first.art.get('thumb') == FILM['info']['thumb']
          and first.art.get('poster') == FILM['info']['poster'],
          'each title sets fanart, thumb and poster')
    check(first.properties.get('Plot', '').startswith('A coastal town'), 'card carries the synopsis')
    check(_meta({'title': 'Shelf', 'video': False, 'info': {}}) == '', 'a missing field stays blank')
    check('2024' in home.getControl(200).items[1].properties.get('Meta', '')
          and 'TV-MA' in home.getControl(200).items[1].properties.get('Meta', '')
          and 'MYSTERY' in home.getControl(200).items[1].properties.get('Meta', ''),
          'a series card shows its own metadata')
    check('provider' not in started, 'the home screen does not start the playback provider')

    home.getControl(200).position = 1
    home.onFocus(200)
    print('focused %s meta %s' % (home.getProperty('HeroTitle'), home.getProperty('HeroMeta')))
    check(home.getProperty('HeroTitle') == 'THE MIDNIGHT LIBRARY', 'focused card becomes the hero')
    check('2024' in home.getProperty('HeroMeta') and 'secret schedule' not in home.getProperty('HeroSynopsis'),
          'focus replaces the hero metadata and synopsis')
    check('lives she did not live' in home.getProperty('HeroSynopsis'), 'hero synopsis follows the focused title')
    check(home.getControl(101).color == _SCRIM, 'the scrim stays matte slate while the art changes')

    home.getControl(200).position = 0
    home.onAction(Action(2))
    print('stepped %s meta %s' % (home.getProperty('HeroTitle'), home.getProperty('HeroMeta')))
    check(home.getProperty('HeroTitle') == 'THE MIDNIGHT LIBRARY', 'right moves the hero to the next title')
    check('TV-MA' in home.getProperty('HeroMeta'), 'right updates the metadata strip')
    check(home.getProperty('HeroFanart') == SHOW['info']['fanart'], 'right updates the hero backdrop')

    home.onClick(302)
    ready = wait_until(lambda: home.path == '__prime_movies__' and home.getControl(200).size() >= 2)
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
    check(title.xml == 'PrimeSceneTitle.xml', 'detail view loads PrimeSceneTitle.xml')
    check(title.getProperty('Kicker') == 'FILM', 'film kicker')
    check(title.getProperty('HeroTitle') == 'HARBOR LIGHTS', 'detail title')
    check('2021' in title.getProperty('Meta') and '1H 42M' in title.getProperty('Meta')
          and '16+' in title.getProperty('Meta'), 'detail metadata strip')
    check('secret schedule' in title.getProperty('Synopsis'), 'synopsis is on the detail view')
    check(title.getControl(210).size() == 1, 'detail row lists the other title')

    title.onClick(310)
    print('play %s' % Player.shared.played)
    check(Player.shared.played == [FILM_URL], 'watch now hands the existing playback URL to the player')
    note = (Path(PROFILE) / 'aurora-playback.json').read_text(encoding='utf-8')
    check('Harbor Lights' in note, 'playback note carries the title for the overlay')

    title.onClick(210)
    check(Player.shared.played[-1] == SHOW_URL, 'a related card uses the same playback handoff')
    home.getControl(200).position = 0
    home.onClick(320)
    check(Player.shared.played[-1] == FILM_URL, 'the hero watch button plays the focused title')

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
    ready = wait_until(lambda: home.path == '__prime_tv__')
    check(ready, 'tv shortcut opens that category')

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
    if quiet is not None:
        check(quiet.year == '' and quiet.plot == '' and quiet.rating == '' and quiet.fanart_url == '',
              'a card without those fields leaves them blank')

    settings = Settings()
    settings.data_source = 1
    check(open_interface(g, settings) is False, 'a non-website source asks for the website data source')
    check(any('Website' in message for _heading, message in dialog.messages),
          'the website data source message is shown')

    overlay = PlayerOverlay('PrimeScenePlayer.xml', str(ADDON), 'Default', '1080i')
    overlay.info = {'title': 'Harbor Lights', 'meta': '2021  •  FILM', 'position': 754, 'duration': 9360, 'volume': 35}
    overlay.doModal()
    print('')
    print('=== player ===')
    print('elapsed %s total %s progress %s' % (
        overlay.getProperty('Elapsed'), overlay.getProperty('Total'), overlay.getControl(400).percent))
    check(overlay.xml == 'PrimeScenePlayer.xml', 'player overlay loads PrimeScenePlayer.xml')
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
    shown = [window for window in WindowXML.shown if getattr(window, 'xml', '') == 'PrimeScenePlayer.xml']
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
