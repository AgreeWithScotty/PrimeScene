"""Build Smooth Prime's description-preload update from the verified 0.2.5 ZIP."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.6', '0.2.5'
BASE_SHA256 = '4ea37b2f1336407a02403654bca53eba5f42a57b0115a4be010c6174aecee254'
OVERLAYS = ('aurora.py', 'public_catalog.py', 'description_cache.py')
NOTES = '''0.2.6
- Preload missing descriptions for the focused card, the next ten cards and two previous cards.
- Keep descriptions already delivered in Amazon's HTML/JSON when duplicate title cards are merged.
- Reuse account-scoped descriptions across navigation and openings; clear them on sign-out.
- Process one background metadata request at a time and check explicit Play jobs between requests.
- Preserve page prefetching, card selection, artwork read-ahead, Aurora styling and playback.
- Record description coverage, cache hits, prefetch timing and unavailable/failure outcomes in Diagnostics.
'''


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=root/'source/rewrite')
    parser.add_argument('--base', type=Path, default=root/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output', type=Path, default=root/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    args = parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest() == BASE_SHA256
    assert not args.output.exists(), 'Published versions must not be overwritten'
    prefix = ID+'/'
    with zipfile.ZipFile(args.base) as base:
        contents = {name:base.read(name) for name in base.namelist() if not name.endswith('/')}
    old_aurora = ast.parse(contents[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
    for name in OVERLAYS:
        contents[prefix+'resources/lib/'+name] = (args.source_dir/name).read_bytes()
    root_xml = ET.fromstring(contents[prefix+'addon.xml'])
    assert root_xml.get('id') == ID and root_xml.get('version') == BASE_VERSION
    root_xml.set('version', VERSION)
    root_xml.find("extension[@point='xbmc.addon.metadata']/news").text = NOTES.strip().replace('\n','[CR]')
    ET.indent(root_xml, space='  ')
    contents[prefix+'addon.xml'] = ET.tostring(root_xml,encoding='utf-8',xml_declaration=True)+b'\n'
    contents[prefix+'changelog.txt'] = NOTES.encode()
    contents[prefix+'README.md'] += (b'\n\nDescriptions in 0.2.6\n\n'
        b'Descriptions supplied with catalog pages are merged into the matching title cards. '
        b'Missing descriptions load ahead for the current card, ten following cards and two previous cards. '
        b'This uses bounded background metadata work, yields to explicit Play jobs between requests, '
        b'and never holds the primary page-reader lane. Account-scoped saved descriptions expire after '
        b'24 hours and are erased on explicit sign-out. Diagnostics report actual coverage, source, '
        b'request time and success/unavailable/failure. Provider response availability is not inferred '
        b'from syntax or package checks. No simulated provider or Kodi tests were run.\n')
    new_aurora = ast.parse(contents[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
    for name in ('PlayerOverlay','play_row','playback_row','choose_playback','watch_playback','note_playback','_resolve_playable','_play_resolved'):
        if name.startswith('_'):
            old_class=next(n for n in old_aurora.body if isinstance(n,ast.ClassDef) and n.name=='AuroraHome')
            new_class=next(n for n in new_aurora.body if isinstance(n,ast.ClassDef) and n.name=='AuroraHome')
            old_nodes,new_nodes=old_class.body,new_class.body
        else:
            old_nodes,new_nodes=old_aurora.body,new_aurora.body
        old=next(n for n in old_nodes if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name==name)
        new=next(n for n in new_nodes if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name==name)
        assert ast.dump(old)==ast.dump(new), 'Playback source changed: '+name
    for name,data in contents.items():
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'),feature_version=(3,8))
        elif name.endswith('.xml'):
            ET.fromstring(data)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for name,data in sorted(contents.items()):
            package.writestr(name,data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
    print(json.dumps({'version':VERSION,'files':len(contents),'bytes':args.output.stat().st_size,
                     'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
                     'playback_source_preserved':True,'simulated_tests_run':False},indent=2))


if __name__ == '__main__':
    main()
