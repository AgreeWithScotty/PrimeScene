"""Build a standalone Kodi add-on ZIP. Its permanent ID never changes by version."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
AMAZON = ROOT.parent
AUDIT = AMAZON / 'analysis/amazon-vod-20261110'
ID = 'plugin.video.primeinterface'
NAME = 'Prime Scene'
MAINTAINER = 'AgreeWithScotty'


def build(version='0.1.28', output=None, repository_updates=True):
    assert re.fullmatch(r'\d+\.\d+\.\d+', version), 'Use a Kodi numeric release version'
    output = Path(output) if output else ROOT / 'releases' / version
    if (output/'published.json').exists():
        raise ValueError('This release is already published. Increment the version instead of replacing its ZIP.')
    addon = output / ID
    original = AUDIT / 'upstream/plugin.video.amazon-test'
    for path in original.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        dest = addon / path.relative_to(original)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    # Bring the validated fixes and interface into OUR package, not the installed
    # original add-on. All imports below remain inside this package's resources.
    for name in ('network.py', 'login.py'):
        shutil.copyfile(AUDIT / 'candidate/plugin.video.amazon-test/resources/lib' / name,
                        addon / 'resources/lib' / name)
    interface = AMAZON / 'Dedicated Interface/candidate/plugin.video.amazon-test'
    for path in interface.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            dest = addon / path.relative_to(interface)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
    shutil.copyfile(ROOT / 'src/profile_bootstrap.py', addon / 'resources/lib/profile_bootstrap.py')
    for name in ('dedicated_ui.py','interface_diagnostics.py','dedicated_bridge.py','category_art.py'):
        shutil.copyfile(ROOT/'src'/name,addon/'resources/lib'/name)
    shutil.copyfile(ROOT/'independent/public_catalog.py', addon/'resources/lib/public_catalog.py')
    for name in ('prime_signin.py', 'prime_device.py', 'prime_diagnostics.py'):
        shutil.copyfile(ROOT/'independent'/name, addon/'resources/lib'/name)
    for name in ('prime_login.py', 'account_state.py', 'session_store.py'):
        shutil.copyfile(ROOT/'src'/name, addon/'resources/lib'/name)
    # All login entry points (first opening, settings and session renewal) use
    # the native form workflow. Remove the inherited credential-entry and device
    # registration functions; compatibility helpers still serve other features.
    login_path = addon/'resources/lib/login.py'
    login_source = login_path.read_text(encoding='utf-8')
    login_lines = login_source.splitlines(keepends=True)
    old_functions = [node for node in ast.parse(login_source).body
                     if isinstance(node, ast.FunctionDef) and node.name in ('LogIn', 'registerDevice', 'remLoginData')]
    assert len(old_functions) == 3
    for node in sorted(old_functions, key=lambda node: node.lineno, reverse=True):
        login_lines[node.lineno - 1:node.end_lineno] = (
            ['from .prime_login import LogIn\n'] if node.name == 'LogIn' else
            ['from .account_state import remLoginData\n'] if node.name == 'remLoginData' else [])
    login_path.write_text(''.join(login_lines), encoding='utf-8', newline='\n')
    # Scope account writes to the invocation that began them. A sign-out rotates
    # the generation, preventing delayed responses from restoring old data.
    config_path = addon/'resources/lib/configs.py'
    config_source = config_path.read_text(encoding='utf-8')
    config_source = config_source.replace('def writeConfig(cfile, value):', 'def _writeConfigUnchecked(cfile, value):')
    config_source += '''

_prime_scene_epoch = None

def writeConfig(cfile, value):
    from .common import Globals
    from .account_state import account_epoch, persist_if_current
    global _prime_scene_epoch
    profile = Globals().DATA_PATH
    if _prime_scene_epoch is None:
        _prime_scene_epoch = account_epoch(profile)
    return persist_if_current(profile, _prime_scene_epoch,
                              lambda: _writeConfigUnchecked(cfile, value),
                              allow_signed_out=cfile in ('proxyaddress', 'UserAgent', 'UAlist'))
'''
    config_path.write_text(config_source, encoding='utf-8', newline='\n')
    network_path = addon/'resources/lib/network.py'
    network_source = network_path.read_text(encoding='utf-8')
    marker = "    retval = {} if rjson else ''\n"
    assert network_source.count(marker) == 1
    network_source = network_source.replace(marker, marker + '''    from .account_state import account_epoch, current_account
    from . import configs
    global _prime_scene_epoch
    if _prime_scene_epoch is None:
        _prime_scene_epoch = account_epoch(_g.DATA_PATH)
    request_epoch = _prime_scene_epoch
    if configs._prime_scene_epoch is None:
        configs._prime_scene_epoch = request_epoch
    if not current_account(_g.DATA_PATH, request_epoch, allow_signed_out=not bool(useCookie)):
        return retval
''')
    network_source = network_source.replace('_session = None\n', '_session = None\n_prime_scene_epoch = None\n', 1)
    marker = '        getURL.lastResponseCode = r.status_code'
    assert network_source.count(marker) == 1
    network_source = network_source.replace(marker, '''        if not current_account(_g.DATA_PATH, request_epoch, allow_signed_out=not bool(useCookie)):
            return retval
''' + marker)
    marker = '            saveUserCookies(session.cookies)\n'
    assert network_source.count(marker) == 1
    network_source = network_source.replace(marker,
                                            '            saveUserCookies(session.cookies, request_epoch=request_epoch)\n')
    network_path.write_text(network_source, encoding='utf-8', newline='\n')
    users_path = addon/'resources/lib/users.py'
    users_source = users_path.read_text(encoding='utf-8')
    users_source = users_source.replace('def loadUsers():\n', '''def loadUsers():
    from .account_state import is_signed_out
    if is_signed_out(_g.DATA_PATH):
        _s.login_acc = ''
        return []
''', 1)
    marker = 'def saveUserCookies(cookieJar, cachedUsers=None):\n'
    assert users_source.count(marker) == 1
    users_source = users_source.replace(marker,
                                        'def saveUserCookies(cookieJar, cachedUsers=None, request_epoch=None):\n')
    marker = "    user['cookie'] = cookieJar.get_dict()\n"
    assert users_source.count(marker) == 1
    users_source = users_source.replace(marker, '''    # A completed request may refresh a real session, but a legacy flat-cookie
    # fallback must never create a website session or borrow a newer generation.
    from . import configs
    from .session_store import persist_session
    expected_epoch = request_epoch if request_epoch is not None else configs._prime_scene_epoch
    if expected_epoch is not None:
        persist_session(_g.DATA_PATH, expected_epoch, cookieJar)
''' + marker)
    user_lines = users_source.splitlines(keepends=True)
    remove_user = next(node for node in ast.parse(users_source).body
                       if isinstance(node, ast.FunctionDef) and node.name == 'removeUser')
    user_lines[remove_user.lineno - 1:remove_user.end_lineno] = ['from .account_state import remLoginData as removeUser\n']
    users_path.write_text(''.join(user_lines), encoding='utf-8', newline='\n')
    service_path = addon/'resources/lib/service.py'
    service_source = service_path.read_text(encoding='utf-8')
    service_source = service_source.replace('from .common import Settings', 'from .common import Globals, Settings')
    marker = '        self.freqCheck = 60\n'
    assert service_source.count(marker) == 1
    service_source = service_source.replace(marker, '''        from .account_state import account_epoch
        self._prime_scene_epoch = account_epoch(Globals().DATA_PATH)
''' + marker)
    marker = '        self.wl_export = self._s.wl_export\n'
    service_source = service_source.replace(marker, marker + '''        from .account_state import refresh_service_session
        refresh_service_session(Globals(), self)
''')
    marker = '            ct = time()\n'
    assert service_source.count(marker) == 1
    service_source = service_source.replace(marker, '''            from .account_state import refresh_service_session
            refresh_service_session(Globals(), self)
''' + marker)
    service_path.write_text(service_source, encoding='utf-8', newline='\n')
    proxy_path = addon/'resources/lib/proxy.py'
    proxy_source = proxy_path.read_text(encoding='utf-8')
    marker = '        from urllib.parse import urlparse, parse_qsl\n'
    assert proxy_source.count(marker) == 1
    proxy_source = proxy_source.replace(marker, marker + '''        from .common import Globals
        from .account_state import refresh_service_session, is_signed_out
        g = Globals()
        self._prime_scene_request_epoch = refresh_service_session(g, self.server)
        if is_signed_out(g.DATA_PATH):
            raise ConnectionAbortedError('Prime Scene is signed out')
''')
    marker = '        return r.status_code, r.headers, r if stream else r.content.decode(\'utf-8\')'
    assert proxy_source.count(marker) == 1
    proxy_source = proxy_source.replace(marker, '''        from .common import Globals
        from .account_state import current_account
        if not current_account(Globals().DATA_PATH, self._prime_scene_request_epoch):
            r.close()
            raise ConnectionAbortedError('The Prime Scene account session ended')
''' + marker)
    marker = 'headers=headers, cookies=cookie, stream=stream, verify=self.server._s.ssl_verif)'
    assert proxy_source.count(marker) == 1
    proxy_source = proxy_source.replace(marker, marker[:-1] + ', timeout=(10, 20))')
    proxy_path.write_text(proxy_source, encoding='utf-8', newline='\n')
    web_api_path = addon/'resources/lib/web_api.py'
    web_api = web_api_path.read_text(encoding='utf-8')
    marker = '        self._g = globalsInstance\n'
    assert web_api.count(marker) == 1
    web_api = web_api.replace(marker, marker + '''        from .account_state import account_epoch
        self._prime_scene_epoch = account_epoch(globalsInstance.DATA_PATH)
''')
    web_api = web_api.replace('    def _Flush(self, bFlushCacheData=True, bFlushVideoData=False):',
                            '    def _FlushUnchecked(self, bFlushCacheData=True, bFlushVideoData=False):')
    marker = '    def _LoadCache(self):'
    assert web_api.count(marker) == 1
    web_api = web_api.replace(marker, '''    def _Flush(self, bFlushCacheData=True, bFlushVideoData=False):
        from .account_state import persist_if_current
        return persist_if_current(self._g.DATA_PATH, self._prime_scene_epoch,
                                  lambda: self._FlushUnchecked(bFlushCacheData, bFlushVideoData))

''' + marker)
    traverse_root = '        node = self._catalog\n        pathLen = len(pathList)'
    assert web_api.count(traverse_root) == 1
    web_api = web_api.replace(traverse_root, '''        # Storefront cards use the same details parser and playback routes,
        # but these navigation nodes are scoped to the dedicated window.
        if pathList[0] == '__storefront__':
            node = {'__storefront__': getattr(self, '_prime_scene_details', {})}
        else:
            node = self._catalog
        pathLen = len(pathList)''')
    # Keep the old detail cache but replace the menu cache containing six-card continuations.
    web_api = web_api.replace("f'PVCatalog{self._g.MarketID}.pvcp'", "f'PVCatalogPages{self._g.MarketID}.pvcp'")
    split_page = "                if len(items) > 6 and 'state' not in cnt and 'widgets' not in cnt:"
    assert web_api.count(split_page) == 1
    web_api = web_api.replace(split_page, """                listing_card = getattr(self, '_prime_scene_listing_card', None)
                if 'state' in cnt or 'widgets' in cnt:
                    listing_card = None
                if listing_card is None and len(items) > 6 and 'state' not in cnt and 'widgets' not in cnt:""")
    eager_movie = "                        elif t not in ['season', 'show']"
    assert web_api.count(eager_movie) == 1
    web_api = web_api.replace(eager_movie, """                        elif listing_card is not None:
                            listing_card(o, item, iu)
""" + eager_movie)
    web_api_path.write_text(web_api, encoding='utf-8', newline='\n')
    skin_xml = addon/'resources/skins/Default/1080i'
    skin_xml.mkdir(parents=True, exist_ok=True)
    for name in ('Font.xml', 'PrimeSceneHome.xml', 'PrimeSceneTitle.xml', 'PrimeScenePlayer.xml'):
        shutil.copyfile(ROOT/'skins/Default/1080i'/name, skin_xml/name)
    legacy_window = skin_xml/'PrimeStreaming.xml'
    if legacy_window.exists():
        legacy_window.unlink()
    for obsolete in (addon/'resources/skins/Default/media').glob('category-*.png'):
        obsolete.unlink()
    for asset in (ROOT/'src/assets').glob('category-*.webp'):
        shutil.copyfile(asset,addon/'resources/skins/Default/media'/asset.name)
    shutil.copyfile(ROOT/'src/gui.py',addon/'gui.py')
    shutil.copyfile(ROOT/'src/view_diagnostics.py',addon/'view_diagnostics.py')
    if repository_updates:
        assert tuple(map(int,version.split('.'))) >= (0,1,4), 'Repository updates require a newer release version'
        shutil.copyfile(ROOT/'src/check_updates.py',addon/'check_updates.py')
    startup_path=addon/'resources/lib/startup.py'
    startup=startup_path.read_text(encoding='utf-8')
    old="""    if not xbmcvfs.exists(os.path.join(_g.DATA_PATH, 'settings.xml')):
        _g.addon.openSettings()
        exit()
"""
    assert startup.count(old)==1
    startup=startup.replace(old,"""    if not xbmcvfs.exists(os.path.join(_g.DATA_PATH, 'settings.xml')):
        _g.addon.setSetting('data_source', '0')
        _g.addon.setSetting('playmethod', '3')
""")
    old="""    if not getConfig('UserAgent'):
        getUA()

    users = loadUsers()
"""
    assert startup.count(old)==1
    startup=startup.replace(old,"""    users = loadUsers()
    if not users and mode is None:
        # First launch is an explicit Prime Scene login, before background requests.
        from .account_state import is_signed_out
        if not is_signed_out(_g.DATA_PATH) and _g.dialog.yesno('Prime Scene',
                               'Sign in to Amazon Prime Video to start watching.',
                               nolabel='Cancel', yeslabel='Sign in'):
            if not getConfig('UserAgent'):
                getUA()
            from .login import LogIn
            LogIn()
        users = loadUsers()
    if users and not getConfig('UserAgent'):
        getUA()

""")
    marker='    if users:\n'
    assert startup.count(marker)==1
    startup=startup.replace(marker,'''    if mode is None and path in ('', '/'):
        # Queue the GUI script without waiting for it. Release the directory
        # request so Kodi can activate the custom window on its main thread.
        import xbmcplugin
        if _g.pluginhandle >= 0:
            xbmcplugin.endOfDirectory(_g.pluginhandle, succeeded=False, cacheToDisc=False)
        xbmc.executebuiltin('RunScript("' + os.path.join(_g.PLUGIN_PATH, 'gui.py') + '")')
        return

'''+marker)
    if repository_updates:
        marker="    users = loadUsers()\n"
        assert startup.count(marker)>=1
        startup=startup.replace(marker,'''    if mode == 'CheckUpdates':
        import xbmcplugin
        if _g.pluginhandle >= 0:
            xbmcplugin.endOfDirectory(_g.pluginhandle, cacheToDisc=False)
        xbmc.executebuiltin('RunScript("' + os.path.join(_g.PLUGIN_PATH, 'check_updates.py') + '")')
        return

'''+marker,1)
    start=startup.index('    elif None is mode:')
    end=startup.index("    elif mode == 'PlayVideo':",start)
    startup=startup[:start]+startup[end:]
    startup_path.write_text(startup,encoding='utf-8',newline='\n')
    # Runtime URI/actions and the settings service are isolated from Amazon VOD.
    for path in addon.rglob('*'):
        if path.is_file() and path.suffix in ('.py', '.xml', '.po'):
            text = path.read_text(encoding='utf-8-sig')
            text = text.replace('Prime Interface', NAME)
            # RunPlugin's own lookup requires a PLUGIN extension. Our video app
            # now launches as SCRIPT; non-playback actions use script dispatch.
            text = text.replace('RunPlugin(', 'RunScript(' + ID + ',')
            text = text.replace('plugin://plugin.video.amazon-test', 'plugin://' + ID)
            text = text.replace('special://profile/addon_data/plugin.video.amazon-test',
                                'special://profile/addon_data/' + ID)
            text = text.replace('amazon_settingsmonitor_state', 'primeinterface_settingsmonitor_state')
            text = text.replace('Amazon Movies', NAME + ' Movies').replace('Amazon TV', NAME + ' TV')
            if path.name == 'common.py':
                text = text.replace("self._globals['addon'] = Addon()",
                                    "self._globals['addon'] = Addon('" + ID + "')")
                text = text.replace('def MechanizeLogin(preferToken=False):\n', '''def MechanizeLogin(preferToken=False):
    from .account_state import is_signed_out
    if is_signed_out(Globals().DATA_PATH):
        return False
''', 1)
                marker = '    # if Token not requested or not avaiable use cookie\n'
                assert text.count(marker) == 1
                text = text.replace(marker, '''    # Keep token preference intact; website requests use the original full
    # cookie scope before considering the old name/value compatibility store.
    if _s.data_source == 0 or not preferToken:
        from .session_store import requests_session_cookies
        session_cookies = requests_session_cookies(Globals().DATA_PATH)
        if session_cookies is not None:
            return session_cookies

''' + marker)
            if path.name == 'atv_api.py':
                text = text.replace("data = getURL(f'https://raw.githubusercontent.com/Sandmann79/xbmc/master/plugin.video.amazon-test/resources/menu/{self._g.MarketID}.json')",
                                    'data = None  # Use the menu data bundled with this independent add-on.')
            if path.name == 'settings.xml':
                text = text.replace('id="plugin.video.amazon-test"', 'id="' + ID + '"')
                # Upstream contains raw URL ampersands; produce valid settings XML.
                text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', text)
            path.write_text(text, encoding='utf-8', newline='\n')
    default = addon / 'default.py'
    if repository_updates:
        settings=ET.parse(addon/'resources/settings.xml')
        category=ET.SubElement(settings.getroot().find('section'),'category',{'id':'updates','label':'32090','help':''})
        group=ET.SubElement(category,'group',{'id':'1'})
        setting=ET.SubElement(group,'setting',{'id':'check_updates','type':'action','label':'32091','help':''})
        ET.SubElement(setting,'level').text='0'
        ET.SubElement(setting,'data').text='RunScript('+ID+',plugin://'+ID+'/?mode=CheckUpdates)'
        control=ET.SubElement(setting,'control',{'type':'button','format':'action'})
        ET.SubElement(control,'close').text='false'
        diagnostic_category = ET.SubElement(settings.getroot().find('section'), 'category',
                                             {'id': 'diagnostics', 'label': '32092'})
        diagnostic_group = ET.SubElement(diagnostic_category, 'group', {'id': '1'})
        diagnostic_setting = ET.SubElement(diagnostic_group, 'setting',
                                           {'id': 'view_diagnostics', 'type': 'action', 'label': '32093'})
        ET.SubElement(diagnostic_setting, 'level').text = '0'
        ET.SubElement(diagnostic_setting, 'data').text = 'RunScript(special://home/addons/' + ID + '/view_diagnostics.py)'
        diagnostic_control = ET.SubElement(diagnostic_setting, 'control', {'type': 'button', 'format': 'action'})
        ET.SubElement(diagnostic_control, 'close').text = 'true'
        settings.write(addon/'resources/settings.xml',encoding='utf-8',xml_declaration=True)
        language=addon/'resources/language/resource.language.en_gb/strings.po'
        with language.open('a',encoding='utf-8') as messages:
            messages.write('\nmsgctxt "#32090"\nmsgid "Updates"\nmsgstr ""\n\n'
                           'msgctxt "#32091"\nmsgid "Check for updates"\nmsgstr ""\n\n'
                           'msgctxt "#32092"\nmsgid "Diagnostics"\nmsgstr ""\n\n'
                           'msgctxt "#32093"\nmsgid "View diagnostic report"\nmsgstr ""\n')
    shutil.copyfile(ROOT/'src/entry.py', default)
    metadata = ET.parse(addon / 'addon.xml')
    root = metadata.getroot()
    root.set('id', ID); root.set('name', NAME); root.set('version', version)
    root.set('provider-name', MAINTAINER)
    application = root.find("extension[@point='xbmc.python.pluginsource']")
    assert application is not None
    application.set('point', 'xbmc.python.script')
    application.set('library', 'default.py')
    application.find('provides').text = 'video'
    details = root.find("extension[@point='xbmc.addon.metadata']")
    for tag in ('summary', 'description', 'forum', 'source', 'disclaimer'):
        for node in list(details.findall(tag)):
            details.remove(node)
    ET.SubElement(details, 'summary', {'lang':'en_GB'}).text = 'Progressive Prime Video browsing in a dedicated interface'
    ET.SubElement(details, 'description', {'lang':'en_GB'}).text = (
        'Prime Video add-on maintained by AgreeWithScotty. '
        'Includes GPL-3.0-or-later backend code derived from Sandmann79/xbmc. '
        'See NOTICE.md for upstream credits. Not affiliated with Amazon.')
    details.find('reuselanguageinvoker').text = 'false'
    ET.indent(metadata, space='  ')
    metadata.write(addon / 'addon.xml', encoding='utf-8', xml_declaration=True)
    media = addon/'resources/skins/Default/media'
    for pattern in ('prime-nav-*.png', 'prime-card-gold*.png', 'prime-poster-mask.png'):
        for texture in (ROOT/'src/assets').glob(pattern):
            shutil.copyfile(texture, media/texture.name)
    art_dir = addon/'resources/art'
    art_dir.mkdir(parents=True, exist_ok=True)
    for name in ('prime-scene-icon-v2.png', 'prime-scene-fanart-v2.png'):
        shutil.copyfile(ROOT/'src/assets'/name, art_dir/name)
    shutil.copyfile(art_dir/'prime-scene-icon-v2.png', addon/'icon.png')
    shutil.copyfile(art_dir/'prime-scene-fanart-v2.png', addon/'fanart.png')
    # Remove inherited Amazon clearlogo from our metadata; keep backend credits.
    assets=details.find('assets')
    logo=assets.find('clearlogo')
    if logo is not None:assets.remove(logo)
    assets.find('icon').text='resources/art/prime-scene-icon-v2.png'
    assets.find('fanart').text='resources/art/prime-scene-fanart-v2.png'
    metadata.write(addon / 'addon.xml', encoding='utf-8', xml_declaration=True)
    shutil.copyfile(ROOT/'README.md',addon/'README.md')
    if repository_updates:
        readme=(addon/'README.md').read_text(encoding='utf-8')
        readme=re.sub(r'Current release:\d+\.\d+\.\d+\.', 'Current release:'+version+'.', readme)
        readme=re.sub(r'plugin\.video\.primeinterface-\d+\.\d+\.\d+\.zip',ID+'-'+version+'.zip',readme)
        readme=readme.replace('There is no hosted update repository yet; updates are installed using release ZIPs.',
                              'Install the Prime Scene Repository once to enable Kodi updates. Settings -> Updates -> Check for updates reports the result in place and lets Kodi install updates automatically when enabled.')
        (addon/'README.md').write_text(readme,encoding='utf-8')
    shutil.copyfile(ROOT/'NOTICE.md',addon/'NOTICE.md')
    for report in ('validation.json','interface-validation.json','first-login-validation.json','window-startup-validation.json','presentation-validation.json','upstream-release-verification.json','update-validation.json'):
        if (ROOT/report).is_file():
            shutil.copyfile(ROOT/report,addon/report)
    notes=version+'\n'
    notes+='- Preserve the authenticated Amazon website session with its original cookie domains, paths, flags and expiry.\n'
    notes+='- Refresh an existing website session only within the account generation that began the request.\n'
    notes+='- Use the independent authenticated catalog reader for Home, Movies and TV.\n'
    notes+='- Display the dashboard immediately and load Amazon cards in the background.\n'
    notes+='- Publish cards as each real continuation page arrives and retain the current selection.\n'
    notes+='- Keep token-based playback and the legacy account-cookie fallback available.\n'
    notes+='- Open the dedicated application directly from Video add-ons, without first activating Kodi Videos.\n'
    notes+='- Keep the same add-on ID, saved account, playback resolver URLs and normal updates.\n'
    notes+='- Dispatch settings and background actions as scripts and record launch mode and native window IDs.\n'
    notes+='- Record complete sign-in stages, redacted redirects, HTTP responses, form structures, server errors and exception locations.\n'
    notes+='- Correlate each real attempt with the installed version and timing; keep secrets out of diagnostics.\n'
    notes+='- Add a bottom sidebar account button: Sign in, or the signed-in account name with Sign out.\n'
    notes+='- Clear the signed-out account, sessions, cached lists, history and account artwork.\n'
    notes+='- Block delayed background writes from restoring signed-out account data.\n'
    notes+='- Consume the native authorization return directly after verification, without requesting it as a web page.\n'
    notes+='- Report HTTP status and sign-in stage, and allow correction of rejected verification codes.\n'
    notes+='- Replace sign-in with the native Prime Scene email, password and verification workflow.\n'
    notes+='- Handle verification-method choices, email/SMS codes, approval messages and security images in Kodi.\n'
    notes+='- Complete device authorization and save playback credentials before reporting sign-in success.\n'
    notes+='- Preserve saved accounts when authorization fails or is canceled.\n'
    notes+='- Match the main background to the pure black card backgrounds.\n'
    notes+='- Narrow the sidebar and give the recovered width to the catalog.\n'
    notes+='- Use pure black card backgrounds in every view.\n'
    notes+='- Refine Home, browsing grids and title information for viewing across the room.\n'
    notes+='- Load the first two catalog pages, then fetch the next page when you enter the newest page.\n'
    notes+='- Fit category pictures without cropping, remove gold card frames, and brighten selection throughout.\n'
    notes+='- Fill Home with two rows of four large category tiles and keep the landing page free of scrolling.\n'
    notes+='- Prefetch catalog pages farther ahead and stop the grid wrapping to the top at the end.\n'
    notes+='- Keep your selected card as new catalog pages arrive.\n'
    notes+='- Start the Prime Scene window sooner and keep update checks on the current screen.\n'
    notes+='- Show update status in Settings and let Kodi install available updates when automatic updates are on.\n'
    notes+='- List AgreeWithScotty as the Prime Scene maintainer and retain upstream credits.\n'
    (addon/'changelog.txt').write_text(notes,encoding='utf-8')
    # Verify the whole runnable package, not just newly authored modules.
    for path in addon.rglob('*.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'), feature_version=(3,8))
    for path in addon.rglob('*.xml'):
        ET.parse(path)
    archive=output/(ID+'-'+version+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(addon.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
                z.write(path,ID+'/'+path.relative_to(addon).as_posix())
    evidence={'id':ID,'name':NAME,'version':version,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'upstream_base':'Sandmann79 Amazon VOD1.2.4+matrix.1',
              'old_addon_runtime_dependency':False,'repository_updates':repository_updates,'profile':'special://profile/addon_data/'+ID,
              'application_entry':'xbmc.python.script','provides':'video',
              'files':sum(1 for p in addon.rglob('*') if p.is_file() and '__pycache__' not in p.parts)}
    (output/'release.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--version',default='0.1.28')
    parser.add_argument('--repository-updates',action=argparse.BooleanOptionalAction,default=True)
    args=parser.parse_args()
    print(build(args.version,repository_updates=args.repository_updates))
