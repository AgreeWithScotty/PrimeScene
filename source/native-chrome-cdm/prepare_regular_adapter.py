"""Prepare an exact-version owned adapter and an isolated portable runtime."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import traceback
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
BRIDGE = HERE/'chrome-bridge'
WORK = BRIDGE/'regular-runtime'
SOURCE = BRIDGE/'client-source'
REPORT = {'operation': 'prepare_regular_portable_adapter', 'attempt': uuid.uuid4().hex[:16],
          'installed_files_changed': False, 'events': [],
          'user_approved_pin': '158.0.8127.89'}
START = time.monotonic()
try:
    meta = json.loads((WORK/'prepared.json').read_text())
    version = meta['version']
    assert version == REPORT['user_approved_pin']
    REPORT['version'] = version
    backup = WORK/'source-before'
    backup.mkdir(exist_ok=True)
    for path in SOURCE.iterdir():
        if path.suffix in ('.cc', '.h') and not (backup/path.name).exists():
            shutil.copy2(path, backup/path.name)
    paths = ['chrome/VERSION', 'content/common/child_process.mojom', 'media/mojo/mojom/cdm_service.mojom',
             'media/mojo/mojom/content_decryption_module.mojom', 'media/mojo/mojom/frame_interface_factory.mojom',
             'media/mojo/mojom/output_protection.mojom', 'media/mojo/mojom/decryptor.mojom']
    for name in paths:
        begun = time.monotonic()
        url = 'https://chromium.googlesource.com/chromium/src/+/refs/tags/'+version+'/'+name+'?format=TEXT'
        with urllib.request.urlopen(url, timeout=25) as response:
            data = base64.b64decode(response.read(2*1024*1024), validate=True)
            target = WORK/('fixture158__'+name.replace('/', '__'))
            target.write_bytes(data)
            REPORT['events'].append({'stage': 'exact_runtime_source', 'url': url,
                'status': response.status, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'elapsed_ms': round((time.monotonic()-begun)*1000), 'result': 'success'})
    (WORK/'client-source').mkdir(exist_ok=True)
    derive = (HERE/'derive_chrome_bridge_protocol.py').read_text()
    derive = derive.replace("HERE=Path(__file__).resolve().parent/'chrome-bridge'", 'HERE=Path('+repr(str(WORK))+')')
    derive = derive.replace("SRC=HERE/'client-sdk/src'", 'SRC=Path('+repr(str(BRIDGE/'client-sdk/src'))+')')
    derive = derive.replace('158.0.8127.37', version)
    script = WORK/'derive_exact_protocol.py'
    script.write_text(derive)
    subprocess.run(['python', str(script)], check=True, capture_output=True)
    shutil.copy2(WORK/'client-source/protocol_fixture.h', SOURCE/'protocol_fixture.h')
    REPORT['derived_ping_ordinal'] = json.loads((WORK/'exact-protocol-derivation.json').read_text())['interfaces']['ChildProcessHost']['Ping']
    v = version.split('.')
    header = ('// Pinned genuine regular Chrome runtime; never use a system browser.\n#pragma once\n'
              '#include <string>\n'
              'constexpr char kChromeRuntimeVersion[] = "'+version+'";\n'
              'constexpr DWORD kChromeVersionMS = '+str((int(v[0])<<16)|int(v[1]))+'u;\n'
              'constexpr DWORD kChromeVersionLS = '+str((int(v[2])<<16)|int(v[3]))+'u;\n'
              'inline std::wstring ChromeRuntimeExecutable(const std::wstring& root) { return root+L"\\\\runtime\\\\chrome.exe"; }\n'
              'inline std::wstring ChromeRuntimeCdm(const std::wstring& root) { return root+L"\\\\runtime\\\\'+version+'\\\\WidevineCdm\\\\_platform_specific\\\\win_x64\\\\widevinecdm.dll"; }\n')
    (SOURCE/'runtime_fixture.h').write_text(header)
    original = WORK/'extracted/Chrome-bin'
    runtime = WORK/'test-bin/runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    wanted = ['chrome.exe', version+'/'+version+'.manifest'] + [version+'/'+name for name in
        ('chrome.dll', 'chrome_elf.dll', 'icudtl.dat', 'v8_context_snapshot.bin',
         'chrome.exe.sig', 'chrome.dll.sig', 'resources.pak', 'chrome_100_percent.pak',
         'chrome_200_percent.pak', 'locales/en-US.pak')]
    wanted += [p.relative_to(original).as_posix() for p in (original/version/'WidevineCdm').rglob('*') if p.is_file()]
    REPORT['runtime_files'] = []
    for relative in wanted:
        source = original/relative
        destination = runtime/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        REPORT['runtime_files'].append({'path': relative, 'bytes': source.stat().st_size,
                                       'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    REPORT['runtime_bytes'] = sum(f['bytes'] for f in REPORT['runtime_files'])
    REPORT['result'] = 'prepared_for_actual_portable_hd_test'
except Exception as error:
    REPORT.update(result='failure', exception={'type': type(error).__name__, 'status': getattr(error, 'code', None),
        'frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                   for f in traceback.extract_tb(error.__traceback__)]})
finally:
    REPORT['elapsed_ms'] = round((time.monotonic()-START)*1000)
    (WORK/'adapter-preparation.json').write_text(json.dumps(REPORT, indent=2))
    print(json.dumps(REPORT))
