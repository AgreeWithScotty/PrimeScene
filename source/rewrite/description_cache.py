"""Private, generation-scoped description metadata for catalog read-ahead."""
from hashlib import sha256
import json
from pathlib import Path
from time import time

from .account_state import current_account, persist_if_current
from .prime_diagnostics import exception_details
from .public_catalog import IMAGE_HOSTS, safe_url

FIELDS = ('plot', 'year', 'duration', 'runtime', 'mpaa', 'rating', 'genre', 'genres',
          'seasons', 'season', 'episode', 'premiered', 'mediatype', 'fanart')
MAX_AGE = 24 * 60 * 60


class DescriptionCache:
    def __init__(self, g, epoch, report):
        self.profile, self.epoch, self.report = Path(g.DATA_PATH), epoch, report
        self.folder = self.profile / 'description-cache'
        self.memory = {}

    def _path(self, key):
        return self.folder / (sha256(key.encode()).hexdigest() + '.json')

    def load(self, key):
        if key in self.memory:
            return self.memory[key]
        info = None
        try:
            path = self._path(key)
            if path.is_file() and current_account(self.profile, self.epoch):
                if path.stat().st_size > 256 * 1024:
                    raise ValueError('Description cache exceeds its size limit')
                data = json.loads(path.read_text(encoding='utf-8'))
                if (data.get('schema') == 2 and data.get('epoch') == self.epoch
                        and 0 <= time() - data['saved'] < MAX_AGE
                        and isinstance(data.get('info'), dict)):
                    candidate = {field: data['info'][field] for field in FIELDS if field in data['info']}
                    if 'fanart' in candidate and not safe_url(candidate['fanart'], IMAGE_HOSTS):
                        candidate.pop('fanart')
                    if isinstance(candidate.get('plot'), str) and candidate['plot'].strip():
                        info = candidate
                        self.report({'event': 'description_cache_hit', 'source': 'saved',
                                     'backdrop_present': bool(info.get('fanart')), 'result': 'success'})
        except Exception as error:
            self.report({'event': 'description_cache_read_failed', 'stage': 'cache_read',
                         'exception': exception_details(error), 'result': 'failure'})
        self.memory[key] = info
        return info

    def save(self, key, values):
        info = {field: values[field] for field in FIELDS if field in values}
        if 'fanart' in info and not safe_url(info['fanart'], IMAGE_HOSTS):
            info.pop('fanart')
        if not isinstance(info.get('plot'), str) or not info['plot'].strip():
            return
        try:
            content = json.dumps({'schema': 2, 'epoch': self.epoch, 'saved': time(), 'info': info},
                                 ensure_ascii=True)
            if len(content.encode()) > 256 * 1024:
                raise ValueError('Description cache exceeds its size limit')
            path = self._path(key)

            def write():
                self.folder.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.new')
                temporary.write_text(content, encoding='utf-8')
                temporary.replace(path)
            stored = persist_if_current(self.profile, self.epoch, write)
            if stored:
                self.memory[key] = dict(info)
            self.report({'event': 'description_cache_saved', 'stage': 'cache_write',
                         'backdrop_present': bool(info.get('fanart')),
                         'result': 'success' if stored else 'cancelled'})
        except Exception as error:
            self.report({'event': 'description_cache_write_failed', 'stage': 'cache_write',
                         'exception': exception_details(error), 'result': 'failure'})
