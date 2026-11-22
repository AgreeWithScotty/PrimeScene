"""Package the navigation repair over the verified, immutable 0.2.20 release."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

here=Path(__file__).resolve().parent;project=here.parent
current=project/'current/plugin.video.primeinterface'
previous=project/'releases/0.2.20';release=project/'releases/0.2.21'
stage=release/'publication';source=stage/'source/rewrite'
assert not (release/'published.json').exists()
archive=release/'plugin.video.primeinterface-0.2.21.zip'
assert not archive.exists(),'Numbered artifacts are immutable once built'
subprocess.run([sys.executable,str(here/'audit_release.py')],check=True)
audit=json.loads((here/'source-audit.json').read_text())
base=previous/'plugin.video.primeinterface-0.2.20.zip'
assert hashlib.sha256(base.read_bytes()).hexdigest()=='24b23d67f310d760f2c8e5ff0de343848b1a64733f010652fdca8278da463b87'
prefix='plugin.video.primeinterface/'
with zipfile.ZipFile(base) as z:before={n:z.read(n) for n in z.namelist() if not n.endswith('/')}
files={prefix+p.relative_to(current).as_posix():p.read_bytes() for p in current.rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
notes='''0.2.21
- Start keyboard/remote focus on the In Progress top tab.
- Fetch In Progress from Amazon on every visit and playback return; discard old saved and visible lists.
- Remove locally saved playback-card merging and deduplicate repeated provider product identities.
- Keep In Progress show/season progress fresh without local completion overrides.
- Avoid reactivating the catalog window when Kodi already restored it, and remove catalog window fades.
- Hide the entire add-on interface during native fullscreen playback.
- Close the optional skip-scene dialog only when it is actually shown.
- Preserve originating-card return, stock Kodi controls, native authentication and other sections' preloading.
- Record fresh-list/cache cleanup, native window/view/focus/selection, timing and failure diagnostics.
'''
manifest=ET.fromstring(files[prefix+'addon.xml'])
assert manifest.get('version')=='0.2.20';manifest.set('version','0.2.21')
manifest.find("extension[@point='xbmc.addon.metadata']/news").text=notes.strip().replace('\n','[CR]')
ET.indent(manifest,space='  ')
files[prefix+'addon.xml']=ET.tostring(manifest,encoding='utf-8',xml_declaration=True)
files[prefix+'changelog.txt']=notes.encode()
files[prefix+'README.md']+=b'\n\n0.2.21: fresh In Progress and stable playback return\n\n'+(
    b'In Progress is an Amazon-only fresh snapshot for each visit and playback return. '
    b'Old progress/page files are deleted; no local history cards are merged. '
    b'Other sections retain first-two-page warming and descriptive artwork preloading. '
    b'The catalog avoids activating an already restored native window and has no open/close fades. '
    b'All add-on controls are hidden during native fullscreen video. '
    b'Authentication modules are byte-identical to 0.2.20. '
    b'Real-run diagnostics capture cleanup, fresh requests and native return state. '
    b'Source/archive checks do not claim observed post-change device playback behavior.\n')
changed=sorted(n for n in files if files[n]!=before.get(n))
allowed={'addon.xml','changelog.txt','README.md','resources/skins/Default/1080i/PrimeSceneHome.xml'}|{
    'resources/lib/'+name+'.py' for name in ('aurora','navigation','dedicated_bridge','public_catalog',
        'tv_catalog','home_catalog','watch_home','progress_cache','playback')}
assert set(changed)<={prefix+n for n in allowed},set(changed)-{prefix+n for n in allowed}
assert set(before)<=set(files)
for name,data in files.items():
    if name.endswith('.py'):ast.parse(data.decode('utf-8-sig'),feature_version=(3,8))
    elif name.endswith('.xml'):ET.fromstring(data)
source.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for name,data in sorted(files.items()):z.writestr(name,data)
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert all(z.read(n)==data for n,data in files.items())
details={'version':'0.2.21','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
    'bytes':archive.stat().st_size,'files':len(files)}
evidence=dict(details,source_audit=audit,changed_files=changed,
    preserved_baseline_files=sum(n not in changed for n in before),
    supplied_recording={'frames':401,'duration_seconds':13.366633,
        'dark_transition_seconds':6.167,'normal_catalog_seconds':6.267,
        'measurement':'mean frame luminance; supplied pre-change recording only'},
    kodi_window_manager_source='https://github.com/xbmc/xbmc/blob/master/xbmc/guilib/GUIWindowManager.cpp',
    source_observation='ActivateWindow_Internal closes the current window before sending WINDOW_INIT, including repeat activation.',
    in_progress_policy='fresh Amazon only; no saved/local-merged list; native resume/provider reporting retained',
    left_edge_fragment_source_confirmed=False,
    left_edge_containment='hide all app controls during native fullscreen; skip dialog close is idempotent',
    post_change_device_playback_observed=False,mocked_tests_run=False)
(release/'release.json').write_text(json.dumps(details,indent=2),encoding='utf-8')
(release/'navigation-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
(source/'navigation-0.2.21.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
for name in changed:
    if name.endswith(('.py','.xml')) and name!=prefix+'addon.xml':
        (source/Path(name).name).write_bytes(files[name])
for name in ('addon.xml','changelog.txt','README.md'):(current/name).write_bytes(files[prefix+name])
(source/'README.md').write_text('''# Smooth Prime 0.2.21

In Progress requests a new Amazon snapshot on every visit, including return
from playback. Memory/disk/bridge caches are bypassed; the obsolete local
watch-progress store and saved Home page are deleted under the account epoch
guard. No saved playback-card ledger is merged. Repeated actual product IDs
are collapsed without guessing that unrelated same-title editions are equal.
Provider completion fields are read fresh for In Progress series/season views.
Normal Movies/TV/Sports/Purchases caching and preloading remain.

Initial focus is the In Progress top tab; clicking a category still moves to
its first card. Native playback return waits for Kodi's natural restoration
and activates the catalog only if it is still inactive. No catalog window
fade animations remain. Every app control is in a group hidden during native
fullscreen video. The optional skip dialog closes once, only if shown.
Normal section return preserves the existing originating card/view/history;
In Progress return selects the matching card from the new provider snapshot.

Diagnostics cover cleanup, cache bypass, request/attempt IDs, real HTTP status
and redacted redirects/structure, window/view/card counts, focus/selection,
active dialog and elapsed times. Failures retain exception locations without
private account identifiers, passwords, codes, tokens or cookie values.

Concrete checks cover syntax, relative imports/exports, exact baseline byte
preservation, unchanged authentication and unchanged player code except the
skip-dialog lifecycle, archive CRC/hash and actual publication downloads.
The supplied pre-change recording and Kodi window-manager source informed the
repair. No simulated provider/Kodi tests or post-change device-success claims.
Full backend original-code replacement remains unfinished; keep NOTICE.md.
''',encoding='utf-8')
index=(previous/'publication/addons.xml').read_text(encoding='utf-8')
index,count=re.subn(r'<addon\b[^>]*\bid="plugin\.video\.primeinterface"[^>]*>.*?</addon>',
    lambda _:ET.tostring(manifest,encoding='unicode'),index,count=1,flags=re.S)
assert count==1
old=ET.fromstring((previous/'publication/addons.xml').read_bytes());new=ET.fromstring(index)
assert [ET.tostring(n) for n in old if n.get('id')!='plugin.video.primeinterface']==[
    ET.tostring(n) for n in new if n.get('id')!='plugin.video.primeinterface']
(stage/'addons.xml').write_bytes(index.encode());(stage/'addons.xml.md5').write_text(hashlib.md5(index.encode()).hexdigest())
dest=stage/'packages/plugin.video.primeinterface';dest.mkdir(parents=True,exist_ok=True)
shutil.copyfile(archive,dest/archive.name);(dest/'changelog-0.2.21.txt').write_text(notes,encoding='utf-8')
tools=stage/'tools';tools.mkdir(exist_ok=True)
shutil.copyfile(__file__,tools/'package_navigation_release.py')
publication=json.loads((previous/'publication/publication.json').read_text(encoding='utf-8'))
publication['app_version']='0.2.21'
for p in stage.rglob('*'):
    if p.is_file() and p.name!='publication.json':
        publication['files'][p.relative_to(stage).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
(stage/'publication.json').write_text(json.dumps(publication,indent=2),encoding='utf-8')
plan=[]
for p in sorted(stage.rglob('*')):
    if p.is_file():
        data=p.read_bytes();plan.append({'path':p.relative_to(stage).as_posix(),'size':len(data),
            'sha':hashlib.sha1(('blob '+str(len(data))+'\0').encode()+data).hexdigest()})
(release/'upload-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
print(json.dumps(dict(details,publication_files=len(plan),changed_files=len(changed)),indent=2))
