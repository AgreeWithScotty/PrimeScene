"""Rebuild the navigation release from its verified published base and source overlays.

In a PrimeScene checkout: python tools/package_navigation_release.py
Local preparation may pass --source-dir, --base and --output explicitly.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

ID = 'plugin.video.primeinterface'
VERSION = '0.2.7'
BASE_VERSION = '0.2.6'
BASE_SHA256 = '3ce7fa2b5dbae5c1bb5917d00a9fb32017a63dc255979179ac29761678016c51'
NAME = 'Smooth Prime'
SUMMARY = 'Smooth Prime: Prime Video in the Aurora Cinema interface'
DESCRIPTION = ('Smooth Prime is a Prime Video add-on maintained by AgreeWithScotty. '
               'Aurora Cinema is the home and player screen. Website sign-in and the storefront reader use your own account. '
               'Includes GPL-3.0-or-later backend code derived from Sandmann79/xbmc. See NOTICE.md for upstream credits. Not affiliated with Amazon.')
OVERLAYS = {'aurora.py': 'resources/lib/aurora.py', 'account_state.py': 'resources/lib/account_state.py',
            'dedicated_bridge.py': 'resources/lib/dedicated_bridge.py', 'navigation.py': 'resources/lib/navigation.py',
            'artwork_cache.py': 'resources/lib/artwork_cache.py',
            'description_cache.py': 'resources/lib/description_cache.py',
            'public_catalog.py': 'resources/lib/public_catalog.py',
            'gui.py': 'gui.py', 'title_art.py': 'resources/lib/title_art.py',
            'view_diagnostics.py': 'view_diagnostics.py', 'prime_login.py': 'resources/lib/prime_login.py',
            'prime_signin.py': 'resources/lib/prime_signin.py', 'prime_device.py': 'resources/lib/prime_device.py',
            'skins/Default/1080i/PrimeSceneHome.xml': 'resources/skins/Default/1080i/PrimeSceneHome.xml'}
NOTES = '''0.2.7
- Rebranded to Smooth Prime; new icon; removed Amazon logo asset.
'''
# Files that are not source overlays. "Prime Scene Repository" stays: that is the other add-on.
TEXT_REPLACEMENTS = {
    'check_updates.py': (
        ("HEADING = 'Prime Scene updates'", "HEADING = 'Smooth Prime updates'"),
        ("'Prime Scene {} is up to date.'", "'Smooth Prime {} is up to date.'"),
        ("'Prime Scene updated to {}. Reopen Prime Scene to use the new version.'",
         "'Smooth Prime updated to {}. Reopen Smooth Prime to use the new version.'"),
        ("'Could not verify the Prime Scene update. Check your connection and try again.'",
         "'Could not verify the Smooth Prime update. Check your connection and try again.'"),
    ),
    'resources/lib/startup.py': (
        ("_g.dialog.yesno('Prime Scene',", "_g.dialog.yesno('Smooth Prime',"),
    ),
    'resources/lib/common.py': (
        ("'Prime Scene Movies'", "'Smooth Prime Movies'"),
        ("'Prime Scene TV'", "'Smooth Prime TV'"),
    ),
    'resources/settings.xml': (
        ('<default>Prime Scene Movies</default>', '<default>Smooth Prime Movies</default>'),
        ('<default>Prime Scene TV</default>', '<default>Smooth Prime TV</default>'),
    ),
    'resources/lib/playback.py': (
        ("name or 'Prime Scene'", "name or 'Smooth Prime'"),
    ),
    'resources/lib/proxy.py': (
        ("raise ConnectionAbortedError('Prime Scene is signed out')",
         "raise ConnectionAbortedError('Smooth Prime is signed out')"),
        ("raise ConnectionAbortedError('The Prime Scene account session ended')",
         "raise ConnectionAbortedError('The Smooth Prime account session ended')"),
    ),
    'NOTICE.md': (
        ('Prime Scene is maintained', 'Smooth Prime is maintained'),
        ('Prime Scene modifications', 'Smooth Prime modifications'),
    ),
}


def normalize_brand(text):
    """Map the 0.2.7 display name back to 0.2.6 so playback structure can be compared."""
    return (text.replace('S M O O T H   P R I M E', 'P R I M E   S C E N E')
            .replace('SMOOTH PRIME', 'PRIME SCENE')
            .replace('Smooth Prime', 'Prime Scene'))


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=repository/'source/rewrite')
    parser.add_argument('--base', type=Path, default=repository/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output', type=Path, default=repository/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    parser.add_argument('--icon', type=Path, default=repository/'packages'/ID/'icon.png')
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256, 'The published base differs'
    assert not args.output.exists(), 'Do not overwrite an existing numbered release'
    prefix = ID + '/'
    with zipfile.ZipFile(args.base) as baseline:
        contents = {name: baseline.read(name) for name in baseline.namelist() if not name.endswith('/')}
    for source, target in OVERLAYS.items():
        contents[prefix+target] = (args.source_dir/source).read_bytes()
    for relative, pairs in TEXT_REPLACEMENTS.items():
        text = contents[prefix+relative].decode('utf-8-sig')
        for old, new in pairs:
            assert text.count(old) == 1, 'Missing branded text in %s: %s' % (relative, old)
            text = text.replace(old, new, 1)
        contents[prefix+relative] = text.encode('utf-8')
    icon = args.icon.read_bytes()
    assert icon.startswith(b'\x89PNG\r\n\x1a\n') and icon[12:16] == b'IHDR', 'icon.png is not a PNG'
    width, height = struct.unpack('>II', icon[16:24])
    assert (width, height) == (512, 512) and icon[24] == 8 and icon[25] == 2, 'icon.png must be solid 512x512 RGB'
    contents[prefix+'icon.png'] = icon
    assert prefix+'clearlogo.png' in contents, 'The Amazon clearlogo is already gone from the base'
    del contents[prefix+'clearlogo.png']
    root = ET.fromstring(contents[prefix+'addon.xml'])
    assert root.get('version') == BASE_VERSION
    assert root.get('id') == ID
    root.set('name', NAME)
    root.set('version', VERSION)
    details = root.find("extension[@point='xbmc.addon.metadata']")
    details.find('summary').text = SUMMARY
    details.find('description').text = DESCRIPTION
    details.find('news').text = NOTES.strip().replace('\n', '[CR]')
    assets = details.find('assets')
    logo = assets.find('clearlogo')
    if logo is not None:
        assets.remove(logo)
    assert assets.find('icon') is not None and assets.find('icon').text == 'icon.png'
    assert assets.find('fanart') is not None
    ET.indent(root, space='  ')
    contents[prefix+'addon.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
    contents[prefix+'changelog.txt'] = NOTES.encode('utf-8')
    readme = contents[prefix+'README.md'].decode('utf-8-sig')
    assert readme.startswith('# Prime Scene for Prime Video')
    readme = readme.replace('# Prime Scene for Prime Video', '# Smooth Prime for Prime Video', 1)
    readme = readme.replace('during Prime Scene playback', 'during Smooth Prime playback', 1)
    readme = readme.replace('Current release: 0.2.4.', 'Current release: %s.' % VERSION, 1)
    contents[prefix+'README.md'] = readme.encode('utf-8')
    for path, data in contents.items():
        if path.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), feature_version=(3, 8))
        elif path.endswith('.xml'):
            ET.fromstring(data)
    def lookup(tree, name):
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name:
                return node
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == name:
                        return child
        raise AssertionError('Missing '+name)
    old = ast.parse(zipfile.ZipFile(args.base).read(prefix+'resources/lib/aurora.py').decode())
    new = ast.parse(normalize_brand(contents[prefix+'resources/lib/aurora.py'].decode()))
    for name in ('PlayerOverlay', 'play_row', 'playback_row', 'choose_playback', 'watch_playback', 'note_playback',
                 '_resolve_playable', '_play_resolved'):
        assert ast.dump(lookup(old, name)) == ast.dump(lookup(new, name)), 'Playback changed: '+name
    assert prefix+'resources/lib/description_cache.py' in contents
    assert prefix+'clearlogo.png' not in contents
    assert b'clearlogo' not in contents[prefix+'addon.xml']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    changelog = repository/'packages'/ID/('changelog-'+VERSION+'.txt')
    changelog.write_bytes(NOTES.encode('utf-8'))
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path, data in sorted(contents.items()):
            package.writestr(path, data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
        assert prefix+'clearlogo.png' not in package.namelist()
    print(json.dumps({'version': VERSION, 'name': NAME, 'files': len(contents),
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      'bytes': args.output.stat().st_size, 'clearlogo': False,
                      'preserved_playback_components': True}, indent=2))


if __name__ == '__main__':
    main()
