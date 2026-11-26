"""Generate only a stock-Chrome-compatible IPC client's dependency graph.

This does not compile a browser, launch a CDM, or test an Amazon license.
The source tag is an installed-binary ABI fixture, not a current-release claim.
"""
import json
import hashlib
import ast
import os
from pathlib import Path
import subprocess
import time
import traceback
import uuid

HERE=Path(__file__).resolve().parent/'chrome-bridge'
ROOT=HERE/'client-sdk'
SRC=HERE.parents[3]/'.prime-mojo/src'
START=time.monotonic()
REPORT={'operation':'generate_scoped_mojo_client_build_graph','attempt':uuid.uuid4().hex[:16],
        'source_tag':'158.0.8127.37','chrome_build':False,'kodi_build':False,
        'provider_requests':0,'compilation_requested':False,'events':[]}

def patch_rust_depfile_parser():
    path=SRC/'build/rust/gni_impl/rustc_wrapper.py'
    pristine=path.with_suffix('.py.upstream')
    if not pristine.exists():pristine.write_bytes(path.read_bytes())
    original=pristine.read_text()
    helper=r'''
def split_makefile_paths(value):
    """Decode Make-escaped whitespace without discarding Windows separators."""
    words=[];word=[];position=0
    while position<len(value):
        char=value[position]
        if char=='\\' and position+1<len(value) and value[position+1] in ' \t#\\':
            word.append(value[position+1]);position+=2;continue
        if char.isspace():
            if word:words.append(''.join(word));word=[]
        else:word.append(char)
        position+=1
    if word:words.append(''.join(word))
    return words

def escape_makefile_path(path):
    return path.replace('\\','\\\\').replace(' ','\\ ').replace('#','\\#')

'''
    replacements={
        '\ndef normalize_path(':helper+'\ndef normalize_path(',
        'rhs_group.split()':'split_makefile_paths(rhs_group)',
        'for f in files.split()':'for f in split_makefile_paths(files)',
        'lhs_file = normalize_path(lhs_file, abs_build_root)':
            'lhs_file = normalize_path(split_makefile_paths(lhs_file)[0], abs_build_root)',
        'rhs_files = " ".join(rhs_files)':
            'rhs_files = " ".join(escape_makefile_path(f) for f in rhs_files)',
    }
    derived=original
    for before,after in replacements.items():
        if derived.count(before)!=1:raise ValueError('Unexpected Rust build-wrapper source layout')
        derived=derived.replace(before,after)
    path.write_text(derived)
    return {'stage':'rust_make_depfile_paths','result':'patched_build_helper',
        'reason':'actual compiler dependency paths contain this workspace name with spaces',
        'upstream_sha256':hashlib.sha256(original.encode()).hexdigest(),
        'derived_sha256':hashlib.sha256(derived.encode()).hexdigest()}

try:
    REPORT['events'].append(patch_rust_depfile_parser())
    (SRC/'client/transport_probe.cc').write_bytes((HERE/'client-source/transport_probe.cc').read_bytes())
    (SRC/'client/kodi_cdm_proxy.cc').write_bytes((HERE/'client-source/kodi_cdm_proxy.cc').read_bytes())
    (SRC/'client/proxy_abi_probe.cc').write_bytes((HERE/'client-source/proxy_abi_probe.cc').read_bytes())
    for header in (HERE/'client-source').glob('*.h'):
        (SRC/'client'/header.name).write_bytes(header.read_bytes())
    sdk=ROOT/'windows-sdk/c'
    msvc=Path(r'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207')
    includes=[msvc/'include']+[sdk/'Include/10.0.28000.0'/part for part in ('ucrt','shared','um','winrt')]
    libraries=[msvc/'lib/x64',sdk/'um/x64',sdk/'ucrt/x64']
    for path in includes+libraries:
        if not path.is_dir():raise FileNotFoundError(str(path))
    # The official NuGet SDK is staged, not registered with Visual Studio.
    # Supply its actual paths instead of invoking the machine SDK installer setup.
    metadata=SRC/'build/toolchain/win/win_toolchain_data.gni'
    pristine=metadata.with_suffix('.gni.upstream')
    if not pristine.exists():pristine.write_bytes(metadata.read_bytes())
    quote=lambda value:json.dumps(str(value).replace('\\','/')).replace('$','\\$')
    array=lambda values:'[ '+', '.join(quote(value) for value in values)+' ]'
    lines=['# Generated metadata for this x64 IPC client only. Upstream saved adjacent.',
           'win_toolchain_data_x64 = {',
           '  vc_bin_dir = '+quote(msvc/'bin/Hostx64/x64'),
           '  msvc_dir = '+quote(msvc),'  windows_kits_dir = '+quote(sdk),
           '  paths = '+quote(str(sdk/'bin/10.0.28000.0/x64')+';'+os.environ['PATH'])]
    for name,values in [('include_flags_I',['/I'+str(p) for p in includes]),
                        ('include_flags_imsvc',['-imsvc'+str(p) for p in includes]),
                        ('libpath_flags',['-libpath:'+str(p) for p in libraries]),
                        ('libpath_lldlink_flags',['-libpath:'+str(p) for p in libraries])]:
        lines.extend(['  '+name+'_list = '+array(values),
                      '  '+name+' = '+quote(' '.join('"'+value+'"' for value in values))])
    lines.append('}')
    metadata.write_text('\n'.join(lines)+'\n')
    toolchain=['import("//build/toolchain/win/toolchain.gni")']
    for name,template in [('toolchain','msvc_toolchain'),
            ('toolchain_for_rust_host_build_tools','msvc_rust_host_build_tools_toolchain'),
            ('toolchain_with_system_allocator','msvc_system_allocator_toolchain'),
            ('toolchain_host_with_system_allocator','msvc_system_allocator_toolchain')]:
        toolchain.append(template+'("'+name+'") {\n'+'''  environment = "environment.x64"
  cl = rebase_path("$clang_base_path/bin/clang-cl.exe", root_build_dir)
  sys_include_flags = win_toolchain_data_x64.include_flags_imsvc
  sys_lib_flags = win_toolchain_data_x64.libpath_lldlink_flags + " -libpath:" + rebase_path("$clang_base_path/lib/clang/$clang_version/lib/windows", root_build_dir)
  toolchain_args = {
    current_cpu = "x64"
    current_os = "win"
    is_clang = true
  }
}
''')
    (SRC/'client/BUILD.gn').write_text('\n'.join(toolchain))
    REPORT['client_build_metadata']={'upstream_sha256':hashlib.sha256(pristine.read_bytes()).hexdigest(),
        'generated_sha256':hashlib.sha256(metadata.read_bytes()).hexdigest(),
        'installed_msvc_compatibility_fixture':'14.44.35207','staged_sdk':'10.0.28000.2957',
        'source_algorithms_modified':False}
    upstream_config=(SRC/'build/config/BUILDCONFIG.gn').read_text()
    # GN buildconfigs must execute directly (importing clashes with built-ins).
    # Keep upstream intact and derive only the client's standard-library policy.
    (SRC/'client/BUILDCONFIG.gn').write_text('use_libcxx_modules = false\n'+upstream_config)
    (SRC/'.gn').write_text('buildconfig = "//client/BUILDCONFIG.gn"\n')
    (SRC/'BUILD.gn').write_text('''# Standalone IPC transport client only. No browser/player target.
group("default") {
  deps = [ ":transport_probe" ]
}
executable("transport_probe") {
  # use_blink=false omits LaunchOptions' common definitions from //base.
  # Compile that unchanged upstream source for this standalone process client.
  sources = [ "client/transport_probe.cc", "base/process/launch.cc" ]
  deps = [ "//base", "//mojo/core/embedder", "//mojo/public/cpp/system" ]
}
shared_library("prime_cdm_proxy") {
  output_name = "widevinecdm"
  sources = [ "client/kodi_cdm_proxy.cc" ]
  defines = [ "CDM_IMPLEMENTATION" ]
  libs = [ "crypt32.lib", "version.lib", "runtimeobject.lib", "user32.lib", "ole32.lib" ]
}
executable("proxy_abi_probe") {
  sources = [ "client/proxy_abi_probe.cc" ]
  libs = [ "crypt32.lib" ]
}
''')
    # These are gclient checkout facts for this deliberately scoped source tree.
    (SRC/'build/config/gclient_args.gni').write_text('''build_with_chromium = true
checkout_android = false
checkout_android_native_support = false
checkout_ios = false
checkout_linux = false
checkout_mac = false
checkout_win = true
checkout_chromeos = false
checkout_fuchsia = false
checkout_blink = false
generate_location_tags = false
''')
    deps_tree=ast.parse((HERE/'fixture158__DEPS').read_text())
    arguments=next(node.value for node in deps_tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='gclient_gn_args' for target in node.targets))
    variables=next(node.value for node in deps_tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='vars' for target in node.targets))
    defaults={ast.literal_eval(key):value for key,value in zip(variables.keys,variables.values)}
    generated=[]
    for name in ast.literal_eval(arguments):
        if name in ('build_with_chromium','checkout_android','checkout_fuchsia','generate_location_tags'):continue
        if name.startswith('checkout_'):value=False
        elif name.startswith('cros_boards'):value=[]
        else:
            node=defaults[name]
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='Str' and len(node.args)==1:
                node=node.args[0]
            value=ast.literal_eval(node)
        generated.append(name+' = '+json.dumps(value))
    with (SRC/'build/config/gclient_args.gni').open('a') as output:output.write('\n'.join(generated)+'\n')
    out=SRC/'out/client';out.mkdir(parents=True,exist_ok=True)
    environment_values={key:os.environ[key] for key in
        ('SYSTEMROOT','TEMP','TMP','PATHEXT') if key in os.environ}
    environment_values.update(INCLUDE=';'.join(map(str,includes)),LIB=';'.join(map(str,libraries)),
        PATH=str(sdk/'bin/10.0.28000.0/x64')+';'+str(msvc/'bin/Hostx64/x64')+';'+os.environ['PATH'],
        VSINSTALLDIR=r'C:\Program Files\Microsoft Visual Studio\2022\Community',
        WINDOWSSDKDIR=str(sdk),WindowsSDKVersion='10.0.28000.0')
    (out/'environment.x64').write_bytes(('\0'.join(key+'='+value for key,value in environment_values.items())+'\0\0').encode('utf-8'))
    args='''target_cpu = "x64"
custom_toolchain = "//client:toolchain"
host_toolchain = "//client:toolchain"
is_clang = true
is_debug = false
is_component_build = false
is_unsafe_developer_build = false
use_custom_libcxx = false
use_custom_libcxx_for_host = false
use_clang_modules = false
enable_precompiled_headers = false
use_allocator_shim = true
use_partition_alloc = true
use_perfetto_trace_processor = false
enable_rust = true
use_blink = false
symbol_level = 0
chrome_pgo_phase = 0
clang_use_chrome_plugins = false
visual_studio_path = "C:/Program Files/Microsoft Visual Studio/2022/Community"
visual_studio_version = "2022"
windows_sdk_version = "10.0.28000.0"
'''
    args+='windows_sdk_path = "'+(ROOT/'windows-sdk/c').as_posix()+'"\n'
    args+='wdk_path = "'+(ROOT/'windows-sdk/c').as_posix()+'"\n'
    (out/'args.gn').write_text(args)
    environment=dict(os.environ,DEPOT_TOOLS_WIN_TOOLCHAIN='0',GYP_MSVS_VERSION='2022')
    command=[str(ROOT/'gn-source-fixture/gn.exe'),'gen','out/client','--fail-on-unused-args']
    REPORT['gn_selection']='exact installed-Chrome source-tag build compatibility fixture; latest version separately verified'
    result=subprocess.run(command,cwd=SRC,env=environment,capture_output=True,text=True,timeout=45,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    (HERE/'client-sdk-gn-diagnostic.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
    REPORT.update(result='generated' if result.returncode==0 else 'generation_failed',
                  exit_code=result.returncode,diagnostic_file='client-sdk-gn-diagnostic.txt')
    print((result.stdout+result.stderr)[-5000:])
except Exception as error:
    REPORT.update(result='failure',exception={'type':type(error).__name__,
        'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                  for f in traceback.extract_tb(error.__traceback__)]})
finally:
    REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
    (HERE/('client-sdk-configuration-'+REPORT['attempt']+'.json')).write_text(json.dumps(REPORT,indent=2))
    (HERE/'client-sdk-configuration.json').write_text(json.dumps(REPORT,indent=2))
    print(json.dumps(REPORT))
