"""Local immutable-release packager. Remote publication is GitHub-plugin only."""
import ast,hashlib,json,re,shutil,sys,zipfile
from pathlib import Path
import xml.etree.ElementTree as E
from audit_package_assets import inspect as inspect_assets
project=Path(__file__).resolve().parent.parent
spec_file=Path(sys.argv[1]).resolve();spec=json.loads(spec_file.read_text(encoding='utf-8'));task=spec_file.parent
version,previous=spec['version'],spec['previous']
assert all(re.fullmatch(r'\d+\.\d+\.\d+',v) for v in (version,previous))
root=project/'current/plugin.video.primeinterface';release=project/'releases'/version;prior=project/'releases'/previous
stage=release/'publication';source=stage/'source/rewrite';archive=release/(root.name+'-'+version+'.zip');prefix=root.name+'/'
replace_unpublished='--replace-unpublished' in sys.argv[2:]
assert not (release/'published.json').exists() and (not archive.exists() or replace_unpublished),'Published releases cannot be overwritten'
base=prior/(root.name+'-'+previous+'.zip');expected=json.loads((prior/'release.json').read_text())['sha256']
assert hashlib.sha256(base.read_bytes()).hexdigest()==expected
with zipfile.ZipFile(base) as z:before={n:z.read(n) for n in z.namelist() if not n.endswith('/')}
files={prefix+p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
asset_audit=inspect_assets(root)
if spec.get('restore_from'):
    # Explicit historical rollback: preserve the complete verified snapshot,
    # including its old unused assets. Never exempt arbitrary new files.
    restore=spec['restore_from'];assert re.fullmatch(r'\d+\.\d+\.\d+',restore)
    snapshot=project/'releases'/restore;historical=snapshot/(root.name+'-'+restore+'.zip')
    assert hashlib.sha256(historical.read_bytes()).hexdigest()==json.loads((snapshot/'release.json').read_text())['sha256']
    with zipfile.ZipFile(historical) as z:restored={n:z.read(n) for n in z.namelist() if not n.endswith('/')}
    assert set(files)==set(restored),'Historical rollback must restore the complete package'
    metadata={prefix+n for n in ('addon.xml','README.md','changelog.txt')}
    assert all(data==restored[n] for n,data in files.items() if n not in metadata),'Historical runtime must match exactly'
else:
    assert not asset_audit['unused_files'],('Unreferenced package assets/data',asset_audit['unused_files'])
assert not asset_audit['missing_references'],('Missing package assets',asset_audit['missing_references'])
notes=version+'\n'+'\n'.join('- '+line for line in spec['changes'])+'\n'
manifest=E.fromstring(files[prefix+'addon.xml']);assert manifest.get('version') in ((previous,version) if replace_unpublished else (previous,));manifest.set('version',version)
manifest.find("extension[@point='xbmc.addon.metadata']/news").text=notes.strip().replace('\n','[CR]');E.indent(manifest,space='  ')
files[prefix+'addon.xml']=E.tostring(manifest,encoding='utf-8',xml_declaration=True);files[prefix+'changelog.txt']=notes.encode()
files[prefix+'README.md']=files[prefix+'README.md'].split(('\n\n'+version+': ').encode())[0]+('\n\n'+version+': '+spec['readme_append']+'\n').encode()
changed=sorted(n for n,d in files.items() if before.get(n)!=d)
removed=sorted(set(before)-set(files));assert set(removed)=={prefix+n for n in spec.get('removed_files',[])},removed
allowed={'addon.xml','changelog.txt','README.md'}|set(spec['allowed_changes'])
assert set(changed)<={prefix+n for n in allowed},set(changed)-{prefix+n for n in allowed}
for n,d in files.items():
    if n.endswith('.py'):ast.parse(d.decode('utf-8-sig'),feature_version=(3,8))
    elif n.endswith('.xml'):E.fromstring(d)
source.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for n,d in sorted(files.items()):z.writestr(n,d)
with zipfile.ZipFile(archive) as z:assert z.testzip() is None and all(z.read(n)==d for n,d in files.items())
details={'version':version,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size,'files':len(files)}
evidence=dict(details,changed_files=changed,removed_files=removed,kodi_ui_outcome_observed=False,mocked_provider_or_kodi_tests=False)
for key,path in spec.get('evidence_files',{}).items():evidence[key]=json.loads((task/path).read_text(encoding='utf-8'))
(release/'release.json').write_text(json.dumps(details,indent=2));(release/'change-evidence.json').write_text(json.dumps(evidence,indent=2))
(release/'asset-reference-audit.json').write_text(json.dumps(asset_audit,indent=2))
(source/('change-'+version+'.json')).write_text(json.dumps(evidence,indent=2));(source/'README.md').write_text(spec['source_readme'],encoding='utf-8')
for n in changed:
    relative=n[len(prefix):]
    if relative.startswith('resources/lib/') and n.endswith(('.py','.json')):(source/Path(n).name).write_bytes(files[n])
    elif n.endswith('.xml') and relative.startswith('resources/skins/'):
        (source/Path(n).name).write_bytes(files[n]);dest=source/relative[len('resources/'):];dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(files[n])
index=(prior/'publication/addons.xml').read_text(encoding='utf-8')
index,count=re.subn(r'<addon\b[^>]*\bid="plugin\.video\.primeinterface"[^>]*>.*?</addon>',lambda _:E.tostring(manifest,encoding='unicode'),index,count=1,flags=re.S);assert count==1
(stage/'addons.xml').write_bytes(index.encode());(stage/'addons.xml.md5').write_text(hashlib.md5(index.encode()).hexdigest())
dest=stage/'packages'/root.name;dest.mkdir(parents=True,exist_ok=True);shutil.copyfile(archive,dest/archive.name);(dest/('changelog-'+version+'.txt')).write_text(notes)
pub=json.loads((prior/'publication/publication.json').read_text());pub['app_version']=version
for p in stage.rglob('*'):
    if p.is_file() and p.name!='publication.json':pub['files'][p.relative_to(stage).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
(stage/'publication.json').write_text(json.dumps(pub,indent=2));plan=[]
for p in sorted(stage.rglob('*')):
    if p.is_file():
        d=p.read_bytes();plan.append({'path':p.relative_to(stage).as_posix(),'size':len(d),'sha':hashlib.sha1(('blob '+str(len(d))+'\0').encode()+d).hexdigest()})
(release/'upload-plan.json').write_text(json.dumps(plan,indent=2))
for name in ('addon.xml','README.md','changelog.txt'):(root/name).write_bytes(files[prefix+name])
print(json.dumps(dict(details,changed_files=len(changed),publication_files=len(plan)),indent=2))
