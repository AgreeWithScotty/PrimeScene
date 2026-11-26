"""Omit unrelated Chromium test targets from the isolated client's build files.

GN loads dependencies from every target defined in a visited BUILD.gn, even when
the target isn't compiled. Keep pristine build files alongside each derived file;
never edit protocol, sandbox, CDM or other C/C++ implementation sources.
"""
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

HERE=Path(__file__).resolve().parent/'chrome-bridge'
SRC=HERE/'client-sdk/src'
START=time.monotonic()
REPORT={'operation':'scope_standalone_client_build_targets','attempt':uuid.uuid4().hex[:16],
        'source_implementation_changed':False,'omitted_targets':[]}
CALL=re.compile(r'(?m)^[ \t]*(\w+)\([^\n]*\)[ \t]*\{')
TEST_KINDS={'test','fuzzer_test','fuzztest','nocompile_source_set','rust_test','rust_unit_test',
            'test_with_coverage','absl_test','fuzzable_proto_library'}
TEST_NAMES={'perfetto_unittests','sql_query_proto_to_string','sqlite3_lpm_fuzzer_core'}

def closing(text,opening):
    depth=0;position=opening;quoted=False
    while position<len(text):
        char=text[position]
        if quoted:
            if char=='\\':position+=2;continue
            if char=='"':quoted=False
        elif char=='"':quoted=True
        elif char=='#':
            newline=text.find('\n',position)
            if newline<0:return None
            position=newline;continue
        elif char=='{':depth+=1
        elif char=='}':
            depth-=1
            if depth==0:return position+1
        position+=1
    return None

try:
    for path in SRC.rglob('BUILD.gn'):
        relative=path.relative_to(SRC).as_posix()
        if relative.startswith(('client/','out/')):continue
        pristine=path.with_suffix('.gn.upstream')
        original=(pristine if pristine.exists() else path).read_text(encoding='utf-8')
        spans=[];past=0
        for match in CALL.finditer(original):
            if match.start()<past:continue
            end=closing(original,match.end()-1)
            if end is None:raise ValueError('Unbalanced build target '+relative)
            body=original[match.end():end-1]
            kind=match.group(1)
            if kind in ('if','foreach','template','toolchain','tool'):continue
            named=re.search(r'\("([^"]+)"',match.group(0))
            if kind in TEST_KINDS or (named and named.group(1) in TEST_NAMES) or re.search(r'(?m)^\s*testonly\s*=\s*true\s*(?:#.*)?$',body):
                spans.append((match.start(),end));past=end
        flags_only=relative=='mojo/public/cpp/bindings/BUILD.gn'
        perfetto_msvc=relative=='third_party/perfetto/src/tracing/BUILD.gn'
        if not spans and not flags_only and not perfetto_msvc:
            # Restore production Rust targets omitted by the earlier C++-only
            # graph attempt; they now use the genuine staged Rust compiler.
            if pristine.exists() and path.read_text(encoding='utf-8')!=original:
                path.write_text(original,encoding='utf-8')
            continue
        derived=original
        for start,end in reversed(spans):derived=derived[:start]+'# Unrelated test/Rust facade omitted for the C++ transport client.\n'+derived[end:]
        # The Windows Mojo implementation has no crypto includes or symbols.
        # Its stale broad dependency otherwise pulls in unrelated TPM/Rust code.
        if relative=='mojo/core/BUILD.gn':
            derived=derived.replace('  deps = [ "//crypto" ]',
                '  # Standalone Windows transport has no crypto source dependency.\n  deps = []')
        if flags_only:
            # The raw transport client only consumes the original build flags.
            # Do not traverse the separate C++/JavaScript bindings libraries.
            derived=original[:original.index('# Logically part of the "bindings_base" target')]
        if perfetto_msvc:
            # The actual MSVC STL reports Perfetto's supported shared_ptr
            # atomic overloads as deprecated. Scope its documented suppression
            # to this target; retain the original implementation and warnings.
            marker='source_set("client_api_without_backends") {'
            if derived.count(marker)!=1:raise ValueError('Unexpected Perfetto target layout')
            derived=derived.replace(marker,marker+'\n  defines = [ "_SILENCE_CXX20_OLD_SHARED_PTR_ATOMIC_SUPPORT_DEPRECATION_WARNING" ]',1)
        if not pristine.exists():pristine.write_text(original,encoding='utf-8')
        path.write_text(derived,encoding='utf-8')
        REPORT['omitted_targets'].append({'file':relative,'count':len(spans),'flags_only':flags_only,
            'msvc_shared_ptr_deprecation_compatibility':perfetto_msvc,
            'upstream_sha256':hashlib.sha256(original.encode()).hexdigest(),
            'derived_sha256':hashlib.sha256(derived.encode()).hexdigest()})
    REPORT['result']='scoped'
finally:
    REPORT['elapsed_ms']=round((time.monotonic()-START)*1000)
    (HERE/'client-build-scope.json').write_text(json.dumps(REPORT,indent=2))
    print(json.dumps({'result':REPORT.get('result'),'files':len(REPORT['omitted_targets']),
        'omitted_build_targets':sum(e['count'] for e in REPORT['omitted_targets']),
        'elapsed_ms':REPORT['elapsed_ms']}))
