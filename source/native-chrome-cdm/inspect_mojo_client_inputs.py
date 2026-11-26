"""Read the generated real client build graph without compiling unrelated targets."""
import json
from pathlib import Path
import subprocess
import time
import uuid

HERE=Path(__file__).resolve().parent/'chrome-bridge'
ROOT=HERE/'client-sdk'
SRC=HERE.parents[3]/'.prime-mojo/src'
START=time.monotonic()
REPORT={'operation':'inspect_actual_ipc_client_inputs','attempt':uuid.uuid4().hex[:16]}
try:
    result=subprocess.run([str(ROOT/'gn-source-fixture/gn.exe'),'desc','out/client','*','--format=json','--all-toolchains'],
        cwd=SRC,capture_output=True,text=True,timeout=45,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        REPORT.update(result='gn_desc_failed',exit_code=result.returncode,diagnostic=(result.stdout+result.stderr)[-3000:])
    else:
        targets=json.loads(result.stdout)
        (ROOT/'all-targets.json').write_text(result.stdout,encoding='utf-8')
        root='//:transport_probe'
        selected=set();queue=[root];absent=[]
        while queue:
            name=queue.pop()
            if name in selected:continue
            if name not in targets:absent.append(name);continue
            selected.add(name);node=targets[name]
            for field in ('deps','public_deps','data_deps'):
                queue.extend(node.get(field,[]))
        files=set()
        for name in selected:
            node=targets[name]
            for field in ('sources','public','inputs','data','script'):
                value=node.get(field,[])
                for path in ([value] if isinstance(value,str) else value):
                    if path.startswith('//') and not path.startswith('//out/') and '$' not in path:
                        files.add(path[2:])
        missing=sorted(path for path in files if not (SRC/path).exists())
        (ROOT/'selected-client-targets.json').write_text(json.dumps({name:targets[name] for name in sorted(selected)},indent=2))
        REPORT.update(result='inspected',all_targets=len(targets),selected_targets=len(selected),
            selected_files=len(files),missing_files=missing,missing_target_descriptions=sorted(set(absent)),
            root_fields=sorted(targets.get(root,{})))
finally:
    REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
    (HERE/'client-build-inputs.json').write_text(json.dumps(REPORT,indent=2))
    print(json.dumps({key:REPORT.get(key) for key in ('result','all_targets','selected_targets','selected_files',
        'missing_target_descriptions','root_fields','diagnostic')}))
    print(json.dumps({'missing_files':len(REPORT.get('missing_files',[])),
        'first_missing':REPORT.get('missing_files',[])[:12],'elapsed_ms':REPORT['elapsed_ms']}))
