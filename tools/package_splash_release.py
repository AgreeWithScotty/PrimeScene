"""Build Smooth Prime 0.2.13 from verified 0.2.12 with the chosen icon and splash."""
import argparse
import ast
import hashlib
import struct
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.13', '0.2.12'
BASE_SHA256 = 'e5565c786ac25a316bf81f5788b238693c29b47b037d58acdd4827d9a3cef8e1'
ICON_SHA256 = '332ea2a2f91e6cbb4e6470ae6b66e94b9c25b42f4b00cb9322a3d1543a3fad62'
OVERLAYS = {
    'aurora.py': 'resources/lib/aurora.py',
    'skins/Default/1080i/PrimeSceneHome.xml': 'resources/skins/Default/1080i/PrimeSceneHome.xml',
    'skins/Default/media/splash.png': 'resources/skins/Default/media/splash.png',
}
NOTES = '''0.2.13
- Use the black-and-white SMOOTH / FOR AMAZON PRIME icon.
- Show that lockup as the add-on fanart and on the home window until the first cards appear.
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
    before = ast.parse(contents[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
    for source, target in OVERLAYS.items():
        contents[prefix+target] = (args.source_dir/source).read_bytes()
    icon = (repository/'packages'/ID/'icon.png').read_bytes()
    assert hashlib.sha256(icon).hexdigest() == ICON_SHA256
    assert icon.startswith(b'\x89PNG\r\n\x1a\n') and icon[12:16] == b'IHDR'
    width, height = struct.unpack('>II', icon[16:24])
    assert (width, height) == (512, 512) and icon[24] == 8 and icon[25] == 2
    contents[prefix+'icon.png'] = icon
    splash = contents[prefix+'resources/skins/Default/media/splash.png']
    assert splash.startswith(b'\x89PNG\r\n\x1a\n') and splash[12:16] == b'IHDR'
    sw, sh = struct.unpack('>II', splash[16:24])
    assert (sw, sh) == (1920, 1080) and len(splash) < 1000 * 1024
    updated = ast.parse(contents[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
    for name in ('PlayerOverlay', 'choose_playback', 'playback_row', 'play_row', 'watch_playback', 'note_playback'):
        old = next(node for node in before.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name)
        new = next(node for node in updated.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name)
        assert ast.dump(old) == ast.dump(new), name+' unexpectedly changed'
    metadata = ET.fromstring(contents[prefix+'addon.xml'])
    assert metadata.get('id') == ID and metadata.get('name') == 'Smooth Prime' and metadata.get('version') == BASE_VERSION
    metadata.set('version', VERSION)
    details = metadata.find("extension[@point='xbmc.addon.metadata']")
    details.find('news').text = NOTES.strip().replace('\n', '[CR]')
    fanart = details.find('assets/fanart')
    fanart.text = 'resources/skins/Default/media/splash.png'
    assert details.find('assets/icon').text == 'icon.png'
    ET.indent(metadata, space='  ')
    contents[prefix+'addon.xml'] = ET.tostring(metadata, encoding='utf-8', xml_declaration=True)+b'\n'
    contents[prefix+'changelog.txt'] = NOTES.encode()
    for name, data in contents.items():
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), feature_version=(3, 8))
        elif name.endswith('.xml'):
            ET.fromstring(data)
        assert b'00a8e0' not in data.lower() and b'00a8e1' not in data.lower(), name
    window = ET.fromstring(contents[prefix+'resources/skins/Default/1080i/PrimeSceneHome.xml'])
    assert any((node.text or '').strip() == 'splash.png' for node in window.iter('texture'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    (repository/'packages'/ID/('changelog-'+VERSION+'.txt')).write_bytes(NOTES.encode())
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, data in sorted(contents.items()):
            package.writestr(name, data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
        assert hashlib.sha256(package.read(prefix+'icon.png')).hexdigest() == ICON_SHA256
        fanart_path = ET.fromstring(package.read(prefix+'addon.xml')).find("extension[@point='xbmc.addon.metadata']/assets/fanart").text
        assert prefix+fanart_path in package.namelist()
    print({'version': VERSION, 'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
           'bytes': args.output.stat().st_size, 'fanart': fanart_path, 'icon_sha256': ICON_SHA256})


if __name__ == '__main__':
    main()
