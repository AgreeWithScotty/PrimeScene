"""Rebuild Smooth Prime's TV navigation/artwork update from published 0.2.7."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ID, VERSION, BASE_VERSION = 'plugin.video.primeinterface', '0.2.8', '0.2.7'
BASE_SHA256 = '92bd624812311c996ba0916bf37d1c848b527a1ee70376845aca03de790e6f9d'
OVERLAYS = {'aurora.py':'resources/lib/aurora.py', 'public_catalog.py':'resources/lib/public_catalog.py',
            'dedicated_bridge.py':'resources/lib/dedicated_bridge.py', 'navigation.py':'resources/lib/navigation.py',
            'description_cache.py':'resources/lib/description_cache.py',
            'skins/Default/1080i/PrimeSceneHome.xml':'resources/skins/Default/1080i/PrimeSceneHome.xml'}
NOTES = '''0.2.8
- TV shows open their episode browser instead of playing a resume or first episode automatically.
- Choose a season on the show page; individual episodes and movies start their own playback.
- Keep the selected show, episode position and season when navigating back.
- Prefer Amazon's declared landscape card art; fit complete images into uniform black card frames.
- Keep portrait fallback art separate from the landscape card source and preserve a clear fixed focus frame.
- Read the selected title's metadata instead of classifying a show from one of its child episodes.
- Preserve description/artwork/page read-ahead, the Smooth Prime branding, and the existing player.
'''


def main():
    repository=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,default=repository/'source/rewrite')
    parser.add_argument('--base',type=Path,default=repository/'packages'/ID/(ID+'-'+BASE_VERSION+'.zip'))
    parser.add_argument('--output',type=Path,default=repository/'packages'/ID/(ID+'-'+VERSION+'.zip'))
    args=parser.parse_args()
    assert hashlib.sha256(args.base.read_bytes()).hexdigest()==BASE_SHA256
    assert not args.output.exists(), 'Published numbered releases must not be overwritten'
    prefix=ID+'/'
    with zipfile.ZipFile(args.base) as base:
        contents={name:base.read(name) for name in base.namelist() if not name.endswith('/')}
    previous=ast.parse(contents[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
    for source,target in OVERLAYS.items():
        contents[prefix+target]=(args.source_dir/source).read_bytes()
    updated=ast.parse(contents[prefix+'resources/lib/aurora.py'].decode('utf-8-sig'))
    for name in ('PlayerOverlay','play_row','watch_playback','note_playback'):
        old=next(node for node in previous.body if isinstance(node,(ast.ClassDef,ast.FunctionDef)) and node.name==name)
        new=next(node for node in updated.body if isinstance(node,(ast.ClassDef,ast.FunctionDef)) and node.name==name)
        assert ast.dump(old)==ast.dump(new), 'The existing player component changed: '+name
    metadata=ET.fromstring(contents[prefix+'addon.xml'])
    assert metadata.get('id')==ID and metadata.get('name')=='Smooth Prime' and metadata.get('version')==BASE_VERSION
    metadata.set('version',VERSION)
    metadata.find("extension[@point='xbmc.addon.metadata']/news").text=NOTES.strip().replace('\n','[CR]')
    ET.indent(metadata,space='  ')
    contents[prefix+'addon.xml']=ET.tostring(metadata,encoding='utf-8',xml_declaration=True)+b'\n'
    contents[prefix+'changelog.txt']=NOTES.encode()
    contents[prefix+'README.md']+=(b'\n\nTV navigation and artwork in 0.2.8\n\n'
        b'Movies and individual episodes play their own video. TV shows open an episode browser, '
        b'with a season selector when Amazon provides multiple linked seasons. The app does not '
        b'auto-select a resume/first episode for a show card. Selected-title metadata stays separate '
        b'from child episode metadata. Series pages retain the original catalog position for Back. '
        b'Cards prefer declared landscape artwork and use a fixed black frame with complete-image '
        b'fitting for portrait-only fallbacks. No image is stretched to pretend it has another shape. '
        b'Diagnostics record title actions, returned kinds, season/episode counts and art coverage. '
        b'No simulated Kodi/provider tests or real-device UI-success claims were made.\n')
    for name,data in contents.items():
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'),feature_version=(3,8))
        elif name.endswith('.xml'):
            ET.fromstring(data)
    assert prefix+'clearlogo.png' not in contents
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for name,data in sorted(contents.items()):
            package.writestr(name,data)
    with zipfile.ZipFile(args.output) as package:
        assert package.testzip() is None
    print(json.dumps({'version':VERSION,'name':'Smooth Prime','files':len(contents),
                     'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
                     'bytes':args.output.stat().st_size,'player_components_preserved':True,
                     'simulated_tests_run':False},indent=2))


if __name__=='__main__':
    main()
