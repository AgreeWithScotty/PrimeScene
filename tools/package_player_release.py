"""Package stock Kodi playback controls on the verified 0.2.16 release."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.17', '0.2.16'
BASE_SHA256 = '4cc7de65de27ac276a9c93d7af9f96e82487737267ec7c7b256698dd1f36f52c'
OVERLAYS = {
    'aurora.py': 'resources/lib/aurora.py',
    'service.py': 'resources/lib/service.py',
}
REMOVED = (
    'resources/skins/Default/1080i/PrimeScenePlayer.xml',
    'resources/skins/Default/media/audio.png',
    'resources/skins/Default/media/dim.png',
    'resources/skins/Default/media/next.png',
    'resources/skins/Default/media/pause.png',
    'resources/skins/Default/media/prev.png',
    'resources/skins/Default/media/speaker.png',
    'resources/skins/Default/media/stop.png',
    'resources/skins/Default/media/subtitles.png',
)
NOTES = '''0.2.17
- Removed custom playback control bar; Kodi's standard player controls restored.
'''
REMOVED_CLASS = 'Player' + 'Overlay'
REMOVED_WINDOW = 'PrimeScene' + 'Player.xml'


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=repository / 'source/rewrite')
    parser.add_argument('--base', type=Path, default=repository / 'packages' / ID / (ID + '-' + BASE_VERSION + '.zip'))
    parser.add_argument('--output', type=Path, default=repository / 'packages' / ID / (ID + '-' + VERSION + '.zip'))
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256
    assert not args.output.exists(), 'Published numbered releases are immutable'
    prefix = ID + '/'
    with zipfile.ZipFile(args.base) as package:
        contents = {name: package.read(name) for name in package.namelist() if not name.endswith('/')}
    before = dict(contents)
    for source, target in OVERLAYS.items():
        contents[prefix + target] = (args.source_dir / source).read_bytes()
    for name in REMOVED:
        assert prefix + name in contents, name + ' was not in the previous release'
        del contents[prefix + name]
    old, new = (ast.parse(data[prefix + 'resources/lib/aurora.py'].decode('utf-8-sig'))
                for data in (before, contents))
    for name in ('play_row', 'choose_playback', 'playback_row', 'note_playback'):
        a, b = (next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
                for tree in (old, new))
        assert ast.dump(a) == ast.dump(b), name + ' unexpectedly changed'
    assert any(isinstance(node, ast.ClassDef) and node.name == REMOVED_CLASS for node in old.body)
    assert not any(isinstance(node, ast.ClassDef) and node.name == REMOVED_CLASS for node in new.body)
    old_class, new_class = (next(node for node in tree.body if isinstance(node, ast.ClassDef)
                                 and node.name == 'AuroraHome') for tree in (old, new))
    for name in ('_start_playback', '_capture_playback_origin'):
        a, b = (next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
                for cls in (old_class, new_class))
        assert ast.dump(a) == ast.dump(b), 'Playback launch changed: ' + name
    packaged_aurora = contents[prefix + 'resources/lib/aurora.py'].decode('utf-8')
    packaged_service = contents[prefix + 'resources/lib/service.py'].decode('utf-8')
    assert REMOVED_WINDOW not in packaged_aurora and 'Dialog.Close' not in packaged_aurora
    assert 'videoosd' not in packaged_aurora and 'seekbar' not in packaged_aurora
    assert 'xbmc.Player().play' in packaged_aurora and 'watch_playback' in packaged_service
    assert REMOVED_CLASS not in packaged_service and 'Dialog.Close' not in packaged_service
    for archived, data in contents.items():
        if not archived.endswith(('.py', '.xml', '.po', '.md', '.txt')):
            continue
        text = data.decode('utf-8', 'replace')
        assert REMOVED_WINDOW not in text, archived
        for texture in ('stop.png', 'subtitles.png', 'audio.png', 'pause.png', 'prev.png',
                        'dim.png', 'speaker.png'):
            if texture in text:
                raise AssertionError('%s still mentions %s' % (archived, texture))
    metadata = ET.fromstring(contents[prefix + 'addon.xml'])
    assert metadata.get('id') == ID and metadata.get('version') == BASE_VERSION
    metadata.set('version', VERSION)
    details = metadata.find("extension[@point='xbmc.addon.metadata']")
    details.find('news').text = NOTES.strip().replace('\n', '[CR]')
    description = details.find('description')
    description.text = description.text.replace(
        'Aurora Cinema is the home and player screen.',
        'Aurora Cinema is the home screen.')
    ET.indent(metadata, space='  ')
    contents[prefix + 'addon.xml'] = ET.tostring(metadata, encoding='utf-8', xml_declaration=True) + b'\n'
    contents[prefix + 'changelog.txt'] = NOTES.encode()
    contents[prefix + 'README.md'] += (
        b'\n\n0.2.17 stock Kodi playback controls\n'
        b'The custom playback bar is removed. A title still starts through PlayVideo and '
        b'setResolvedUrl, and Kodi draws its own OSD, seek bar and player menus. '
        b'The catalog window is unchanged and still returns to the card that started playback.\n')
    allowed = {prefix + target for target in OVERLAYS.values()}
    allowed.update(prefix + name for name in ('addon.xml', 'changelog.txt', 'README.md'))
    assert set(contents) - set(before) == set()
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
        names = package.namelist()
    assert all(prefix + name not in names for name in REMOVED)
    print(json.dumps({
        'version': VERSION,
        'files': len(contents),
        'removed': list(REMOVED),
        'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'bytes': args.output.stat().st_size,
        'playback_launch_preserved': True,
        'custom_playback_window_packaged': False,
    }, indent=2))


if __name__ == '__main__':
    main()
