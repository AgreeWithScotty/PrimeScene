"""Read-only Widevine binary/manifest facts; no CDM, profile or DRM changes."""
import re
from pathlib import Path
from .prime_diagnostics import exception_details


def challenge_facts(challenge):
    """Observe public protocol flags without exposing identification or proof data.

    Encrypted identification stays encrypted. Never decode device tokens, names,
    certificates, file hashes, signatures, nonces, or content identification.
    """
    def varint(data, offset):
        value=0
        for shift in range(0,70,7):
            if offset>=len(data):raise ValueError('Truncated CDM protocol integer')
            byte=data[offset];offset+=1;value|=(byte&127)<<shift
            if not byte&128:return value,offset
        raise ValueError('CDM protocol integer exceeds its limit')

    def fields(data):
        if len(data)>1024*1024:raise ValueError('CDM protocol exceeds its limit')
        data=memoryview(data);offset=0;values={};count=0
        while offset<len(data):
            count+=1
            if count>10000:raise ValueError('CDM protocol has too many fields')
            tag,offset=varint(data,offset);number,wire=tag>>3,tag&7
            if not number:raise ValueError('Invalid CDM protocol field')
            if wire==0:value,offset=varint(data,offset)
            elif wire in (1,2,5):
                if wire==2:size,offset=varint(data,offset)
                else:size=8 if wire==1 else 4
                end=offset+size
                if end>len(data):raise ValueError('Truncated CDM protocol field')
                value=data[offset:end];offset=end
            else:raise ValueError('Unsupported CDM protocol wire type')
            values.setdefault(number,[]).append((wire,value))
        return values

    def scalar(values,key):
        entries=values.get(key,[])
        return entries[0][1] if len(entries)==1 and entries[0][0]==0 else None

    def section(values,key):
        entries=values.get(key,[])
        return entries[0][1] if len(entries)==1 and entries[0][0]==2 else None

    signed=fields(challenge)
    facts={'signed_message_type':{1:'license_request',4:'service_certificate_request'}.get(scalar(signed,1),'other'),
           'signature_present':bool(section(signed,3)),
           'remote_attestation_present':bool(section(signed,5)),
           'challenge_altered':False}
    if scalar(signed,1)!=1:return facts
    payload=section(signed,2)
    if payload is None:raise ValueError('CDM license request has no protocol payload')
    request=fields(payload)
    plain=section(request,1);encrypted=section(request,8)
    facts.update(request_type={1:'new',2:'renewal',3:'release'}.get(scalar(request,3),'other'),
        protocol_version=scalar(request,6) if scalar(request,6) in (20,21,22) else None,
        identification_mode='both' if plain is not None and encrypted is not None else
            'encrypted' if encrypted is not None else 'clear' if plain is not None else 'absent',
        vmp_data_present=None,cdm_max_hdcp_version=None,
        proof_observation='encrypted' if encrypted is not None and plain is None else 'unavailable')
    if plain is not None:
        client=fields(plain)
        facts.update(vmp_data_present=bool(section(client,7)),proof_observation='clear_identification_metadata')
        capabilities=section(client,6)
        if capabilities is not None:
            level=scalar(fields(capabilities),4)
            facts['cdm_hdcp_capability_enum']=level if level in (0,1,2,3,4,5,255) else None
            facts['cdm_max_hdcp_version']={0:'none',1:'1.x',2:'2.0',3:'2.1',4:'2.2',5:'2.3',255:'no_digital_output'}.get(level)
    return facts


def denial_details(value):
    """Provider enums and mentioned requirements only, never free-form text."""
    stack=[value];visited=0;messages=[];exceptions=[];codes=[]
    while stack and visited<2000:
        node=stack.pop();visited+=1
        if isinstance(node,dict):
            for key,item in node.items():
                if isinstance(item,(dict,list)):stack.append(item)
                elif isinstance(item,str):
                    if key in ('message','errorMessage','reason'):messages.append(item[:4096])
                    elif key in ('code','errorCode') and re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',item):codes.append(item)
                    elif key in ('exception','errorType','type') and item in (
                            'DRMDeniedException','DRMInvalidRequestException','DRMInternalException'):
                        exceptions.append(item)
        elif isinstance(node,list):stack.extend(node[:100])
    text=' '.join(messages).lower()
    return {'exception_types':sorted(set(exceptions)),
        'vmp_explicit_code':any(code.endswith('.VmpValidationRequired') for code in codes),
        'message_enums':sorted(set(messages)&{
            'downgrade_video_definition_to_sd','downgrade_video_definition_to_hd',
            'vmp_validation_required','unsupported_cdm_version','insufficient_security_level',
            'hdcp_version_not_supported'}),
        'requirements_mentioned':{key:bool(re.search(pattern,text)) for key,pattern in (
            ('vmp',r'\bvmp\b|verified[ _-]media[ _-]path'),('hdcp',r'\bhdcp\b'),
            ('hardware_security',r'hardware[ _-](?:security|secure)|\bl1\b'),
            ('cdm_version',r'cdm[ _-]version|unsupported[ _-]cdm'))}}


def windows_binary_version(path):
    """Read the PE version resource without loading or executing the CDM."""
    import ctypes
    from ctypes import wintypes
    class VersionInfo(ctypes.Structure):
        _fields_=[(name,wintypes.DWORD) for name in (
            'signature','structure_version','file_ms','file_ls','product_ms','product_ls',
            'flags_mask','flags','os','type','subtype','date_ms','date_ls')]
    api=ctypes.WinDLL('version.dll',winmode=0x00000800,use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype=wintypes.DWORD
    api.GetFileVersionInfoW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID]
    api.GetFileVersionInfoW.restype=wintypes.BOOL
    api.VerQueryValueW.argtypes=[wintypes.LPCVOID,wintypes.LPCWSTR,ctypes.POINTER(wintypes.LPVOID),ctypes.POINTER(wintypes.UINT)]
    api.VerQueryValueW.restype=wintypes.BOOL
    size=api.GetFileVersionInfoSizeW(str(path),None)
    if not 0<size<=1024*1024:raise OSError('CDM version resource unavailable')
    data=ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path),0,size,data):raise ctypes.WinError(ctypes.get_last_error())
    pointer=wintypes.LPVOID();length=wintypes.UINT()
    if not api.VerQueryValueW(data,'\\',ctypes.byref(pointer),ctypes.byref(length)) or length.value<ctypes.sizeof(VersionInfo):
        raise OSError('CDM version resource has no fixed information')
    value=ctypes.cast(pointer,ctypes.POINTER(VersionInfo)).contents
    if value.signature!=0xFEEF04BD:raise ValueError('Invalid PE version signature')
    return '.'.join(str(n) for n in (value.product_ms>>16,value.product_ms&0xffff,
                                    value.product_ls>>16,value.product_ls&0xffff))


def record_environment(platform,report):
    if platform in ('android','webos'):return
    try:
        from inputstreamhelper.widevine.widevine import load_widevine_config,widevinecdm_path
        import xbmcvfs
        config=load_widevine_config() or {}
        declared=config.get('version')
        declared=declared if isinstance(declared,str) and re.fullmatch(r'\d+(?:\.\d+){1,4}',declared) else None
        path=widevinecdm_path()
        path=Path(xbmcvfs.translatePath(path)) if path else None
        exists=bool(path and path.is_file())
        facts={'event':'widevine_binary_environment','stage':'widevine_client_capability',
            'device_platform':platform,'manifest_version':declared,'binary_present':exists,
            'binary_bytes':path.stat().st_size if exists else None,
            'manifest_abi':{key:config[key] for key in ('x-cdm-interface-versions','x-cdm-host-versions','x-cdm-module-versions')
                if isinstance(config.get(key),str) and re.fullmatch(r'[0-9, ]{1,80}',config[key])},
            'result':'observed' if exists else 'unavailable'}
        if platform=='windows' and exists:
            try:facts['binary_version']=windows_binary_version(path)
            except Exception as error:facts['binary_version_error']=exception_details(error)
        report(facts)
    except Exception as error:
        report({'event':'widevine_binary_environment','stage':'widevine_client_capability',
            'device_platform':platform,'exception':exception_details(error),'result':'unavailable'})
