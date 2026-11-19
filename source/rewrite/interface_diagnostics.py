"""Small allowlisted startup trace; no URLs, account names or exception text."""
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
import xbmc
from .prime_diagnostics import exception_details

_lock = Lock()


def record(globals_instance, phase, error=None):
    entry = {'time': datetime.now(timezone.utc).isoformat(), 'phase': phase}
    if error is not None:
        entry['error_type'] = type(error).__name__
        entry['exception'] = exception_details(error)
    try:
        entry['version'] = globals_instance.addon.getAddonInfo('version')
    except AttributeError:
        pass
    xbmc.log('[Prime Scene] phase=' + phase +
             (' error=' + entry['error_type'] if error is not None else ''),
             getattr(xbmc, 'LOGINFO', 1))
    try:
        with _lock:
            path = Path(globals_instance.DATA_PATH) / 'interface-startup.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry) + '\n')
            with (path.parent / 'prime-scene-diagnostics.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(entry, operation='interface')) + '\n')
    except (OSError, AttributeError):
        pass
