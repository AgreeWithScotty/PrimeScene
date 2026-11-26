"""Stage only official build prerequisites locally; never install system tools."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import tarfile
import time
import traceback
import urllib.request
import uuid
import zipfile

HERE=Path(__file__).resolve().parent/'chrome-bridge'
ROOT=HERE/'client-sdk'
START=time.monotonic()
REV='llvmorg-24-init-14453-ge063be79-1'
SDK='10.0.28000.2957'
REPORT={'operation':'stage_official_mojo_client_build_prerequisites','attempt':uuid.uuid4().hex[:16],
        'system_installation':False,'chrome_or_kodi_build':False,'events':[],
        'llvm_latest_stable_checked':{'version':'23.1.6','source':'https://llvm.org/',
            'selection_reason':'Google compiler below is the exact installed-Chrome ABI build fixture, not a stable LLVM selection'},
        'sdk_selection':{'version':SDK,'source':'https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp/index.json',
            'reason':'newest published non-preview Microsoft SDK package; source fixture expects SDK family 28000'},
        'rust_latest_stable_checked':{'version':'1.100.0','date':'2026-11-12',
            'source':'https://static.rust-lang.org/dist/channel-rust-stable.toml',
            'selection_reason':'Google package below matches the installed Chrome source fixture compiler pin'}}
JOBS=[('clang', 'https://commondatastorage.googleapis.com/chromium-browser-clang/Win/clang-'+REV+'.tar.xz'),
      ('sdk-common','https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp/'+SDK+'/microsoft.windows.sdk.cpp.'+SDK+'.nupkg'),
      ('sdk-x64','https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp.x64/'+SDK+'/microsoft.windows.sdk.cpp.x64.'+SDK+'.nupkg'),
      ('rust','https://commondatastorage.googleapis.com/chromium-browser-clang/Win/rust-toolchain-8a9dd7abdf013ec10cfeaaa3685feba18ef1c90c-1-llvmorg-24-init-14453-ge063be79.tar.xz'),
      ('gn-source-fixture','https://chrome-infra-packages.appspot.com/dl/gn/gn/windows-amd64/+/git_revision:938087829fcbcc8fd87835ee301dd522c7637685'),
      ('ninja','https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-win.zip')]
REPORT['ninja_selection']={'version':'1.13.2','source':'https://github.com/ninja-build/ninja/releases/tag/v1.13.2',
    'reason':'newest published maintainer release verified from the latest-release redirect'}
REPORT['gn_selection']={'latest_verified':'2644 (0e41dcd9b564)',
    'used_revision':'938087829fcbcc8fd87835ee301dd522c7637685',
    'reason':'exact source-tag build-tool compatibility fixture; latest GN introduces stricter public-header graph requirements than the pinned source generator'}

def checked_path(root,name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name:
        raise ValueError('Archive path rejected')
    destination=root.joinpath(*path.parts).resolve()
    if os.path.commonpath([str(root.resolve()),str(destination)])!=str(root.resolve()):
        raise ValueError('Archive path escaped stage')
    return destination

def stage(job):
    name,url=job;begin=time.monotonic()
    event={'stage':'stage_build_prerequisite','name':name,'request_id':uuid.uuid4().hex[:12],
           'url':url,'result':'failure'}
    try:
        marker=ROOT/(name+'-staged.json')
        if marker.is_file():return dict(json.loads(marker.read_text()),reused_staged_package=True)
        packages=ROOT/'packages';packages.mkdir(exist_ok=True)
        if shutil.disk_usage(ROOT).free<5*1024*1024*1024:raise ValueError('Insufficient free staging space')
        archive_file=packages/(name+('.tar.xz' if name in ('clang','rust') else '.nupkg'))
        if not archive_file.exists():
            digest=hashlib.sha256();size=0
            partial=archive_file.with_suffix(archive_file.suffix+'.part')
            with urllib.request.urlopen(url,timeout=45) as reply,partial.open('wb') as output:
                event['http_status']=reply.status
                while True:
                    chunk=reply.read(1024*1024)
                    if not chunk:break
                    size+=len(chunk)
                    if size>(512 if name=='rust' else 256)*1024*1024:
                        raise ValueError('Build prerequisite size limit')
                    digest.update(chunk);output.write(chunk)
            partial.replace(archive_file)
            event.update(archive_bytes=size,sha256=digest.hexdigest())
        else:
            event.update(archive_bytes=archive_file.stat().st_size,
                         sha256=hashlib.file_digest(archive_file.open('rb'),'sha256').hexdigest(),from_local_archive=True)
        root=ROOT/({'clang':'src/third_party/llvm-build/Release+Asserts','rust':'src/third_party/rust-toolchain',
                    'gn-source-fixture':'gn-source-fixture','ninja':'ninja'}.get(name,'windows-sdk'))
        root.mkdir(parents=True,exist_ok=True)
        count=expanded=skipped=0
        if name in ('clang','rust'):
            with tarfile.open(archive_file,'r:xz') as archive:
                for entry in archive:
                    count+=1
                    if count>100000:raise ValueError('Archive entry limit')
                    target=checked_path(root,entry.name)
                    if entry.isdir():target.mkdir(parents=True,exist_ok=True);continue
                    if not entry.isfile():skipped+=1;continue
                    if entry.name.lower().endswith('.pdb'):skipped+=1;continue
                    expanded+=entry.size
                    if entry.size>1024*1024*1024 or expanded>4*1024*1024*1024:
                        raise ValueError('Expanded compiler size limit')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with archive.extractfile(entry) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
            if name=='clang':(root/'cr_build_revision').write_text(REV+'\n')
        else:
            with zipfile.ZipFile(archive_file) as archive:
                for entry in archive.infolist():
                    count+=1
                    if count>100000:raise ValueError('SDK entry limit')
                    target=checked_path(root,entry.filename)
                    # Official SDK payload is in c/. Package metadata is not needed.
                    if (name not in ('gn-source-fixture','ninja') and not entry.filename.startswith('c/')) or entry.is_dir():skipped+=1;continue
                    # A local x64 client does not need ARM/x86 binaries or libraries.
                    parts=PurePosixPath(entry.filename).parts
                    if name=='sdk-x64' and any(p in ('arm','arm64','x86') for p in parts):skipped+=1;continue
                    expanded+=entry.file_size
                    if entry.file_size>256*1024*1024 or expanded>3*1024*1024*1024:
                        raise ValueError('Expanded SDK size limit')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with archive.open(entry) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
        event.update(result='success',archive_entries=count,expanded_bytes=expanded,skipped_entries=skipped,
                     elapsed_ms=round((time.monotonic()-begin)*1000))
        marker.write_text(json.dumps(event,indent=2))
    except Exception as error:
        event.update(result='failure',http_status=getattr(error,'code',event.get('http_status')),
            exception={'type':type(error).__name__,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)]},elapsed_ms=round((time.monotonic()-begin)*1000))
    return event

if __name__=='__main__':
    try:
        # The Microsoft payloads compose the same SDK root, so stage sequentially.
        with ThreadPoolExecutor(max_workers=1) as pool:
            for event in pool.map(stage,JOBS):
                REPORT['events'].append(event)
                print(json.dumps(event),flush=True)
        REPORT['result']='staged' if all(e['result']=='success' for e in REPORT['events']) else 'failure'
    finally:
        REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
        for path in [HERE/'client-toolchain-preparation.json',HERE/('client-toolchain-'+REPORT['attempt']+'.json')]:
            path.write_text(json.dumps(REPORT,indent=2))
        print(json.dumps({'result':REPORT.get('result'),'elapsed_ms':REPORT['elapsed_ms']}),flush=True)
