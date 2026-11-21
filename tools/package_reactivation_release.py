"""Package native catalog reactivation on the verified 0.2.15 release."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.16', '0.2.15'
BASE_SHA256 = '4f48af3220d7f755c34b63f2c7aee6855efef89f8cacc938160841edf2ed2f78'
OVERLAYS = {name+'.py': 'resources/lib/'+name+'.py' for name in ('aurora',)}
NOTES = '''0.2.16
- Restore the native catalog view and rebind cards whenever Kodi reinitializes the existing window after video.
- Keep the originating card, page, season and navigation position without a catalog fetch.
- Confirm native card count, view, selected index and focused control in the return diagnostics.
- Preserve existing playback, sign-out, TV episode browsing, preloading and SMOOTH branding.
'''


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=repository/'source/rewrite')
    parser.add_argument('--base', type=Path, default=repository/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output', type=Path, default=repository/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256
    assert not args.output.exists(), 'Published numbered releases are immutable'
    prefix = ID+'/'
    with zipfile.ZipFile(args.base) as package:
        contents = {name: package.read(name) for name in package.namelist() if not name.endswith('/')}
    before = dict(contents)
    for source, target in OVERLAYS.items():
        contents[prefix+target] = (args.source_dir/source).read_bytes()
    old, new = (ast.parse(data[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
                for data in (before, contents))
    for name in ('PlayerOverlay', 'play_row', 'watch_playback', 'choose_playback', 'playback_row'):
        a, b = (next(node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                     and node.name == name) for tree in (old, new))
        assert ast.dump(a) == ast.dump(b), name+' unexpectedly changed'
    old_class, new_class = (next(node for node in tree.body if isinstance(node, ast.ClassDef)
                               and node.name == 'AuroraHome') for tree in (old, new))
    for name in ('_start_playback', '_capture_playback_origin'):
        a, b = (next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
                for cls in (old_class, new_class))
        assert ast.dump(a) == ast.dump(b), 'Playback return changed: '+name
    metadata = ET.fromstring(contents[prefix+'addon.xml'])
    assert metadata.get('id') == ID and metadata.get('version') == BASE_VERSION
    metadata.set('version', VERSION)
    metadata.find("extension[@point='xbmc.addon.metadata']/news").text = NOTES.strip().replace('\n', '[CR]')
    ET.indent(metadata, space='  ')
    contents[prefix+'addon.xml'] = ET.tostring(metadata, encoding='utf-8', xml_declaration=True)+b'\n'
    contents[prefix+'changelog.txt'] = NOTES.encode()
    contents[prefix+'README.md'] += (b'\n\n0.2.16 native catalog reactivation\n'
        b'Reselect view 200 and rebind retained cards on each native onInit after playback. '
        b'Restore selected card/focus after actual reactivation, preserving the original exit target. '
        b'Return diagnostics include native/logical card counts, view, focus and selected index. '
        b'No live device return success is claimed from artifact/source checks.\n')
    allowed = {prefix+target for target in OVERLAYS.values()}
    allowed.update(prefix+name for name in ('addon.xml', 'changelog.txt', 'README.md'))
    assert set(contents)-set(before) == set()
    assert all(data == before[name] for name, data in contents.items() if name not in allowed)
    for name, data in contents.items():
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), feature_version=(3, 8))
        elif name.endswith('.xml'):
            ET.fromstring(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, data in sorted(contents.items()):
            package.writestr(name, data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
    print(json.dumps({'version': VERSION, 'files': len(contents),
        'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(), 'bytes': args.output.stat().st_size,
        'resolver_overlay_and_branding_preserved': True, 'mocked_provider_tests_run': False}, indent=2))


if __name__ == '__main__':
    main()
