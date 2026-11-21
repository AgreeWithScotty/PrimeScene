"""Rebuild Smooth Prime with an electric ultramarine accent from published 0.2.10."""
import argparse
import ast
import hashlib
import json
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.11', '0.2.10'
BASE_SHA256 = 'e7a86948884714318c5bdd9e59daafc4929852ef7599e2c7cc023ce2323ea6c8'
OLD = (0, 168, 225)  # #00A8E1, the flat accent one digit from #00A8E0
NEW = (0x4F, 0x5B, 0xFF)
ACCENT_TEXTURES = (
    'resources/art/home.png',
    'resources/art/next.png',
    'resources/art/prime.png',
)
TEXT_REPLACEMENTS = {
    'resources/lib/common.py': ("'FF00A8E0'", "'FF4F5BFF'"),
    'resources/settings.xml': ('<default>FF00A8E0</default>', '<default>FF4F5BFF</default>'),
}
NOTES = '''0.2.11
- Replace the Prime Video accent cyan with electric ultramarine #4F5BFF.
- Recolor the flat accent icons, including lighter edge blends of that blue.
'''


def _white_mix(r, g, b, tolerance=22):
    """Return how much white is mixed into #00A8E1, when the pixel is that blue."""
    if min(r, g, b) > 236:
        return None
    if max(r, g, b) < 12:
        return None
    mix = r / 255.0
    predicted_g = OLD[1] * (1 - mix) + 255 * mix
    predicted_b = OLD[2] * (1 - mix) + 255 * mix
    if abs(g - predicted_g) <= tolerance and abs(b - predicted_b) <= tolerance and (b + g) > r + 40:
        return mix
    return None


def recolor_accent(data):
    image = Image.open(BytesIO(data)).convert('RGBA')
    pixels = image.load()
    changed = 0
    for y in range(image.size[1]):
        for x in range(image.size[0]):
            r, g, b, a = pixels[x, y]
            mix = _white_mix(r, g, b)
            if mix is None:
                continue
            pixels[x, y] = (
                int(round(NEW[0] * (1 - mix) + 255 * mix)),
                int(round(NEW[1] * (1 - mix) + 255 * mix)),
                int(round(NEW[2] * (1 - mix) + 255 * mix)),
                a,
            )
            changed += 1
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue(), changed


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=repository/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output', type=Path, default=repository/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256
    assert not args.output.exists(), 'Published numbered releases must not be overwritten'
    prefix = ID + '/'
    with zipfile.ZipFile(args.base) as base:
        contents = {name: base.read(name) for name in base.namelist() if not name.endswith('/')}
    counts = {}
    for relative, (old, new) in TEXT_REPLACEMENTS.items():
        text = contents[prefix + relative].decode('utf-8')
        assert text.count(old) == 1, relative
        contents[prefix + relative] = text.replace(old, new, 1).encode('utf-8')
        counts[relative] = 1
    for relative in ACCENT_TEXTURES:
        recolored, changed = recolor_accent(contents[prefix + relative])
        assert changed > 0, relative
        contents[prefix + relative] = recolored
        counts[relative] = changed
    assert contents[prefix + 'icon.png'] == zipfile.ZipFile(args.base).read(prefix + 'icon.png')
    metadata = ET.fromstring(contents[prefix + 'addon.xml'])
    assert metadata.get('id') == ID and metadata.get('name') == 'Smooth Prime' and metadata.get('version') == BASE_VERSION
    metadata.set('version', VERSION)
    metadata.find("extension[@point='xbmc.addon.metadata']/news").text = NOTES.strip().replace('\n', '[CR]')
    ET.indent(metadata, space='  ')
    contents[prefix + 'addon.xml'] = ET.tostring(metadata, encoding='utf-8', xml_declaration=True) + b'\n'
    contents[prefix + 'changelog.txt'] = NOTES.encode()
    for name, data in contents.items():
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), feature_version=(3, 8))
        elif name.endswith('.xml'):
            ET.fromstring(data)
        blob = data.lower()
        assert b'00a8e0' not in blob and b'00a8e1' not in blob, name
    args.output.parent.mkdir(parents=True, exist_ok=True)
    changelog = repository/'packages'/ID/('changelog-' + VERSION + '.txt')
    changelog.write_bytes(NOTES.encode())
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, data in sorted(contents.items()):
            package.writestr(name, data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
        packed = b''.join(package.read(name).lower() for name in package.namelist())
        assert b'00a8e0' not in packed and b'ff00a8e0' not in packed
    print(json.dumps({'version': VERSION, 'name': 'Smooth Prime', 'files': len(contents),
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      'bytes': args.output.stat().st_size, 'replacements': counts,
                      'icon_unchanged': True}, indent=2))


if __name__ == '__main__':
    main()
