"""Persistent pinned runtime and immutable adapter versions across add-on updates."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from time import monotonic
import uuid
from .prime_diagnostics import exception_details


def layout(profile,manifest):
    version=manifest['chrome_abi_fixture']
    if not isinstance(version,str) or not re.fullmatch(r'\d+(?:\.\d+){3}',version):
        raise ValueError('Invalid persistent Chrome version')
    artifacts=manifest['artifacts']
    if set(artifacts)!= {'widevinecdm.dll','transport_probe.exe'}:
        raise ValueError('Invalid persistent adapter inventory')
    if any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in artifacts.values()):
        raise ValueError('Invalid persistent adapter checksum')
    identity=hashlib.sha256(json.dumps(artifacts,sort_keys=True).encode('utf-8')).hexdigest()
    root=Path(profile)/'native/chrome-cdm'/version
    return root,root/'adapters'/identity


def prepare_adapter(source,root,manifest,report):
    started=monotonic();attempt=uuid.uuid4().hex[:16];temporary=None
    # Only the small owned adapter pair changes on an add-on update.
    identity=hashlib.sha256(json.dumps(manifest['artifacts'],sort_keys=True).encode('utf-8')).hexdigest()
    destination=Path(root)/'adapters'/identity
    copied=0
    def matches():
        return all((destination/name).is_file() and
            hashlib.sha256((destination/name).read_bytes()).hexdigest()==digest
            for name,digest in manifest['artifacts'].items())
    try:
        if not destination.exists():
            destination.parent.mkdir(parents=True,exist_ok=True)
            temporary=destination.with_name('prepare-'+attempt);temporary.mkdir()
            for name,digest in manifest['artifacts'].items():
                data=(Path(source)/name).read_bytes()
                if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Native adapter artifact checksum mismatch')
                (temporary/name).write_bytes(data);copied+=1
            try:temporary.rename(destination)
            except FileExistsError:pass
        if not matches():raise ValueError('Persistent native adapter checksum mismatch')
        report({'event':'chrome_cdm_adapter_prepared','stage':'persistent_native_adapter',
            'adapter_attempt':attempt,'adapter_identity':identity,'copied_files':copied,
            'storage':'addon_data','elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
        return destination
    except Exception as error:
        report({'event':'chrome_cdm_adapter_prepared','stage':'persistent_native_adapter',
            'adapter_attempt':attempt,'elapsed_ms':round((monotonic()-started)*1000),
            'exception':exception_details(error),'result':'failure'})
        raise
    finally:
        if temporary is not None and temporary.exists():
            try:
                if temporary.resolve().parent!=destination.parent.resolve():raise ValueError('Unsafe adapter staging cleanup')
                shutil.rmtree(str(temporary))
            except Exception as error:
                report({'event':'chrome_cdm_adapter_staging_cleanup','stage':'persistent_native_adapter',
                    'adapter_attempt':attempt,'exception':exception_details(error),'result':'failure'})


def owned_directory(profile,value):
    try:
        parts=Path(value).resolve().relative_to((Path(profile)/'native/chrome-cdm').resolve()).parts
        return (len(parts)==3 and re.fullmatch(r'\d+(?:\.\d+){3}',parts[0])
            and parts[1]=='adapters' and re.fullmatch('[a-f0-9]{64}',parts[2]))
    except (ValueError,OSError):return False
