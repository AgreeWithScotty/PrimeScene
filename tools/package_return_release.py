"""Package the playback return and quiet navigation change on verified 0.2.8."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.9', '0.2.8'
BASE_SHA256 = 'ca74bb5785141fb204fda9c81de1c9df76504acb06a75a6bd5b382252a3c3ee6'
OVERLAYS = {'aurora.py': 'resources/lib/aurora.py',
            'dedicated_bridge.py': 'resources/lib/dedicated_bridge.py'}
NOTES = '''0.2.9
- Return to the application Home when a movie or episode ends or is stopped.
- Restore the existing window after playback controls close; do not open a second app.
- Keep pending playback requests alive until playback starts instead of deleting their return state.
- Keep current cards and hero artwork visible while a new catalog page arrives.
- Remove navigation loading text and the title-resolution busy dialog.
- Home, Movies and TV navigation appear only in the top selectors.
- Preserve landscape cards, TV episode browsing, season selection and description/artwork read-ahead.
- Record playback request IDs, terminal reason, actual return window, timings and explicit failures.
'''


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=repository/'source/rewrite')
    parser.add_argument('--base', type=Path, default=repository/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output', type=Path, default=repository/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256
    assert not args.output.exists(), 'Never overwrite a numbered published release'
    prefix = ID+'/'
    with zipfile.ZipFile(args.base) as package:
        contents = {name: package.read(name) for name in package.namelist() if not name.endswith('/')}
    before = dict(contents)
    for name, target in OVERLAYS.items():
        contents[prefix+target] = (args.source_dir/name).read_bytes()
    old, new = (ast.parse(data[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
                for data in (before, contents))
    for name in ('PlayerOverlay', 'choose_playback', 'playback_row'):
        a, b = (next(node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                     and node.name == name) for tree in (old, new))
        assert ast.dump(a) == ast.dump(b), name+' unexpectedly changed'
    metadata = ET.fromstring(contents[prefix+'addon.xml'])
    assert metadata.get('id') == ID and metadata.get('name') == 'Smooth Prime'
    assert metadata.get('version') == BASE_VERSION
    metadata.set('version', VERSION)
    metadata.find("extension[@point='xbmc.addon.metadata']/news").text = NOTES.strip().replace('\n', '[CR]')
    ET.indent(metadata, space='  ')
    contents[prefix+'addon.xml'] = ET.tostring(metadata, encoding='utf-8', xml_declaration=True)+b'\n'
    contents[prefix+'changelog.txt'] = NOTES.encode()
    contents[prefix+'README.md'] += (b'\n\nPlayback return and quiet navigation in 0.2.9\n\n'
        b'Terminal playback restores the existing Home window after the overlay closes. '
        b'Return targets are invalidated on exit or account changes; a newer playback '
        b'cancels an old return command. The UI keeps existing cards while new pages '
        b'arrive and displays no navigation loading text or title-resolution spinner. '
        b'Home/Movies/TV navigation stays in the top selectors. Actual-run diagnostics '
        b'correlate request IDs, terminal reasons, observed window IDs and outcomes.\n')
    allowed = {prefix+target for target in OVERLAYS.values()}
    allowed.update(prefix+name for name in ('addon.xml', 'changelog.txt', 'README.md'))
    assert contents.keys() == before.keys()
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
    print(json.dumps({'version': VERSION, 'name': 'Smooth Prime', 'files': len(contents),
        'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(), 'bytes': args.output.stat().st_size,
        'player_overlay_and_title_policy_preserved': True, 'backend_bytes_preserved': True,
        'simulated_tests_run': False}, indent=2))


if __name__ == '__main__':
    main()
