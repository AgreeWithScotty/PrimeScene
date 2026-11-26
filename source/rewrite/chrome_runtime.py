"""Provision the exact private Chrome runtime directly from Google on first use."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from time import monotonic, sleep
import uuid
from .prime_diagnostics import exception_details
from .chrome_runtime_download import PreparationCancelled, fetch_and_extract


def _checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _inventory(manifest):
    if manifest.get('schema') != 3 or manifest.get('runtime_source') != 'google_direct_download':
        raise ValueError('Unsupported private Chrome manifest')
    rows = {}
    total = 0
    for row in manifest['runtime_files']:
        name = row['path']
        path = PurePosixPath(name)
        if (not isinstance(name, str) or path.is_absolute() or '\\' in name or ':' in name or
                path.as_posix() != name or not path.parts or any(part in ('', '.', '..') for part in path.parts)):
            raise ValueError('Unsafe bundled Chrome member')
        if name.casefold() in rows or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            raise ValueError('Invalid bundled Chrome member metadata')
        if not isinstance(row['bytes'], int) or not 0 <= row['bytes'] <= 600*1024*1024:
            raise ValueError('Invalid bundled Chrome member size')
        rows[name.casefold()] = row
        total += row['bytes']
    if not rows or len(rows) > 256 or total != manifest['runtime_expanded_bytes'] or total > 700*1024*1024:
        raise ValueError('Invalid bundled Chrome inventory size')
    return rows


def _ready(runtime, rows, report=None):
    started=monotonic();hashed_bytes=0;hashed_files=0;cached_files=0
    inventory_id=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    receipt=runtime.parent/'runtime-verification.json'
    previous={}
    try:
        value=json.loads(receipt.read_text(encoding='utf-8'))
        if (isinstance(value,dict) and value.get('schema')==1 and value.get('inventory_id')==inventory_id
                and isinstance(value.get('files'),dict)):previous=value['files']
    except (OSError,ValueError,TypeError):pass
    current={}
    for row in rows.values():
        path = runtime/row['path']
        try:
            before=path.stat()
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&1024:return False
            signature=[before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_ino]
            if before.st_size!=row['bytes']:
                return False
            if previous.get(row['path'])==signature:
                cached_files+=1
            else:
                if _checksum(path)!=row['sha256']:return False
                after=path.stat()
                if signature!=[after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_ino]:return False
                hashed_files+=1;hashed_bytes+=row['bytes']
            current[row['path']]=signature
        except FileNotFoundError:
            return False
    temporary=None
    try:
        if current!=previous:
            temporary=receipt.with_name('runtime-verification-'+uuid.uuid4().hex+'.tmp')
            temporary.write_text(json.dumps({'schema':1,'inventory_id':inventory_id,'files':current}),encoding='utf-8')
            os.replace(str(temporary),str(receipt))
    except OSError as error:
        if report:report({'event':'chrome_cdm_integrity_receipt','stage':'private_chrome_runtime',
            'exception':exception_details(error),'result':'failure'})
    finally:
        if temporary is not None:
            try:temporary.unlink(missing_ok=True)
            except OSError as error:
                if report:report({'event':'chrome_cdm_integrity_receipt_cleanup','stage':'private_chrome_runtime',
                    'exception':exception_details(error),'result':'failure'})
    if report:report({'event':'chrome_cdm_runtime_integrity','stage':'private_chrome_runtime',
        'hashed_bytes':hashed_bytes,'hashed_files':hashed_files,'unchanged_verified_files':cached_files,
        'inventory_id':inventory_id,'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
    return True


@contextmanager
def _install_lock(root, cancel):
    # Windows releases the byte-range lock on process exit, including crashes.
    import msvcrt
    with (root/'chrome-runtime-install.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        deadline = monotonic()+1800
        while True:
            if cancel():
                raise PreparationCancelled()
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if monotonic() >= deadline:
                    raise TimeoutError('Bundled Chrome preparation is busy')
                sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _discard(path, root):
    # Only a verified immediate child of the owned native directory is removed.
    if path.resolve().parent != root.resolve() or path.is_symlink():
        raise ValueError('Unsafe Chrome preparation cleanup path')
    if path.exists():
        shutil.rmtree(str(path))


def ensure(root, manifest, report, cancel=lambda: False, progress=lambda percent, text: False):
    started = monotonic()
    attempt = uuid.uuid4().hex[:16]
    stage = None
    retired = None
    outcome = 'failure'
    def emit(result, **facts):
        if 'operation' in facts:facts['runtime_operation']=facts.pop('operation')
        report(dict(event='chrome_cdm_runtime_preparation', stage='private_chrome_runtime',
                    runtime_attempt=attempt, chrome_version=manifest.get('chrome_abi_fixture'),
                    elapsed_ms=round((monotonic()-started)*1000), result=result, **facts))
    try:
        root = Path(root)
        rows = _inventory(manifest)
        runtime = root/'runtime'
        if cancel():
            raise PreparationCancelled()
        if _ready(runtime, rows, report):
            outcome = 'success'
            emit('success', operation='verify_existing_runtime', files=len(rows),
                 download_required=False, system_chrome_required=False, automatic_updates=False)
            return runtime
        with _install_lock(root, cancel):
            if _ready(runtime, rows, report):
                outcome = 'success'
                emit('success', operation='runtime_prepared_by_other_invocation', files=len(rows))
                return runtime
            # A crashed process releases the lock, so earlier staging directories
            # are orphaned and can be removed without interrupting another setup.
            for old in root.iterdir():
                if re.fullmatch('chrome-runtime-unpack-[a-f0-9]{16}', old.name):
                    _discard(old,root)
                    emit('success',operation='recover_interrupted_runtime_download')
            stage = root/('chrome-runtime-unpack-'+attempt)
            stage.mkdir()
            candidate=fetch_and_extract(root,stage,manifest,rows,emit,cancel,progress)
            if runtime.exists():
                retired = root/('chrome-runtime-replaced-'+attempt)
                runtime.rename(retired)
            try:
                candidate.rename(runtime)
            except Exception:
                if retired is not None:
                    retired.rename(runtime)
                    retired = None
                raise
            outcome = 'success'
            emit('success', operation='install_verified_google_runtime', files=len(rows),
                 expanded_bytes=manifest['runtime_expanded_bytes'],
                 system_chrome_required=False, automatic_updates=False)
            progress(100,'HD playback components are ready')
            return runtime
    except PreparationCancelled:
        outcome = 'cancelled'
        emit('cancelled', operation='prepare_private_runtime')
        raise
    except Exception as error:
        emit('failure', operation='prepare_private_runtime', exception=exception_details(error))
        raise
    finally:
        for path in (stage, retired):
            if path is not None:
                try:
                    _discard(path, root)
                    emit('success',operation='runtime_staging_cleanup')
                except Exception as error:
                    emit('failure', operation='runtime_staging_cleanup',
                         preparation_result=outcome, exception=exception_details(error))
