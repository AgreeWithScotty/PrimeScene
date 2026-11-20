"""Aurora Cinema windows for Prime Scene.

Layout and the charcoal tint follow SmoothFlix Aurora Cinema. The home hero
is drawn by the skin from the focused card. Playback uses the existing Prime
Scene PlayVideo URL. No separate Kodi skin is required.
"""
from json import dumps, load
from pathlib import Path
from queue import Empty, Queue
from re import IGNORECASE
from re import compile as compile_re
from re import fullmatch
from threading import Event, Lock, RLock, Thread, current_thread
from time import time, monotonic
from traceback import format_exc

import xbmc
import xbmcaddon
import xbmcgui

from .dedicated_bridge import CatalogSigninRequired, ProviderBridge
from .interface_diagnostics import record
from .title_art import render_title
from .navigation import StorefrontLoader
from .artwork_cache import ImageCache
from .description_cache import DescriptionCache
from .prime_diagnostics import DiagnosticLog, exception_details

_BACK = (9, 10, 92)
# The visible scrim stays on one matte tone. It does not cycle, and it never
# stands in for missing artwork. PrimeSceneHome.xml paints this on control 101.
_SCRIM = '99141A20'
_NOTE = 'aurora-playback.json'
_NOTE_WINDOW = 120
ADDON_ID = 'plugin.video.primeinterface'
# Kodi 21 WindowXML::WindowXML looks up xmlFilename in the active skin first
# (<skin>/<res>/<xmlFilename>) and only then in
# <scriptPath>/resources/skins/<defaultSkin>/<defaultRes>/<xmlFilename>.
# Estuary always ships Home.xml, so that name loads the skin window. These
# names are not skin windows, so the add-on files are the ones that open.
_HOME_XML = 'PrimeSceneHome.xml'
_PLAYER_XML = 'PrimeScenePlayer.xml'
# Amazon's public image service. A width suffix keeps the texture cache on a
# predictable file instead of the full master.
_AMAZON_IMAGE = compile_re(
    r'^(https://(?:m\.media-amazon\.com|images-na\.ssl-images-amazon\.com)/images/.+?)'
    r'(?:\._[^./]+_)?(\.(?:jpe?g|png|webp))$',
    IGNORECASE)
_CARD_ART = 720
_HERO_ART = 1280
_DESCRIPTION_AHEAD = 10
_DESCRIPTION_BEHIND = 2


def _addon_path():
    return xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')


def _http(value):
    return value if isinstance(value, str) and value.startswith(('https://', 'http://')) else ''


def _progress(info):
    raw = (info or {}).get('percentplayed')
    if raw in (None, ''):
        raw = (info or {}).get('progress') or 0
    try:
        return max(0, min(100, int(raw)))
    except (TypeError, ValueError):
        return 0


def _kind(info):
    return str((info or {}).get('mediatype') or '').casefold()


def _year_label(info):
    year = info.get('year')
    if isinstance(year, int) and not isinstance(year, bool):
        return str(year)
    if isinstance(year, str) and year.strip()[:4].isdigit():
        return year.strip()[:4]
    premiered = info.get('premiered') or info.get('aired') or ''
    if isinstance(premiered, str) and premiered.strip()[:4].isdigit():
        return premiered.strip()[:4]
    return ''


def _runtime_label(value):
    if isinstance(value, str):
        text = ' '.join(value.split())
        if not text or text == '0':
            return ''
        if not fullmatch(r'\d+(\.\d+)?', text):
            return text.upper()
        value = float(text)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ''
    number = int(value)
    if number <= 0:
        return ''
    minutes = number // 60 if number >= 180 else number
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return '%dH %dM' % (hours, mins)
    if hours:
        return '%dH' % hours
    return '%dM' % mins


def _rating_label(info):
    mpaa = info.get('mpaa')
    if isinstance(mpaa, str) and mpaa.strip():
        return ' '.join(mpaa.upper().split())
    rating = info.get('rating')
    if isinstance(rating, bool) or rating in (None, ''):
        return ''
    if isinstance(rating, (int, float)):
        return '%g' % rating
    if isinstance(rating, str) and rating.strip():
        return ' '.join(rating.upper().split())
    return ''


def _genre_label(info):
    genre = info.get('genre')
    if genre in (None, ''):
        genre = info.get('genres')
    names = []
    if isinstance(genre, str):
        names = [part.strip() for part in genre.replace('·', ',').split(',') if part.strip()]
    elif isinstance(genre, (list, tuple)):
        for entry in genre:
            text = ' '.join(str(entry).split())
            if text:
                names.append(text)
    cleaned = [' '.join(name.upper().split()) for name in names if name]
    if not cleaned:
        return ''
    return '  ·  '.join(cleaned[:3])


def _plain(text):
    return ' '.join((text or '').split())


def _count_label(value):
    if isinstance(value, bool) or value in (None, ''):
        return ''
    if isinstance(value, (int, float)) and value > 0:
        return str(int(value))
    if isinstance(value, str) and value.strip().isdigit() and int(value.strip()) > 0:
        return str(int(value.strip()))
    return ''


def _meta(row):
    """Year, runtime, rating and genre — or season info. Blank when unknown."""
    info = row.get('info') or {}
    kind = _kind(info)
    bits = []
    season = info.get('season')
    episode = info.get('episode')
    if kind == 'episode' and (season or episode):
        bits.append('S%sE%s' % (str(season or 0).zfill(2), str(episode or 0).zfill(2)))
    elif kind == 'season' and season:
        bits.append('SEASON %s' % season)
    year = _year_label(info)
    if year:
        bits.append(year)
    seasons = _count_label(info.get('seasons') or info.get('seasoncount'))
    if kind in ('tvshow', 'show', 'series') and seasons:
        bits.append('%s %s' % (seasons, 'SEASON' if seasons == '1' else 'SEASONS'))
    else:
        runtime = _runtime_label(info.get('duration') if info.get('duration') not in (None, '') else info.get('runtime'))
        if runtime:
            bits.append(runtime)
    rating = _rating_label(info)
    if rating:
        bits.append(rating)
    genre = _genre_label(info)
    if genre:
        bits.append(genre)
    return '  ·  '.join(bits)


def _detail_meta(row):
    return _meta(row)


def _browse_path(pluginid, url):
    marker = pluginid + 'pv/browse/'
    if isinstance(url, str) and url.startswith(marker):
        return url.split('pv/browse/', 1)[1]
    return None


_SECTION_PATHS = {'root', '__prime_home__', '__prime_movies__', '__prime_tv__'}
_DETAIL_KEYS = ('plot', 'year', 'duration', 'runtime', 'mpaa', 'rating', 'genre', 'genres',
                'seasons', 'season', 'episode', 'premiered', 'mediatype', 'fanart', 'thumb', 'poster')


def _detail_path(pluginid, row):
    path = _browse_path(pluginid, (row or {}).get('url'))
    if not path or path in _SECTION_PATHS or path.startswith(('__prime_page__/', '__legacy')):
        return None
    return path


def _needs_detail(row):
    info = (row or {}).get('info') or {}
    return not (_plain(info.get('plot') or '') and _meta(row))


def _richer(rows):
    """Prefer a present value, and the longer synopsis when several rows have one."""
    info = {}
    for row in rows or []:
        current = row.get('info') or {}
        for key in _DETAIL_KEYS:
            value = current.get(key)
            if value in (None, '', [], {}):
                continue
            if key == 'plot' and len(_plain(str(value))) <= len(_plain(str(info.get('plot') or ''))):
                continue
            if key == 'plot' or not info.get(key):
                info[key] = value
    return info


def _is_season_row(row):
    info = (row or {}).get('info') or {}
    if _kind(info) == 'season':
        return True
    season = info.get('season')
    return season not in (None, '', 0, '0') and _kind(info) != 'episode'


def _season_number(row):
    info = (row or {}).get('info') or {}
    try:
        return int(info.get('season') or 0)
    except (TypeError, ValueError):
        return 1 << 20


def _season_folders(pluginid, rows):
    folders = []
    for row in rows or []:
        if playback_row(row):
            continue
        path = _browse_path(pluginid, row.get('url'))
        if path:
            folders.append((path, row))
    if not folders:
        return []
    if len(folders) == 1 or all(_is_season_row(row) for _path, row in folders):
        folders.sort(key=lambda item: _season_number(item[1]))
        return folders
    return []


def _fit_art(url, width):
    """Request a cacheable width from Amazon's image host. Other URLs pass through."""
    url = _http(url)
    if not url:
        return ''
    base, _, query = url.partition('?')
    match = _AMAZON_IMAGE.match(base)
    if not match:
        return url
    sized = '%s._SX%d_.%s' % (match.group(1), int(width), match.group(2).lstrip('.'))
    return sized + ('?' + query if query else '')


def _play_url(url):
    return isinstance(url, str) and 'mode=PlayVideo' in url


def _nested_playable(value, row):
    if isinstance(value, str):
        if not _play_url(value):
            return None
        return {'title': row.get('title') or 'Smooth Prime', 'url': value, 'video': True,
                'info': dict(row.get('info') or {})}
    if isinstance(value, dict) and value.get('video', True) and _play_url(value.get('url')):
        return value
    return None


def _episode_order(row):
    info = row.get('info') or {}

    def number(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1 << 20

    return (number(info.get('season')), number(info.get('episode')), row.get('title') or '')


def choose_playback(rows):
    """The one PlayVideo row a detail page can start, and why.

    A movie is its feature. A series prefers the episode already in progress,
    otherwise the earliest season and episode. No row means there is no single
    playable episode and the caller may show the list.
    """
    found, seen = [], set()
    for row in rows or []:
        target = playback_row(row)
        if not target:
            continue
        url = target.get('url') or ''
        if not _play_url(url) or url in seen:
            continue
        seen.add(url)
        found.append(target)
    episodes = [row for row in found if _kind(row.get('info')) == 'episode']
    if episodes:
        resumed = [row for row in episodes if _progress(row.get('info')) > 0]
        if resumed:
            resumed.sort(key=lambda row: (_progress(row.get('info')), _episode_order(row)))
            return resumed[-1], 'resume'
        episodes.sort(key=_episode_order)
        return episodes[0], 'first'
    if len(found) == 1:
        kind = _kind(found[0].get('info'))
        return found[0], 'movie' if kind in ('movie', 'video', '') else 'only'
    if found:
        return found[0], 'only'
    return None, 'picker'


def playback_row(row):
    """The PlayVideo row a card click should start, or None.

    A movie or episode card already carries mode=PlayVideo. A series card
    plays its next episode, or the first episode, when that playable row is
    already on the card. A series with only a detail link has no single
    playable item here.
    """
    if not isinstance(row, dict):
        return None
    if _play_url(row.get('url')):
        return row
    info = row.get('info') if isinstance(row.get('info'), dict) else {}
    for key in ('next', 'next_episode', 'resume'):
        found = _nested_playable(info.get(key), row)
        if found:
            return found
    episodes = info.get('episodes')
    if isinstance(episodes, (list, tuple)):
        for episode in episodes:
            found = _nested_playable(episode, row)
            if found:
                return found
    return None


def _item(row, title_art='', image_cache=None):
    info = row.get('info') or {}
    title = (row.get('title') or 'Untitled').upper()
    item = xbmcgui.ListItem(label=title)
    thumb = _fit_art(info.get('thumb') or info.get('poster') or info.get('fanart'), _CARD_ART)
    poster = _fit_art(info.get('poster') or info.get('thumb'), _CARD_ART)
    fanart = _fit_art(info.get('fanart'), _HERO_ART)
    art = {}
    if thumb:
        art['thumb'] = thumb
        art['landscape'] = thumb
    if poster:
        art['poster'] = poster
        art['icon'] = poster
    if fanart:
        art['fanart'] = fanart
    if image_cache is not None:
        art = {key: image_cache.local(value) for key, value in art.items()}
    if art:
        item.setArt(art)
    meta = _meta(row)
    plot = _plain(info.get('plot') or '')
    item.setProperty('Meta', meta)
    item.setProperty('Watched', '%s%% WATCHED' % _progress(info) if _progress(info) else meta)
    item.setProperty('Plot', plot)
    item.setProperty('Year', _year_label(info))
    item.setProperty('Runtime', _runtime_label(info.get('duration') if info.get('duration') not in (None, '') else info.get('runtime')))
    item.setProperty('Rating', _rating_label(info))
    item.setProperty('Genre', _genre_label(info))
    item.setProperty('MediaType', _kind(info))
    item.setProperty('Id', str(row.get('url') or ''))
    item.setProperty('Identity', str(row.get('identity') or ''))
    if title_art:
        item.setProperty('TitleArt', title_art)
    try:
        tag = item.getVideoInfoTag()
        progress = _progress(info)
        if progress and hasattr(tag, 'setResumePoint'):
            tag.setResumePoint(progress, 100)
        if title and hasattr(tag, 'setTitle'):
            tag.setTitle(title)
        if plot and hasattr(tag, 'setPlot'):
            tag.setPlot(plot)
        year = _year_label(info)
        if year and hasattr(tag, 'setYear'):
            tag.setYear(int(year))
        if _kind(info) and hasattr(tag, 'setMediaType'):
            tag.setMediaType({'tvshow': 'tvshow', 'movie': 'movie', 'episode': 'episode', 'season': 'season'}.get(_kind(info), _kind(info)))
    except Exception:
        pass
    return item


def _note_path(profile):
    return Path(profile) / _NOTE


def note_playback(profile, title, meta=''):
    """Remember the title the existing player is about to start."""
    if not profile:
        return
    path = _note_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps({
        'title': title or 'Smooth Prime',
        'meta': meta or '',
        'armed': time(),
    }), encoding='utf-8')


def _read_note(profile):
    path = _note_path(profile)
    try:
        with path.open(encoding='utf-8') as handle:
            note = load(handle)
    except (OSError, ValueError):
        return None
    return note if isinstance(note, dict) else None


def _clear_note(profile):
    try:
        _note_path(profile).unlink()
    except OSError:
        pass


def overlay_due(playing, note, now, latched):
    """True while a Prime Scene title we just started is on screen."""
    if not playing or not isinstance(note, dict):
        return False
    if latched:
        return True
    try:
        armed = float(note.get('armed') or 0)
    except (TypeError, ValueError):
        return False
    return 0 <= now - armed <= _NOTE_WINDOW


def play_row(row, profile):
    note_playback(profile, row.get('title') or 'Smooth Prime', _detail_meta(row))
    xbmc.Player().play(row.get('url') or '')


def _clock(seconds):
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, secs)
    return '%d:%02d' % (minutes, secs)


def failure_detail(error):
    """One line for the Kodi dialog. The traceback is logged separately."""
    text = ' '.join(str(error).split())
    name = type(error).__name__
    if not text or text == name:
        return name
    return name + ': ' + text


def _prepare(window, args):
    # Kodi builds the WindowXML from the filename before this Python __init__.
    # Calling the base constructor again passes those arguments a second time.
    window.xml = args[0] if args else getattr(window, 'xml', '')
    if not hasattr(window, 'properties'):
        window.properties = {}
    if not hasattr(window, 'controls'):
        window.controls = {}


class AuroraHome(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        self.g = kwargs.pop('globals_instance')
        self.initialize_provider = kwargs.pop('initialize_provider', None)
        self.account_name = kwargs.pop('account_name', None)
        self.account_action = kwargs.pop('account_action', None)
        self.offer_signin = kwargs.pop('offer_signin', False)
        self.launch_diagnostics = kwargs.pop('launch_diagnostics', None)
        _prepare(self, args)
        self.init_error = None
        self.jobs = Queue()
        self.stop = Event()
        self.lock = RLock()
        self.generation = 0
        self.rows = []
        self.history = []
        self.path = 'root'
        self.query = None
        self.heading = 'Home'
        self.nav = 'home'
        self.next_path = None
        self.pending_path = None
        self.prefetch_at = 0
        self.busy = False
        self.result = None
        self.worker = None
        self.artwork = set()
        self.account_generation = None
        self._ready = False
        self._restore = None
        self.detail_jobs = Queue()
        self.detail_worker = None
        self.bridge = None
        self.bridge_lock = Lock()
        self.detail_cache = {}
        self._play_token = 0
        self._allow_enrich = True
        self.loader = None
        self.image_cache = None
        self.visual_worker = None
        self.items, self.row_keys = [], set()
        self.views, self.known_details, self.title_cache = {}, {}, {}
        self._focus_signature, self._focus_since, self._enriched_focus = None, 0, None
        self._painted_details = set()
        self._navigation_started = monotonic()
        self.description_cache = None
        self.description_attempts = set()
        self._description_window = None
        self.performance = DiagnosticLog(self.g.DATA_PATH, self.g.addon.getAddonInfo('version'),
                                         operation='interface_performance')

    def onInit(self):
        if self._ready:
            return
        self._ready = True
        record(self.g, 'window_initialized')
        if self.launch_diagnostics is not None:
            self.launch_diagnostics({'event': 'window_on_init', 'current_window_id': xbmcgui.getCurrentWindowId()})
        try:
            self._initialize_controls()
        except Exception as error:
            self.init_error = failure_detail(error)
            self.init_error_type = type(error).__name__
            xbmc.log('[Prime Scene] The interface could not initialize\n' + format_exc(), xbmc.LOGERROR)
            record(self.g, 'window_init_failed', error)
            self.stop.set()
            self.close()
            return

    def _initialize_controls(self):
        if self.getCurrentContainerId() != 200:
            xbmc.executebuiltin('Container.SetViewMode(200)', True)
        if self.getCurrentContainerId() != 200:
            raise RuntimeError('Kodi did not activate the Smooth Prime catalog rail')
        self.update_account_button()
        if self.account_name is not None and not self.account_name():
            self.show_signed_out()
            if self.launch_diagnostics is not None:
                self.launch_diagnostics({'event': 'interface_ready', 'signed_in': False,
                                         'current_window_id': xbmcgui.getCurrentWindowId()})
            if self.offer_signin:
                self.offer_signin = False
                if self.g.dialog.yesno('Smooth Prime', 'Sign in to Amazon Prime Video to start watching.',
                                       nolabel='Cancel', yeslabel='Sign in'):
                    self.onClick(306)
            return
        if self.worker is not None:
            return
        if self.account_name is not None:
            from .account_state import account_epoch
            self.account_generation = account_epoch(self.g.DATA_PATH)
        self._set_hero_status('SMOOTH PRIME', 'LOADING YOUR HOME')
        self.worker = Thread(target=self._work, name='PrimeCatalog', daemon=True)
        self.detail_worker = Thread(target=self._detail_loop, name='PrimeDetails', daemon=True)
        self.visual_worker = Thread(target=self._visual_loop, name='PrimeVisuals', daemon=True)
        self.nav = 'home'
        self.navigate('root', remember=False, heading='Home')
        self.setFocusId(200)
        self.worker.start()
        self.detail_worker.start()
        self.visual_worker.start()
        if self.launch_diagnostics is not None:
            self.launch_diagnostics({'event': 'interface_ready', 'signed_in': True,
                                     'current_window_id': xbmcgui.getCurrentWindowId()})

    def update_account_button(self):
        name = self.account_name() if self.account_name is not None else ''
        self.setProperty('AccountLabel', name or 'Sign in')

    def show_signed_out(self):
        with self.lock:
            self.generation += 1
            self.rows = []
            self.history.clear()
            self.artwork.clear()
            self.views.clear()
            self.items, self.row_keys = [], set()
            self.title_cache.clear()
            self.known_details.clear()
            self.description_attempts.clear()
            self.description_cache = None
            self.detail_cache.clear()
            self.path, self.query, self.next_path = 'root', None, None
            self.busy = False
            self.nav = ''
        self.clearList()
        self.setProperty('Nav', '')
        self.setProperty('Section', '')
        self.setProperty('AccountLabel', 'Sign in')
        self._set_hero_status('SIGN IN', 'SIGN IN TO PRIME VIDEO TO BROWSE AND WATCH.')
        self.setFocusId(306)

    def _set_hero_status(self, title, tag):
        self.setProperty('HeroKicker', '')
        self.setProperty('HeroTitle', title)
        self.setProperty('HeroMeta', tag)
        self.setProperty('HeroTag', tag)
        self.setProperty('HeroSynopsis', '')
        self.setProperty('HeroFanart', '')
        self.setProperty('HeroTitleArt', self.title_cache.get(title.upper(), ''))

    def pause_catalog(self, status='Signing out...'):
        self.stop.set()
        self.setProperty('AccountLabel', status)
        self.setProperty('HeroTag', ' '.join(status.upper().split()))
        self.setProperty('HeroMeta', ' '.join(status.upper().split()))
        with self.lock:
            self.generation += 1
            self.rows = []
            self.history.clear()
            self.next_path = None
            self.views.clear()
            self.items, self.row_keys = [], set()
            self.title_cache.clear()
            self.known_details.clear()
        self.clearList()
        for worker in (self.worker, self.detail_worker, self.visual_worker):
            if worker is not None:
                while worker.is_alive():
                    worker.join(0.1)
        self.worker = None
        self.detail_worker = None
        self.visual_worker = None
        self.bridge = None
        self.jobs = Queue()
        self.detail_jobs = Queue()
        self.detail_cache = {}
        self._painted_details.clear()
        self.title_cache.clear()
        self.known_details.clear()
        self.artwork.clear()
        self.views.clear()
        self.description_cache = None
        self.description_attempts.clear()
        self._description_window = None

    def _save_view(self):
        if self.rows:
            self.views[(self.path, self.query)] = {'rows': list(self.rows), 'items': list(self.items),
                'next': self.next_path or self.pending_path, 'prefetch_at': self.prefetch_at, 'selected': self._selected_index()}
            if len(self.views) > 12:
                self.views.pop(next(iter(self.views)))

    def navigate(self, path, remember=True, query=None, selection=0, heading=None):
        if self.account_name is not None and not self.account_name():
            self.show_signed_out()
            return
        with self.lock:
            if (path, query) == (self.path, self.query) and self.rows:
                self.setProperty('Nav', self.nav if self.nav in ('home', 'movies', 'tv') else '')
                return
            self._save_view()
            if remember and (path, query) != (self.path, self.query):
                previous_nav = {'root': 'home', '__prime_home__': 'home', '__prime_movies__': 'movies',
                                '__prime_tv__': 'tv'}.get(self.path, self.nav)
                self.history.append((self.path, self.query, self.heading, previous_nav, self._selected_index()))
            self.path = path
            self.query = query
            self.heading = heading or 'Home'
            self.generation += 1
            self.rows = []
            self.items, self.row_keys = [], set()
            self.next_path = None
            self.pending_path = None
            self.prefetch_at = 0
            self.busy = True
            self._restore = selection or None
            token = self.generation
            self._navigation_started = monotonic()
            cached = self.views.get((path, query))
        self.performance({'event': 'navigation_started', 'generation': token,
                          'section': self.nav if self.nav in ('home', 'movies', 'tv') else 'detail_or_search',
                          'cached_view': bool(cached)})
        self.setProperty('Nav', self.nav if self.nav in ('home', 'movies', 'tv') else '')
        self.setProperty('Section', (self.heading or 'Home').upper())
        self.clearList()
        if cached:
            with self.lock:
                self.rows, self.items = list(cached['rows']), list(cached['items'])
                self.row_keys = {row.get('url') for row in self.rows}
                self.next_path, self.prefetch_at = cached['next'], cached['prefetch_at']
                self.busy = False
                self.addItems(self.items)
                self.setCurrentListPosition(selection or cached['selected'])
            self.performance({'event': 'navigation_restored', 'source': 'view_memory', 'cards': len(self.rows),
                              'generation': token,
                              'elapsed_ms': round((monotonic() - self._navigation_started) * 1000), 'result': 'success'})
            return
        self._set_hero_status((self.heading or 'Smooth Prime').upper(), 'LOADING TITLES')
        # Drop queued obsolete work. In-flight public requests continue only to
        # warm their cache; the worker stops waiting when generation changes.
        while True:
            try:
                self.jobs.get_nowait()
            except Empty:
                break
        self.jobs.put((token, path, query, selection))

    def _selected_index(self):
        position = self.getCurrentListPosition()
        if position is None or position < 0:
            position = 0
        return position

    def _note_artwork(self, info):
        art = {key: info[key] for key in ('thumb', 'poster', 'fanart') if info.get(key)}
        used = set(art.values())
        used.update(_fit_art(value, width) for value in art.values() for width in (_CARD_ART, _HERO_ART))
        fresh = {value for value in used if isinstance(value, str) and value.startswith('https://')} - self.artwork
        if not fresh or self.account_name is None:
            return set()
        self.artwork.update(fresh)
        return fresh

    def _publish(self, token, packet):
        started = monotonic()
        if self.account_generation is not None:
            from .account_state import current_account
            if not current_account(self.g.DATA_PATH, self.account_generation):
                return
        with self.lock:
            if self.stop.is_set() or token != self.generation:
                return
            staged = []
            seen = set(self.row_keys)
            for row in packet.get('rows') or []:
                key = row.get('url')
                if key in seen:
                    continue
                seen.add(key)
                info = dict(row.get('info') or {})
                staged.append(dict(row, info=info))
        # Publish cheap card objects immediately. The visual worker renders only
        # the focused/nearby title plates after these cards are already usable.
        fresh_artwork = set()
        for row in staged:
            fresh_artwork.update(self._note_artwork(row['info']))
        if fresh_artwork:
            try:
                with (Path(self.g.DATA_PATH) / 'account-artwork.jsonl').open('a', encoding='utf-8') as output:
                    output.write(''.join(dumps(value) + '\n' for value in fresh_artwork))
            except OSError as error:
                self.performance({'event': 'artwork_tracking_failed', 'exception': exception_details(error), 'result': 'failure'})
        additions = [_item(row, self.title_cache.get((row.get('title') or '').upper(), ''),
                           self.image_cache) for row in staged]
        with self.lock:
            if self.stop.is_set() or token != self.generation:
                return
            had = bool(self.rows)
            if additions:
                self.addItems(additions)
            self.rows.extend(staged)
            self.items.extend(additions)
            self.row_keys.update(row.get('url') for row in staged)
            for row in staged:
                if row.get('identity') and row.get('detail_url'):
                    self.known_details[row['identity']] = {'title': row['title'], 'lazyLoadURL': row['detail_url']}
            self.next_path = packet.get('next')
            count = len(self.rows)
            restore = self._restore
        if additions and restore is not None and restore < count:
            self.setCurrentListPosition(restore)
            with self.lock:
                if self._restore == restore:
                    self._restore = None
        if additions:
            self.setProperty('Section', (self.heading or 'Home').upper())
            self.performance({'event': 'cards_published', 'added': len(additions), 'total': count,
                'generation': token,
                'build_and_bind_ms': round((monotonic() - started) * 1000),
                'since_navigation_ms': round((monotonic() - self._navigation_started) * 1000), 'result': 'success'})

    def _work(self):
        try:
            # The landing dashboard does not need the playback provider. Movies,
            # TV and legacy sections ask the bridge for it when they are opened.
            bridge = ProviderBridge(None, self.g, initialize_provider=self.initialize_provider)
            self.bridge = bridge
            self.loader = StorefrontLoader(self.g)
            self.image_cache = ImageCache(self.g, self.loader.epoch)
            self.description_cache = DescriptionCache(self.g, self.loader.epoch, self.performance)
            self.loader.warm()
            record(self.g, 'catalog_worker_ready')
            self._catalog_loop(bridge)
        except Exception as error:
            record(self.g, 'provider_init_failed', error)
            if not self.stop.is_set():
                self._set_hero_status('SMOOTH PRIME', 'COULD NOT OPEN THE CATALOG. YOUR SIGN-IN IS SAVED.')
            self.busy = False
            self.stop.set()
        finally:
            self.stop.set()
            if self.loader is not None:
                self.loader.close()
            if self.image_cache is not None:
                self.image_cache.close()

    def _catalog_loop(self, bridge):
        monitor = xbmc.Monitor()
        while not self.stop.is_set() and not monitor.abortRequested():
            try:
                token, path, query, _selection = self.jobs.get(timeout=0.15)
            except Empty:
                self._prefetch()
                continue
            if token != self.generation:
                continue
            page_start = len(self.rows)
            started = monotonic()
            try:
                record(self.g, 'catalog_request_started')
                if query is None and self.loader.handles(path):
                    cached = self.loader.peek(path)
                    if cached:
                        self._publish(token, cached)
                    future = self.loader.request(path)
                    while not future.done() and token == self.generation and not self.stop.is_set():
                        self.stop.wait(0.025)
                    if token != self.generation or self.stop.is_set():
                        self.performance({'event': 'navigation_wait_cancelled',
                            'generation': token,
                            'elapsed_ms': round((monotonic() - started) * 1000), 'result': 'cancelled'})
                        continue
                    packet = future.result()
                    if packet:
                        self._publish(token, packet)
                else:
                    with self.bridge_lock:
                        if token != self.generation or self.stop.is_set():
                            continue
                        bridge.browse_progressive(
                            path, lambda packet: self._publish(token, packet),
                            lambda: token == self.generation and not self.stop.is_set(), query=query)
                record(self.g, 'catalog_request_finished')
                with self.lock:
                    if token == self.generation and not self.stop.is_set():
                        if path.endswith('/nextPage') and len(self.rows) > page_start:
                            self.prefetch_at = page_start
                        if not self.rows:
                            self._set_hero_status((self.heading or 'Smooth Prime').upper(),
                                                  'NO TITLES WERE RETURNED. TRY HOME OR SEARCH.')
            except BaseException as error:
                record(self.g, 'catalog_request_failed', error)
                xbmc.log('[Prime Scene] Catalog operation failed: ' + type(error).__name__, xbmc.LOGERROR)
                with self.lock:
                    if token == self.generation and not self.stop.is_set():
                        message = ('USE THE ACCOUNT BUTTON AND CHOOSE SIGN IN AGAIN.'
                                   if isinstance(error, CatalogSigninRequired) else
                                   'UNABLE TO FINISH LOADING. RETURN HOME OR OPEN DIAGNOSTICS.')
                        self._set_hero_status((self.heading or 'Smooth Prime').upper(), message)
            finally:
                with self.lock:
                    if token == self.generation:
                        self.busy = False
                        self.pending_path = None
                        self._prefetch()

    def _prefetch(self):
        with self.lock:
            if self.busy or not self.next_path or self.stop.is_set():
                return
            if self._selected_index() >= self.prefetch_at:
                path = self.next_path
                self.pending_path = path
                self.next_path = None
                self.busy = True
                self.jobs.put((self.generation, path, None, 0))

    def _selected_row(self):
        with self.lock:
            index = self._selected_index()
            if not 0 <= index < len(self.rows):
                return None
            return dict(self.rows[index])

    def _open_browse(self, row):
        path = _browse_path(self.g.pluginid, row.get('url'))
        if path:
            self.navigate(path, heading=row.get('title') or 'Smooth Prime')
            return True
        if '?mode=Search' in (row.get('url') or ''):
            self._search()
            return True
        return False

    def _visual_loop(self):
        """Keep expensive title drawing and image downloads away from input/binding."""
        while not self.stop.is_set():
            with self.lock:
                token = self.generation
                index = self._selected_index() if self.rows else 0
                nearby = [(position, dict(self.rows[position]), self.items[position])
                          for position in range(index, min(index + 10, len(self.rows)))]
            if not nearby:
                self.stop.wait(0.1)
                continue
            try:
                urls = []
                for offset, (_position, row, _item_ref) in enumerate(nearby):
                    info = row.get('info') or {}
                    urls.append(_fit_art(info.get('thumb') or info.get('poster') or info.get('fanart'), _CARD_ART))
                    if offset < 3:
                        urls.append(_fit_art(info.get('fanart'), _HERO_ART))
                if self.image_cache is not None:
                    self.image_cache.warm([url for url in urls if url])
                for offset, (position, row, item) in enumerate(nearby):
                    if self.stop.is_set() or token != self.generation:
                        break
                    title = (row.get('title') or 'Untitled').upper()
                    if offset < 3 and title not in self.title_cache:
                        started = monotonic()
                        plate = render_title(title, Path(self.g.DATA_PATH) / 'title-art')
                        if self.stop.is_set():
                            break
                        self.title_cache[title] = plate
                        self.performance({'event': 'title_art_ready', 'index': position,
                            'elapsed_ms': round((monotonic() - started) * 1000), 'result': 'success'})
                    if title in self.title_cache and item.getProperty('TitleArt') != self.title_cache[title]:
                        item.setProperty('TitleArt', self.title_cache[title])
                    if self.image_cache is None:
                        continue
                    info = row.get('info') or {}
                    thumb = _fit_art(info.get('thumb') or info.get('poster') or info.get('fanart'), _CARD_ART)
                    local = self.image_cache.local(thumb)
                    if local and local != thumb and item.getProperty('LocalThumb') != local:
                        item.setArt({'thumb': local, 'landscape': local})
                        item.setProperty('LocalThumb', local)
                    hero = _fit_art(info.get('fanart'), _HERO_ART)
                    local = self.image_cache.local(hero)
                    if local and local != hero and item.getProperty('LocalHero') != local:
                        item.setArt({'fanart': local})
                        item.setProperty('LocalHero', local)
            except Exception as error:
                self.performance({'event': 'visual_work_failed', 'exception': exception_details(error), 'result': 'failure'})
                self.stop.wait(0.5)
            self.stop.wait(0.1)

    def _detail_loop(self):
        while not self.stop.is_set():
            try:
                job = self.detail_jobs.get(timeout=0.05)
            except Empty:
                if self._allow_enrich and not self.stop.is_set():
                    self._queue_enrich()
                continue
            if self.stop.is_set() or not job:
                return
            kind = job[0]
            if kind == 'enrich':
                if job[2] == self.generation and _detail_path(self.g.pluginid, self._selected_row()) == job[1]:
                    self._enrich(job[1])
            elif kind == 'play':
                self._play_resolved(job[1], job[2], job[3], job[4])

    def _queue_enrich(self):
        """Read the focused title and next ten descriptions before selection."""
        if self.bridge is None or self.stop.is_set():
            return
        with self.lock:
            selected = self._selected_index()
            generation = self.generation
            order = (list(range(selected, min(selected + _DESCRIPTION_AHEAD + 1, len(self.rows))))
                     + list(range(max(0, selected - _DESCRIPTION_BEHIND), selected)))
            candidates = [(index, dict(self.rows[index])) for index in order]
        window = (generation, selected, len(candidates))
        if window != self._description_window:
            self._description_window = window
            self.performance({'event': 'description_window_started', 'stage': 'read_ahead',
                              'generation': generation, 'selected_index': selected,
                              'candidate_count': len(candidates), 'ahead': _DESCRIPTION_AHEAD})
        for index, row in candidates:
            if self.stop.is_set() or generation != self.generation:
                return
            path = _detail_path(self.g.pluginid, row)
            if not path or _plain((row.get('info') or {}).get('plot') or ''):
                continue
            cached = self.detail_cache.get(path)
            if cached and _plain((cached.get('info') or {}).get('plot') or ''):
                if (generation, path) not in self._painted_details:
                    self._paint_cached(path)
                continue
            if path in self.description_attempts:
                continue
            if not self.detail_jobs.empty():
                return
            self.description_attempts.add(path)
            saved = self.description_cache.load(path) if self.description_cache is not None else None
            if saved:
                if cached is None:
                    cached = {'playable': None, 'how': 'metadata', 'info': saved}
                    self.detail_cache[path] = cached
                else:
                    cached.setdefault('info', {}).update(saved)
                self._paint_cached(path)
                continue
            started = monotonic()
            self.performance({'event': 'description_prefetch_started', 'stage': 'detail_metadata',
                              'generation': generation, 'index': index, 'offset': index - selected})
            try:
                self._enrich(path, description=True)
                info = (self.detail_cache.get(path) or {}).get('info') or {}
                available = bool(_plain(info.get('plot') or ''))
                self.performance({'event': 'description_prefetch_completed', 'stage': 'detail_metadata',
                    'generation': generation, 'index': index, 'description_present': available,
                    'elapsed_ms': round((monotonic() - started) * 1000),
                    'result': 'cancelled' if self.stop.is_set() else 'success' if available else 'unavailable'})
            except Exception as error:
                self.performance({'event': 'description_prefetch_failed', 'stage': 'detail_metadata',
                    'generation': generation, 'index': index,
                    'elapsed_ms': round((monotonic() - started) * 1000),
                    'exception': exception_details(error), 'result': 'failure'})
            # Check explicit Play jobs between requests; there is never a queue
            # of ten old hover lookups in front of a user's action.
            return
        self._queue_focus_metadata()

    def _queue_focus_metadata(self):
        row = self._selected_row()
        path = _detail_path(self.g.pluginid, row)
        signature = (self.generation, path)
        if signature != self._focus_signature:
            self._focus_signature, self._focus_since = signature, monotonic()
            return
        if not path or not _needs_detail(row):
            return
        if monotonic() - self._focus_since < 0.45 or signature == self._enriched_focus:
            return
        cached = self.detail_cache.get(path)
        if cached and cached.get('how') != 'error':
            if signature not in self._painted_details:
                self._paint_cached(path)
                self._painted_details.add(signature)
            self._enriched_focus = signature
            return
        if cached and cached.get('how') == 'error':
            return
        self._enriched_focus = signature
        if self.detail_jobs.empty():
            self._enrich(path)

    def _enrich(self, path, description=False):
        if self.stop.is_set():
            return
        cached = self.detail_cache.get(path)
        if cached and cached.get('how') != 'error' and (not description or (cached.get('info') or {}).get('plot')):
            self._paint_cached(path)
            return
        packet = self._fetch_detail(path)
        if packet == 'wait' or self.stop.is_set():
            return
        if packet is None:
            self.detail_cache[path] = {'playable': None, 'how': 'error', 'info': {}}
            return
        playable, how = choose_playback(packet.get('rows'))
        self._remember(path, playable, how if playable is not None else 'metadata', packet)

    def _fetch_detail(self, path):
        bridge = self.bridge
        if bridge is None or self.stop.is_set():
            return 'wait'
        packet = {'rows': []}

        def publish(part):
            if part:
                packet['rows'].extend(part.get('rows') or [])

        try:
            with self.bridge_lock:
                if self.stop.is_set():
                    return 'wait'
                with self.lock:
                    bridge.details.update(self.known_details)
                bridge.browse_progressive(path, publish, lambda: not self.stop.is_set())
        except Exception as error:
            xbmc.log('[Prime Scene] Detail lookup failed: ' + type(error).__name__, xbmc.LOGERROR)
            return None
        return packet

    def _resolve_playable(self, path, depth=0):
        packet = self._fetch_detail(path)
        if packet == 'wait':
            return None, 'wait', None
        if packet is None:
            return None, 'error', None
        rows = packet.get('rows') or []
        playable, how = choose_playback(rows)
        if playable is not None or depth >= 1:
            return playable, how if playable is not None else 'picker', packet
        folders = _season_folders(self.g.pluginid, rows)
        if not folders:
            return None, 'picker' if rows else 'empty', packet
        child = folders[0][0]
        if not child or child == path:
            return None, 'picker', packet
        nested, nested_how, nested_packet = self._resolve_playable(child, depth + 1)
        merged = {'rows': rows + ((nested_packet or {}).get('rows') or [])}
        if nested is not None:
            label = 'first' if _kind(nested.get('info')) == 'episode' else nested_how
            return nested, label, merged
        return None, 'picker', packet

    def _remember(self, path, playable, how, packet):
        if self.account_generation is not None:
            from .account_state import current_account
            if not current_account(self.g.DATA_PATH, self.account_generation):
                return
        info = _richer((packet or {}).get('rows'))
        self.detail_cache[path] = {'playable': playable, 'how': how, 'info': info}
        if self.description_cache is not None:
            self.description_cache.save(path, info)
        xbmc.log('[Prime Scene] Playback choice for %s: %s' % (path, how), xbmc.LOGINFO)
        self._paint_cached(path)

    def _paint_cached(self, path):
        cached = self.detail_cache.get(path)
        if not cached:
            return
        info = cached.get('info') or {}
        if not info:
            return
        self._painted_details.add((self.generation, path))
        with self.lock:
            matches = [(index, dict(row)) for index, row in enumerate(self.rows)
                       if _detail_path(self.g.pluginid, row) == path]
        for index, row in matches:
            current = dict(row.get('info') or {})
            for key, value in info.items():
                if value not in (None, '', [], {}) and (current.get(key) in (None, '', [], {})
                        or key == 'plot' and len(_plain(str(value))) > len(_plain(str(current.get(key) or '')))):
                    current[key] = value
            with self.lock:
                if index < len(self.rows) and _detail_path(self.g.pluginid, self.rows[index]) == path:
                    self.rows[index]['info'] = dict(current)
                    item = self.items[index]
                else:
                    continue
            painted = {'title': row.get('title'), 'info': current}
            item.setProperty('Meta', _meta(painted))
            item.setProperty('Plot', _plain(current.get('plot') or ''))
            item.setProperty('Year', _year_label(current))
            runtime = current.get('duration') if current.get('duration') not in (None, '') else current.get('runtime')
            item.setProperty('Runtime', _runtime_label(runtime))
            item.setProperty('Rating', _rating_label(current))
            item.setProperty('Genre', _genre_label(current))
            art = {}
            fanart = _fit_art(current.get('fanart'), _HERO_ART)
            thumb = _fit_art(current.get('thumb') or current.get('poster'), _CARD_ART)
            if fanart:
                art['fanart'] = fanart
            if thumb:
                art['thumb'] = thumb
            if art and hasattr(item, 'setArt'):
                item.setArt(art)

    def _play_resolved(self, token, generation, path, title):
        try:
            cached = self.detail_cache.get(path)
            if cached is None or cached.get('how') in ('error', 'wait', 'metadata'):
                playable, how, packet = self._resolve_playable(path)
                if how == 'wait':
                    self.g.dialog.ok('Smooth Prime', 'The catalogue is still opening. Try this title again.')
                    return
                if packet is not None:
                    self._remember(path, playable, how, packet)
                    cached = self.detail_cache.get(path)
                else:
                    cached = {'playable': None, 'how': 'error', 'info': {}}
                    self.detail_cache[path] = cached
            if token != self._play_token or generation != self.generation or self.stop.is_set():
                return
            playable = (cached or {}).get('playable')
            how = (cached or {}).get('how')
            if playable is not None:
                xbmc.log('[Prime Scene] Playing %s (%s) without a detail page' % (path, how), xbmc.LOGINFO)
                play_row(playable, self.g.DATA_PATH)
                return
            if how == 'picker':
                self.navigate(path, heading=title or 'Smooth Prime')
                return
            self.g.dialog.ok('Smooth Prime', 'This title did not return a video to play.')
        finally:
            if token == self._play_token:
                self.setProperty('Busy', '')
                xbmc.executebuiltin('Dialog.Close(busydialognocancel)')

    def _search(self):
        phrase = self.g.dialog.input('Search Prime Video')
        if not isinstance(phrase, str) or not phrase.strip():
            return
        self.nav = ''
        self.navigate('search', query=phrase.strip(), heading='Search')

    def onClick(self, control_id):
        if control_id == 301:
            self.nav = 'home'
            self.history.clear()
            self.navigate('root', remember=False, heading='Home')
        elif control_id == 302:
            self.nav = 'movies'
            self.navigate('__prime_movies__', heading='Movies')
        elif control_id == 303:
            self.nav = 'tv'
            self.navigate('__prime_tv__', heading='TV')
        elif control_id == 304:
            self._search()
        elif control_id == 305:
            self.g.addon.openSettings()
            self.update_account_button()
            if self.account_name is not None and not self.account_name():
                self.pause_catalog()
                self.stop = Event()
                self.show_signed_out()
            elif self.account_generation is not None:
                from .account_state import current_account
                if not current_account(self.g.DATA_PATH, self.account_generation):
                    self.pause_catalog()
                    self.stop = Event()
                    self._initialize_controls()
        elif control_id == 306 and self.account_action is not None:
            changed = False
            try:
                changed = self.account_action(self.pause_catalog)
            except Exception as error:
                self.g.dialog.ok('Smooth Prime', str(error) if isinstance(error, RuntimeError)
                                 else 'Account controls could not finish (' + type(error).__name__ + ').')
            finally:
                self.stop = Event()
                self.update_account_button()
                if self.account_name and self.account_name() and (changed or self.worker is None):
                    self._initialize_controls()
                elif self.account_name is not None and not self.account_name():
                    self.show_signed_out()
        elif control_id == 200:
            row = self._selected_row()
            if not row:
                return
            target = playback_row(row)
            path = _detail_path(self.g.pluginid, row)
            if target is None and path:
                cached = self.detail_cache.get(path)
                if cached and cached.get('playable') is not None:
                    target = cached['playable']
            if target is not None:
                play_row(target, self.g.DATA_PATH)
                return
            if not path:
                self.g.dialog.ok('Smooth Prime', 'This menu item is unavailable here. Open Settings for account options.')
                return
            # The rail card has no ASIN. Resolve it away from this click and
            # hand the result to PlayVideo. The listing stays where it is.
            self._play_token += 1
            token = self._play_token
            self.setProperty('Busy', '1')
            xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
            self.detail_jobs.put(('play', token, self.generation, path, row.get('title') or 'Smooth Prime'))

    def onAction(self, action):
        # Left and right belong to the list. The hero reads Container(200),
        # so updating it from here only delays the move.
        action_id = action.getId()
        if action_id not in _BACK or self.stop.is_set():
            return
        if self.path != 'root' and self.history:
            record(self.g, 'catalog_back')
            path, query, heading, nav, selected = self.history.pop()
            self.nav = nav
            self.navigate(path, remember=False, query=query, heading=heading, selection=selected)
        else:
            self.stop.set()
            record(self.g, 'window_exit_to_kodi_home')
            self.close()


_SEEK_STEPS = {1: -10, 2: 10, 20: 10, 21: -10, 22: 30, 23: -30, 16: 30, 17: -30, 77: 10, 78: -10}
_PAUSE_ACTIONS = {12, 79, 229}
_INFO_ACTIONS = {11, 18, 24, 163}
_SKIN_OSDS = ('videoosd', 'seekbar', 'videobookmarks', 'playerprocessinfo')
_CHROME_SECONDS = 5


def _playing(player):
    try:
        return bool(player.isPlaying())
    except Exception:
        return False


def _player_paused():
    try:
        return bool(xbmc.getCondVisibility('Player.Paused'))
    except Exception:
        return False


class PlayerOverlay(xbmcgui.WindowXMLDialog):
    """One set of playback controls for the whole Prime Scene title.

    The dialog stays open from the start of playback until it ends. Back hides
    the chrome the first time and stops the second time, so Estuary's VideoOSD
    never becomes the control scheme. Pause, OK, info and seek bring the same
    chrome back. automatic is off in the headless test, which calls maintain().
    """

    automatic = True

    def __init__(self, *args, **kwargs):
        _prepare(self, args)
        self.info = {}
        self.player = None
        self._subs = []
        self._audios = []
        self._chrome_at = 0
        self._halt = Event()
        self._pump = None
        self._ready_player = False

    def onInit(self):
        if self._ready_player:
            return
        self._ready_player = True
        info = self.info or {}
        self.setProperty('Title', (info.get('title') or 'SMOOTH PRIME').upper())
        self.setProperty('Meta', info.get('meta') or '')
        self.apply_times(info.get('position') or 0, info.get('duration') or 0)
        volume = self.getControl(520)
        if hasattr(volume, 'setPercent'):
            volume.setPercent(int(info.get('volume') or 40))
        self.reveal()
        if self.automatic and self._pump is None:
            self._pump = Thread(target=self._run, name='AuroraPlayer', daemon=True)
            self._pump.start()

    def _run(self):
        while not self._halt.is_set():
            self.maintain()
            if self._halt.wait(0.2):
                return

    def close(self):
        self._halt.set()
        pump = self._pump
        self._pump = None
        if pump is not None and pump is not current_thread() and pump.is_alive():
            pump.join(1)
        super(PlayerOverlay, self).close()

    def reveal(self):
        self._chrome_at = time()
        self.setProperty('Chrome', '1')
        try:
            self.setFocusId(501)
        except Exception:
            pass

    def hide_chrome(self):
        self.setProperty('Chrome', '')

    def onAction(self, action):
        aid = action.getId()
        hidden = self.getProperty('Chrome') != '1'
        if aid in _BACK:
            if hidden:
                self._stop_playback()
            else:
                self.hide_chrome()
            return
        if aid == 13:
            self._stop_playback()
            return
        if aid == 25:
            self.reveal()
            self._choose_subtitles()
            return
        delta = _SEEK_STEPS.get(aid)
        if delta is not None and (hidden or aid not in (1, 2)):
            self._seek(delta)
            self.reveal()
            return
        if hidden and aid in _PAUSE_ACTIONS.union({7}):
            self.reveal()
            xbmc.executebuiltin('PlayerControl(Play)')
            return
        if aid in _INFO_ACTIONS:
            self.reveal()
            return
        if aid in _PAUSE_ACTIONS:
            self.reveal()
            xbmc.executebuiltin('PlayerControl(Play)')
            return
        if aid == 7:
            self._chrome_at = time()

    def onClick(self, control_id):
        self.reveal()
        if control_id == 500:
            self._seek(-10)
        elif control_id == 501:
            xbmc.executebuiltin('PlayerControl(Play)')
        elif control_id == 502:
            self._seek(10)
        elif control_id == 503:
            self._stop_playback()
        elif control_id == 504:
            self._choose_subtitles()
        elif control_id == 505:
            self._choose_audio()

    def _seek(self, delta):
        player = self.player
        if player is None:
            return
        try:
            now = float(player.getTime() or 0)
            total = float(player.getTotalTime() or 0)
        except Exception:
            return
        target = max(0, now + delta)
        if total:
            target = min(total, target)
        try:
            player.seekTime(target)
        except Exception:
            xbmc.executebuiltin('PlayerControl(Seek)')
        self.apply_times(target, total)

    def _stop_playback(self):
        xbmc.executebuiltin('PlayerControl(Stop)')
        player = self.player
        if player is not None:
            try:
                player.stop()
            except Exception:
                pass
        self.close()

    def refresh_streams(self):
        player = self.player
        subs, audios = [], []
        if player is not None:
            try:
                subs = list(player.getAvailableSubtitleStreams() or [])
            except Exception:
                subs = []
            try:
                audios = list(player.getAvailableAudioStreams() or [])
            except Exception:
                audios = []
        self._subs = subs
        self._audios = audios
        self.setProperty('HasSubtitles', '1' if subs else '')
        self.setProperty('HasAudio', '1' if len(audios) > 1 else '')

    def _choose_subtitles(self):
        self.refresh_streams()
        if not self._subs:
            return
        choice = xbmcgui.Dialog().select('Subtitles', ['Off'] + self._subs)
        player = self.player
        if choice < 0 or player is None:
            return
        try:
            if choice == 0:
                if hasattr(player, 'showSubtitles'):
                    player.showSubtitles(False)
            else:
                if hasattr(player, 'setSubtitleStream'):
                    player.setSubtitleStream(choice - 1)
                if hasattr(player, 'showSubtitles'):
                    player.showSubtitles(True)
        except Exception:
            pass

    def _choose_audio(self):
        self.refresh_streams()
        if len(self._audios) < 2:
            return
        choice = xbmcgui.Dialog().select('Audio', self._audios)
        player = self.player
        if choice < 0 or player is None:
            return
        try:
            if hasattr(player, 'setAudioStream'):
                player.setAudioStream(choice)
        except Exception:
            pass

    def reclaim_skin_osd(self):
        """Drop Estuary's OSD if a key opened it, and show this chrome instead."""
        opened = False
        for name in _SKIN_OSDS:
            try:
                active = xbmc.getCondVisibility('Window.IsActive(%s)' % name)
            except Exception:
                active = False
            if active:
                xbmc.executebuiltin('Dialog.Close(%s,true)' % name)
                opened = True
        if opened:
            self.reveal()
        return opened

    def apply_times(self, position, duration):
        self.setProperty('Elapsed', _clock(position))
        self.setProperty('Total', _clock(duration))
        percent = 0
        if duration:
            percent = max(0, min(100, int(100 * float(position) / float(duration))))
        bar = self.getControl(400)
        if hasattr(bar, 'setPercent'):
            bar.setPercent(percent)

    def tick(self, player):
        try:
            position = player.getTime()
            duration = player.getTotalTime()
        except Exception:
            return
        self.apply_times(position, duration)

    def maintain(self):
        player = self.player
        if player is None or not _playing(player):
            self.close()
            return
        self.reclaim_skin_osd()
        self.tick(player)
        self.refresh_streams()
        if self.getProperty('Chrome') == '1' and not _player_paused():
            if time() - (self._chrome_at or 0) >= _CHROME_SECONDS:
                self.hide_chrome()


def watch_playback(profile):
    """Keep Aurora's controls up for the whole of a Prime Scene playback.

    doModal takes the remote while our file is playing, including after the
    chrome was hidden and after the dialog was closed early. Other videos do
    not open it, and the dialog is closed when playback ends.
    """
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    latched = False
    while not monitor.abortRequested():
        playing = _playing(player)
        note = _read_note(profile)
        if overlay_due(playing, note, time(), latched):
            latched = True
            dialog = PlayerOverlay(_PLAYER_XML, _addon_path(), 'Default', '1080i')
            dialog.player = player
            dialog.profile = profile
            dialog.info = {
                'title': (note or {}).get('title') or '',
                'meta': (note or {}).get('meta') or '',
                'volume': 40,
            }
            try:
                dialog.doModal()
            except Exception:
                xbmc.log('[Prime Scene] Player controls stopped\n' + format_exc(), xbmc.LOGERROR)
            if not _playing(player):
                latched = False
                _clear_note(profile)
        else:
            latched = False
            if not playing:
                _clear_note(profile)
        if monitor.waitForAbort(0.2):
            break


def open_interface(globals_instance, settings_instance, initialize_provider=None, account_name=None,
                   account_action=None, offer_signin=False, launch_diagnostics=None):
    if settings_instance.data_source != 0:
        globals_instance.dialog.ok('Smooth Prime', 'Choose Website under Settings > Data Source to browse Smooth Prime.')
        return False
    record(globals_instance, 'window_creating')
    window = AuroraHome(_HOME_XML, globals_instance.PLUGIN_PATH, 'Default', '1080i',
                        globals_instance=globals_instance, initialize_provider=initialize_provider,
                        account_name=account_name, account_action=account_action,
                        offer_signin=offer_signin, launch_diagnostics=launch_diagnostics)
    try:
        window.doModal()
    finally:
        window.stop.set()
        for worker in (window.worker, getattr(window, 'detail_worker', None), getattr(window, 'visual_worker', None)):
            if worker is not None:
                while worker.is_alive():
                    worker.join(0.1)
    if window.init_error is not None:
        if launch_diagnostics is not None:
            launch_diagnostics({'event': 'interface_initialization_failed',
                                'error_type': getattr(window, 'init_error_type', None) or window.init_error,
                                'error': window.init_error,
                                'current_window_id': xbmcgui.getCurrentWindowId()})
        globals_instance.dialog.ok('Smooth Prime', 'The interface could not initialize (' +
                                   window.init_error + '). Your sign-in is saved.')
        return False
    return window.result
