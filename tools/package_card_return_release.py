"""Package the playback return and quiet navigation change on verified 0.2.9."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.10', '0.2.9'
BASE_SHA256 = 'ba35de7cd507a7ed4e07e38ead0e9c33869aede53b50494d30729079071957ac'
OVERLAYS = {'aurora.py': 'resources/lib/aurora.py'}
NOTES = '''0.2.10
- Return to the movie or episode card that launched playback when it ends or stops.
- Preserve the original page, selected card, loaded items, season and Back history.
- Capture the return position at click time, including asynchronous title resolution.
- Restore the existing native window without navigating to Home or fetching the page again.
- Keep newer appended pages when the originating view is still present.
- Clear saved playback context on sign-out and retain stale-return guards.
- Record expected/actual card positions and whether the originating card was restored.
- Preserve all 0.2.9 navigation, artwork, episode browsing and preloading changes.
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
    contents[prefix+'README.md'] = contents[prefix+'README.md'].replace(
        b'Terminal playback restores the existing Home window after the overlay closes.',
        b'Terminal playback restores the originating selected card after the overlay closes.')
    contents[prefix+'README.md'] += (b'\n\nPlayback return and quiet navigation in 0.2.10\n\n'
        b'Terminal playback restores the originating movie or episode card after the overlay closes. '
        b'Return targets are invalidated on exit or account changes; a newer playback '
        b'cancels an old return command. The UI keeps existing cards while new pages '
        b'arrive and displays no navigation loading text or title-resolution spinner. '
        b'Home/Movies/TV navigation stays in the top selectors. Actual-run diagnostics '
        b'correlate request IDs, terminal reasons, observed window IDs and outcomes. '
        b'Card identity, page, selection, season and Back history are captured at click time. '
        b'The current view is reused when possible; an in-memory snapshot restores it otherwise.\n')
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
