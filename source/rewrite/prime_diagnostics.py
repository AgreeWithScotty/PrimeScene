"""Structured real-run diagnostics without authentication secrets."""
from datetime import datetime, timezone
from collections import deque
import hashlib
from itertools import islice
import json
from pathlib import Path
import re
import secrets
import sys
from threading import RLock
from time import monotonic
import traceback
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit
from .private_storage import append_report,write_private

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
        self.lines = deque(maxlen=2048)

    def replay_current(self, replace=False):
        """Restore this redacted attempt after replacing an account's profile."""
        with self.lock:
            if not self.active():
                return
            try:
                for name in self._files():
                    path = self.profile / name
                    if replace or not path.exists():
                        write_private(path,''.join(self.lines).encode('utf-8'))
            except (OSError,ValueError) as error:
                self._write_failure(error)

    def _files(self):
        return ('prime-scene-diagnostics.jsonl', 'prime-scene-signin.jsonl') if self.operation == 'signin' else (
            'prime-scene-diagnostics.jsonl',)

    def _write_failure(self, error, line=''):
        try:
            import xbmc
            xbmc.log('[Prime Scene] diagnostic_write_failed ' + type(error).__name__, xbmc.LOGERROR)
            if line:
                xbmc.log('[Prime Scene] ' + line, xbmc.LOGERROR)
        except ImportError:
            sys.stderr.write('Prime Scene diagnostic_write_failed ' + type(error).__name__ + '\n')

    def secret(self, value):
        if value:
            value = str(value)
            with self.lock:
                self.values.update((value, quote(value, safe=''), quote_plus(value), json.dumps(value)[1:-1]))

    def redact(self, value, depth=0):
        if depth>12:return '[structure-limit]'
        if isinstance(value, dict):
            sensitive={'password','passwd','passcode','otp','otpcode','otppin','verificationcode',
                'verification_code','access','refresh','access_token','refresh_token','authorization',
                'proxy-authorization','cookie','set-cookie','code_verifier','client_secret',
                'customerid','customer_id','actorid','actor_id','deviceid','device_id','account_name',
                'email','license','widevine2challenge','widevine2license'}
            return {str(k):'[redacted]' if str(k).lower() in sensitive and not isinstance(v,bool)
                else self.redact(v,depth+1) for k,v in islice(value.items(),200)}
        if isinstance(value, (list, tuple)):
            return [self.redact(v,depth+1) for v in value[:200]]
        if not isinstance(value, str):
            return value
        for secret in sorted(self.values, key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
        value = re.sub(r'https?://[^\s<>"\']+', '[redacted-address]', value)
        value = re.sub(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b', '[redacted-email]', value)
        value = re.sub(r'(?i)\b(?:bearer|basic)\s+[a-z0-9+/_=.-]+','[redacted-authorization]',value)
        value = re.sub(r'(?i)\b(?:password|otpcode|verification_code|access_token|refresh_token|cookie)\s*[:=][^\r\n]+',
            '[redacted-credential]',value)
        value = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b','[redacted-token]',value)
        return value[:2000]

    def __call__(self, event):
        try:
            if not self.active():
                return
        except Exception as error:
            # A failed account guard must never release the original event.
            event = {'event':'diagnostic_account_guard_failed','stage':'logging_guard',
                'exception':exception_details(error),'result':'failure'}
        with self.lock:
            self.sequence += 1
            if self.sequence == 1:
                environment = {'python': sys.version.split()[0], 'platform': sys.platform}
                try:
                    import xbmc
                    environment['kodi'] = xbmc.getInfoLabel('System.BuildVersion')
                except ImportError:
                    pass
                event = dict(event, environment=environment)
            record = self.redact(dict(event, schema=2, operation=self.operation, attempt=self.attempt, sequence=self.sequence,
                                      version=self.version, time=datetime.now(timezone.utc).isoformat(),
                                      elapsed_ms=round((monotonic() - self.started) * 1000)))
            line = json.dumps(record, ensure_ascii=True) + '\n'
            self.lines.append(line)
            try:
                self.profile.mkdir(parents=True, exist_ok=True)
                for name in self._files():
                    append_report(self.profile/name,line)
            except (OSError,ValueError) as error:
                # A missing report must be visible rather than silently ignored.
                self._write_failure(error, line)
