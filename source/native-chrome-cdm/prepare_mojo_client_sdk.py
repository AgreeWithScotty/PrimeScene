"""Prepare a scoped IPC/sandbox client SDK, not a Chrome or Kodi build.

Source is the published tag matching the actual installed Chrome ABI fixture.
GN is fetched from the maintainer's latest published binary package. All files
stay in this workspace; source archives are extracted with traversal/link guards.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
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
SRC=ROOT/'src'
TAG='158.0.8127.37'
START=time.monotonic()
REPORT={'operation':'prepare_scoped_mojo_client_sdk','attempt':uuid.uuid4().hex[:16],
    'source_tag':TAG,'selection_reason':'exact installed Chrome ABI compatibility fixture, not a current-release claim',
    'chrome_build_selected':False,'kodi_build_selected':False,'system_installation':False,'events':[]}
DIRECTORIES=['base','mojo','sandbox','build','build_overrides','third_party/ipcz',
    'third_party/abseil-cpp','testing','tools/clang','tools/rust','tools/grit','buildtools',
    'crypto','third_party/compiler-rt','third_party/boringssl','third_party/protobuf',
    'third_party/zlib','third_party/snappy','tools/metrics']
EXTERNAL={
    'third_party/compiler-rt/src':('https://chromium.googlesource.com/external/github.com/llvm/llvm-project/compiler-rt','b8403c8cb61be59a519c3cf63217190c414bdf07'),
    'third_party/perfetto':('https://chromium.googlesource.com/external/github.com/google/perfetto','214c983f4c72c442d71415d9c81a7e1ed11566a7'),
    'third_party/snappy/src':('https://chromium.googlesource.com/external/github.com/google/snappy','32ded457c0b1fe78ceb8397632c416568d6714a0'),
    'third_party/icu':('https://chromium.googlesource.com/chromium/deps/icu','c670d124c9b6aa92c0b492cf543808300e0e6103'),
}


def checked_path(parent,name):
    rel=PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe SDK archive path')
    target=parent.joinpath(*rel.parts).resolve()
    if os.path.commonpath([str(parent.resolve()),str(target)])!=str(parent.resolve()):
        raise ValueError('SDK archive path escaped destination')
    return target


def fetch(item):
    name,url=item;started=time.monotonic()
    try:
        with urllib.request.urlopen(url,timeout=45) as reply:
            data=reply.read(64*1024*1024+1)
            if len(data)>64*1024*1024:raise ValueError('SDK archive exceeds compressed size limit')
            event={'stage':'fetch_scoped_client_source','directory':name,'status':reply.status,
                'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                'elapsed_ms':round((time.monotonic()-started)*1000),'result':'success'}
            return name,data,event
    except Exception as error:
        return name,None,{'stage':'fetch_scoped_client_source','directory':name,'status':getattr(error,'code',None),
            'exception_type':type(error).__name__,'elapsed_ms':round((time.monotonic()-started)*1000),'result':'failure'}


try:
    SRC.mkdir(parents=True,exist_ok=True)
    jobs=[]
    for name in DIRECTORIES:
        marker=ROOT/(name.replace('/','__')+'.json')
        if marker.is_file():
            REPORT['events'].append(dict(json.loads(marker.read_text()),reused_verified_extract=True));continue
        jobs.append((name,'https://chromium.googlesource.com/chromium/src/+archive/refs/tags/'+TAG+'/'+name+'.tar.gz'))
    for name,(repository,revision) in EXTERNAL.items():
        marker=ROOT/(name.replace('/','__')+'.json')
        if marker.is_file():
            REPORT['events'].append(dict(json.loads(marker.read_text()),reused_verified_extract=True));continue
        jobs.append((name,repository+'/+archive/'+revision+'.tar.gz'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        for name,data,event in pool.map(fetch,jobs):
            REPORT['events'].append(event)
            if name in EXTERNAL:event.update(repository=EXTERNAL[name][0],source_revision=EXTERNAL[name][1])
            if data is None:continue
            destination=SRC.joinpath(*PurePosixPath(name).parts);destination.mkdir(parents=True,exist_ok=True)
            count=skipped=expanded=0
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
                for member in archive:
                    count+=1
                    if count>100000:raise ValueError('SDK archive entry count limit')
                    target=checked_path(destination,member.name)
                    if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
                    if not member.isfile():skipped+=1;continue
                    expanded+=member.size
                    if member.size>32*1024*1024 or expanded>512*1024*1024:raise ValueError('Expanded SDK size limit')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with archive.extractfile(member) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
            event.update(extracted_files=count,links_or_special_entries_skipped=skipped,expanded_bytes=expanded)
            (ROOT/(name.replace('/','__')+'.json')).write_text(json.dumps(event,indent=2))
            print(json.dumps(event),flush=True)
    gn_dir=ROOT/'gn';gn_dir.mkdir(exist_ok=True)
    package=ROOT/'gn-latest.zip'
    if not (gn_dir/'gn.exe').exists():
        url='https://chrome-infra-packages.appspot.com/dl/gn/gn/windows-amd64/+/latest'
        with urllib.request.urlopen(url,timeout=30) as response:
            data=response.read(32*1024*1024+1)
            if len(data)>32*1024*1024:raise ValueError('GN package size limit')
        package.write_bytes(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                if entry.is_dir():continue
                target=checked_path(gn_dir,entry.filename);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(entry))
        REPORT['gn_package']={'source':url,'selection':'maintainer_latest_published',
            'archive_sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    REPORT['gn_binary_sha256']=hashlib.sha256((gn_dir/'gn.exe').read_bytes()).hexdigest()
    REPORT['result']='prepared' if all(e['result']=='success' for e in REPORT['events']) else 'partial_fetch_failure'
except Exception as error:
    REPORT.update(result='failure',exception={'type':type(error).__name__,
        'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)]})
finally:
    REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
    (HERE/('client-sdk-preparation-'+REPORT['attempt']+'.json')).write_text(json.dumps(REPORT,indent=2))
    (HERE/'client-sdk-preparation.json').write_text(json.dumps(REPORT,indent=2))
    print(json.dumps({'result':REPORT.get('result'),'events':len(REPORT['events']),
        'gn_binary_sha256':REPORT.get('gn_binary_sha256'),'exception':REPORT.get('exception'),
        'elapsed_ms':REPORT['elapsed_ms']}),flush=True)
