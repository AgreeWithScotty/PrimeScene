"""Fetch only missing source groups in the actual generated client dependency closure."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import argparse
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import tarfile
import time
import traceback
import urllib.request
import uuid

import resolve_mojo_client_graph as graph

HERE=graph.HERE
ROOT=HERE/'client-sdk'
SRC=graph.SRC
START=time.monotonic()
REPORT={'operation':'prepare_actual_transport_client_source_inputs','attempt':uuid.uuid4().hex[:16],
        'provider_requests':0,'chrome_or_kodi_build':False,'events':[]}

def group_for(path):
    vendor='third_party/rust/chromium_crates_io/vendor/'
    if path.startswith(vendor):return vendor+path[len(vendor):].split('/')[0]
    bssl='third_party/boringssl/src/'
    if path.startswith(bssl):
        sub=path[len(bssl):].split('/')
        return bssl+('/'.join(sub[:2]) if sub[0]=='third_party' else sub[0])
    for prefix in sorted(graph.EXTERNAL,key=len,reverse=True):
        if path.startswith(prefix+'/'):return prefix
    if path.startswith('third_party/'):return '/'.join(path.split('/')[:2])
    return path.rsplit('/',1)[0]

def stage(group):
    begin=time.monotonic();event={'stage':'source_group','path':group,'request_id':uuid.uuid4().hex[:12]}
    try:
        destination=(SRC/group).resolve();destination.relative_to(SRC.resolve())
        if not group.startswith(('third_party/','tools/','base/','build/','media/cdm/api')):
            raise ValueError('Group outside standalone client source scope')
        repository='https://chromium.googlesource.com/chromium/src'
        revision='refs/tags/'+graph.TAG;subpath=group
        for prefix,(repo,commit) in sorted(graph.EXTERNAL.items(),key=lambda pair:-len(pair[0])):
            if group==prefix or group.startswith(prefix+'/'):
                repository=repo;revision=commit;subpath=group[len(prefix):].strip('/');break
        url=repository+'/+archive/'+revision+('/'+subpath if subpath else '')+'.tar.gz'
        event.update(url=url,source_revision=revision)
        cache=ROOT/'source-archives';cache.mkdir(exist_ok=True)
        archive_file=cache/(hashlib.sha256(group.encode()).hexdigest()[:20]+'.tar.gz')
        if not archive_file.is_file():
            digest=hashlib.sha256();count=0;partial=archive_file.with_suffix('.part')
            try:response=urllib.request.urlopen(url,timeout=45)
            except graph.urllib.error.HTTPError as error:
                if error.code not in (429,502,503,504):raise
                event['initial_http_status']=error.code
                response=urllib.request.urlopen(url+'?retry='+event['request_id'],timeout=45)
            with response,partial.open('wb') as output:
                event['http_status']=response.status
                while True:
                    data=response.read(1024*1024)
                    if not data:break
                    count+=len(data)
                    if count>128*1024*1024:raise ValueError('Source group compressed size limit')
                    digest.update(data);output.write(data)
            partial.replace(archive_file)
            event.update(archive_bytes=count,sha256=digest.hexdigest())
        else:
            with archive_file.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
            event.update(archive_bytes=archive_file.stat().st_size,sha256=digest,from_local_archive=True)
        count=expanded=skipped=0;destination.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive_file,'r:gz') as archive:
            for entry in archive:
                count+=1
                relative=PurePosixPath(entry.name)
                if relative.is_absolute() or '..' in relative.parts or '\\' in entry.name or ':' in entry.name:
                    raise ValueError('Unsafe source archive path')
                target=destination.joinpath(*relative.parts).resolve();target.relative_to(destination)
                if entry.isdir():target.mkdir(parents=True,exist_ok=True);continue
                if not entry.isfile():skipped+=1;continue
                expanded+=entry.size
                if count>100000 or entry.size>64*1024*1024 or expanded>512*1024*1024:
                    raise ValueError('Source group expansion limit')
                # Build metadata may already have the scoped client derivation.
                if target.is_file() and (target.name=='BUILD.gn' or target.suffix=='.gni'):
                    continue
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(entry) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
        event.update(result='success',entries=count,expanded_bytes=expanded,special_entries_skipped=skipped)
    except Exception as error:
        event.update(result='failure',http_status=getattr(error,'code',event.get('http_status')),
            exception={'type':type(error).__name__,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)]})
    event['elapsed_ms']=round((time.monotonic()-begin)*1000)
    return event

if __name__=='__main__':
    try:
        parser=argparse.ArgumentParser()
        parser.add_argument('--extra-group',action='append',default=[])
        options=parser.parse_args()
        inputs=json.loads((HERE/'client-build-inputs.json').read_text())
        missing=[path for path in inputs['missing_files'] if not (SRC/path).exists()]
        groups=set(options.extra_group)
        for path in missing:
            if path=='chrome/VERSION':
                event=graph.fetch(path);REPORT['events'].append(event)
            else:groups.add(group_for(path))
        # Coalesce parents before parallel extraction so no two jobs write the
        # same source subtree (e.g. Rust support plus individual vendor crates).
        groups={group for group in groups if not any(group.startswith(parent+'/') for parent in groups if parent!=group)}
        REPORT['source_groups']=sorted(groups)
        print(json.dumps({'stage':'selected_input_groups','missing_files':len(missing),'groups':len(groups)}),flush=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            for event in pool.map(stage,sorted(groups)):
                REPORT['events'].append(event)
                print(json.dumps({key:event.get(key) for key in ('path','result','archive_bytes','expanded_bytes','http_status','exception')}),flush=True)
        remaining=[path for path in missing if not (SRC/path).exists()]
        REPORT.update(result='prepared' if not remaining else 'missing_inputs_remain',remaining_missing_files=remaining)
    finally:
        REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
        for path in [HERE/'client-source-input-preparation.json',HERE/('client-source-inputs-'+REPORT['attempt']+'.json')]:
            path.write_text(json.dumps(REPORT,indent=2))
        print(json.dumps({'result':REPORT.get('result'),'remaining_missing':len(REPORT.get('remaining_missing_files',[])),
            'elapsed_ms':REPORT['elapsed_ms']}),flush=True)
