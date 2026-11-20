"""Rebuild the navigation release from its verified published base and source overlays.

In a PrimeScene checkout: python tools/package_navigation_release.py
Local preparation may pass --source-dir, --base and --output explicitly.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID = 'plugin.video.primeinterface'
VERSION = '0.2.5'
BASE_VERSION = '0.2.4'
BASE_SHA256 = 'ec42a0139d874f117aecd5443ed2ebc177d36598490f8a7628227ae2ce51251b'
OVERLAYS = {'aurora.py': 'resources/lib/aurora.py', 'account_state.py': 'resources/lib/account_state.py',
            'dedicated_bridge.py': 'resources/lib/dedicated_bridge.py', 'navigation.py': 'resources/lib/navigation.py',
            'artwork_cache.py': 'resources/lib/artwork_cache.py',
            'skins/Default/1080i/PrimeSceneHome.xml': 'resources/skins/Default/1080i/PrimeSceneHome.xml'}
NOTES = '''0.2.5
- Keep visited screens and their selected cards ready for Back and tab navigation.
- Load Home, Movies and TV ahead, sharing in-flight requests and warming their first two real pages.
- Bind one page at a time through Kodi's native window list instead of rebinding the full rail for each card.
- Display cards before drawing title plates; prepare nearby titles and artwork in the background.
- Keep storefront requests independent of title-detail lookups. Enrich only after focus settles.
- Keep account cache reads out of writer transactions and preserve complete sign-out cleanup.
- Record real navigation, request, card-binding, title-rendering and artwork timings in Diagnostics.
'''


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=repository/'source/rewrite')
    parser.add_argument('--base', type=Path, default=repository/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output', type=Path, default=repository/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256, 'The published base differs'
    assert not args.output.exists(), 'Do not overwrite an existing numbered release'
    prefix = ID + '/'
    with zipfile.ZipFile(args.base) as baseline:
        contents = {name: baseline.read(name) for name in baseline.namelist() if not name.endswith('/')}
    for source, target in OVERLAYS.items():
        contents[prefix+target] = (args.source_dir/source).read_bytes()
    root = ET.fromstring(contents[prefix+'addon.xml'])
    assert root.get('version') == BASE_VERSION
    root.set('version', VERSION)
    root.find("extension[@point='xbmc.addon.metadata']/news").text = NOTES.strip().replace('\n','[CR]')
    ET.indent(root, space='  ')
    contents[prefix+'addon.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
    contents[prefix+'changelog.txt'] = NOTES.encode('utf-8')
    readme = contents[prefix+'README.md'].decode('utf-8-sig')
    readme += '\n\nNavigation in 0.2.5\n\n'+(
        'Home, Movies and TV load in the background. Visited views keep their cards and selection. '
        'First pages are saved only inside the active account profile, fresh for 15 minutes and '
        'displayable for up to 24 hours while refreshing. Explicit sign-out erases these pages, '
        'artwork and title caches. Unknown Amazon continuations remain explicit diagnostic failures. '
        'Settings > Diagnostics contains real request, binding, title and image timings. '
        'A first uncached provider response still depends on the real service; no simulated '
        'provider or Kodi timing tests were used to claim responsiveness.\n')
    contents[prefix+'README.md'] = readme.encode('utf-8')
    for path, data in contents.items():
        if path.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), feature_version=(3,8))
        elif path.endswith('.xml'):
            ET.fromstring(data)
    old = ast.parse(zipfile.ZipFile(args.base).read(prefix+'resources/lib/aurora.py').decode())
    new = ast.parse(contents[prefix+'resources/lib/aurora.py'].decode())
    for name in ('PlayerOverlay','play_row','playback_row','choose_playback','watch_playback','note_playback'):
        a = next(node for node in old.body if isinstance(node,(ast.ClassDef,ast.FunctionDef)) and node.name==name)
        b = next(node for node in new.body if isinstance(node,(ast.ClassDef,ast.FunctionDef)) and node.name==name)
        assert ast.dump(a)==ast.dump(b), 'Playback changed: '+name
    assert root.get('id') == ID
    assert root.find("extension[@point='xbmc.python.script']") is not None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for path,data in sorted(contents.items()):
            package.writestr(path,data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
    print(json.dumps({'version':VERSION,'files':len(contents),'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      'bytes':args.output.stat().st_size,'preserved_playback_components':True,'simulated_tests_run':False},indent=2))


if __name__ == '__main__':
    main()
