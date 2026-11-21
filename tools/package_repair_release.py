"""Package TV/focus/account-lifecycle repairs on the verified 0.2.13 release."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.14', '0.2.13'
BASE_SHA256 = 'f8d83a6d18c926cda7a120424b8eb12bdd47814ac40e9a4450064b28f8490f9f'
OVERLAYS = {name+'.py': 'resources/lib/'+name+'.py' for name in
    ('aurora', 'dedicated_bridge', 'tv_catalog', 'account_state', 'service', 'prime_login', 'prime_diagnostics')}
OVERLAYS['gui.py'] = 'gui.py'
NOTES = '''0.2.14
- Clicking a top category selects and focuses its first card once the real cards arrive.
- Read episode children independently of season-folder rendering; open the selected season when episodes are absent.
- Read current Amazon TV detail state as a fallback, retaining actual episode IDs and season links.
- Invalidate the account before stopping workers; attempt every sign-out cleanup stage and audit remaining files.
- Block a new login while cleanup is incomplete and show Finish sign out for a retry.
- Sign in again now purges the previous session first; fresh login is saved only after Amazon verifies it.
- Confirm completed sign-out and verified sign-in visibly, with correlated stage diagnostics.
- Guard title-art writes against account invalidation and notify active UI/service interpreters to drop old state.
- Preserve the SMOOTH icon/splash, Prime-only sections, playback controls, card return and preloading.
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
    for name in ('_start_playback', '_capture_playback_origin', '_return_loop'):
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
    contents[prefix+'README.md'] += (b'\n\nTV, focus and account lifecycle in 0.2.14\n\n'
        b'Top category clicks focus the first real card once, without resetting later appends. '
        b'TV listings use actual episode relationships and current Amazon detail state when '
        b'cached season rendering loses episodes. Sign-out revokes the local account generation '
        b'first, runs all cleanup stages, checks for remaining account files and blocks sign-in '
        b'if cleanup is incomplete. Sign in again purges the old session first. A website login '
        b'is saved and announced only after Amazon verifies the session. Amazon determines '
        b'which verification challenges are required. Diagnostics record actual focus, episode '
        b'structure, purge stages, residual counts and verification outcomes. Only neutral '
        b'coordination state and the new cleanup report remain after successful local purge.\n')
    allowed = {prefix+target for target in OVERLAYS.values()}
    allowed.update(prefix+name for name in ('addon.xml', 'changelog.txt', 'README.md'))
    assert set(contents)-set(before) == {prefix+'resources/lib/tv_catalog.py'}
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
        'player_return_and_branding_preserved': True, 'mocked_provider_tests_run': False}, indent=2))


if __name__ == '__main__':
    main()
