"""Derive exact-fixture IPC ordinals with Chromium's own parser and algorithm.

Only public source/build metadata is consumed. No process, CDM or provider runs.
The child's actual Ping ordinal must agree before derived service ordinals are
used; a build may override the public default salt and is not assumed to match.
"""
import ast
import hashlib
import json
from pathlib import Path
import struct
import sys
import time
import traceback
import uuid

HERE=Path(__file__).resolve().parent/'chrome-bridge'
SRC=HERE/'client-sdk/src'
sys.path.insert(0,str(SRC/'mojo/public/tools/mojom'))
from mojom.parse import parser,conditional_features,ast as mojom_ast
from mojom.generate import module

def derive():
    start=time.monotonic()
    report={'operation':'derive_exact_chrome_ipc_protocol','attempt':uuid.uuid4().hex[:16],
            'source_tag':'158.0.8127.37','provider_requests':0,'events':[],
            'runtime_salt_match_required':True}
    try:
        generator=SRC/'mojo/public/tools/bindings/mojom_bindings_generator.py'
        tree=ast.parse(generator.read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='ScrambleMethodOrdinals')
        scope={'hashlib':hashlib,'struct':struct}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(generator),'exec'),scope)
        salt=(HERE/'fixture158__chrome__VERSION').read_bytes()
        report['salt_source']='public exact-tag chrome/VERSION; default upstream build input'
        report['salt_sha256']=hashlib.sha256(salt).hexdigest()
        interfaces={}
        for path in ['content/common/child_process.mojom','media/mojo/mojom/cdm_service.mojom',
                     'media/mojo/mojom/content_decryption_module.mojom',
                     'media/mojo/mojom/frame_interface_factory.mojom',
                     'media/mojo/mojom/output_protection.mojom','media/mojo/mojom/decryptor.mojom']:
            file=HERE/('fixture158__'+path.replace('/','__'))
            source=file.read_text()
            parsed=parser.Parse(source,path)
            conditional_features.RemoveDisabledDefinitions(parsed,['is_win'])
            for definition in parsed.definition_list:
                if not isinstance(definition,mojom_ast.Interface):continue
                interface=module.Interface(str(definition.mojom_name))
                for method in definition.body:
                    if not isinstance(method,mojom_ast.Method):continue
                    ordinal=method.ordinal.value if method.ordinal is not None else None
                    interface.AddMethod(str(method.mojom_name),ordinal=ordinal)
                scope['ScrambleMethodOrdinals']([interface],salt)
                interfaces[interface.mojom_name]={m.mojom_name:m.ordinal for m in interface.methods}
            report['events'].append({'stage':'parse_exact_idl','path':path,'result':'success',
                'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
        report['interfaces']=interfaces
        wanted={
            'kExpectedPing':interfaces['ChildProcessHost']['Ping'],
            'kBindCdmService':interfaces['ChildProcess']['BindServiceInterface'],
            'kGetCdmService':interfaces['CdmServiceBroker']['GetService'],
            'kCreateCdmFactory':interfaces['CdmService']['CreateCdmFactory'],
            'kCreateCdm':interfaces['CdmFactory']['CreateCdm'],
            'kFrameGetOrigin':interfaces['FrameInterfaceFactory']['GetCdmOrigin'],
            'kFrameBindEmbedder':interfaces['FrameInterfaceFactory']['BindEmbedderReceiver'],
            'kCdmSetClient':interfaces['ContentDecryptionModule']['SetClient'],
            'kCdmSetCertificate':interfaces['ContentDecryptionModule']['SetServerCertificate'],
            'kCdmGenerateRequest':interfaces['ContentDecryptionModule']['CreateSessionAndGenerateRequest'],
            'kCdmOnMessage':interfaces['ContentDecryptionModuleClient']['OnSessionMessage'],
            'kCdmUpdateSession':interfaces['ContentDecryptionModule']['UpdateSession'],
            'kCdmOnKeysChange':interfaces['ContentDecryptionModuleClient']['OnSessionKeysChange'],
            'kOutputQuery':interfaces['OutputProtection']['QueryStatus'],
            'kOutputEnable':interfaces['OutputProtection']['EnableProtection'],
            'kDecryptorInitializeVideo':interfaces['Decryptor']['InitializeVideoDecoder'],
            'kDecryptorInitialize':interfaces['Decryptor']['Initialize'],
            'kDecryptorDecodeVideo':interfaces['Decryptor']['DecryptAndDecodeVideo'],
            'kDecryptorDecrypt':interfaces['Decryptor']['Decrypt'],
            'kCdmCloseSession':interfaces['ContentDecryptionModule']['CloseSession'],
            'kCdmLoadSession':interfaces['ContentDecryptionModule']['LoadSession'],
            'kCdmRemoveSession':interfaces['ContentDecryptionModule']['RemoveSession'],
            'kCdmPolicy':interfaces['ContentDecryptionModule']['GetStatusForPolicy'],
            'kCdmOnClosed':interfaces['ContentDecryptionModuleClient']['OnSessionClosed'],
            'kCdmOnExpiry':interfaces['ContentDecryptionModuleClient']['OnSessionExpirationUpdate'],
            'kDecryptorReset':interfaces['Decryptor']['ResetDecoder'],
            'kDecryptorDeinitialize':interfaces['Decryptor']['DeinitializeDecoder'],
        }
        header='// Derived from exact-tag public Chromium IDL and official ordinal generator.\n'
        header+='// Runtime Ping must match; source salt is not assumed to match official binary.\n'
        header+='\n'.join('constexpr uint32_t '+name+' = '+str(value)+'u;' for name,value in wanted.items())+'\n'
        (HERE/'client-source/protocol_fixture.h').write_text(header)
        report['result']='derived_not_runtime_verified'
    except Exception as error:
        report.update(result='failure',exception={'type':type(error).__name__,
            'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                      for f in traceback.extract_tb(error.__traceback__)]})
    report['elapsed_ms']=round((time.monotonic()-start)*1000)
    destination=HERE/'exact-protocol-derivation.json'
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__':derive()
