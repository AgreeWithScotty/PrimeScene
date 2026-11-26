"""Resolve missing public build metadata for the isolated IPC client, boundedly."""
import base64
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import traceback
import urllib.request
import urllib.parse
import urllib.error
import uuid

BASE=Path(__file__).resolve().parent
HERE=BASE/'chrome-bridge'
SRC=HERE/'client-sdk/src'
START=time.monotonic()
REPORT={'operation':'resolve_standalone_client_graph','attempt':uuid.uuid4().hex[:16],
        'provider_requests':0,'chrome_or_kodi_build':False,'events':[]}
TAG='158.0.8127.37'
EXTERNAL={
 'third_party/boringssl/src':('https://boringssl.googlesource.com/boringssl.git','ea9f3a34c3977144c45d272be81842fd891a0bdb'),
 'third_party/compiler-rt/src':('https://chromium.googlesource.com/external/github.com/llvm/llvm-project/compiler-rt','b8403c8cb61be59a519c3cf63217190c414bdf07'),
 'third_party/perfetto':('https://chromium.googlesource.com/external/github.com/google/perfetto','214c983f4c72c442d71415d9c81a7e1ed11566a7'),
 'third_party/snappy/src':('https://chromium.googlesource.com/external/github.com/google/snappy','32ded457c0b1fe78ceb8397632c416568d6714a0'),
 'third_party/icu':('https://chromium.googlesource.com/chromium/deps/icu','c670d124c9b6aa92c0b492cf543808300e0e6103'),
}

def add_exact_external_roots():
    """Read literal dependency data without executing DEPS or its functions."""
    module=ast.parse((HERE/'fixture158__DEPS').read_text())
    values={}
    dependency_nodes=[]
    for assignment in module.body:
        if not isinstance(assignment,ast.Assign):continue
        names=[target.id for target in assignment.targets if isinstance(target,ast.Name)]
        if 'vars' in names:
            values={ast.literal_eval(key):value for key,value in zip(assignment.value.keys,assignment.value.values)}
        if 'deps' in names and isinstance(assignment.value,ast.Dict):dependency_nodes=[*zip(assignment.value.keys,assignment.value.values)]
    def literal(node,depth=0):
        if depth>20:raise ValueError('Dependency recursion limit')
        if isinstance(node,ast.Constant):return node.value
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):return literal(node.left,depth+1)+literal(node.right,depth+1)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and len(node.args)==1:
            if node.func.id=='Str':return literal(node.args[0],depth+1)
            if node.func.id=='Var':return literal(values[literal(node.args[0],depth+1)],depth+1)
        if isinstance(node,ast.Dict):
            return literal(next(value for key,value in zip(node.keys,node.values) if ast.literal_eval(key)=='url'),depth+1)
        raise ValueError('Not literal public dependency data')
    for key,node in dependency_nodes:
        try:
            name=ast.literal_eval(key);url=literal(node)
            if not name.startswith('src/') or not isinstance(url,str) or '@' not in url:continue
            repository,revision=url.rsplit('@',1)
            if not re.fullmatch(r'[0-9a-f]{40}',revision):continue
            if not (urllib.parse.urlparse(repository).hostname or '').endswith('.googlesource.com'):continue
            EXTERNAL[name[4:]]=(repository,revision)
        except (ValueError,KeyError,StopIteration,TypeError):continue

add_exact_external_roots()

def fetch(relative):
    begin=time.monotonic();event={'stage':'missing_build_metadata','path':relative,
        'request_id':uuid.uuid4().hex[:12]}
    try:
        target=SRC.joinpath(*relative.split('/')).resolve()
        target.relative_to(SRC.resolve())
        if relative!='chrome/VERSION' and not relative.startswith(('base/','build/','buildtools/','mojo/','ipc/','sandbox/','crypto/','third_party/','tools/','testing/','services/tracing/')):
            raise ValueError('Dependency outside standalone client source allowlist')
        if relative!='chrome/VERSION' and not target.suffix in ('.gn','.gni','.py','.pydeps','.json','.json5','.txt','.tmpl'):
            raise ValueError('Missing file is not build metadata')
        if target.exists():raise ValueError('Refusing to overwrite an existing build input')
        repository='https://chromium.googlesource.com/chromium/src';revision='refs/tags/'+TAG;path=relative
        for prefix,(repo,commit) in sorted(EXTERNAL.items(),key=lambda pair:-len(pair[0])):
            if relative.startswith(prefix+'/'):
                repository=repo;revision=commit;path=relative[len(prefix)+1:];break
        url=repository+'/+/'+revision+'/'+path+'?format=TEXT'
        event['url']=url
        try:response=urllib.request.urlopen(url,timeout=25)
        except urllib.error.HTTPError as error:
            if error.code not in (429,502,503,504):raise
            event['initial_http_status']=error.code
            time.sleep(.3)
            response=urllib.request.urlopen(url+'&retry='+event['request_id'],timeout=25)
        with response:
            raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('Metadata size limit')
            data=base64.b64decode(raw,validate=True)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            event.update(result='success',http_status=response.status,bytes=len(data),
                         sha256=hashlib.sha256(data).hexdigest(),final_url=response.geturl())
    except Exception as error:
        event.update(result='failure',http_status=getattr(error,'code',None),
            exception={'type':type(error).__name__,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)]})
    event['elapsed_ms']=round((time.monotonic()-begin)*1000)
    return event

if __name__=='__main__':
    try:
        # Crate BUILD metadata is independent: fetch the known literal dependency
        # closure in batches, instead of rerunning GN once per crate.
        for depth in range(4):
            missing=set()
            for build in SRC.rglob('BUILD.gn'):
                if '/out/' in build.as_posix():continue
                for label in re.findall(r'"//(third_party/rust/[^"\s]+)"',build.read_text(encoding='utf-8')):
                    directory=label.split(':',1)[0].rstrip('/')
                    if not directory or '$' in directory or '*' in directory or Path(directory).suffix or '/vendor/' in directory:continue
                    relative=directory+'/BUILD.gn'
                    if not (SRC/relative).exists():missing.add(relative)
            if not missing:break
            jobs=sorted(missing)[:64]
            with ThreadPoolExecutor(max_workers=3) as pool:events=list(pool.map(fetch,jobs))
            REPORT['events'].extend(events)
            print(json.dumps({'stage':'rust_metadata_batch','depth':depth,'requested':len(jobs),
                'succeeded':sum(event['result']=='success' for event in events),
                'failure_count':sum(event['result']!='success' for event in events)}),flush=True)
            if any(event['result']!='success' for event in events):break
        for iteration in range(12):
            scoped=subprocess.run(['python',str(BASE/'scope_mojo_client_build.py')],capture_output=True,text=True,timeout=30,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            if scoped.returncode:raise RuntimeError('Build scoping failed')
            result=subprocess.run(['python',str(BASE/'configure_mojo_client.py')],capture_output=True,text=True,timeout=45,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            current=json.loads((HERE/'client-sdk-configuration.json').read_text())
            if current.get('result')=='generated':REPORT['result']='generated';break
            if current.get('result')=='failure':
                REPORT.update(result='client_configuration_failed',configuration_exception=current.get('exception'))
                print(json.dumps({'configuration_failure':current.get('exception')}),flush=True);break
            diagnostic=(HERE/'client-sdk-gn-diagnostic.txt').read_text()
            match=re.search(r'Unable to load "([^"]+)"',diagnostic)
            if not match:match=re.search(r'I resolved this to "([^"]+)"',diagnostic)
            if not match:
                REPORT.update(result='requires_build_configuration_change',diagnostic=diagnostic[-4000:]);break
            file=Path(match.group(1));relative=file.resolve().relative_to(SRC.resolve()).as_posix()
            event=fetch(relative);REPORT['events'].append(event);print(json.dumps(event),flush=True)
            if event['result']!='success':REPORT['result']='metadata_fetch_failed';break
        else:REPORT['result']='bounded_iteration_limit'
    except Exception as error:
        REPORT.update(result='failure',exception={'type':type(error).__name__,
            'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)]})
    finally:
        REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
        for file in [HERE/'client-graph-resolution.json',HERE/('client-graph-resolution-'+REPORT['attempt']+'.json')]:
            file.write_text(json.dumps(REPORT,indent=2))
        print(json.dumps({'result':REPORT.get('result'),'events':len(REPORT['events']),
            'diagnostic':REPORT.get('diagnostic'),'exception':REPORT.get('exception'),
            'elapsed_ms':REPORT['elapsed_ms']}),flush=True)
