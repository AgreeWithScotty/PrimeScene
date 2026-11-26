"""Compile only the real IPC client, hidden and below normal scheduling priority."""
import hashlib
import argparse
import json
from pathlib import Path
import platform
import re
import subprocess
import time
import traceback
import uuid

BASE=Path(__file__).resolve().parent
HERE=BASE/'chrome-bridge'
ROOT=HERE/'client-sdk'
SRC=HERE.parents[3]/'.prime-mojo/src'
START=time.monotonic()
REPORT={'operation':'build_standalone_chrome_ipc_transport_client','attempt':uuid.uuid4().hex[:16],
        'provider_requests':0,'chrome_build':False,'kodi_build':False,'installed_files_changed':False,
        'workers':2,'priority':'below_normal','environment':platform.platform(),'events':[]}
child=None

def save():
    REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
    data=json.dumps(REPORT,indent=2)
    (HERE/'client-compile-result.json').write_text(data)
    (HERE/('client-compile-'+REPORT['attempt']+'.json')).write_text(data)

try:
    parser=argparse.ArgumentParser()
    parser.add_argument('--incremental',action='store_true',help='Existing graph; refresh only owned client source')
    parser.add_argument('--owned-source-dir',type=Path,help='Isolated owned adapter sources for a concrete runtime test')
    parser.add_argument('--target',choices=['transport_probe','prime_cdm_proxy','proxy_abi_probe'],default='transport_probe')
    options=parser.parse_args()
    REPORT['target']=options.target
    helpers=() if options.incremental else ('scope_mojo_client_build.py','configure_mojo_client.py')
    if options.incremental:
        changed=[]
        owned_source=options.owned_source_dir or HERE/'client-source'
        owned_source=owned_source.resolve(strict=True)
        owned_source.relative_to(BASE.parent.resolve())
        REPORT['owned_source_directory']=str(owned_source)
        for source in owned_source.iterdir():
            if source.suffix not in ('.cc','.h'):continue
            destination=SRC/'client'/source.name
            content=source.read_bytes()
            if not destination.exists() or destination.read_bytes()!=content:
                destination.write_bytes(content);changed.append(source.name)
        REPORT['events'].append({'stage':'refresh_owned_client_source','result':'success','changed':changed})
    for helper in helpers:
        result=subprocess.run(['python',str(BASE/helper)],capture_output=True,text=True,timeout=60,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        REPORT['events'].append({'stage':helper,'exit_code':result.returncode})
        if result.returncode:raise RuntimeError('Client build setup failed')
    configuration=json.loads((HERE/'client-sdk-configuration.json').read_text())
    if configuration['result']!='generated':
        REPORT['configuration_failure']=configuration
        diagnostic=HERE/'client-sdk-gn-diagnostic.txt'
        if diagnostic.is_file():
            REPORT['configuration_diagnostic']=diagnostic.read_text(errors='replace')[-6000:]
            print(REPORT['configuration_diagnostic'],flush=True)
        raise RuntimeError('Client graph is not generated')
    source_inputs=json.loads((HERE/'client-source-input-preparation.json').read_text())
    if source_inputs.get('remaining_missing_files'):raise RuntimeError('Client source inputs remain missing')
    for key,path in [('gn',ROOT/'gn-source-fixture/gn.exe'),('ninja',ROOT/'ninja/ninja.exe')]:
        result=subprocess.run([str(path),'--version'],capture_output=True,text=True,timeout=5,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        REPORT[key]={'version':result.stdout.strip(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    log=HERE/('client-compile-'+REPORT['attempt']+'.log')
    REPORT['log_file']=log.name
    with log.open('wb') as output:
        child=subprocess.Popen([str(ROOT/'ninja/ninja.exe'),'-C',str(SRC/'out/client'),'-j','2',options.target],
            cwd=SRC,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        REPORT.update(result='running',owned_ninja_pid=child.pid)
        save();print(json.dumps({'stage':'compile_started','attempt':REPORT['attempt'],'pid':child.pid,'workers':2}),flush=True)
        next_update=time.monotonic()+20
        while child.poll() is None:
            if time.monotonic()-START>1200:raise TimeoutError('Bounded client compilation exceeded20minutes')
            if time.monotonic()>next_update:
                print(json.dumps({'stage':'compiling','elapsed_ms':round((time.monotonic()-START)*1000),
                    'log_bytes':log.stat().st_size}),flush=True)
                next_update=time.monotonic()+20
            time.sleep(.25)
    REPORT.update(result='compiled' if child.returncode==0 else 'compile_failed',exit_code=child.returncode)
    REPORT['log_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
    if child.returncode:
        diagnostic=log.read_text(encoding='utf-8',errors='replace')
        REPORT['diagnostic_tail']=diagnostic[-10000:]
        clean=re.sub(r'\x1b\[[0-9;]*m','',diagnostic)
        errors=[line for line in clean.splitlines() if len(line)<1200 and
            re.search(r'(^FAILED|error:|fatal error|GetFullPathName|^ERROR|Traceback)',line,re.I)]
        REPORT['error_lines']=errors
        print('\n'.join(errors[:18]) if errors else clean[-3500:],flush=True)
    else:
        artifact=SRC/'out/client'/('widevinecdm.dll' if options.target=='prime_cdm_proxy' else options.target+'.exe')
        REPORT['artifact']={'file':artifact.name,'bytes':artifact.stat().st_size,
            'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}
except Exception as error:
    REPORT.update(result='failure',exception={'type':type(error).__name__,
        'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
            for f in traceback.extract_tb(error.__traceback__)]})
finally:
    if child and child.poll() is None:
        stopped=subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,
            timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        REPORT['owned_compiler_cleanup']={'exit_code':stopped.returncode}
    save()
    print(json.dumps({'result':REPORT.get('result'),'exit_code':REPORT.get('exit_code'),
        'exception':REPORT.get('exception'),'artifact':REPORT.get('artifact'),
        'elapsed_ms':REPORT['elapsed_ms']}),flush=True)
