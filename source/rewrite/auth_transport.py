"""Bounded JSON transport for account discovery and device authorization.

This module has no dependency on the catalog/player network implementation.
"""
import json
import re
import secrets
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .prime_diagnostics import exception_details, route
from .prime_signin import SignInError, SignInHTTPError
from .regional_hosts import AUTH_API_DOMAINS,AUTH_API_HOSTS

LIMIT = 1024 * 1024
AUTH_DOMAINS = AUTH_API_DOMAINS
HOSTS = AUTH_API_HOSTS
KNOWN = frozenset(('customerConfig territoryConfig homeRegion locale uxLocale defaultVideoWebsite '
    'primeSignupBaseUrl avMarketplace access_token refresh_token expires_in token_type error '
    'error_description errorCode errorMessage message type code response success').split())


def structure(value, depth=0):
    if isinstance(value, dict):
        known = sorted(set(value) & KNOWN)
        return {'kind': 'object', 'known_fields': known, 'other_fields': len(value)-len(known),
            'children': {key: structure(value[key], depth+1) for key in known
                if depth < 4 and isinstance(value[key], (dict, list))}}
    if isinstance(value, list):
        return {'kind': 'array', 'count': len(value)}
    return {'kind': type(value).__name__}


def error_details(value, observer):
    """Retain useful server errors without serializing arbitrary account data."""
    errors = []
    def walk(node, private=False):
        if isinstance(node, dict):
            for key, child in node.items():
                hidden = private or any(word in key.lower() for word in
                    ('token', 'cookie', 'customer', 'email', 'device', 'secret', 'password', 'actor'))
                if hidden and isinstance(child, (str, int)) and hasattr(observer, 'secret'):
                    observer.secret(child)
                elif key in ('error', 'errorCode', 'error_description', 'errorMessage', 'message', 'code', 'type') and isinstance(child, str):
                    text = re.sub(r'https?://\S+|\b\S+@\S+\b|\b[\w+./=-]{24,}\b|\d{5,}', '[redacted]', child)
                    errors.append({key: text[:500]})
                if isinstance(child, (dict, list)):
                    walk(child, hidden)
        elif isinstance(node, list):
            for child in node:
                walk(child, private)
    walk(value)
    return errors[:12]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def json_request(address, *, observer, stage, payload=None, headers=None):
    parts = urlsplit(address)
    if (parts.scheme != 'https' or parts.hostname not in HOSTS or parts.username
            or parts.password or parts.port not in (None, 443)):
        raise SignInError('The account service address is invalid.')
    request_id = secrets.token_hex(16)
    started = monotonic()
    fields = {'Accept': 'application/json', 'x-amzn-requestid': request_id}
    fields.update({key: value for key, value in (headers or {}).items()
                   if key.lower() not in ('host', 'cookie', 'authorization')})
    body = None
    if payload is not None:
        fields['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode('utf-8')
    observer({'event': 'account_request_started', 'stage': stage, 'request_id': request_id,
        'method': 'POST' if body is not None else 'GET', 'address': route(address),
        'payload_fields': sorted(payload) if isinstance(payload, dict) else []})
    status = None
    try:
        try:
            response = build_opener(_NoRedirect()).open(Request(address, data=body, headers=fields), timeout=20)
        except HTTPError as response_error:
            response = response_error
        with response:
            status = response.code
            final = response.geturl()
            info = {'event': 'account_response_received', 'stage': stage, 'request_id': request_id,
                'status': status, 'address': route(final),
                'content_type': response.headers.get_content_type(),
                'server_request_id': response.headers.get('x-amzn-requestid') or response.headers.get('x-amz-rid'),
                'network_ms': round((monotonic()-started)*1000)}
            if response.headers.get('Location'):
                info['redirect'] = route(response.headers['Location'])
            observer(info)
            raw = response.read(LIMIT+1)
        if len(raw) > LIMIT:
            raise SignInError('The account service response exceeded its size limit.')
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            observer({'event': 'account_response_format', 'stage': stage, 'request_id': request_id,
                'status': status, 'bytes': len(raw), 'format': 'not-json', 'result': 'failure'})
            if status >= 300:
                raise SignInHTTPError(status, stage) from None
            raise SignInError('The account service did not return JSON.') from None
        errors = error_details(value, observer)
        observer({'event': 'account_response_parsed', 'stage': stage, 'request_id': request_id,
            'status': status, 'bytes': len(raw), 'structure': structure(value), 'server_errors': errors})
        if status < 200 or status >= 300:
            raise SignInHTTPError(status, stage)
        if not isinstance(value, dict):
            raise SignInError('The account service response was not an object.')
        observer({'event': 'account_request_complete', 'stage': stage, 'request_id': request_id,
            'elapsed_ms': round((monotonic()-started)*1000), 'result': 'success'})
        return value
    except Exception as error:
        observer({'event': 'account_request_failed', 'stage': stage, 'request_id': request_id,
            'status': status, 'exception': exception_details(error),
            'elapsed_ms': round((monotonic()-started)*1000), 'result': 'failure'})
        raise
