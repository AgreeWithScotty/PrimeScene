"""Package the menu-focus release on the published 0.2.21 archive.

The top navigation drops the white focus pill. Static labels use a wide
feathered ultramarine glyph glow, and the account label uses farther offset
copies of its own text. Playback bytes from 0.2.21 are left untouched.
"""
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.22', '0.2.21'
BASE_SHA256 = 'eaf808c32bfa4c8f52a58b0c67e764fd86d57ce150e1c8189943b593278a6d7f'
NOTES = """0.2.22
- Menu focus now glows around the text instead of a white oval.
- Remove the line under the active tab; the glow shows which item is focused.
"""
GLOW_TEXTURES = (
    'nav-glow-in-progress.png',
    'nav-glow-movies.png',
    'nav-glow-tv.png',
    'nav-glow-sports.png',
    'nav-glow-purchases.png',
    'nav-glow-search.png',
    'nav-glow-settings.png',
)
PRESERVE = (
    'resources/lib/aurora.py',
    'resources/lib/playback.py',
    'resources/lib/service.py',
    'resources/lib/prime_login.py',
    'resources/lib/prime_signin.py',
)


def _news(notes):
    return notes.strip().replace('\n', '[CR]')


def _replace_release_text(xml_text):
    updated, version_count = re.subn(
        r'version="%s"' % re.escape(BASE_VERSION),
        'version="%s"' % VERSION,
        xml_text,
        count=1)
    assert version_count == 1
    updated, news_count = re.subn(
        r'<news>.*?</news>',
        '<news>%s</news>' % _news(NOTES),
        updated,
        count=1,
        flags=re.DOTALL)
    assert news_count == 1
    return updated


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    repository = Path(__file__).resolve().parents[1]
    base = repository / 'packages' / ID / (ID + '-' + BASE_VERSION + '.zip')
    output = repository / 'packages' / ID / (ID + '-' + VERSION + '.zip')
    source_home = repository / 'source' / 'rewrite' / 'PrimeSceneHome.xml'
    media = repository / 'source' / 'rewrite' / 'skins' / 'Default' / 'media'
    assert _sha256(base) == BASE_SHA256
    assert not output.exists(), 'Published numbered releases are immutable'
    home_text = source_home.read_text(encoding='utf-8')
    assert 'focus-pill.png' not in home_text
    assert 'Window.Property(Nav)' not in home_text
    assert 'nav-glow-movies.png' in home_text
    ET.fromstring(home_text)
    prefix = ID + '/'
    with zipfile.ZipFile(base) as package:
        contents = {name: package.read(name) for name in package.namelist() if not name.endswith('/')}
    before = dict(contents)
    home_name = prefix + 'resources/skins/Default/1080i/PrimeSceneHome.xml'
    contents[home_name] = source_home.read_bytes()
    glow_names = []
    for texture in GLOW_TEXTURES:
        archived = prefix + 'resources/skins/Default/media/' + texture
        data = (media / texture).read_bytes()
        assert data.startswith(b'\x89PNG\r\n\x1a\n')
        contents[archived] = data
        glow_names.append(archived)
    addon_name = prefix + 'addon.xml'
    contents[addon_name] = _replace_release_text(contents[addon_name].decode('utf-8')).encode('utf-8')
    contents[prefix + 'changelog.txt'] = NOTES.encode('utf-8')
    allowed = {home_name, addon_name, prefix + 'changelog.txt'}
    assert set(contents) - set(before) == set(glow_names)
    for name, data in contents.items():
        if name not in allowed and name not in glow_names:
            assert data == before[name], name
    for relative in PRESERVE:
        assert contents[prefix + relative] == before[prefix + relative]
    packaged_home = contents[home_name].decode('utf-8')
    assert 'focus-pill.png' not in packaged_home
    ET.fromstring(packaged_home)
    for name, data in contents.items():
        if name.endswith('.py'):
            compile(data.decode('utf-8-sig'), name, 'exec')
        elif name.endswith('.xml'):
            ET.fromstring(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, data in sorted(contents.items()):
            package.writestr(name, data)
    with zipfile.ZipFile(output) as package:
        assert package.testzip() is None
        packed_home = package.read(home_name).decode('utf-8')
        assert 'focus-pill.png' not in packed_home
        ET.fromstring(packed_home)
    changelog = repository / 'packages' / ID / ('changelog-' + VERSION + '.txt')
    changelog.write_bytes(NOTES.replace('\n', '\r\n').encode('utf-8'))
    addons = repository / 'addons.xml'
    addons.write_text(_replace_release_text(addons.read_text(encoding='utf-8')), encoding='utf-8')
    digest = hashlib.md5(addons.read_bytes()).hexdigest().encode('ascii')
    (repository / 'addons.xml.md5').write_bytes(digest)
    publication_path = repository / 'publication.json'
    publication = json.loads(publication_path.read_bytes())
    publication['app_version'] = VERSION
    files = publication['files']
    tracked = [
        'addons.xml',
        'addons.xml.md5',
        'packages/%s/changelog-%s.txt' % (ID, VERSION),
        'packages/%s/%s-%s.zip' % (ID, ID, VERSION),
        'source/rewrite/PrimeSceneHome.xml',
        'source/rewrite/skins/Default/1080i/PrimeSceneHome.xml',
        'tools/package_menu_focus_release.py',
    ]
    tracked.extend('source/rewrite/skins/Default/media/' + name for name in GLOW_TEXTURES)
    for relative in tracked:
        files[relative] = _sha256(repository / relative)
    publication_path.write_bytes(json.dumps(publication, indent=2).replace('\n', '\r\n').encode('utf-8'))
    # The manifest records this file. Hash it after the write only if the write did not
    # include a stale self-hash; the self-hash was computed from the script bytes, which
    # do not contain publication.json, so the recorded hash already matches the file.
    assert files['tools/package_menu_focus_release.py'] == _sha256(Path(__file__))
    print(json.dumps({
        'version': VERSION,
        'files': len(contents),
        'added': [name.split('/')[-1] for name in glow_names],
        'sha256': _sha256(output),
        'bytes': output.stat().st_size,
        'focus_pill_in_home_xml': False,
        'playback_bytes_preserved': True,
    }, indent=2))


if __name__ == '__main__':
    main()
