"""Fetch one pinned Google payload and extract it without running its installer."""
import hashlib
import subprocess
from time import monotonic, sleep
from urllib.parse import urlsplit
import urllib.request
import uuid
from .prime_diagnostics import route


class PreparationCancelled(Exception):
    pass


def _origin(address):
    parsed = urlsplit(address)
    if (parsed.scheme != 'https' or parsed.hostname not in ('edgedl.me.gvt1.com', 'dl.google.com') or
            parsed.port not in (None, 443) or parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment):
        raise ValueError('Unapproved pinned Chrome download address')


def _checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def fetch_and_extract(root, stage, manifest, rows, emit, cancel, progress):
    config = manifest['runtime_download']
    _origin(config['url'])
    expected_size = config['bytes']
    if not isinstance(expected_size, int) or not 0 < expected_size <= 700*1024*1024:
        raise ValueError('Invalid pinned Chrome payload size')
    extractor = root/'7zr.exe'
    if _checksum(extractor) != manifest['runtime_extractor']['sha256']:
        raise ValueError('Runtime extractor checksum mismatch')
    installer = stage/'google-runtime-payload.exe'
    redirects = []
    request_id = uuid.uuid4().hex[:12]
    download_started = monotonic()
    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, address):
            _origin(address)
            if len(redirects) >= 5:
                raise ValueError('Pinned Chrome redirect limit')
            redirects.append({'status': code, 'from': route(request.full_url), 'to': route(address)})
            return super().redirect_request(request, fp, code, message, headers, address)
    def checkpoint(percent, message):
        if cancel() or progress(percent, message):
            raise PreparationCancelled()
    checkpoint(0, 'Downloading HD playback components')
    emit('started', runtime_operation='download_google_runtime', request_id=request_id,
         address=route(config['url']), expected_bytes=expected_size)
    total = 0
    checksum = hashlib.sha256()
    last_progress = monotonic()
    deadline = download_started+1800
    try:
        request = urllib.request.Request(config['url'], headers={'User-Agent': 'SmoothPrime-Runtime/1',
                                                               'Accept-Encoding': 'identity'})
        with urllib.request.build_opener(Redirects()).open(request, timeout=30) as response:
            _origin(response.url)
            if response.status != 200:
                raise ValueError('Unexpected Chrome payload HTTP status')
            length = response.headers.get('Content-Length')
            if length is not None and int(length) != expected_size:
                raise ValueError('Pinned Chrome payload length mismatch')
            with installer.open('xb') as output:
                while True:
                    if cancel():
                        raise PreparationCancelled()
                    if monotonic() > deadline:
                        raise TimeoutError('Pinned Chrome download deadline')
                    chunk = response.read(1024*1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected_size:
                        raise ValueError('Pinned Chrome payload exceeds bound')
                    output.write(chunk)
                    checksum.update(chunk)
                    if monotonic()-last_progress >= .25:
                        checkpoint(min(85, int(total*85/expected_size)), 'Downloading HD playback components')
                        last_progress = monotonic()
            if total != expected_size or checksum.hexdigest() != config['sha256']:
                raise ValueError('Pinned Chrome payload checksum mismatch')
            emit('success', runtime_operation='download_google_runtime', request_id=request_id,
                 status=response.status, address=route(response.url), redirects=redirects,
                 received_bytes=total, sha256=checksum.hexdigest(),
                 http_elapsed_ms=round((monotonic()-download_started)*1000))
    except Exception as error:
        emit('cancelled' if isinstance(error, PreparationCancelled) else 'failure',
             runtime_operation='download_google_runtime', request_id=request_id,
             status=getattr(error, 'code', None), address=route(config['url']), redirects=redirects,
             received_bytes=total, http_elapsed_ms=round((monotonic()-download_started)*1000))
        raise
    checkpoint(87, 'Preparing HD playback components')
    started = monotonic()
    # Read only the authenticated fixed payload. Never run its installer/updater.
    command = [str(extractor), 'x', '-y', '-bsp0', '-bso0', '-bse0', '-o'+str(stage), str(installer)]
    command += ['Chrome-bin/'+row['path'] for row in rows.values()]
    child = None
    try:
        child = subprocess.Popen(command, cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        while child.poll() is None:
            checkpoint(90, 'Preparing HD playback components')
            if monotonic()-started > 120:
                raise TimeoutError('Pinned Chrome extraction deadline')
            sleep(.1)
        if child.returncode != 0:
            raise RuntimeError('Pinned Chrome extraction failed')
        candidate = stage/'Chrome-bin'
        present = [p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file()]
        normalized = {name.casefold() for name in present}
        # Google's Windows payload preserves "Locales" while the pinned
        # Windows file inventory uses "locales". Compare Windows path identity.
        if normalized != set(rows) or len(present) != len(normalized):
            emit('failure',runtime_operation='extract_google_runtime',expected_files=len(rows),
                 actual_files=len(present),missing_files=sorted(set(rows)-normalized),
                 unexpected_files=sorted(normalized-set(rows)))
            raise ValueError('Extracted Chrome inventory mismatch')
        for row in rows.values():
            path = candidate/row['path']
            path.resolve().relative_to(candidate.resolve())
            if path.stat().st_size != row['bytes'] or _checksum(path) != row['sha256']:
                raise ValueError('Extracted Chrome member checksum mismatch')
            checkpoint(96, 'Verifying HD playback components')
        emit('success', runtime_operation='extract_google_runtime', files=len(rows),
             extractor_version=manifest['runtime_extractor']['version'], exit_code=child.returncode,
             extractor_elapsed_ms=round((monotonic()-started)*1000), installer_executed=False)
        return candidate
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
            emit('success', runtime_operation='extractor_process_cleanup', exit_code=child.returncode)
