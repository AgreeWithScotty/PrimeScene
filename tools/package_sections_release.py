"""Build the four-section catalog update from verified Smooth Prime 0.2.11."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.12', '0.2.11'
BASE_SHA256 = '926dbb90833e9aa8e70b680a48493d95475172b3b45780fc40ad3a9a236c35d6'
OVERLAYS = {name+'.py': 'resources/lib/'+name+'.py' for name in
    ('aurora', 'public_catalog', 'catalog_access', 'purchases_catalog', 'dedicated_bridge', 'navigation')}
OVERLAYS['skins/Default/1080i/PrimeSceneHome.xml'] = 'resources/skins/Default/1080i/PrimeSceneHome.xml'
NEW_FILES = {'resources/lib/catalog_access.py', 'resources/lib/purchases_catalog.py'}
NOTES = '''0.2.12
- Remove Home and open Movies by default.
- Use Movies, TV, Sports and Purchases as the four main selectors.
- Read Movies/TV from Amazon's Included with Prime feed, split by title type and explicit Prime evidence.
- Exclude unconfirmed, channel-only, rent/buy-only, preview-only and free-with-ads-only entries from Movies/TV.
- Sports reads Amazon's actual sports storefront and preserves live-event title types.
- Purchases follows Amazon's signed-in Purchases & Rentals library link; it does not substitute Watchlist.
- Warm the first two real pages for each section and invalidate old mixed-catalog page caches.
- Preserve the new SP icon, ultramarine styling, episode browser, descriptions/artwork read-ahead and return to the playing card.
- Record access/filter counts, library discovery, HTTP/redirect/parser details and explicit failures.
'''


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=repository/'source/rewrite')
    parser.add_argument('--base', type=Path, default=repository/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output', type=Path, default=repository/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256
    assert not args.output.exists(), 'Never overwrite a published numbered release'
    prefix = ID+'/'
    with zipfile.ZipFile(args.base) as archive:
        contents = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
    before = dict(contents)
    for source, target in OVERLAYS.items():
        contents[prefix+target] = (args.source_dir/source).read_bytes()
    old, new = (ast.parse(data[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
                for data in (before, contents))
    for name in ('PlayerOverlay', 'choose_playback', 'playback_row', 'play_row', 'watch_playback', 'note_playback'):
        a, b = (next(node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                     and node.name == name) for tree in (old, new))
        assert ast.dump(a) == ast.dump(b), name+' unexpectedly changed'
    metadata = ET.fromstring(contents[prefix+'addon.xml'])
    assert metadata.get('id') == ID and metadata.get('version') == BASE_VERSION
    assert metadata.get('name') == 'Smooth Prime'
    metadata.set('version', VERSION)
    metadata.find("extension[@point='xbmc.addon.metadata']/news").text = NOTES.strip().replace('\n', '[CR]')
    ET.indent(metadata, space='  ')
    contents[prefix+'addon.xml'] = ET.tostring(metadata, encoding='utf-8', xml_declaration=True)+b'\n'
    contents[prefix+'changelog.txt'] = NOTES.encode()
    contents[prefix+'README.md'] += (b'\n\nFour-section catalog in 0.2.12\n\n'
        b'Movies opens by default. The main selectors are Movies, TV, Sports and Purchases. '
        b'Movies/TV use the Included with Prime storefront and only retain typed titles '
        b'with explicit Prime access evidence. A generic entitlement or SVOD label does '
        b'not establish Prime inclusion. Unknown access is excluded. Sports uses its own '
        b'storefront. Purchases follows actual account-library links and reads only the '
        b'library content; Watchlist and recommendations are not substituted. The signed-in '
        b'library flow has not been observed with a live account. Diagnostics record '
        b'filter coverage, route discovery and protocol structure. Existing playback, '
        b'return-to-card behavior, SP artwork and ultramarine styling remain.\n')
    allowed = {prefix+target for target in OVERLAYS.values()}
    allowed.update(prefix+name for name in ('addon.xml', 'changelog.txt', 'README.md'))
    assert set(contents)-set(before) == {prefix+name for name in NEW_FILES}
    assert all(data == before[name] for name, data in contents.items() if name not in allowed)
    for name, data in contents.items():
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), feature_version=(3, 8))
        elif name.endswith('.xml'):
            ET.fromstring(data)
    window = ET.fromstring(contents[prefix+'resources/skins/Default/1080i/PrimeSceneHome.xml'])
    controls = window.find('controls')
    buttons = {int(node.get('id')): node for node in controls if node.get('type') == 'button'}
    expected = {301: 'MOVIES', 302: 'TV', 303: 'SPORTS', 308: 'PURCHASES'}
    assert all(buttons[key].findtext('label') == value for key, value in expected.items())
    assert not any(node.findtext('label') == 'HOME' for node in controls)
    ids = [node.get('id') for node in window.iter('control') if node.get('id')]
    assert len(ids) == len(set(ids))
    for key in (301, 302, 303, 308, 304, 305, 306):
        node = buttons[key]
        assert 0 <= int(node.findtext('left')) < 1920
        assert int(node.findtext('left'))+int(node.findtext('width')) <= 1920
        assert int(node.findtext('onleft')) in buttons and int(node.findtext('onright')) in buttons
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, data in sorted(contents.items()):
            package.writestr(name, data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
    print(json.dumps({'version': VERSION, 'name': 'Smooth Prime', 'files': len(contents),
        'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(), 'bytes': args.output.stat().st_size,
        'playback_and_branding_preserved': True, 'simulated_tests_run': False}, indent=2))


if __name__ == '__main__':
    main()
