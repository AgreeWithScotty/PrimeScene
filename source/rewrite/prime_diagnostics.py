"""Structured real-run diagnostics without authentication secrets."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
from threading import RLock
from time import monotonic
import traceback
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

ROUTE_WORDS = set(('ap signin cvf maplanding approval verify otp password mfa resend confirm '
                   'validateCaptcha auth-redirect settings storefront detail tv movie gp video '
                   'auth register token verifyOtp verifyCaptcha claimspicker polling').split())


def route(url):
    try:
        parts = urlsplit(url)
        segments = [s if s in ROUTE_WORDS else '#' + hashlib.sha256(s.encode()).hexdigest()[:8]
                    for s in parts.path.split('/') if s]
        return {'host': parts.hostname, 'route': '/' + '/'.join(segments),
                'query_keys': sorted(set(k for k, _ in parse_qsl(parts.query))),
                'authorization_code_present': 'openid.oa2.authorization_code' in dict(parse_qsl(parts.query))}
    except ValueError:
        return {'route': 'invalid-address'}


def exception_details(error):
    result = {'type': type(error).__name__, 'frames': [
        {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
        for frame in traceback.extract_tb(error.__traceback__)]}
    for key in ('status', 'stage', 'code', 'errno'):
        value = getattr(error, key, None)
        if isinstance(value, int) or (key == 'stage' and isinstance(value, str)):
            result[key] = value
    if isinstance(getattr(error, 'verify_code', None), int):
        result['tls_verify_code'] = error.verify_code
        result['tls_verify_message'] = getattr(error, 'verify_message', '')
    reason = getattr(error, 'reason', None)
    if reason is not None:
        result['reason_type'] = type(reason).__name__
        if isinstance(getattr(reason, 'errno', None), int):
            result['reason_errno'] = reason.errno
    if error.__cause__ is not None:
        result['cause'] = exception_details(error.__cause__)
    return result


class DiagnosticLog:
    def __init__(self, profile, version, active=lambda: True, operation='signin'):
        self.profile, self.version, self.active = Path(profile), version, active
        self.attempt = secrets.token_hex(8)
        self.operation = operation
        self.started = monotonic()
        self.sequence = 0
        self.values = set()
        self.lock = RLock()

    def secret(self, value):
        if value:
            value = str(value)
            self.values.update((value, quote(value, safe=''), quote_plus(value), json.dumps(value)[1:-1]))

    def redact(self, value):
        if isinstance(value, dict):
            return {str(k): self.redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(v) for v in value]
        if not isinstance(value, str):
            return value
        for secret in sorted(self.values, key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
        value = re.sub(r'https?://[^\s<>"\']+', '[redacted-address]', value)
        value = re.sub(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b', '[redacted-email]', value)
        return value[:2000]

    def __call__(self, event):
        try:
            if not self.active():
                return
        except Exception as error:
            event = dict(event, diagnostic_state_check_error=type(error).__name__)
        with self.lock:
            self.sequence += 1
            record = self.redact(dict(event, schema=2, operation=self.operation, attempt=self.attempt, sequence=self.sequence,
                                      version=self.version, time=datetime.now(timezone.utc).isoformat(),
                                      elapsed_ms=round((monotonic() - self.started) * 1000)))
            line = json.dumps(record, ensure_ascii=True) + '\n'
            try:
                self.profile.mkdir(parents=True, exist_ok=True)
                files = ('prime-scene-diagnostics.jsonl',)
                if self.operation == 'signin':
                    files += ('prime-scene-signin.jsonl',)
                for name in files:
                    with (self.profile / name).open('a', encoding='utf-8') as output:
                        output.write(line)
                        output.flush()
            except OSError as error:
                # A missing report must be visible rather than silently ignored.
                try:
                    import xbmc
                    xbmc.log('[Prime Scene] diagnostic_write_failed ' + type(error).__name__, xbmc.LOGERROR)
                    xbmc.log('[Prime Scene] ' + line, xbmc.LOGERROR)
                except ImportError:
                    sys.stderr.write('Prime Scene diagnostic_write_failed ' + type(error).__name__ + '\n')
