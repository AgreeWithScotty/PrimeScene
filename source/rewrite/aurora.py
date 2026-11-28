"""Aurora Cinema windows for Prime Scene.

Layout and the charcoal tint follow SmoothFlix Aurora Cinema. The home hero
is drawn by the skin from the focused card. Playback uses the existing Prime
Scene PlayVideo URL. No separate Kodi skin is required.
"""
from json import dumps, load, loads
from copy import deepcopy
from concurrent.futures import Future
from pathlib import Path
from queue import Empty, Full, Queue
from secrets import token_hex
from re import IGNORECASE
from re import compile as compile_re
from re import fullmatch
from threading import Event, Lock, RLock, Thread, current_thread
from time import time, monotonic
from traceback import format_exc

import xbmc
import xbmcaddon
import xbmcgui

from .catalog_gateway import CatalogSigninRequired, CatalogGateway
from .display_text import clean
from .interface_diagnostics import record
from .title_art import render_title
from .navigation import StorefrontLoader
from .artwork_cache import ImageCache
from .image_quality import source_image,source_facts,POLICY_REVISION as ARTWORK_POLICY
from .description_cache import DescriptionCache
from .card_presentation import viewing_label, duration_seconds, position_seconds, progress_label, progress as progress_percent
from .prime_diagnostics import DiagnosticLog, exception_details, route

_BACK = (9, 10, 92)
# The visible scrim stays on one matte tone. It does not cycle, and it never
# stands in for missing artwork. PrimeSceneHome.xml paints this on control 101.
_SCRIM = '99000000'
_NOTE = 'aurora-playback.json'
_NOTE_WINDOW = 120
_HOME_REGISTRY = 'smoothprime.interface'
_RETURN_COMMAND = 'PlaybackReturn'
ADDON_ID = 'plugin.video.primeinterface'
# Kodi 21 WindowXML::WindowXML looks up xmlFilename in the active skin first
# (<skin>/<res>/<xmlFilename>) and only then in
# <scriptPath>/resources/skins/<defaultSkin>/<defaultRes>/<xmlFilename>.
# Estuary always ships Home.xml, so that name loads the skin window. These
# names are not skin windows, so the add-on files are the ones that open.
_HOME_XML = 'PrimeSceneHome.xml'
_ACCOUNT_READY_CONTROL = 8999
# Every role uses the original, including small cards and service tiles.
_CARD_ART = None
_HERO_ART = None
_DESCRIPTION_AHEAD = 10
_DESCRIPTION_BEHIND = 2
_BACKDROP_AHEAD = 2


def _http(value):
    return value if isinstance(value, str) and value.startswith(('https://', 'http://')) else ''


def _progress(info):
    return int(progress_percent(info or {}))


def _kind(info):
    value = str((info or {}).get('mediatype') or (info or {}).get('contentType') or '').casefold().replace('_', '').replace(' ', '')
    return {'series': 'tvshow', 'show': 'tvshow', 'tvseries': 'tvshow', 'tv': 'tvshow',
            'film': 'movie', 'tvseason': 'season', 'tvepisode': 'episode'}.get(value, value)


def _tv_container(row):
    info = (row or {}).get('info') or {}
    kind = _kind(info)
    if kind in ('tvshow', 'season'):
        return True
    if kind in ('movie', 'episode', 'video', 'live', 'event'):
        return False
    return bool(info.get('episodes') or info.get('next_episode')
                or _count_label(info.get('seasons') or info.get('seasoncount'))
                or _count_label(info.get('season')) and not _count_label(info.get('episode')))


def _tv_packet(packet):
    kind = _kind((packet or {}).get('info'))
    if kind in ('movie', 'episode', 'video', 'live', 'event'):
        return False
    return (kind in ('tvshow', 'season') or bool((packet or {}).get('seasons'))
            or any(_tv_container(row) or _kind(row.get('info')) == 'episode'
                   for row in (packet or {}).get('rows') or []))


def _rail_art(info):
    """Use a declared landscape variant before any portrait-only fallback."""
    return (info.get('landscape') or info.get('thumb') or info.get('fanart') or info.get('poster') or '')


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
    return ' '.join(clean(text or '').split())


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
    elif kind == 'season' and season is not None:
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
    if info.get('access_label'):
        bits.append(info['access_label'])
    return clean('  ·  '.join(bits))


def _detail_meta(row):
    return _meta(row)


def _browse_path(pluginid, url):
    marker = pluginid + 'pv/browse/'
    if isinstance(url, str) and url.startswith(marker):
        return url.split('pv/browse/', 1)[1]
    return None


_PRIMARY_SECTIONS = {'__watching__': ('home', 'In Progress'), '__prime_movies__': ('movies', 'Movies'), '__prime_tv__': ('tv', 'TV'),
                     '__prime_sports__': ('extras', 'Sports'), '__prime_channels__': ('extras', 'Add-on subscriptions'),
                     '__extras__': ('extras','Extras'), '__prime_watchlist__': ('watchlist', 'Watchlist')}
_NAV_VALUES = ('home', 'movies', 'tv', 'extras', 'watchlist')
_SECTION_PATHS = set(_PRIMARY_SECTIONS) | {'root', '__prime_home__', '__legacy_sports__','__genres__/movies','__genres__/tv',
    '__prime_purchases__','__purchases__/movies','__purchases__/tv'}
_DETAIL_KEYS = ('plot', 'year', 'duration', 'runtime', 'mpaa', 'rating', 'genre', 'genres',
                'seasons', 'season', 'episode', 'premiered', 'mediatype', 'fanart', 'thumb', 'poster', 'landscape')


def _detail_path(pluginid, row):
    path = _browse_path(pluginid, (row or {}).get('url'))
    if not path or path in _SECTION_PATHS or path.startswith(('__prime_page__/', '__legacy')):
        return None
    return path


def _needs_detail(row):
    info = (row or {}).get('info') or {}
    return not (_plain(info.get('plot') or '') and _meta(row) and info.get('fanart'))


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
    if _kind(info) in ('movie', 'episode', 'video', 'live', 'event'):
        return False
    season = info.get('season')
    return season not in (None, '', 0, '0') or bool(compile_re(r'^(?:Season|Series)\s+\d+\b', IGNORECASE).match(str(row.get('title') or '')))


def _season_number(row):
    info = (row or {}).get('info') or {}
    try:
        return int(info.get('season') or 0)
    except (TypeError, ValueError):
        return 1 << 20


def _fit_art(url, width=None):
    """Always use the master; the compatibility width argument cannot cap it."""
    return source_image(_http(url))


def _hero_art(info):
    """Keep the backdrop independent of the small image used by the card."""
    return _fit_art(info.get('fanart') or _rail_art(info), _HERO_ART)


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


def choose_playback(rows, requested_kind='', selected_id=None):
    """Play a movie/specific episode only; series and seasons are browse actions."""
    if requested_kind in ('tvshow', 'season'):
        return None, 'browse'
    if not requested_kind and any(_tv_container(row) or _kind(row.get('info')) == 'episode' for row in rows or []):
        return None, 'browse'
    found, seen = [], set()
    for row in rows or []:
        target = playback_row(row)
        if not target:
            continue
        kind = _kind(target.get('info'))
        if requested_kind == 'movie' and kind == 'episode':
            continue
        if requested_kind == 'episode' and kind not in ('episode', 'video', ''):
            continue
        url = target.get('url') or ''
        if not _play_url(url) or url in seen:
            continue
        seen.add(url)
        found.append(target)
    if selected_id:
        exact = [row for row in found if row.get('identity') == selected_id]
        if len(exact) == 1:
            return exact[0], requested_kind or 'selected'
    if len(found) == 1:
        kind = _kind(found[0].get('info'))
        return found[0], 'movie' if kind in ('movie', 'video', '') else 'only'
    return None, 'picker'


def playback_row(row):
    """Series/season cards always browse; only explicit playable leaves play."""
    if not isinstance(row, dict):
        return None
    if _tv_container(row):
        return None
    if _play_url(row.get('url')):
        return row
    info = row.get('info') if isinstance(row.get('info'), dict) else {}
    if _kind(info) not in ('movie', 'episode', 'video', 'live', 'event'):
        return None
    for key in ('resume',):
        found = _nested_playable(info.get(key), row)
        if found:
            return found
    return None


def _item(row, title_art='', image_cache=None, report=None):
    info = row.get('info') or {}
    title = (clean(row.get('title') or '') or 'Untitled').upper()
    item = xbmcgui.ListItem(label=title)
    thumb = _fit_art(_rail_art(info), _CARD_ART)
    poster = _fit_art(info.get('poster') or info.get('thumb'), _CARD_ART)
    fanart = _fit_art(info.get('fanart'), _HERO_ART)
    hero = _hero_art(info)
    art = {}
    if thumb:
        art['thumb'] = thumb
        art['landscape'] = thumb
    if poster:
        art['poster'] = poster
        art['icon'] = poster
    if fanart:
        art['fanart'] = fanart
    if hero:
        art['hero'] = hero
    sources=dict(art)
    if image_cache is not None:
        art = {key: image_cache.local(value) for key, value in art.items()}
    if art:
        item.setArt(art)
        if report:report({'event':'artwork_sources_bound','stage':'artwork_binding','artwork_policy':ARTWORK_POLICY,
            'roles':[dict(source_facts(value),role=key,address=route(value),
                local_cache_used=art[key]!=value) for key,value in sources.items()],
            'result':'success'})
    meta = _meta(row)
    plot = _plain(info.get('plot') or '')
    item.setProperty('Meta', meta)
    item.setProperty('Watched', '%s%% WATCHED' % _progress(info) if _progress(info) else meta)
    item.setProperty('ViewingLabel',viewing_label(info))
    item.setProperty('ProgressLabel',clean(progress_label(info) or viewing_label(info)))
    card_bits=[]
    if _kind(info)=='episode':card_bits.append('S%sE%s'%(str(info.get('season') or 0).zfill(2),str(info.get('episode') or 0).zfill(2)))
    elif _kind(info)=='season' and info.get('season') is not None:card_bits.append('Season '+str(info['season']))
    elif _year_label(info):card_bits.append(_year_label(info))
    secondary=_runtime_label(info.get('duration') if info.get('duration') not in (None,'') else info.get('runtime'))
    if secondary:card_bits.append(secondary)
    item.setProperty('CardMeta',clean('  ·  '.join(card_bits)))
    item.setProperty('Plot', plot)
    item.setProperty('Year', _year_label(info))
    item.setProperty('Runtime', _runtime_label(info.get('duration') if info.get('duration') not in (None, '') else info.get('runtime')))
    item.setProperty('Rating', clean(_rating_label(info)))
    item.setProperty('Genre', clean(_genre_label(info)))
    item.setProperty('MediaType', _kind(info))
    item.setProperty('HeroSource', 'provider_heroshot' if info.get('fanart') else 'cover_preview')
    item.setProperty('Id', str(row.get('url') or ''))
    item.setProperty('Identity', str(row.get('identity') or ''))
    if title_art:
        item.setProperty('TitleArt', title_art)
    try:
        tag = item.getVideoInfoTag()
        progress = _progress(info)
        if hasattr(tag, 'setResumePoint'):
            known_total=duration_seconds(info);total=known_total or 100
            position=position_seconds(info) if known_total else progress
            tag.setResumePoint(position if position is not None else total*progress/100,total)
        if title and hasattr(tag, 'setTitle'):
            tag.setTitle(title)
        if plot and hasattr(tag, 'setPlot'):
            tag.setPlot(plot)
        year = _year_label(info)
        if year and hasattr(tag, 'setYear'):
            tag.setYear(int(year))
        if _kind(info) and hasattr(tag, 'setMediaType'):
            tag.setMediaType({'tvshow': 'tvshow', 'movie': 'movie', 'episode': 'episode', 'season': 'season'}.get(_kind(info), 'video'))
    except Exception as error:
        if report:report({'event':'card_metadata_binding_failed','stage':'native_video_tag',
            'media_kind':_kind(info),'exception':exception_details(error),'result':'failure'})
    return item


def _note_path(profile):
    return Path(profile) / _NOTE


def note_playback(profile, title, meta=''):
    """Keep one request ID across UI and resolver, including its live return target."""
    if not profile:
        return
    path = _note_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = _read_note(profile) or {}
    target = xbmcgui.Window(10000).getProperty(_HOME_REGISTRY)
    reuse = (0 <= time() - float(previous.get('armed') or 0) < _NOTE_WINDOW
             and previous.get('target') == target)
    note = {'title': title or 'Smooth Prime', 'meta': meta or (previous.get('meta') if reuse else '') or '',
            'armed': previous.get('armed') if reuse else time(), 'target': target,
            'request_id': previous.get('request_id') if reuse else token_hex(8)}
    try:
        temporary = path.with_suffix('.new')
        temporary.write_text(dumps(note), encoding='utf-8')
        temporary.replace(path)
    except OSError as error:
        DiagnosticLog(profile, xbmcaddon.Addon(ADDON_ID).getAddonInfo('version'),
                      operation='playback_return')({'event': 'playback_note_failed',
            'request_id': note['request_id'], 'exception': exception_details(error), 'result': 'failure'})


def _read_note(profile):
    path = _note_path(profile)
    try:
        with path.open(encoding='utf-8') as handle:
            note = load(handle)
    except (OSError, ValueError):
        return None
    return note if isinstance(note, dict) else None


def _clear_note(profile, request_id=None):
    try:
        if request_id and (_read_note(profile) or {}).get('request_id') != request_id:
            return
        _note_path(profile).unlink()
    except OSError:
        pass


def play_row(row, profile, epoch=None):
    _clear_note(profile)
    note_playback(profile, row.get('title') or 'Smooth Prime', _detail_meta(row))
    address=row.get('url') or ''
    if not address.startswith('plugin://'+ADDON_ID+'/'):
        raise ValueError('Playback must use this add-on selected-title action')
    if epoch is not None:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts=urlsplit(address);query=dict(parse_qsl(parts.query));query['account_epoch']=epoch
        address=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
    xbmc.executebuiltin('RunScript(%s,%s)'%(ADDON_ID,dumps(address)))


def _request_playback_return(note, reason, report, progress_ready=False):
    """Signal the existing live window, never launch a replacement application."""
    request_id = (note or {}).get('request_id')
    try:
        target = (note or {}).get('target')
        if not target or xbmcgui.Window(10000).getProperty(_HOME_REGISTRY) != target:
            report({'event': 'playback_return_cancelled', 'request_id': request_id,
                    'reason': 'interface_closed_or_account_changed', 'result': 'cancelled'})
            return False
        destination = loads(target)
        window = xbmcgui.Window(int(destination['window_id']))
        if window.getProperty('InterfaceSession') != destination['session']:
            raise RuntimeError('The return window is no longer the same interface')
        previous=window.getProperty(_RETURN_COMMAND)
        previous=loads(previous) if previous else {}
        if previous.get('request_id')==request_id and previous.get('progress_ready') and not progress_ready:
            return True
        window.setProperty(_RETURN_COMMAND, dumps({'request_id': request_id, 'reason': reason,'progress_ready':progress_ready}))
        report({'event': 'playback_return_requested', 'request_id': request_id, 'reason': reason,
                'progress_ready':progress_ready,
                'target_window_id': destination['window_id'],
                'current_window_id': xbmcgui.getCurrentWindowId(), 'result': 'success'})
        return True
    except Exception as error:
        report({'event': 'playback_return_request_failed', 'request_id': request_id,
                'exception': exception_details(error), 'result': 'failure'})
        return False


def _top_selector_row(pluginid, row):
    path = _browse_path(pluginid, row.get('url'))
    if path in _SECTION_PATHS:
        return True
    # Only navigation entries: real titles called Home retain their identity.
    return (not row.get('identity') and not row.get('detail_url') and not row.get('video')
            and not _kind(row.get('info'))
            and str(row.get('title') or '').strip().casefold() in
            ('home', 'movie', 'movies', 'tv', 'tv shows', 'sports', 'watchlist', 'purchases', 'purchases & rentals'))


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


class _AccountMonitor(xbmc.Monitor):
    def __init__(self, window):
        super(_AccountMonitor, self).__init__()
        self.window = window

    def onNotification(self, sender, method, data):
        if self.window is None or sender != ADDON_ID:return
        if method.rsplit('.', 1)[-1] == 'account_verified':
            self.window._queue_account_refresh()
        elif method.rsplit('.', 1)[-1] == 'account_invalidated':
            self.window.stop.set()
            from .account_state import clear_runtime
            try:
                clear_runtime(self.window.g)
            finally:
                self.window.show_signed_out()


class AuroraHome(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        self.g = kwargs.pop('globals_instance')
        self.account_name = kwargs.pop('account_name', None)
        self.account_action = kwargs.pop('account_action', None)
        self._account_refresh_pending = Event()
        self._account_refresh_lock = RLock()
        self._account_refreshing = False
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
        self._back_menu_ready=False
        self.path = '__genres__/movies'
        self.query = None
        self.heading = 'Movies'
        self.nav = 'movies'
        self.next_path = None
        self.pending_path = None
        self.prefetch_at = 0
        self._page_retry_at, self._page_retry_count = 0, 0
        self.busy = False
        self.result = None
        self.worker = None
        self.artwork = set()
        self.account_generation = None
        self._ready = False
        self._signed_out_view = False
        self._progress_refresh_generation = None
        self._restore = None
        self.detail_jobs = Queue()
        self.detail_worker = None
        self.play_jobs, self.play_worker = Queue(), None
        self.genre_worker = None
        self.genre_jobs = Queue()
        self.preloaded_catalogs = Queue(maxsize=4)
        self._genre_focus = None
        self._genre_panel_reported = None
        self._genre_selection = None
        self.genre_cache = {}
        self.genre_requested = set()
        self.genre_owner = None
        self._genre_rows = []
        self.service_rows=[];self._service_future=None;self._tile_items={};self._tile_art={}
        self.bridge = None
        self.bridge_lock = Lock()
        self.metadata_bridge, self.metadata_lock = None, Lock()
        self.known_continuations = {}
        self.detail_cache = {}
        self.detail_inflight = {}
        self._season_focus=None;self._season_focus_since=0;self._season_attempts={}
        self._play_token = 0
        self._allow_enrich = True
        self.loader = None
        self.image_cache = None
        self.genre_image_cache=None;self.genre_backdrops=None
        self._startup=None;self._genre_discovery='pending';self._genre_warm_signature=None
        self.visual_worker = None
        self.items, self.row_keys = [], set()
        self.views, self.known_details, self.title_cache = {}, {}, {}
        self._focus_signature, self._focus_since, self._enriched_focus = None, 0, None
        self._painted_details = set()
        self._navigation_started = monotonic()
        self.description_cache = None
        self.description_attempts = {}
        self._description_window = None
        self.series_context = None
        self.return_worker = None
        self.window_id = None
        self.interface_session = None
        self._replace_rail = False
        self.playback_origin = None
        self._returned_playback_request = None
        self._focus_first_generation = None
        self._focus_receipt = None
        self._reactivation_ready = Event()
        self._resume_selection = None
        self._pending_progress_return = None
        self._progress_return_request = None
        self.account_monitor = _AccountMonitor(self)
        self.performance = DiagnosticLog(self.g.DATA_PATH, self.g.addon.getAddonInfo('version'),
                                         operation='interface_performance')
        self.setProperty('StartupSplash','1')

    def onInit(self):
        if self._ready:
            try:
                self._restore_controls('window_on_init')
            except Exception as error:
                self.performance({'event': 'window_reactivation_failed', 'stage': 'window_on_init',
                    'exception': exception_details(error), 'result': 'failure'})
            return
        self._ready = True
        self.window_id = xbmcgui.getCurrentWindowId()
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

    def _restore_controls(self, stage):
        """A live Python window can outlive Kodi's native view controls/textures."""
        started = monotonic()
        before_view = self.getCurrentContainerId()
        before_size = self.getListSize()
        if xbmcgui.getCurrentWindowId() != self.window_id:
            raise RuntimeError('The catalog window is not active for control restoration')
        # WindowXML bypasses CGUIMediaWindow's normal WINDOW_INIT handler.
        # After resources are reallocated, reselect its view explicitly.
        if before_view != 200:
            xbmc.executebuiltin('Container.SetViewMode(200)', True)
        if self.getCurrentContainerId() != 200:
            raise RuntimeError('Kodi did not restore the catalog view')
        if self.stop.is_set():
            self.performance({'event': 'window_reactivation_cancelled', 'stage': stage,
                'reason': 'interface_stopped', 'result': 'cancelled'})
            return
        if self.account_generation is not None:
            from .account_state import current_account
            if not current_account(self.g.DATA_PATH, self.account_generation):
                self.show_signed_out()
                self._reactivation_ready.set()
                return
        if self.genre_owner is not None and self.getProperty('GenrePanel'):
            owner=self.genre_owner;selected=self._browse_list().getSelectedPosition()
            self._genre_rows=[]
            self._populate_genres({301:'movies',302:'tv',303:'extras'}[owner])
            self._browse_list().selectItem(max(0,selected));self.setFocusId(owner)
            self.performance({'event':'browse_grid_controls_restored','stage':stage,'owner':owner,
                'tiles':len(self._genre_rows),'result':'success'})
            self._reactivation_ready.set()
            return
        # The return worker refreshes progress after Kodi restores this window.
        # WINDOW_INIT must only restore bindings, never start another navigation.
        with self.lock:
            selected = self._resume_selection
            origin = self.playback_origin
            if origin and (origin.get('path'),origin.get('query')) != (self.path,self.query):
                origin = None
            if selected is None and origin:
                selected = next((i for i, row in enumerate(self.rows)
                    if row.get('url') == origin['url'] or origin.get('identity')
                    and row.get('identity') == origin['identity']), origin['selected'])
            if selected is None:
                selected = self._selected_index()
            selected = max(0, min(selected, max(0, len(self.rows)-1)))
            if len(self.items) != len(self.rows):
                self.items = [_item(row, self.title_cache.get((row.get('title') or '').upper(), ''),
                                    self.image_cache,self.performance) for row in self.rows]
            # The native control count establishes its binding separately from
            # WindowXML's Python-side list. Rebind only when that binding is lost.
            native_label=xbmc.getInfoLabel('Container(200).NumItems')
            try:native_bound=int(native_label)==len(self.items) and before_size==len(self.items) and before_view==200
            except (TypeError,ValueError):native_bound=False
            if not native_bound:
                self.clearList()
                if self.items:self.addItems(self.items)
            if self.items:
                self.setCurrentListPosition(selected)
                self.setProperty('ShowSplash', '')
            self.setProperty('Nav', self.nav if self.nav in _NAV_VALUES else '')
            self._section_label()
            self._season_properties()
            self.update_account_button()
            self.setFocusId(200 if self.items else self._menu_control())
            native_count = self.getListSize()
            view = self.getCurrentContainerId()
            focus = self.getFocusId()
            position = self._selected_index()
            valid = (view == 200 and native_count == len(self.rows)
                     and focus == (200 if self.items else self._menu_control())
                     and (not self.items or position == selected))
        self.performance({'event': 'window_controls_restored', 'stage': stage,
            'request_id': (origin or {}).get('request_id'),
            'previous_view_id': before_view, 'previous_native_cards': before_size,
            'native_binding_retained':native_bound,'clear_list_called':not native_bound,
            'view_id': view, 'native_cards': native_count, 'logical_cards': len(self.rows),
            'focused_control': focus, 'expected_index': selected, 'actual_index': position,
            'container_cards_label': xbmc.getInfoLabel('Container(200).NumItems'),
            'catalog_worker_alive': bool(self.worker and self.worker.is_alive()),
            'elapsed_ms': round((monotonic()-started)*1000),
            'result': 'success' if valid else 'failure'})
        if not valid:
            raise RuntimeError('Kodi catalog controls did not confirm the restored list and focus')
        self._select_progress_return()
        self._reactivation_ready.set()

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
        self._signed_out_view = False
        if self.account_name is not None:
            from .account_state import account_epoch
            self.account_generation = account_epoch(self.g.DATA_PATH)
        from .account_state import current_account
        report_epoch = self.account_generation
        self.performance = DiagnosticLog(self.g.DATA_PATH, self.g.addon.getAddonInfo('version'),
            active=lambda: report_epoch is None or current_account(self.g.DATA_PATH, report_epoch),
            operation='interface_performance')
        from .display_text import configure
        configure(self.performance)
        self._set_hero_status('', '')
        self.setProperty('ShowSplash', '1')
        self.interface_session = token_hex(8)
        self.setProperty('InterfaceSession', self.interface_session)
        xbmcgui.Window(10000).setProperty(_HOME_REGISTRY, dumps({
            'window_id': self.window_id, 'session': self.interface_session}))
        self.return_worker = Thread(target=self._return_loop, name='PrimeReturn', daemon=True)
        self.worker = Thread(target=self._work, name='PrimeCatalog', daemon=True)
        self.detail_worker = Thread(target=self._detail_loop, name='PrimeDetails', daemon=True)
        self.play_worker = Thread(target=self._play_loop, name='PrimeTitleActions', daemon=True)
        self.visual_worker = Thread(target=self._visual_loop, name='PrimeVisuals', daemon=True)
        self.genre_worker = Thread(target=self._genre_loop,name='PrimeGenres',daemon=True)
        self._startup={'started':monotonic(),'signature':None,'bound_at':None}
        self._genre_discovery='pending';self._genre_warm_signature=None;self.setProperty('StartupSplash','1');self.setProperty('StartupNotice','')
        self.setProperty('GenreEntryFocus','1')
        self._show_genres(301,focus_grid=False)
        self.setFocusId(301)
        self.performance({'event':'startup_focus','control_id':301,'actual_control':self.getFocusId(),
            'initial_view':'movie_genres','catalog_pages_requested':0,'result':'success' if self.getFocusId()==301 else 'failure'})
        self.performance({'event':'interface_configuration','stage':'presentation',
            'content_columns':4,'content_rows':1,'content_flow':'horizontal_fixedlist','content_card_ratio':'16:9','base_background':'000000',
            'genre_artwork':'preloaded_amazon_title_backdrops','genre_layout':'expanding_cinematic_planes',
            'startup_splash_min_seconds':3,'startup_artwork_notice_after_seconds':25,'bundled_genre_backgrounds':False,
            'startup_artwork_section':'movies','startup_artwork_count':4,'remaining_genres_continue':True,
            'presentation_revision':'personal-lists-distinct-art-recovery-draft','genre_artwork_metadata_pages_per_genre':1,
            'startup_artwork_max_wait_seconds':45,'startup_waits_for_tv_metadata':False,
            'genre_plane_width':1600,'genre_edge_plane_width':1760,'genre_spine_width':160,'genre_transition_ms':320,
            'genre_preview_first_pages':2,'genre_preview_lane':'separate_from_startup_pagination',
            'native_binding_retained_when_confirmed':True,'result':'success'})
        self.worker.start()
        self.detail_worker.start()
        self.play_worker.start()
        self.visual_worker.start()
        self.genre_worker.start()
        self.return_worker.start()
        if self.launch_diagnostics is not None:
            self.launch_diagnostics({'event': 'interface_ready', 'signed_in': True,
                                     'current_window_id': xbmcgui.getCurrentWindowId()})

    def update_account_button(self):
        from .account_state import needs_cleanup
        name = self.account_name() if self.account_name is not None else ''
        self.setProperty('AccountLabel', 'Finish sign out' if needs_cleanup(self.g.DATA_PATH) else 'Sign out' if name else 'Sign in')

    def _queue_account_refresh(self):
        if self.window_id is None or self._account_refresh_pending.is_set():return
        self._account_refresh_pending.set()
        report=DiagnosticLog(self.g.DATA_PATH,self.g.addon.getAddonInfo('version'),operation='account_handoff')
        try:
            xbmc.executebuiltin('SendClick(%d,%d)'%(self.window_id,_ACCOUNT_READY_CONTROL))
            report({'event':'account_interface_refresh_queued','stage':'account_handoff','result':'success'})
        except Exception as error:
            self._account_refresh_pending.clear()
            report({'event':'account_interface_refresh_queue_failed','stage':'account_handoff',
                'exception':exception_details(error),'result':'failure'})

    def _refresh_account_interface(self, source):
        with self._account_refresh_lock:
            if self._account_refreshing:return
            self._account_refreshing=True
            self._account_refresh_pending.clear()
            started=monotonic()
            report=DiagnosticLog(self.g.DATA_PATH,self.g.addon.getAddonInfo('version'),operation='account_handoff')
            try:
                from .account_state import account_epoch
                from .accounts import reset_runtime
                reset_runtime(self.g.DATA_PATH)
                signed_in=bool(self.account_name and self.account_name())
                epoch=account_epoch(self.g.DATA_PATH)
                current=(signed_in and self.account_generation==epoch and self.worker is not None
                    and self.worker.is_alive() and not self.stop.is_set())
                if not current:
                    if self.worker is not None:self.pause_catalog('Loading account...')
                    self.stop=Event()
                    if signed_in:self._initialize_controls()
                    else:self.show_signed_out()
                self.update_account_button()
                report({'event':'account_interface_refreshed','stage':'account_handoff','source':source,
                    'signed_in':signed_in,'already_current':current,
                    'catalog_worker_alive':bool(self.worker and self.worker.is_alive()),
                    'native_window_active':xbmcgui.getCurrentWindowId()==self.window_id,
                    'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
            except Exception as error:
                report({'event':'account_interface_refresh_failed','stage':'account_handoff','source':source,
                    'elapsed_ms':round((monotonic()-started)*1000),'exception':exception_details(error),'result':'failure'})
                self.setProperty('StartupSplash','')
                self.g.dialog.ok('Smooth Prime','The account view could not refresh. Your saved sign-in was kept. Open Diagnostics for the report.')
            finally:self._account_refreshing=False

    def show_signed_out(self):
        self._startup=None;self.setProperty('StartupSplash','');self.setProperty('ArtworkRecovery','')
        self._genre_warm_signature=None;self._genre_panel_reported=None;self.artwork.clear()
        self._signed_out_view = True
        self._cancel_genre_selection('account_changed')
        self.setProperty('NavigationBackdrop','')
        from .display_text import reset_account_observations
        reset_account_observations();self._reported_display_signature=None
        self._progress_refresh_generation=None
        self.setProperty('ProgressRefreshing','');self.setProperty('ProgressRefreshArt','')
        self.genre_cache.clear();self.genre_requested.clear();self._genre_rows=[];self.genre_owner=None
        self.service_rows=[];self._service_future=None;self._tile_items={};self._tile_art={}
        self.preloaded_catalogs=Queue(maxsize=4);self._genre_focus=None
        self.setProperty('GenrePanel','')
        self._pending_progress_return = self._progress_return_request = None
        self._resume_selection = None
        self._unregister_return()
        self.playback_origin = None
        with self.lock:
            self.generation += 1
            self.rows = []
            self.history.clear()
            self.artwork.clear()
            self.views.clear()
            self.items, self.row_keys = [], set()
            self.title_cache.clear()
            self.known_details.clear()
            self.known_continuations.clear()
            self.description_attempts.clear()
            self.description_cache = None
            self.detail_cache.clear()
            self.detail_inflight.clear();self._season_attempts.clear();self._season_focus=None
            self.series_context = None
            self.path, self.query, self.next_path = '__prime_movies__', None, None
            self.busy = False
            self.nav = ''
        self.clearList()
        self.setProperty('Nav', '')
        self._season_properties()
        self.setProperty('Section', '')
        self.setProperty('SectionNotice','');self.setProperty('Busy','')
        self.update_account_button()
        self._set_hero_status('SIGN IN', 'SIGN IN TO PRIME VIDEO TO BROWSE AND WATCH.')
        self.setFocusId(306)

    def _set_hero_status(self, title, tag, art=''):
        if title:
            self.setProperty('ShowSplash', '')
        self.setProperty('HeroKicker', '')
        self.setProperty('HeroTitle', title)
        self.setProperty('HeroMeta', '')
        self.setProperty('HeroTag', tag)
        self.setProperty('HeroSynopsis', tag)
        self.setProperty('HeroFanart', art)
        baked={'IN PROGRESS':'status-progress.png','PURCHASES':'status-purchases.png','WATCHLIST':'status-watchlist.png',
            'SEARCH':'status-search.png','SEARCH PRIME VIDEO':'status-search.png'}.get(title.upper())
        plate=str(Path(self.g.PLUGIN_PATH)/'resources/skins/Default/media'/baked) if baked else self.title_cache.get(title.upper(),'')
        self.setProperty('HeroTitleArt',plate)

    def pause_catalog(self, status='Signing out...'):
        self._startup=None;self.setProperty('StartupSplash','');self.setProperty('ArtworkRecovery','')
        self._genre_warm_signature=None;self._genre_panel_reported=None;self.artwork.clear()
        self._cancel_genre_selection('account_changed')
        self.setProperty('NavigationBackdrop','')
        from .display_text import reset_account_observations
        reset_account_observations();self._reported_display_signature=None
        self._progress_refresh_generation=None
        self.setProperty('ProgressRefreshing','');self.setProperty('ProgressRefreshArt','')
        self._pending_progress_return = self._progress_return_request = None
        self._resume_selection = None
        self.stop.set()
        self.setProperty('SectionNotice','');self.setProperty('Busy','')
        self._unregister_return()
        self.playback_origin = None
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
        for worker in (self.worker, self.detail_worker, self.play_worker, self.visual_worker, self.return_worker, self.genre_worker):
            if worker is not None and worker is not current_thread():
                while worker.is_alive():
                    worker.join(0.1)
        self.worker = None
        self.detail_worker = None
        self.play_worker = None
        self.genre_worker = None
        self.genre_jobs = Queue()
        self.preloaded_catalogs = Queue(maxsize=4)
        self._genre_focus = None
        self.genre_cache.clear()
        self.genre_requested.clear()
        self.genre_owner = None
        self._genre_rows = []
        self.service_rows=[];self._service_future=None;self._tile_items={};self._tile_art={}
        self.setProperty('GenrePanel','')
        self.visual_worker = None
        self.return_worker = None
        self.bridge = None
        self.metadata_bridge = None
        self.known_continuations.clear()
        self.jobs = Queue()
        self.detail_jobs = Queue()
        self.play_jobs = Queue()
        self.detail_cache = {}
        self.detail_inflight.clear();self._season_attempts.clear();self._season_focus=None
        self._painted_details.clear()
        self.title_cache.clear()
        self.known_details.clear()
        self.artwork.clear()
        self.views.clear()
        self.description_cache = None
        self.description_attempts.clear()
        self._description_window = None
        self.series_context = None
        self.playback_origin = None
        self._focus_first_generation = self._focus_receipt = None
        with self.performance.lock:
            self.performance.lines.clear()
            self.performance.values.clear()
        reset_account_observations();self._reported_display_signature=None

    def _unregister_return(self):
        registry = xbmcgui.Window(10000)
        expected = dumps({'window_id': self.window_id, 'session': self.interface_session})
        if registry.getProperty(_HOME_REGISTRY) == expected:
            registry.clearProperty(_HOME_REGISTRY)
        self.clearProperty('InterfaceSession')
        self.clearProperty(_RETURN_COMMAND)

    def _refresh_progress_return(self, origin):
        request_id=origin.get('request_id')
        marker=request_id or id(origin)
        with self.lock:
            if self._progress_return_request == marker:return
            self._progress_return_request=marker
            self.nav='home'
            self.navigate('__watching__',remember=False,heading='In Progress',save_current=False)
            self._pending_progress_return={'generation':self.generation,'request_id':request_id,
                'identity':origin.get('identity'),'url':origin.get('url'),'selected':origin['selected']}

    def _select_progress_return(self):
        pending=self._pending_progress_return
        if not pending or not pending.get('received'):return False
        if pending['generation'] != self.generation or xbmcgui.getCurrentWindowId()!=self.window_id:return False
        if self.getCurrentContainerId()!=200 or self.getListSize()!=len(self.rows):return False
        selected=next((i for i,row in enumerate(self.rows) if row.get('url')==pending['url']
            or pending['identity'] and row.get('identity')==pending['identity']),0)
        if self.rows:self.setCurrentListPosition(selected)
        self.setFocusId(200 if self.rows else self._menu_control())
        self._pending_progress_return=None
        self._resume_selection=None
        self.performance({'event':'fresh_progress_return_selected','request_id':pending['request_id'],
            'generation':self.generation,'cards':len(self.rows),'selected_index':selected,
            'focused_control':self.getFocusId(),'fresh_provider_list':True,'result':'success'})
        return True

    def _return_loop(self):
        while not self.stop.wait(0.025):
            command=self.getProperty(_RETURN_COMMAND)
            if not command:continue
            self.clearProperty(_RETURN_COMMAND)
            started=monotonic();request={}
            try:
                request=loads(command);pending=_read_note(self.g.DATA_PATH) or {}
                origin=self.playback_origin
                if origin is None and self._returned_playback_request==request.get('request_id'):
                    continue
                if (self.stop.is_set() or self.getProperty('InterfaceSession')!=self.interface_session
                    or pending.get('request_id') and pending['request_id']!=request.get('request_id')
                    or _playing(xbmc.Player()) or origin and origin.get('request_id') not in (None,request.get('request_id'))):
                    self.performance({'event':'playback_card_return_cancelled','request_id':request.get('request_id'),
                        'reason':'interface_invalidated_or_new_playback','result':'cancelled'});continue
                self._play_token+=1;source='current_view';fresh=bool(origin and origin['path']=='__watching__')
                if fresh:source='fresh_amazon_progress'
                elif origin:
                    self.nav=origin['nav']
                    found=any(row.get('url')==origin['url'] or origin['identity'] and row.get('identity')==origin['identity'] for row in self.rows)
                    if (self.path,self.query)!=(origin['path'],origin['query']) or not found:
                        self._save_view()
                        with self.lock:self.rows=[]
                        self.views[(origin['path'],origin['query'])]=origin['view']
                        self.navigate(origin['path'],remember=False,query=origin['query'],selection=origin['selected'],
                            heading=origin['heading'],series=origin['series'],save_current=False)
                        source='origin_snapshot'
                    self.history=list(origin['history'])
                before=xbmcgui.getCurrentWindowId();activated=False
                # Kodi may already have returned. Activating that same ID forces
                # CloseWindowSync and WINDOW_INIT again, producing the flash.
                if self.stop.is_set():continue
                if xbmcgui.getCurrentWindowId()!=self.window_id:
                    activated=True;self._reactivation_ready.clear()
                    self.performance({'event':'playback_return_activation_requested','stage':'immediate_return',
                        'request_id':request.get('request_id'),'previous_window_id':before,
                        'target_window_id':self.window_id,'screen_mode':xbmc.getInfoLabel('System.ScreenMode') or None,
                        'intentional_grace_ms':0,'activation_dispatch':'asynchronous','result':'started'})
                    xbmc.executebuiltin('ActivateWindow(%d)'%self.window_id,False)
                    self._reactivation_ready.wait(1.0)
                if xbmcgui.getCurrentWindowId()!=self.window_id:raise RuntimeError('Kodi did not return to the catalog window')
                if self.getCurrentContainerId()!=200 or self.getListSize()!=len(self.rows):
                    self._restore_controls('return_native_binding_missing')
                if fresh and request.get('progress_ready'):
                    self._refresh_progress_return(origin)
                elif fresh:
                    self.performance({'event':'progress_refresh_waiting_for_playback','stage':'watch_progress_settlement',
                        'request_id':request.get('request_id'),'generation':self.generation,'cards_retained':len(self.rows),
                        'result':'pending'})
                selected=self._selected_index()
                if origin and not fresh:
                    selected=next((i for i,row in enumerate(self.rows) if row.get('url')==origin['url']
                        or origin['identity'] and row.get('identity')==origin['identity']),origin['selected'])
                    selected=max(0,min(selected,max(0,len(self.rows)-1)))
                if not fresh or not self._select_progress_return():
                    if self.rows:self.setCurrentListPosition(selected)
                    self.setFocusId(200 if self.rows else self._menu_control())
                self.setProperty('Nav',self.nav if self.nav in _NAV_VALUES else '')
                self._section_label()
                self.performance({'event':'playback_card_returned','request_id':request.get('request_id'),
                    'previous_window_id':before,'current_window_id':xbmcgui.getCurrentWindowId(),
                    'target_window_id':self.window_id,'activation_required':activated,
                    'source':source,'cards':len(self.rows),'native_cards':self.getListSize(),
                    'view_id':self.getCurrentContainerId(),'focused_control':self.getFocusId(),
                    'selected_index':self._selected_index(),'active_dialog_id':xbmcgui.getCurrentWindowDialogId(),
                    'elapsed_ms':round((monotonic()-started)*1000),
                    'result':'pending_progress_settlement' if fresh and not request.get('progress_ready') else
                        'pending_fresh_list' if fresh and self._pending_progress_return else 'success'})
                self._returned_playback_request=request.get('request_id')
                if (not fresh or request.get('progress_ready')) and self.playback_origin is origin:self.playback_origin=None
                self._resume_selection=None
            except Exception as error:
                self.performance({'event':'playback_card_return_failed','request_id':request.get('request_id'),
                    'exception':exception_details(error),'elapsed_ms':round((monotonic()-started)*1000),'result':'failure'})

    def _capture_playback_origin(self):
        """Capture at click time, before asynchronous title resolution can finish."""
        with self.lock:
            selected = self._selected_index()
            visible = self.rows[selected] if 0 <= selected < len(self.rows) else {}
            return {'path': self.path, 'query': self.query, 'nav': self.nav,
                'heading': self.heading, 'selected': selected, 'url': visible.get('url'),
                'identity': visible.get('identity'), 'series': deepcopy(self.series_context),
                'history': list(self.history), 'card_count':len(self.rows), 'view': {'rows': list(self.rows) if self.path!='__watching__' else [], 'items': list(self.items) if self.path!='__watching__' else [],
                    'next': self.next_path or self.pending_path, 'prefetch_at': self.prefetch_at,
                    'selected': selected, 'series': deepcopy(self.series_context),
                    'pagination_unavailable':getattr(self,'_personal_pagination_unavailable',False)}}

    def _start_playback(self, row, origin=None,resume_mode='ask', request_token=None, generation=None):
        """Keep the launching card and original browse context until return."""
        with self.lock:
            if self.stop.is_set() or (request_token is not None and (request_token!=self._play_token or generation!=self.generation)):
                return
            self._play_token+=1
            self.setProperty('Busy','')
            origin = origin or self._capture_playback_origin()
        if resume_mode=='start':
            from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
            parts=urlsplit(row['url']);query=dict(parse_qsl(parts.query));query['resume']='start'
            row=dict(row,url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment)))
        self.playback_origin = origin
        self._returned_playback_request = None
        play_row(row, self.g.DATA_PATH, self.account_generation)
        note = _read_note(self.g.DATA_PATH) or {}
        if self.playback_origin is not None:
            self.playback_origin['request_id'] = note.get('request_id')
        self.performance({'event': 'playback_card_origin_saved', 'stage': 'return_origin',
            'request_id': note.get('request_id'), 'index': origin['selected'], 'cards': origin['card_count'],
            'has_card_identity': bool(origin['identity'] or origin['url']),
            'series_context': bool(origin['series']), 'result': 'success'})

    def _section_label(self):
        self.setProperty('Section', '' if self.path in _SECTION_PATHS else clean(self.heading or '').upper())

    def _save_view(self):
        watchlist=self.path=='__prime_watchlist__'
        if (self.path=='__watching__' or (self.series_context or {}).get('watching_only')
                or not watchlist and getattr(self,'_personal_pagination_unavailable',False)):
            self.views.pop((self.path,self.query),None)
            return
        if self.rows:
            self.views[(self.path, self.query)] = {'rows': list(self.rows), 'items': list(self.items),
                'next': self.next_path or self.pending_path, 'prefetch_at': self.prefetch_at, 'selected': self._selected_index(),
                'series': deepcopy(self.series_context),
                'pagination_unavailable':getattr(self,'_personal_pagination_unavailable',False)}
            if watchlist:self.performance({'event':'watchlist_view_saved','stage':'view_memory',
                'generation':self.generation,'cards':len(self.rows),'native_items_retained':True,
                'continuation_requires_refresh':True,'result':'success'})
            if len(self.views) > 12:
                oldest = next((key for key in self.views if key[0] not in _SECTION_PATHS), None)
                if oldest is not None:
                    self.views.pop(oldest)
        elif watchlist:
            # A confirmed empty list must not restore a previous membership.
            self.views.pop((self.path,self.query),None)

    def _dismiss_pending_playback_refresh(self):
        origin=self.playback_origin
        if origin and self._returned_playback_request==origin.get('request_id'):
            self.playback_origin=None
            self.performance({'event':'progress_return_refresh_cancelled','stage':'watch_progress_settlement',
                'request_id':origin.get('request_id'),'reason':'user_navigated_after_return','result':'cancelled'})

    def navigate(self, path, remember=True, query=None, selection=None, heading=None, series=None, save_current=True, intro_art=''):
        if self.stop.is_set():return
        self._back_menu_ready=False
        self._cancel_genre_selection('navigation_changed')
        if (path,query)!=(self.path,self.query):self._dismiss_pending_playback_refresh()
        if self.account_name is not None and not self.account_name():
            self.show_signed_out()
            return
        with self.lock:
            series_changed=series is not None and (series.get('view'),series.get('requested_season')) != (
                (self.series_context or {}).get('view'),(self.series_context or {}).get('requested_season'))
            if (path not in ('__watching__','__prime_watchlist__') and (path, query) == (self.path, self.query)
                    and self.rows and not series_changed and not getattr(self,'_personal_pagination_unavailable',False)):
                self.setProperty('Nav', self.nav if self.nav in _NAV_VALUES else '')
                return
            if save_current:
                self._save_view()
            refreshing_progress=path=='__watching__' and self.path=='__watching__' and bool(self.rows)
            refreshing_watchlist=path=='__prime_watchlist__' and self.path==path and bool(self.rows)
            retaining_cards=refreshing_progress or refreshing_watchlist
            visible_cards = len(self.rows)
            if remember and ((path, query) != (self.path, self.query) or series_changed):
                previous_nav = _PRIMARY_SECTIONS.get(self.path, (self.nav, ''))[0]
                self.history.append((self.path, self.query, self.heading, previous_nav, self._selected_index(),
                                     deepcopy(self.series_context)))
            self.path = path
            self.query = query
            self.heading = heading or 'Movies'
            self.generation += 1
            if not retaining_cards:
                self.rows = []
                self.items, self.row_keys = [], set()
            self.next_path = None
            self.pending_path = None
            self.prefetch_at = 0
            self._page_retry_at,self._page_retry_count=0,0
            self.busy = True
            self._restore = selection
            token = self.generation
            self._catalog_refresh_generation=token if refreshing_watchlist else None
            self._progress_refresh_generation=token if refreshing_progress else None
            self._navigation_started = monotonic()
            if series_changed:self.views.pop((path,query),None)
            cached = self.views.get((path, query)) if path!='__watching__' and not retaining_cards else None
            if cached and cached.get('pagination_unavailable') and path!='__prime_watchlist__':cached=None
            self._personal_pagination_unavailable=False
            self.series_context = deepcopy(cached.get('series')) if cached else deepcopy(series)
        self.performance({'event': 'navigation_started', 'generation': token,
                          'section': self.nav if self.nav in _NAV_VALUES else 'detail_or_search',
                          'cached_view': bool(cached), 'retained_visible_cards': visible_cards,
                          'progress_chrome': False})
        self.setProperty('Nav', self.nav if self.nav in _NAV_VALUES else '')
        self.setProperty('NavigationBackdrop',_fit_art(intro_art,_HERO_ART) if intro_art else '')
        self.setProperty('SectionNotice','')
        # Kodi cannot focus an empty list. Park on this section's selector
        # before clearing it, then focus the new cards when they arrive.
        if not retaining_cards and self.getFocusId() in (200,410,415):
            self._focus_first_generation=token
            self.setFocusId(self._menu_control())
        self._section_label()
        self._season_properties()
        if not refreshing_progress:
            self.setProperty('ProgressRefreshing','');self.setProperty('ProgressRefreshArt','')
        if path == '__watching__':
            for key in list(self.views):
                if key[0] == '__watching__' or (self.views[key].get('series') or {}).get('watching_only'):
                    self.views.pop(key,None)
            if not refreshing_progress:
                self.clearList()
                self._replace_rail=False
                self._set_hero_status('IN PROGRESS','')
            else:
                selected=self._selected_index()
                art=(self.rows[selected].get('info') or {}) if selected<len(self.rows) else {}
                current=self.items[selected] if selected<len(self.items) else None
                frozen=(current.getArt('hero') or current.getArt('fanart')) if current is not None else ''
                self.setProperty('ProgressRefreshArt',frozen or _hero_art(art))
                self.setProperty('ProgressRefreshing','1')
            self.performance({'event':'progress_view_discarded','generation':token,'previous_cards':visible_cards,
                'cache_reuse':False,'result':'success'})
        else:
            self._pending_progress_return=None
        if not cached and not retaining_cards:
            self.clearList()
            self._replace_rail=False
            from .browse_status import browse_intro
            intro=browse_intro(self.nav,path,self.heading,query,self.series_context)
            self._set_hero_status(intro['title'],intro['message'],intro_art)
            self.performance({'event':'section_intro_shown','stage':'section_transition',
                'section':'search' if path=='search' else self.nav,'generation':token,
                'previous_section_cards_removed':True,'native_cards':self.getListSize(),
                'since_navigation_ms':round((monotonic()-self._navigation_started)*1000),'result':'success'})
        if cached:
            self.clearList()
            self._replace_rail = False
            with self.lock:
                self.rows, self.items = list(cached['rows']), list(cached['items'])
                self.row_keys = {row.get('url') for row in self.rows}
                self.next_path, self.prefetch_at = cached['next'], cached['prefetch_at']
                self.busy = False
                self.addItems(self.items)
                self.setCurrentListPosition(cached['selected'] if selection is None else selection)
                self._restore=None
            if self.items:
                self.setProperty('ShowSplash', '')
            self.performance({'event': 'navigation_restored', 'source': 'view_memory', 'cards': len(self.rows),
                              'generation': token,'section':path if path in _SECTION_PATHS else 'detail_or_search',
                              'native_items_reused':True,'fresh_refresh_pending':self.loader is not None and self.loader.handles(path),
                              'elapsed_ms': round((monotonic() - self._navigation_started) * 1000), 'result': 'success'})
            if self.loader is not None and self.loader.handles(path):
                self._catalog_refresh_generation=token;self.next_path=None;self.busy=True
                if path=='__prime_watchlist__':
                    # The rendered preview is reusable; its regional paging is
                    # not. Revalidate the current marketplace on every visit.
                    self._personal_pagination_unavailable=bool(cached.get('pagination_unavailable'))
                    loader=self.loader;future=loader.request(path,refresh=True)
                    future.add_done_callback(lambda result:self._personal_ready(token,path,loader,result))
                    return
                future=self.loader.request(path,refresh=True)
                future.add_done_callback(lambda result:self._cached_refresh_ready(token,path,result))
            return
        # Only a refresh of the same live list retains its own cards.
        self._replace_rail = False
        if not refreshing_progress:self.setProperty('ProgressRefreshing','')
        # Drop queued obsolete work. In-flight public requests continue only to
        # warm their cache; the worker stops waiting when generation changes.
        while True:
            try:
                self.jobs.get_nowait()
            except Empty:
                break
        if self.loader is not None and self.loader.handles(path):
            prefetched=self.loader.peek(path)
            if prefetched:
                self._publish(token,prefetched)
                self._catalog_refresh_generation=token
                if path=='__prime_watchlist__':self.setProperty('SectionNotice','Refreshing Watchlist…')
                self.performance({'event':'navigation_preload_bound','stage':'first_cards',
                    'generation':token,'cards':len(self.rows),
                    'cache_age_seconds':prefetched.get('_cache_age_seconds'),'fresh_refresh_pending':True,
                    'since_navigation_ms':round((monotonic()-self._navigation_started)*1000),'result':'success'})
            if path in ('__watching__','__prime_watchlist__'):
                loader=self.loader
                future=loader.request(path)
                self.performance({'event':'personal_request_dispatched','stage':'direct_personal_request',
                    'section':self.nav,'generation':token,'request_running':future.running(),
                    'catalog_worker_bypassed':True,'fresh_required':True,'result':'started'})
                future.add_done_callback(lambda result:self._personal_ready(token,path,loader,result))
                return
            if prefetched:
                future=self.loader.request(path,refresh=True)
                future.add_done_callback(lambda result:self._cached_refresh_ready(token,path,result))
                return
        self.jobs.put((token, path, query, selection))

    def _cached_refresh_ready(self,token,path,future):
        try:
            packet=future.result()
            if self.stop.is_set() or token!=self.generation:return
            if packet is None:raise RuntimeError('Catalog refresh returned no result')
            self._publish(token,packet)
            self.performance({'event':'cached_catalog_refresh_finished','stage':'background_refresh',
                'generation':token,'cards':len(self.rows),'result':'success'})
        except Exception as error:
            self.performance({'event':'cached_catalog_refresh_failed','stage':'background_refresh',
                'generation':token,'exception':exception_details(error),'result':'failure'})
            if token==self.generation and not self.stop.is_set():
                self.setProperty('SectionNotice','These titles could not be refreshed. Reopen the category to retry.')
        finally:
            with self.lock:
                if token==self.generation:self.busy=False

    def _personal_ready(self,token,path,loader,future,page_path=None,visited=()):
        page_path=page_path or path;continued=False
        try:
            if self.stop.is_set() or token!=self.generation:
                self.performance({'event':'personal_result_discarded','stage':'direct_personal_request',
                    'generation':token,'reason':'section_changed','result':'cancelled'});return
            packet=future.result()
            if packet is None:raise RuntimeError('The personal list ended without a provider result')
            deferred_watchlist=(path=='__prime_watchlist__' and bool(self.rows) and not packet.get('rows')
                and packet.get('next') and not packet.get('empty_confirmed'))
            if not deferred_watchlist:self._publish(token,packet)
            if (path=='__watching__' or not self.rows or deferred_watchlist) and packet.get('next'):
                next_page=packet['next']
                if next_page in visited or next_page==page_path or len(visited)>=99:
                    raise RuntimeError('The In Progress continuation repeated or exceeded its page limit')
                if self.stop.is_set() or token!=self.generation:return
                next_future=loader.request(next_page)
                continued=True
                next_future.add_done_callback(lambda result:self._personal_ready(token,path,loader,result,
                    next_page,visited+(page_path,)))
                self.performance({'event':'progress_next_batch_requested','stage':'fresh_history_paging',
                    'generation':token,'page_number':len(visited)+2,'cards_visible':len(self.rows),
                    'user_scroll_required':False,'result':'started'})
            with self.lock:
                if token!=self.generation or self.stop.is_set():return
                if not self.rows and not self.next_path and not continued:self._display_category_state()
            self.performance({'event':'personal_screen_ready','stage':'direct_personal_request',
                'section':self.nav,'generation':token,'cards':len(self.rows),
                'page_number':len(visited)+1,'history_complete':path=='__watching__' and not continued,
                'since_navigation_ms':round((monotonic()-self._navigation_started)*1000),
                'result':'partial' if continued or packet.get('pagination_unavailable') else 'success'})
        except Exception as error:
            self.performance({'event':'personal_request_failed','stage':'direct_personal_request',
                'generation':token,'page_number':len(visited)+1,'cards_retained':len(self.rows),
                'exception':exception_details(error),'result':'failure'})
            with self.lock:
                if token==self.generation and not self.stop.is_set():
                    if not self.rows:
                        if isinstance(error,CatalogSigninRequired):self._set_hero_status('Sign in','Use Sign out, then sign in again to view your Amazon account.')
                        else:self._display_category_state(unavailable=True)
                    else:self.setProperty('SectionNotice',
                        'Your Amazon session needs verification. Use Sign out, then sign in again.' if isinstance(error,CatalogSigninRequired) else
                        'Your Watchlist could not be refreshed. Select Watchlist to try again.' if path=='__prime_watchlist__' else
                        'Your In Progress list could not be refreshed. Select In Progress to try again.')
                    self._progress_refresh_generation=None
                    if path=='__watching__':
                        self._pending_progress_return=None;self.next_path=None
                    self.setProperty('ProgressRefreshing','')
        finally:
            if path=='__watching__':loader.release(page_path,future)
            with self.lock:
                if token==self.generation and not continued:
                    self.busy=False;self.pending_path=None

    def _selected_index(self):
        position = self.getCurrentListPosition()
        if position is None or position < 0:
            position = 0
        return position

    def _menu_control(self):
        if self.path=='search':return 304
        return {'movies':301,'tv':302,'home':309,'extras':303,'watchlist':308}.get(self.nav,301)

    def _focus_first_card(self):
        if not self.rows:
            self._focus_first_generation = self.generation
            return
        self._focus_first_generation = None
        self._focus_receipt = self.generation
        self.setCurrentListPosition(0)
        self.setFocusId(200)
        self.performance({'event': 'category_focus_requested', 'generation': self.generation,
                          'cards': len(self.rows), 'expected_index': 0, 'target_control': 200})

    def onFocus(self, control_id):
        # WINDOW_INIT and clearing/hiding a native container also produce focus
        # callbacks. Only input actions may turn menu focus into navigation.
        if control_id in (301,302,303,304,305,306,308,309):
            self.setProperty('GenreEntryFocus','1')
            self.performance({'event':'menu_focus_observed','stage':'native_focus',
                'control_id':control_id,'generation':self.generation,'section':self.nav,
                'navigation_dispatched':False,'result':'observed'})
        if control_id == 200 and self._focus_receipt == self.generation:
            self._focus_receipt = None
            self.performance({'event': 'category_focus_observed', 'generation': self.generation,
                'control_id': control_id, 'selected_index': self._selected_index(),
                'result': 'success' if self._selected_index() == 0 else 'failure'})

    def _report_display_text(self):
        from .display_text import observations,replacement_report
        codepoints=sorted(observations())
        replacements=replacement_report()
        signature=(tuple(codepoints),tuple(sorted(replacements['replacements'])),replacements['coverage_source'])
        if signature!=getattr(self,'_reported_display_signature',None):
            self._reported_display_signature=signature
            self.performance({'event':'display_text_glyph_inventory','stage':'text_rendering',
                'non_ascii_codepoints':codepoints,'font_and_replacements':replacements,
                'declared_font':'Body','native_fallback_font':'font13','control_id':330,'result':'success'})

    def _note_artwork(self, info):
        self._report_display_text()
        art = {key: info[key] for key in ('thumb', 'landscape', 'poster', 'fanart') if info.get(key)}
        used = set(art.values())
        used.update(_fit_art(value, width) for value in art.values() for width in (_CARD_ART, _HERO_ART))
        fresh = {value for value in used if isinstance(value, str) and value.startswith('https://')} - self.artwork
        if not fresh or self.account_name is None:
            return set()
        self.artwork.update(fresh)
        return fresh

    def _publish(self, token, packet):
        if getattr(self,'_catalog_refresh_generation',None)==token and not packet.get('_cache_preview'):
            self._publish_progress_refresh(token,packet,kind='watchlist' if self.path=='__prime_watchlist__' else 'catalog')
            return
        if self._progress_refresh_generation==token and self.path=='__watching__':
            self._publish_progress_refresh(token,packet)
            return
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
                if _top_selector_row(self.g.pluginid, row):
                    continue
                key = row.get('url')
                if key in seen:
                    continue
                seen.add(key)
                info = dict(row.get('info') or {})
                saved=self.detail_cache.get(_detail_path(self.g.pluginid,row)) or {}
                for field in _DETAIL_KEYS:
                    if not info.get(field) and (saved.get('info') or {}).get(field):info[field]=saved['info'][field]
                staged.append(dict(row, info=info))
        # Publish cheap card objects immediately. The visual worker renders only
        # the focused/nearby title plates after these cards are already usable.
        fresh_artwork = set()
        for row in staged:
            fresh_artwork.update(self._note_artwork(row['info']))
        if fresh_artwork:
            try:
                from .account_state import persist_if_current
                def track():
                    with (Path(self.g.DATA_PATH) / 'account-artwork.jsonl').open('a', encoding='utf-8') as output:
                        output.write(''.join(dumps(value) + '\n' for value in fresh_artwork))
                persist_if_current(self.g.DATA_PATH,self.account_generation,track)
            except OSError as error:
                self.performance({'event': 'artwork_tracking_failed', 'exception': exception_details(error), 'result': 'failure'})
        additions = [_item(row, self.title_cache.get((row.get('title') or '').upper(), ''),
                           self.image_cache,self.performance) for row in staged]
        with self.lock:
            if self.stop.is_set() or token != self.generation:
                return
            had = bool(self.rows)
            if additions:
                if self._replace_rail:
                    self.clearList()
                    self._replace_rail = False
                self.addItems(additions)
            self.rows.extend(staged)
            self.items.extend(additions)
            self.row_keys.update(row.get('url') for row in staged)
            for row in staged:
                if row.get('identity') and row.get('detail_url'):
                    self.known_details[row['identity']] = {'title': row['title'], 'lazyLoadURL': row['detail_url']}
            self.next_path = packet.get('next')
            self._page_retry_at,self._page_retry_count=0,0
            if packet.get('pagination_unavailable'):self._personal_pagination_unavailable=True
            if packet.get('notice') or not getattr(self,'_personal_pagination_unavailable',False):
                self.setProperty('SectionNotice',packet.get('notice') or '')
            count = len(self.rows)
            restore = self._restore
        if additions and restore is not None and restore < count:
            self.setCurrentListPosition(restore)
            with self.lock:
                if self._restore == restore:
                    self._restore = None
        if additions:
            self.setProperty('ShowSplash', '')
            if self._focus_first_generation == token:
                self._focus_first_card()
            self._section_label()
            self.performance({'event': 'cards_published', 'added': len(additions), 'total': count,
                'selected_index':self._selected_index(),
                'navigation_entries_hidden': sum(_top_selector_row(self.g.pluginid, row) for row in packet.get('rows') or []),
                'generation': token,
                'build_and_bind_ms': round((monotonic() - started) * 1000),
                'since_navigation_ms': round((monotonic() - self._navigation_started) * 1000), 'result': 'success'})
        if self.path == '__watching__':
            pending=self._pending_progress_return
            if pending and pending['generation']==token:pending['received']=True
            self._select_progress_return()

    def _publish_progress_refresh(self,token,packet,kind='progress'):
        from .account_state import current_account
        if self.account_generation is not None and not current_account(self.g.DATA_PATH,self.account_generation):return
        started=monotonic()
        with self.lock:
            if token!=self.generation or self.stop.is_set():return
            selected=self._selected_index();focus=self.getFocusId()
            selected_url=self.rows[selected].get('url') if selected<len(self.rows) else None
            rows=[];seen=set();previous={row.get('url'):row for row in self.rows}
            metadata_retained=0
            for row in packet.get('rows') or []:
                if _top_selector_row(self.g.pluginid,row) or row.get('url') in seen:continue
                if kind!='progress':
                    # A listing can be thinner than its earlier title detail.
                    # Retain presentation fields only, never access/progress
                    # evidence or membership omitted by a confirmed response.
                    info=dict(row.get('info') or {})
                    saved=self.detail_cache.get(_detail_path(self.g.pluginid,row)) or {}
                    for source in ((saved.get('info') or {}),(previous.get(row.get('url'),{}).get('info') or {})):
                        for field in _DETAIL_KEYS:
                            if info.get(field) in (None,'',[],{}) and source.get(field) not in (None,'',[],{}):
                                info[field]=source[field];metadata_retained+=1
                    row=dict(row,info=info)
                seen.add(row.get('url'));rows.append(row)
            if kind=='watchlist' and packet.get('pagination_unavailable'):
                # An incomplete response cannot prove that other cached titles
                # were removed. Retain them with the existing partial notice.
                rows.extend(row for row in self.rows if row.get('url') not in seen)
            keys=[row.get('url') for row in self.rows];items=list(self.items)
            inserted=removed=moved=unchanged=0;fresh_artwork=set()
            for index,row in enumerate(rows):
                key=row.get('url')
                if key in keys and row==previous.get(key):
                    old=keys.index(key);existing=items[old]
                    if old!=index:
                        self.removeItem(old);self.addItem(existing,index)
                        keys.pop(old);items.pop(old);keys.insert(index,key);items.insert(index,existing);moved+=1
                    unchanged+=1
                    continue
                item=_item(row,self.title_cache.get((row.get('title') or '').upper(),''),self.image_cache,self.performance)
                fresh_artwork.update(self._note_artwork(row.get('info') or {}))
                if row.get('identity') and row.get('detail_url'):
                    self.known_details[row['identity']]={'title':row['title'],'lazyLoadURL':row['detail_url']}
                if key in keys:
                    old=keys.index(key);existing=items[old]
                    existing.setLabel(item.getLabel())
                    for prop in ('Meta','CardMeta','Watched','ViewingLabel','ProgressLabel','Plot','Year','Runtime','Rating','Genre','MediaType','Id','Identity','TitleArt','HeroSource'):
                        existing.setProperty(prop,item.getProperty(prop))
                    values=row.get('info') or {};known_total=duration_seconds(values);total=known_total or 100
                    position=position_seconds(values) if known_total else _progress(values)
                    try:existing.getVideoInfoTag().setResumePoint(position if position is not None else total*_progress(values)/100,total)
                    except Exception as error:self.performance({'event':'progress_indicator_update_failed','stage':'in_place_refresh',
                        'exception':exception_details(error),'result':'failure'})
                    existing.setArt({name:item.getArt(name) for name in ('thumb','landscape','poster','icon','fanart','hero') if item.getArt(name)})
                    if old!=index:
                        self.removeItem(old);self.addItem(existing,index)
                        keys.pop(old);items.pop(old);keys.insert(index,key);items.insert(index,existing);moved+=1
                else:
                    self.addItem(item,index);keys.insert(index,key);items.insert(index,item);inserted+=1
            while len(keys)>len(rows):
                self.removeItem(len(keys)-1);keys.pop();items.pop();removed+=1
            self.rows,self.items=rows,items;self.row_keys=set(keys);self.next_path=packet.get('next')
            if fresh_artwork:
                from .account_state import persist_if_current
                def track():
                    with (Path(self.g.DATA_PATH)/'account-artwork.jsonl').open('a',encoding='utf-8') as output:
                        output.write(''.join(dumps(value)+'\n' for value in fresh_artwork))
                try:persist_if_current(self.g.DATA_PATH,self.account_generation,track)
                except OSError as error:self.performance({'event':'artwork_tracking_failed','exception':exception_details(error),'result':'failure'})
            self._progress_refresh_generation=None;self._catalog_refresh_generation=None;self._replace_rail=False
            if rows:self.setCurrentListPosition(next((i for i,row in enumerate(rows) if row.get('url')==selected_url),min(selected,len(rows)-1)))
            if focus==200 and not rows:self.setFocusId(self._menu_control())
            self.setProperty('ShowSplash','');self.setProperty('ProgressRefreshing','')
            if kind!='progress':
                self._personal_pagination_unavailable=bool(packet.get('pagination_unavailable'))
                self.setProperty('SectionNotice',packet.get('notice') or '')
            pending=self._pending_progress_return
            if pending and pending['generation']==token:pending['received']=True
        if kind=='progress':self._select_progress_return()
        self.performance({'event':kind+'_reconciled_in_place','generation':token,'cards':len(rows),
            'inserted':inserted,'removed':removed,'moved':moved,'clear_list_called':False,
            'unchanged_native_items_retained':unchanged,'missing_presentation_fields_retained':metadata_retained,
            'cards_with_artwork':sum(bool(_rail_art(row.get('info') or {})) for row in rows),
            'cards_with_descriptions':sum(bool((row.get('info') or {}).get('plot')) for row in rows),
            'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})

    def _work(self):
        try:
            # Restore early main-catalog loading without opening every genre.
            bridge = CatalogGateway(self.g)
            self.bridge = bridge
            self.metadata_bridge = CatalogGateway(self.g)
            self.loader = StorefrontLoader(self.g,ready=self._catalog_preload_ready)
            self.image_cache = ImageCache(self.g, self.loader.epoch)
            self.genre_image_cache=ImageCache(self.g,self.loader.epoch,genre=True)
            from .genre_backdrops import GenreBackdrops
            self.genre_backdrops=GenreBackdrops(self.g,self.loader.epoch)
            self.description_cache = DescriptionCache(self.g, self.loader.epoch, self.performance)
            self.loader.preload_startup()
            record(self.g, 'catalog_worker_ready')
            self._catalog_loop(bridge)
        except Exception as error:
            record(self.g, 'provider_init_failed', error)
            self._startup=None;self.setProperty('StartupSplash','')
            if not self.stop.is_set():
                self._set_hero_status('SMOOTH PRIME', 'COULD NOT OPEN THE CATALOG. YOUR SIGN-IN IS SAVED.')
            self.busy = False
            self.stop.set()
        finally:
            self.stop.set()
            for bridge in (self.bridge,self.metadata_bridge):
                if bridge is not None:bridge.close()
            if self.genre_backdrops is not None:self.genre_backdrops.close()
            if self.loader is not None:
                self.loader.close()
            if self.genre_image_cache is not None:self.genre_image_cache.close()
            if self.image_cache is not None:
                self.image_cache.close()

    def _display_category_state(self,unavailable=False):
        from .browse_status import category_status
        state=category_status(self.nav,self.path,self.heading,self.query,self.series_context,unavailable)
        self._set_hero_status(state['title'],state['message'])
        self.performance({'event':'category_state_displayed','stage':'category_message','section':self.nav,
            'category_kind':state['kind'],'state':state['state'],'cards':len(self.rows),'generation':self.generation,
            'result':'unavailable' if unavailable else 'empty'})

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
            continuation = path.endswith('/nextPage')
            started = monotonic()
            future = packet = None
            try:
                record(self.g, 'catalog_request_started')
                if path.startswith(('__genre__/','__service__/')):self.loader.register_genre(path)
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
                        if path == '__watching__':self.loader.release(path,future)
                        continue
                    packet = future.result()
                    if path == '__watching__' and packet is None:
                        raise RuntimeError('The fresh In Progress request ended without a provider result')
                    if packet:
                        self._publish(token, packet)
                    if path == '__watching__':self.loader.release(path,future)
                elif self.series_context and query is None:
                    packet = self._episode_listing(path, token)
                    if packet is not None:
                        self._publish(token, packet)
                    elif token!=self.generation or self.stop.is_set():
                        self.performance({'event':'series_navigation_cancelled','stage':'season_listing',
                            'generation':token,'current_generation':self.generation,
                            'elapsed_ms':round((monotonic()-started)*1000),
                            'reason':'view_or_session_changed','result':'cancelled'})
                else:
                    with self.bridge_lock:
                        if token != self.generation or self.stop.is_set():
                            continue
                        self._sync_detail_routes(bridge)
                        bridge.browse_progressive(
                            path, lambda packet: self._publish(token, packet),
                            lambda: token == self.generation and not self.stop.is_set(), query=query)
                record(self.g, 'catalog_request_finished')
                with self.lock:
                    if token == self.generation and not self.stop.is_set():
                        if path.endswith('/nextPage') and len(self.rows) > page_start:
                            self.prefetch_at = max(0,page_start-12)
                            self.performance({'event':'early_page_prefetch_armed','page_start_index':page_start,
                                'request_at_index':self.prefetch_at,'loaded_cards':len(self.rows),'result':'success'})
                        if not self.rows and not self.next_path:
                            self.clearList()
                            self._replace_rail = False
                            self._display_category_state()
            except Exception as error:
                record(self.g, 'catalog_request_failed', error)
                xbmc.log('[Prime Scene] Catalog operation failed: ' + type(error).__name__, xbmc.LOGERROR)
                with self.lock:
                    if token == self.generation and not self.stop.is_set():
                        auth_required=isinstance(error,CatalogSigninRequired)
                        if continuation and self.rows:
                            self._page_retry_count+=1
                            self.next_path=None if auth_required else path
                            delay=min(60,5*2**min(self._page_retry_count-1,4))
                            self._page_retry_at=monotonic()+delay
                            self.setProperty('SectionNotice',
                                'Your Amazon session needs verification. Use Sign out, then sign in again.' if auth_required else
                                'More titles could not be retrieved. Retrying shortly.')
                            self.performance({'event':'catalog_page_retry_scheduled','stage':'continuation',
                                'generation':token,'attempt':self._page_retry_count,'retry_seconds':None if auth_required else delay,
                                'continuation_retained':not auth_required,'cards_retained':len(self.rows),
                                'elapsed_ms':round((monotonic()-started)*1000),
                                'exception':exception_details(error),'result':'authentication_required' if auth_required else 'retry_pending'})
                        if path == '__watching__':
                            self._pending_progress_return = None
                            self._progress_refresh_generation = None
                            self.setProperty('ProgressRefreshing','')
                        if not self.rows:
                            self.clearList()
                            self._replace_rail = False
                            if isinstance(error,CatalogSigninRequired):
                                self._set_hero_status('Sign in','Use Sign out, then sign in again to view this category.')
                            else:self._display_category_state(unavailable=True)
                        else:
                            self.performance({'event':'catalog_failure_content_retained','stage':'category_message',
                                'section':self.nav,'cards':len(self.rows),'generation':self.generation,'result':'failure'})
            finally:
                with self.lock:
                    if token == self.generation:
                        self.busy = False
                        self.pending_path = None
                        self._prefetch()
                if path == '__watching__':
                    self.loader.release(path,future)
                    packet=None;future=None

    def _prefetch(self):
        with self.lock:
            if (self.genre_owner is not None or self.busy or not self.next_path or self.stop.is_set()
                    or monotonic()<self._page_retry_at or xbmc.getCondVisibility('Window.IsVisible(fullscreenvideo)')):
                return
            if self._selected_index() >= self.prefetch_at:
                self.performance({'event':'lookahead_page_requested','generation':self.generation,
                    'selected_index':self._selected_index(),'request_at_index':self.prefetch_at,
                    'loaded_cards':len(self.rows),'result':'success'})
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

    def _season_properties(self):
        context = self.series_context or {}
        self.setProperty('SeasonLabel', clean(context.get('season_label') or ''))
        self.setProperty('SeasonSwitchable', 'true' if context.get('view')=='episodes'
            and context.get('season_label') and len(context.get('seasons') or []) > 1 else '')

    def _open_series(self, row):
        path = _browse_path(self.g.pluginid, row.get('url'))
        if not path and row.get('identity') in self.known_details:
            path = '__storefront__/' + row['identity']
        if not path:
            self.performance({'event': 'series_route_missing', 'stage': 'click', 'result': 'failure'})
            self.g.dialog.ok('Smooth Prime', 'This show did not provide an episode-list link. Open Diagnostics for the report.')
            return
        context = {'title': row.get('title') or 'TV show', 'root_path': path,
                   'opened_kind': _kind(row.get('info')), 'seasons': [],
                   'watching_only': bool(row.get('watching_only')),
                   'view':'episodes' if row.get('season_choice') else 'seasons'}
        if row.get('season_choice') and self.series_context:
            context=deepcopy(self.series_context)
            context.update(view='episodes',requested_path=path,
                requested_season=(row.get('info') or {}).get('season'),season_label=row.get('title') or '')
        if context['watching_only'] or (self.views.get((path,None),{}).get('series') or {}).get('watching_only'):
            self.views.pop((path,None),None)
        if _kind(row.get('info')) == 'season':
            context['requested_season'] = (row.get('info') or {}).get('season')
        context.update(season_selection_started=monotonic(),season_request_id=token_hex(8))
        self.performance({'event': 'title_click', 'action': 'browse_episodes',
                          'media_kind': _kind(row.get('info')),'request_id':context['season_request_id'], 'result': 'success'})
        self.navigate(path, heading=context['title'], series=context,
            intro_art=_hero_art(row.get('info') or {}))

    def _episode_listing(self, path, token):
        with self.lock:
            if token!=self.generation or self.stop.is_set() or not self.series_context:return None
            context = deepcopy(self.series_context)
        continuation = self.bridge.continuations.get(path) if self.bridge else None
        # Season links are account-scoped title metadata, not a progress
        # snapshot. Reuse prepared links even when entering from In Progress;
        # the selected season's episode/progress read below remains fresh.
        season_view=context.get('view')=='seasons'
        prepared=(self.detail_cache.get(path) or {}).get('packet') if season_view else None
        season_source='prepared_title' if prepared and prepared.get('seasons') else 'shared_title_request'
        if prepared and prepared.get('seasons'):packet=deepcopy(prepared)
        elif season_view:packet=self._fetch_detail(path)
        else:packet=(self._read_tv_detail(path, token) if context.get('watching_only') or continuation and continuation[1] == 'tv_detail'
                    else self._fetch_detail(path))
        if packet is None and not self.stop.is_set() and token == self.generation:
            packet = self._read_tv_detail(path, token)
        if packet in (None, 'wait') or token != self.generation or self.stop.is_set():
            return None
        self._remember(path, None, 'browse', packet)
        seasons = packet.get('seasons') or [row for row in packet.get('rows') or [] if _is_season_row(row)]
        if seasons:
            unique = {_browse_path(self.g.pluginid, row.get('url')): row for row in seasons
                      if _browse_path(self.g.pluginid, row.get('url'))}
            seasons = sorted(unique.values(), key=_season_number)
            context['seasons'] = seasons
        else:
            seasons = context.get('seasons') or []
        if context.get('view')=='seasons':
            if not seasons:
                self.performance({'event':'series_seasons_missing','stage':'season_listing',
                    'selected_kind':_kind(packet.get('info')),'rows':len(packet.get('rows') or []),'result':'failure'})
                raise RuntimeError('Amazon did not return season links for this show. Open Diagnostics.')
            rows=[]
            for season in seasons:
                info=dict(packet.get('info') or {})
                info.update({k:v for k,v in (season.get('info') or {}).items() if v not in (None,'',[],{})})
                info['mediatype']='season'
                rows.append(dict(season,info=info,video=False,season_choice=True,watching_only=context.get('watching_only',False)))
            context['seasons']=seasons;context['season_label']=''
            with self.lock:
                if token!=self.generation or self.stop.is_set():return None
                self.series_context=context;self._season_properties()
            self.performance({'event':'series_seasons_published','stage':'season_listing','season_count':len(rows),
                'request_id':context.get('season_request_id'),'source':season_source,'season_metadata_only':True,
                'click_to_seasons_ms':round((monotonic()-context.get('season_selection_started',monotonic()))*1000),
                'automatic_episode_selection':False,'result':'success'})
            return dict(packet,rows=rows,next=None)
        if seasons and not path.endswith('/nextPage'):
            requested_path = context.get('requested_path')
            requested_number = context.get('requested_season')
            if requested_number is None and context.get('opened_kind') != 'tvshow' and _kind(packet.get('info')) == 'season':
                requested_number = (packet.get('info') or {}).get('season')
            chosen = next((row for row in seasons if _browse_path(self.g.pluginid, row['url']) == requested_path), None)
            if chosen is None and requested_number is not None:
                chosen = next((row for row in seasons if str((row.get('info') or {}).get('season')) == str(requested_number)), None)
            if chosen is None:
                chosen = next((row for row in seasons if 0 < _season_number(row) < 1 << 20), seasons[0])
            season_path = _browse_path(self.g.pluginid, chosen['url'])
            chosen_number = (chosen.get('info') or {}).get('season')
            group = next((group for group in packet.get('episode_groups') or []
                          if group.get('identity') == chosen.get('identity')), None)
            if group and group.get('rows'):
                packet = dict(packet, rows=group['rows'])
            present_episodes = [row for row in packet.get('rows') or [] if _kind(row.get('info')) == 'episode']
            already_listed = (chosen_number is not None and bool(present_episodes)
                              and all(str((row.get('info') or {}).get('season')) == str(chosen_number)
                                      for row in present_episodes))
            current_season = chosen.get('identity') == packet.get('selected_id')
            if (not present_episodes or not already_listed and not current_season) and season_path != path:
                packet = self._read_tv_detail(season_path,token) if context.get('watching_only') else self._fetch_detail(season_path)
                if packet is None and not self.stop.is_set() and token == self.generation:
                    packet = self._read_tv_detail(season_path, token)
                if packet in (None, 'wait') or token != self.generation or self.stop.is_set():
                    return None
                self._remember(season_path, None, 'browse', packet)
                group = next((group for group in packet.get('episode_groups') or []
                              if group.get('identity') == chosen.get('identity')), None)
                if group and group.get('rows'):
                    packet = dict(packet, rows=group['rows'])
            context['requested_path'] = season_path
            context['requested_season'] = (chosen.get('info') or {}).get('season')
            context['season_label'] = 'Season ' + str(context['requested_season']) if context['requested_season'] is not None else str(chosen['title'])
        episodes = [row for row in packet.get('rows') or [] if _kind(row.get('info')) == 'episode']
        if not episodes:
            direct = self._read_tv_detail((context or {}).get('requested_path') or path, token)
            if direct is not None:
                packet = direct
                episodes = [row for row in packet.get('rows') or [] if _kind(row.get('info')) == 'episode']
                if packet.get('seasons'):
                    context['seasons'] = packet['seasons']
                    seasons = packet['seasons']
        if token != self.generation or self.stop.is_set():
            return None
        if context.get('watching_only'):
            from .watch_home import complete
            before = len(episodes)
            episodes = [row for row in episodes if not complete(row.get('info') or {})]
            self.performance({'event':'home_episodes_filtered','stage':'series_navigation',
                'source_episodes':before,'unfinished_episodes':len(episodes),'result':'success'})
            packet = dict(packet,rows=episodes)
            if not episodes:
                current_number = context.get('requested_season')
                next_season = next((row for row in seasons if current_number is not None
                    and _season_number(row) > float(current_number) and _season_number(row) < 1 << 20),None)
                if next_season is not None:
                    next_path = _browse_path(self.g.pluginid,next_season['url'])
                    if next_path and next_path != path:
                        context['requested_path'] = next_path
                        context['requested_season'] = (next_season.get('info') or {}).get('season')
                        with self.lock:
                            if token!=self.generation or self.stop.is_set():return None
                            self.series_context = context
                        return self._episode_listing(next_path,token)
        if episodes:
            episodes.sort(key=_episode_order)
            if not context.get('season_label'):
                number = (episodes[0].get('info') or {}).get('season')
                context['season_label'] = 'Season ' + str(number) if number is not None else 'Episodes'
            packet = dict(packet, rows=episodes)
        else:
            packet = dict(packet, rows=[])
        self.performance({'event': 'episode_listing_ready', 'stage': 'series_navigation',
                          'generation': token, 'season_count': len(seasons), 'episode_count': len(episodes),
                          'row_count': len(packet.get('rows') or []),
                          'selected_kind': _kind(packet.get('info')), 'result': 'success' if episodes else 'unclassified'})
        with self.lock:
            if token != self.generation or self.stop.is_set():return None
            self.series_context = context
            self._season_properties()
        return packet

    def _read_tv_detail(self, path, token):
        if self.bridge is None or self.stop.is_set() or token != self.generation:
            return None
        with self.bridge_lock:
            if self.stop.is_set() or token!=self.generation:return None
            self._sync_detail_routes(self.bridge)
            packet = self.bridge.read_tv(path, lambda: not self.stop.is_set() and token == self.generation)
        if packet is not None:
            self._remember(path, None, 'browse', packet)
        return packet

    def _choose_season(self):
        context = deepcopy(self.series_context)
        generation=self.generation
        seasons = (context or {}).get('seasons') or []
        if len(seasons) < 2:
            return
        labels = [clean(str(row.get('title') or 'Season ' + str((row.get('info') or {}).get('season', '')))) for row in seasons]
        selected = self.g.dialog.select('Choose season', labels)
        if selected is None or selected < 0 or selected >= len(seasons):
            return
        if self.stop.is_set() or generation!=self.generation:return
        row = seasons[selected]
        path = _browse_path(self.g.pluginid, row['url'])
        if not path:
            return
        context['requested_path'] = path
        context['requested_season'] = (row.get('info') or {}).get('season')
        context['season_label'] = labels[selected]
        context['view'] = 'episodes'
        # A season change stays within this show's page. Back returns to the
        # catalog, keeping the original show's selected position.
        self.navigate(path, remember=False, heading=context['title'], series=context)
        self.setFocusId(200)

    def _visual_loop(self):
        """Keep expensive title drawing and image downloads away from input/binding."""
        while not self.stop.is_set():
            try:self._warm_genre_backdrops()
            except Exception as error:self.performance({'event':'genre_background_preload_failed','stage':'tile_artwork',
                'exception':exception_details(error),'result':'failure'})
            if xbmc.getCondVisibility('Window.IsVisible(fullscreenvideo)'):
                self.stop.wait(0.2);continue
            self._report_display_text()
            try:self._prepare_catalog_preload()
            except Exception as error:self.performance({'event':'catalog_visual_preload_failed','stage':'preload_artwork',
                'exception':exception_details(error),'result':'failure'})
            if self.genre_owner is not None:
                try:
                    self._preload_grid_focus()
                    self._refresh_tile_art()
                except Exception as error:self.performance({'event':'browse_tile_art_failed','stage':'tile_artwork','exception':exception_details(error),'result':'failure'})
                try:self._advance_startup()
                except Exception as error:self.performance({'event':'startup_progress_failed','stage':'startup_preload',
                    'exception':exception_details(error),'result':'failure'})
                self.stop.wait(.1)
                continue
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
                    urls.append(_fit_art(_rail_art(info), _CARD_ART))
                    if offset < 3:
                        urls.append(_hero_art(info))
                if self.image_cache is not None:
                    self.image_cache.warm([url for url in urls if url],priority=True)
                for offset, (position, row, item) in enumerate(nearby):
                    if self.stop.is_set() or token != self.generation:
                        break
                    title = (clean(row.get('title') or '') or 'Untitled').upper()
                    if offset < 3 and title not in self.title_cache:
                        started = monotonic()
                        from .account_state import persist_if_current
                        rendered = []
                        def draw():
                            rendered.append(render_title(title, Path(self.g.DATA_PATH) / 'title-art', self.performance))
                        if not persist_if_current(self.g.DATA_PATH, self.account_generation, draw):
                            break
                        plate = rendered[0]
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
                    thumb = _fit_art(_rail_art(info), _CARD_ART)
                    local = self.image_cache.local(thumb)
                    if local and local != thumb and item.getProperty('LocalThumb') != local:
                        item.setArt({'thumb': local, 'landscape': local})
                        item.setProperty('LocalThumb', local)
                    hero = _hero_art(info)
                    local = self.image_cache.local(hero)
                    if local and local != hero and item.getProperty('LocalHero') != local:
                        art={'hero':local}
                        if info.get('fanart'):art['fanart']=local
                        item.setArt(art)
                        item.setProperty('LocalHero', local)
                    if offset==0:
                        signature=(token,position,hero,local)
                        if signature!=getattr(self,'_reported_background_signature',None):
                            self._reported_background_signature=signature
                            self.performance({'event':'background_artwork_selected','stage':'hero_binding',
                                'generation':token,'index':position,'requested_width':_HERO_ART,'artwork_policy':ARTWORK_POLICY,
                                'resolution_policy':'provider_original_master','artificial_width_cap':False,
                                'source':'provider_heroshot' if info.get('fanart') else 'cover_preview',
                                'address':route(hero) if hero else None,'local_cache_used':bool(local and local!=hero),
                                'result':'success' if hero else 'unavailable'})
            except Exception as error:
                self.performance({'event': 'visual_work_failed', 'exception': exception_details(error), 'result': 'failure'})
                self.stop.wait(0.5)
            self.stop.wait(0.1)

    def _catalog_preload_ready(self,path,packet):
        if self.stop.is_set() or path=='__watching__' or not packet.get('rows'):return
        try:self.preloaded_catalogs.put_nowait((path,packet))
        except Full:
            self.performance({'event':'catalog_visual_preload_skipped','stage':'preload_artwork',
                'reason':'bounded_queue','result':'cancelled'})

    def _prepare_catalog_preload(self):
        try:path,packet=self.preloaded_catalogs.get_nowait()
        except Empty:return
        if self.stop.is_set() or self.loader is None or not self.loader.current():return
        started=monotonic();rows=(packet.get('rows') or [])[:10];urls=[];fresh=set()
        for index,row in enumerate(rows):
            info=row.get('info') or {};fresh.update(self._note_artwork(info))
            urls.append(_fit_art(_rail_art(info),_CARD_ART))
            if index<2:urls.append(_hero_art(info))
        if fresh:
            from .account_state import persist_if_current
            def track():
                with (Path(self.g.DATA_PATH)/'account-artwork.jsonl').open('a',encoding='utf-8') as output:
                    output.write(''.join(dumps(value)+'\n' for value in fresh))
            persist_if_current(self.g.DATA_PATH,self.account_generation,track)
        if self.image_cache is not None:self.image_cache.warm([url for url in urls if url],priority=path==self.loader.focus_path)
        # Listing descriptions are already in the exact cached packet. Prepare
        # its leading title plate while the genre selector is still visible.
        if rows and self.genre_owner is not None:
            title=clean(rows[0].get('title') or '').upper()
            if title and title not in self.title_cache:
                from .account_state import persist_if_current
                def draw():self.title_cache[title]=render_title(title,Path(self.g.DATA_PATH)/'title-art',self.performance)
                persist_if_current(self.g.DATA_PATH,self.account_generation,draw)
        self.performance({'event':'catalog_visual_preload_ready','stage':'preload_artwork',
            'section':StorefrontLoader.diagnostic_section(path),'cards':len(rows),
            'descriptions_ready':sum(bool((row.get('info') or {}).get('plot')) for row in rows),
            'image_requests_queued':sum(bool(url) for url in urls),
            'prepare_elapsed_ms':round((monotonic()-started)*1000),'result':'success'})

    def _preload_grid_focus(self):
        self._observe_genre_panel()
        self._dispatch_genre_selection()
        if self.loader is None or self.genre_owner not in (301,302):return
        index=self.getControl(410).getSelectedPosition()
        if not 0<=index<len(self._genre_rows):return
        if self._genre_selection and self._genre_selection['index']!=index:
            self._cancel_genre_selection('focus_changed')
        path=self._genre_rows[index]['path'];now=monotonic()
        if self._genre_focus is None or self._genre_focus[0]!=path:
            self._genre_focus=(path,now);return
        if now-self._genre_focus[1]>=.1:
            adjacent=tuple(row['path'] for row in self._genre_rows[index+1:index+2])
            self.loader.preload_focus(path,adjacent)

    def _warm_genre_backdrops(self):
        gallery,cache=self.genre_backdrops,self.genre_image_cache
        if gallery is None or cache is None:return
        if self._genre_discovery=='failed' and self._startup is None and monotonic()>=getattr(self,'_genre_retry_at',0):
            self._genre_retry_at=monotonic()+60;self._genre_discovery='pending'
            self.genre_requested.add('movies');self.genre_jobs.put('movies')
            self.performance({'event':'background_genre_discovery_retry','stage':'genre_grid','result':'retrying'})
        with self.lock:rows=list(self.genre_cache.get('movies',[]))
        gallery.request(rows)
        priority=[];remaining=[]
        for section in ('movies','tv'):
            for index,row in enumerate(rows):
                image=gallery.image(section,row['address'])
                if image:(priority if section=='movies' and index<4 else remaining).append(image)
        signature=tuple(dict.fromkeys(priority))
        for section,paths in (('movies',('__prime_movies__','__purchases__/movies')),('tv',('__prime_tv__','__purchases__/tv'))):
            remaining.extend(gallery.image(section,path) for path in paths if gallery.image(section,path))
        cache.warm(priority,priority=signature!=self._genre_warm_signature)
        self._genre_warm_signature=signature;cache.warm(remaining)
        for image in dict.fromkeys(priority+remaining):
            local=cache.local(image)
            if local!=image:
                cache.dimensions(image)
                self._track_tile_art(image);self._track_tile_art(local)

    def _advance_startup(self):
        if self.stop.is_set():return
        startup=self._startup
        if startup is None and not self.getProperty('ArtworkRecovery'):return
        now=monotonic();elapsed=now-startup['started'] if startup else 0
        required=ready=pending=unavailable=0;images=[]
        gallery,cache=self.genre_backdrops,self.genre_image_cache
        if gallery is not None and cache is not None:
            for row in self.genre_cache.get('movies',[])[:4]:
                required+=1;address=row['address'];image=gallery.image('movies',address)
                status=gallery.state('movies',address)
                if image:
                    try:status=cache.state(image)
                    except Exception as error:status={'state':'image_cache_error','exception':exception_details(error)}
                if status['state'] in ('ready_original','ready_provider_source'):ready+=1
                elif status['state'] in ('metadata_pending','metadata_received_resolving_backdrop','image_download_pending','image_not_queued'):pending+=1
                else:unavailable+=1
                images.append(dict(status,genre=row['title'],genre_address=route(address),image_address=route(image) if image else None))
        else:pending=1
        complete=required>0 and pending==0 and unavailable==0
        if startup is None:
            if complete:
                self.setProperty('ArtworkRecovery','')
                if self.getProperty('GenreMessage')=='Some Prime Video artwork is not available yet. You can browse while it retries.':self.setProperty('GenreMessage','')
            return
        signature=(self._genre_discovery,required,ready,pending,unavailable)
        if signature!=startup['signature']:
            startup['signature']=signature
            self.performance({'event':'startup_artwork_progress','stage':'startup_preload','discovery':signature[0],
                'section':'movies','target_images':4,'visible_images_required':required,'visible_images_ready':ready,
                'pending':pending,'unavailable':unavailable,'images':images,
                'elapsed_ms':round(elapsed*1000),'result':'preparing'})
        if self._genre_discovery=='failed' and elapsed<45 and startup.get('discovery_retries',0)<2:
            if now>=startup.get('retry_at',0):
                startup['discovery_retries']=startup.get('discovery_retries',0)+1;startup['retry_at']=now+5
                self._genre_discovery='pending';self.genre_requested.add('movies');self.genre_jobs.put('movies')
                self.performance({'event':'startup_discovery_retried','stage':'startup_preload',
                    'artwork_attempt':startup['discovery_retries']+1,'elapsed_ms':round(elapsed*1000),'result':'retrying'})
        if complete and startup['bound_at'] is None:startup['bound_at']=now
        if not complete:startup['bound_at']=None
        settled=complete and now-startup['bound_at']>=.2
        if elapsed<3 or not settled and elapsed<45:return
        if self._startup is not startup:return
        if not settled:
            try:
                available=[i for i,row in enumerate(self._genre_rows)
                    if self._tile_items.get(row['path']) is not None and self._tile_items[row['path']].getProperty('TileArt')]
                if available:
                    listing=self.getControl(410);selected=listing.getSelectedPosition()
                    if selected not in available:listing.selectItem(available[0])
            except Exception as error:self.performance({'event':'startup_recovery_selection_failed','stage':'startup_preload',
                'exception':exception_details(error),'result':'failure'})
            self.setProperty('ArtworkRecovery','1')
            self.setProperty('GenreMessage','Some Prime Video artwork is not available yet. You can browse while it retries.')
            self.performance({'event':'startup_artwork_recovery','stage':'startup_preload',
                'elapsed_ms':round(elapsed*1000),'visible_images_required':required,'visible_images_ready':ready,
                'pending':pending,'unavailable':unavailable,'images':images,'discovery':self._genre_discovery,
                'navigation_unblocked':True,'metadata_retries_continue':True,'result':'partial' if ready else 'unavailable'})
        self._startup=None;self.setProperty('StartupSplash','');self.setProperty('StartupNotice','')
        try:self.setFocusId(301)
        except Exception as error:self.performance({'event':'startup_focus_failed','stage':'startup_preload',
            'exception':exception_details(error),'result':'failure'})
        self.performance({'event':'startup_splash_released','stage':'startup_preload','elapsed_ms':round(elapsed*1000),
            'visible_images_required':required,'visible_images_ready':ready,'unavailable':unavailable,
            'pending':pending,'minimum_display_seconds':3,'maximum_artwork_wait_seconds':45,
            'backgrounds_bundled':False,'background_preload_continues':True,'selected_control':self.getFocusId(),
            'result':'success' if settled else 'partial' if ready else 'unavailable'})

    def _detail_loop(self):
        while not self.stop.is_set():
            started=monotonic()
            try:
                job = self.detail_jobs.get(timeout=0.05)
            except Empty:
                try:
                    if self._allow_enrich and not self.stop.is_set() and not xbmc.getCondVisibility('Window.IsVisible(fullscreenvideo)'):
                        self._queue_enrich()
                except Exception as error:
                    self._enriched_focus=None
                    self.performance({'event':'metadata_worker_failed','stage':'read_ahead','generation':self.generation,
                        'elapsed_ms':round((monotonic()-started)*1000),'exception':exception_details(error),'result':'failure'})
                    self.stop.wait(0.5)
                continue
            if self.stop.is_set() or not job:
                return
            try:
                if job[0] == 'enrich' and job[2] == self.generation and _detail_path(self.g.pluginid, self._selected_row()) == job[1]:
                    self._enrich(job[1])
            except Exception as error:
                self.performance({'event':'metadata_worker_failed','stage':'queued_metadata','generation':self.generation,
                    'elapsed_ms':round((monotonic()-started)*1000),'exception':exception_details(error),'result':'failure'})

    def _play_loop(self):
        while not self.stop.is_set():
            try:job=self.play_jobs.get(timeout=0.1)
            except Empty:continue
            self._play_resolved(*job)

    def _queue_enrich(self):
        """Read the focused title and next ten descriptions before selection."""
        if self.bridge is None or self.stop.is_set():
            return
        if self._prepare_focused_seasons():return
        with self.lock:
            selected = self._selected_index()
            generation = self.generation
            order = (list(range(selected, min(selected + _DESCRIPTION_AHEAD + 1, len(self.rows))))
                     + list(range(max(0, selected - _DESCRIPTION_BEHIND), selected)))
            candidates = [(index, dict(self.rows[index])) for index in order]
            if self.genre_owner in (301,302) and self.loader is not None:
                section='movies' if self.genre_owner==301 else 'tv'
                focused=self.loader.focus_path or ''
                preload=focused if focused.startswith('__genre__/'+section+'/') else '__prime_'+section+'__'
                packet=self.loader.peek(preload) or {}
                candidates=list(enumerate((packet.get('rows') or [])[:3]))
                for _,row in candidates:
                    if row.get('identity') and row.get('detail_url'):
                        self.known_details[row['identity']]={'title':row.get('title'),'lazyLoadURL':row['detail_url']}
        window = (generation, selected, len(candidates))
        if window != self._description_window:
            self._description_window = window
            self.performance({'event': 'description_window_started', 'stage': 'read_ahead',
                              'generation': generation, 'selected_index': selected,
                              'candidate_count': len(candidates), 'ahead': _DESCRIPTION_AHEAD,
                              'backdrop_ahead':_BACKDROP_AHEAD})
        for index, row in candidates:
            if self.stop.is_set() or generation != self.generation:
                return
            path = _detail_path(self.g.pluginid, row)
            if not path:continue
            row_info=row.get('info') or {}
            nearby=(index<=_BACKDROP_AHEAD if self.genre_owner in (301,302)
                else selected<=index<=selected+_BACKDROP_AHEAD)
            cached = self.detail_cache.get(path)
            known=(cached or {}).get('info') or {}
            want_plot=not _plain(row_info.get('plot') or known.get('plot') or '')
            want_art=nearby and not (row_info.get('fanart') or known.get('fanart')) and (cached or {}).get('packet') is None
            if not want_plot and not want_art:
                if cached and (generation,path) not in self._painted_details:self._paint_cached(path)
                continue
            saved = self.description_cache.load(path) if self.description_cache is not None else None
            if saved:
                if cached is None:
                    cached = {'playable': None, 'how': 'metadata', 'info': saved}
                    self.detail_cache[path] = cached
                else:
                    cached.setdefault('info', {}).update(saved)
                self._paint_cached(path)
                known=cached.get('info') or {}
                want_plot=not _plain(row_info.get('plot') or known.get('plot') or '')
                want_art=nearby and not (row_info.get('fanart') or known.get('fanart')) and cached.get('packet') is None
                if not want_plot and not want_art:continue
            if monotonic()-self.description_attempts.get(path,float('-inf'))<30:continue
            if not self.detail_jobs.empty():return
            self.description_attempts[path]=monotonic()
            started = monotonic()
            self.performance({'event': 'description_prefetch_started', 'stage': 'detail_metadata',
                              'generation': generation, 'index': index, 'offset': index - selected,
                              'description_requested':want_plot,'backdrop_requested':want_art})
            try:
                self._enrich(path, description=want_plot,artwork=want_art)
                info = (self.detail_cache.get(path) or {}).get('info') or {}
                available = bool(_plain(info.get('plot') or ''))
                backdrop = bool(info.get('fanart'))
                fulfilled = (not want_plot or available) and (not want_art or backdrop)
                self.performance({'event': 'description_prefetch_completed', 'stage': 'detail_metadata',
                    'generation': generation, 'index': index, 'description_present': available,
                    'backdrop_present':backdrop,'backdrop_requested':want_art,
                    'elapsed_ms': round((monotonic() - started) * 1000),
                    'result': 'cancelled' if self.stop.is_set() or generation != self.generation else 'success' if fulfilled else 'unavailable'})
            except Exception as error:
                self.performance({'event': 'description_prefetch_failed', 'stage': 'detail_metadata',
                    'generation': generation, 'index': index,
                    'elapsed_ms': round((monotonic() - started) * 1000),
                    'exception': exception_details(error), 'result': 'failure'})
            # Check explicit Play jobs between requests; there is never a queue
            # of ten old hover lookups in front of a user's action.
            return
        self._queue_focus_metadata()

    def _prepare_focused_seasons(self):
        """Prepare just the settled show, even when its synopsis/art is complete."""
        if self.genre_owner is not None or self.stop.is_set():return False
        row=self._selected_row()
        if not row or not _tv_container(row) or row.get('season_choice'):return False
        path=_detail_path(self.g.pluginid,row)
        if not path:return False
        cached=(self.detail_cache.get(path) or {}).get('packet')
        if cached is not None:return False
        signature=(self.generation,path);now=monotonic()
        if signature!=self._season_focus:
            self._season_focus=signature;self._season_focus_since=now
        if now-self._season_focus_since<.2:return True
        if now-self._season_attempts.get(path,float('-inf'))<30:return False
        self._season_attempts[path]=now;self.description_attempts[path]=now
        request_id=token_hex(8);generation=self.generation;started=monotonic()
        self.performance({'event':'season_preload_started','stage':'focused_title_metadata',
            'request_id':request_id,'generation':generation,'focused_titles':1,'result':'started'})
        try:
            packet=self._fetch_detail(path,background=True)
            if isinstance(packet,dict) and not self.stop.is_set():
                playable,how=choose_playback(packet.get('rows'),_kind(packet.get('info')),packet.get('selected_id'))
                self._remember(path,playable,how if playable is not None else 'metadata',packet)
            cancelled=self.stop.is_set() or generation!=self.generation
            self.performance({'event':'season_preload_finished','stage':'focused_title_metadata',
                'request_id':request_id,'generation':generation,'season_count':len(packet.get('seasons') or []) if isinstance(packet,dict) else 0,
                'elapsed_ms':round((monotonic()-started)*1000),
                'result':'cancelled' if cancelled else 'success' if isinstance(packet,dict) and packet.get('seasons') else 'unavailable'})
        except Exception as error:
            self.performance({'event':'season_preload_failed','stage':'focused_title_metadata',
                'request_id':request_id,'generation':generation,'elapsed_ms':round((monotonic()-started)*1000),
                'exception':exception_details(error),'result':'failure'})
        return True

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
        if cached and cached.get('how') != 'error' and ((cached.get('info') or {}).get('fanart') or cached.get('packet') is not None):
            if signature not in self._painted_details:
                self._paint_cached(path)
                self._painted_details.add(signature)
            self._enriched_focus = signature
            return
        if cached and cached.get('how') == 'error' and monotonic()-cached.get('failed_at',0)<30:return
        self._enriched_focus = signature
        if self.detail_jobs.empty():
            self._enrich(path,artwork=not bool((row.get('info') or {}).get('fanart')))

    def _enrich(self, path, description=False,artwork=False):
        if self.stop.is_set():
            return
        cached = self.detail_cache.get(path)
        if (cached and cached.get('how') != 'error' and (not description or (cached.get('info') or {}).get('plot'))
                and (not artwork or (cached.get('info') or {}).get('fanart') or cached.get('packet') is not None)):
            self._paint_cached(path)
            return
        packet = self._fetch_detail(path,background=True)
        if packet == 'wait' or self.stop.is_set():
            return
        if packet is None:
            self.detail_cache[path] = {'playable': None, 'how': 'error', 'info': {}, 'failed_at':monotonic()}
            self._enriched_focus=None
            return
        playable, how = choose_playback(packet.get('rows'), _kind(packet.get('info')), packet.get('selected_id'))
        self._remember(path, playable, how if playable is not None else 'metadata', packet)

    def _sync_detail_routes(self,bridge):
        with self.lock:
            for key,value in self.known_details.items():bridge.details.setdefault(key,value)
            for key,value in self.known_continuations.items():bridge.continuations.setdefault(key,value)

    def _fetch_detail(self, path, background=False):
        """Share one actual title read between focused preparation and selection."""
        started=monotonic()
        if self.stop.is_set():return 'wait'
        with self.lock:
            cached=self.detail_cache.get(path) or {}
            if cached.get('packet') is not None:
                self.performance({'event':'title_packet_cache_hit','stage':'title_request',
                    'background':background,'result':'success'})
                return deepcopy(cached['packet'])
            future=self.detail_inflight.get(path);owner=future is None
            if owner:
                future=Future();future.request_id=token_hex(8);self.detail_inflight[path]=future
        if not owner:
            self.performance({'event':'title_request_shared','stage':'title_request_wait',
                'request_id':future.request_id,'background':background,'result':'waiting'})
            try:
                while not future.done():
                    if self.stop.wait(.025):
                        self.performance({'event':'title_request_wait_finished','stage':'title_request_wait',
                            'request_id':future.request_id,'elapsed_ms':round((monotonic()-started)*1000),'result':'cancelled'})
                        return 'wait'
                result=future.result()
                self.performance({'event':'title_request_wait_finished','stage':'title_request_wait',
                    'request_id':future.request_id,'elapsed_ms':round((monotonic()-started)*1000),
                    'result':'success' if isinstance(result,dict) else 'cancelled' if result=='wait' else 'failure'})
                return deepcopy(result)
            except Exception as error:
                self.performance({'event':'title_request_wait_failed','stage':'title_request_wait',
                    'request_id':future.request_id,'elapsed_ms':round((monotonic()-started)*1000),
                    'exception':exception_details(error),'result':'failure'})
                raise
        try:
            result=self._fetch_detail_packet(path,background,future.request_id)
            future.set_result(deepcopy(result));return result
        except BaseException as error:
            future.set_exception(error);raise
        finally:
            with self.lock:
                if self.detail_inflight.get(path) is future:self.detail_inflight.pop(path,None)

    def _fetch_detail_packet(self, path, background, request_id):
        bridge = self.metadata_bridge if background else self.bridge
        if bridge is None or self.stop.is_set():
            return 'wait'
        cached = self.detail_cache.get(path) or {}
        if cached.get('packet') is not None:
            self.performance({'event': 'title_packet_cache_hit', 'result': 'success'})
            return deepcopy(cached['packet'])
        packet = {'rows': [], 'next': None, 'info': {}, 'seasons': []}

        def publish(part):
            if part:
                packet['rows'].extend(part.get('rows') or [])
                for key in ('next', 'info', 'seasons', 'selected_id', 'episode_groups'):
                    if key in part:
                        packet[key] = part[key]

        reader_started=monotonic()
        try:
            with self.metadata_lock if background else self.bridge_lock:
                if self.stop.is_set():
                    return 'wait'
                self._sync_detail_routes(bridge)
                observe=DiagnosticLog(self.g.DATA_PATH,self.g.addon.getAddonInfo('version'),
                    active=lambda:bridge._active(lambda:not self.stop.is_set()),operation='title_metadata')
                observe({'event':'title_reader_started','stage':'title_request','parent_request_id':request_id,
                    'background':background,'reader_wait_ms':round((monotonic()-reader_started)*1000),'result':'started'})
                bridge.operation_observer=observe
                try:bridge.browse_progressive(path, publish, lambda: not self.stop.is_set())
                finally:bridge.operation_observer=None
        except Exception as error:
            self.performance({'event':'title_lookup_failed','stage':'native_title_read','exception':exception_details(error),'result':'failure'})
            if isinstance(error,CatalogSigninRequired):raise
            return None
        if self.account_generation is not None:
            from .account_state import current_account
            if not current_account(self.g.DATA_PATH, self.account_generation):
                return None
        with self.lock:
            if self.stop.is_set():return None
            self.known_continuations.update(bridge.continuations)
            for key,value in bridge.details.items():self.known_details.setdefault(key,value)
            record = self.detail_cache.setdefault(path, {'playable': None, 'how': 'metadata', 'info': {}})
            record['packet'] = deepcopy(packet)
        return packet

    def _resolve_playable(self, path, depth=0):
        packet = self._fetch_detail(path)
        if packet == 'wait':
            return None, 'wait', None
        if packet is None:
            return None, 'error', None
        rows = packet.get('rows') or []
        if _tv_packet(packet):
            return None, 'browse', packet
        playable, how = choose_playback(rows, _kind(packet.get('info')), packet.get('selected_id'))
        return playable, how if playable is not None else 'picker' if rows else 'empty', packet

    def _remember(self, path, playable, how, packet):
        if self.account_generation is not None:
            from .account_state import current_account
            if not current_account(self.g.DATA_PATH, self.account_generation):
                return
        info = dict((packet or {}).get('info') or {})
        if _tv_packet(packet):
            playable, how = None, 'browse'
            if _kind(info) not in ('tvshow', 'season'):
                info['mediatype'] = 'tvshow'
            # A show's description belongs to the show, not an arbitrary child
            # episode selected by "longest synopsis" aggregation.
        else:
            for key, value in _richer((packet or {}).get('rows')).items():
                if info.get(key) in (None, '', [], {}):
                    info[key] = value
        self.detail_cache[path] = {'playable': playable, 'how': how, 'info': info,
                                   'packet': deepcopy(packet)}
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
                        or key == 'mediatype' and _kind(current) == 'storefront'
                        or key == 'plot' and len(_plain(str(value))) > len(_plain(str(current.get(key) or '')))):
                    current[key] = value
            with self.lock:
                if index < len(self.rows) and _detail_path(self.g.pluginid, self.rows[index]) == path:
                    self.rows[index]['info'] = dict(current)
                    item = self.items[index]
                else:
                    continue
            painted = {'title': row.get('title'), 'info': current}
            refreshed=_item(painted,image_cache=self.image_cache,report=self.performance)
            item.setProperty('CardMeta',refreshed.getProperty('CardMeta'))
            item.setProperty('ViewingLabel',refreshed.getProperty('ViewingLabel'))
            item.setProperty('ProgressLabel',refreshed.getProperty('ProgressLabel'))
            item.setProperty('Meta', _meta(painted))
            item.setProperty('Plot', _plain(current.get('plot') or ''))
            item.setProperty('Year', _year_label(current))
            runtime = current.get('duration') if current.get('duration') not in (None, '') else current.get('runtime')
            item.setProperty('Runtime', _runtime_label(runtime))
            item.setProperty('Rating', _rating_label(current))
            item.setProperty('Genre', _genre_label(current))
            item.setProperty('MediaType', _kind(current))
            item.setProperty('HeroSource',refreshed.getProperty('HeroSource'))
            art = {}
            fanart = _fit_art(current.get('fanart'), _HERO_ART)
            hero=_hero_art(current)
            thumb = _fit_art(_rail_art(current), _CARD_ART)
            if fanart:
                art['fanart'] = self.image_cache.local(fanart) if self.image_cache else fanart
            if hero:art['hero']=self.image_cache.local(hero) if self.image_cache else hero
            if thumb:
                art['thumb'] = thumb
            if art and hasattr(item, 'setArt'):
                item.setArt(art)

    def _play_resolved(self, token, generation, path, title, origin=None,resume_mode='ask'):
        started=monotonic()
        try:
            if token!=self._play_token or generation!=self.generation or self.stop.is_set():
                self.performance({'event':'title_action_cancelled','stage':'before_detail_resolution',
                    'generation':generation,'request_id':token,'reason':'selection_or_session_changed','result':'cancelled'})
                return
            self.performance({'event':'title_action_started','stage':'detail_resolution',
                'generation':generation,'request_id':token,'result':'started'})
            cached = self.detail_cache.get(path)
            if cached is None or cached.get('how') in ('error', 'wait', 'metadata'):
                playable, how, packet = self._resolve_playable(path)
                if how == 'wait':
                    self.performance({'event': 'title_action_cancelled', 'stage': 'detail_resolution',
                        'generation': generation, 'request_id': token, 'reason': 'catalog_session_unavailable',
                        'result': 'cancelled'})
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
            if how == 'browse' or _tv_packet((cached or {}).get('packet')):
                self._open_series({'title': title, 'url': self.g.pluginid + 'pv/browse/' + path,
                                   'info': (cached or {}).get('info') or {}})
                self.performance({'event':'title_action_finished','stage':'detail_resolution','generation':generation,
                    'request_id':token,'elapsed_ms':round((monotonic()-started)*1000),'result':'seasons_opened'})
                return
            if playable is not None:
                xbmc.log('[Prime Scene] Playing %s (%s) without a detail page' % (path, how), xbmc.LOGINFO)
                self._start_playback(playable, origin,resume_mode=resume_mode,request_token=token,generation=generation)
                self.performance({'event':'title_action_finished','stage':'detail_resolution','generation':generation,
                    'request_id':token,'elapsed_ms':round((monotonic()-started)*1000),'result':'playback_dispatched'})
                return
            if how == 'picker':
                self.navigate(path, heading=title or 'Smooth Prime')
                return
            message = ('This movie or episode did not return its playback item.'
                       if _kind((cached or {}).get('info')) in ('movie', 'episode') else
                       'The title details could not be loaded. Open Diagnostics for the report.')
            self.g.dialog.ok('Smooth Prime', message)
        except Exception as error:
            self.performance({'event':'title_action_failed','stage':'detail_resolution','generation':generation,
                'request_id':token,'elapsed_ms':round((monotonic()-started)*1000),
                'exception':exception_details(error),'result':'failure'})
            if token==self._play_token and generation==self.generation and not self.stop.is_set():
                self.g.dialog.ok('Smooth Prime','Your Amazon session needs verification. Use Sign out, then sign in again.'
                    if isinstance(error,CatalogSigninRequired) else 'This title could not be opened. Open Diagnostics for the complete report.')
        finally:
            if token == self._play_token:
                self.setProperty('Busy', '')

    def _search(self):
        self._show_search()
        phrase = self.g.dialog.input('Search Prime Video')
        if not isinstance(phrase, str) or not phrase.strip():
            return
        self._hide_genres(restore=False)
        self.nav = ''
        self.navigate('search', remember=False,query=phrase.strip(), heading='Search')
        self._focus_first_card()

    def _show_search(self):
        self._back_menu_ready=False
        self.setProperty('NavigationBackdrop','')
        if self.path=='search' and self.query is None and self.genre_owner is None:return
        if self.account_name is not None and not self.account_name():return
        self._dismiss_pending_playback_refresh()
        self._save_view();self._hide_genres(restore=False)
        with self.lock:
            self.generation+=1;self.rows=[];self.items=[];self.row_keys=set()
            self.path='search';self.query=None;self.nav='';self.heading='Search'
            self.history.clear();self.series_context=None
            self.next_path=self.pending_path=None;self.busy=False
            self._progress_refresh_generation=None;self._pending_progress_return=None
            self._focus_first_generation=None
        self.clearList();self._replace_rail=False
        self.setProperty('SectionNotice','')
        self.setProperty('Nav','');self.setProperty('Section','')
        self.setProperty('ProgressRefreshing','');self.setProperty('ProgressRefreshArt','')
        self._season_properties()
        from .browse_status import section_intro
        intro=section_intro('search');self._set_hero_status(intro['title'],intro['message'])
        self.performance({'event':'search_landing_shown','stage':'section_transition',
            'generation':self.generation,'old_section_cards_retained':False,'result':'success'})

    def _activate_section(self,control_id,focus_cards=False):
        if focus_cards:self._back_menu_ready=False
        if control_id in (301,302,303):
            self._show_genres(control_id,focus_grid=focus_cards)
            return
        primary={309:'__watching__',308:'__prime_watchlist__'}
        path=primary[control_id]
        self._hide_genres(restore=False)
        with self.lock:
            self.nav,heading=_PRIMARY_SECTIONS[path]
            if self.path!=path or self.query is not None or focus_cards and not self.busy and (path=='__watching__' or not self.rows):
                self.history.clear();self.navigate(path,remember=False,heading=heading)
                self.performance({'event':'menu_focus_section_activated','control_id':control_id,'section':self.nav,'result':'success'})
        if focus_cards:self._focus_first_card()

    def _show_genres(self,owner,focus_grid=True):
        self._back_menu_ready=False
        self.setProperty('GenreEntryFocus','1')
        self._cancel_genre_selection('menu_selected')
        self.setProperty('NavigationBackdrop','')
        if self.account_name is not None and not self.account_name():return
        self._dismiss_pending_playback_refresh()
        if owner==303 and self._service_future is False:
            self._service_future=None
        section={301:'movies',302:'tv',303:'extras'}[owner]
        if section!='extras' and section not in self.genre_requested:
            self.genre_requested.add(section);self.genre_jobs.put(section)
        if self.genre_owner==owner and self.getProperty('GenrePanel'):
            if focus_grid:self.setFocusId(self._browse_control_id())
            return
        with self.lock:
            self._save_view()
            self.generation+=1
            self.rows=[];self.items=[];self.row_keys=set()
            self.next_path=self.pending_path=None
            self.busy=False;self._focus_first_generation=None
            self._progress_refresh_generation=None;self._pending_progress_return=None
            self.series_context=None;self.history.clear();self.query=None
            self.path='__extras__' if owner==303 else '__genres__/'+section
            self.nav=section;self.genre_owner=owner;self.heading=''
        self.setProperty('ProgressRefreshing','');self.setProperty('ProgressRefreshArt','')
        self.clearList();self._replace_rail=False
        self.setProperty('SectionNotice','');self._set_hero_status('','')
        self.setProperty('ShowSplash','');self.setProperty('Section','');self._season_properties()
        self.setProperty('Nav',section);self.setProperty('GenreMessage','')
        if section!='extras' and section not in self.genre_cache:
            from .genre_catalog import published_genres
            self.genre_cache[section]=published_genres()
        self.setProperty('GenreHeading',{'movies':'Movie genres','tv':'TV genres','extras':'Extras'}[section])
        self.setProperty('GenrePanel','1')
        self._populate_genres(section)
        if focus_grid:self.setFocusId(self._browse_control_id())
        if section!='extras' and section not in self.genre_requested:
            self.genre_requested.add(section);self.genre_jobs.put(section)
        self.performance({'event':'browse_grid_opened','section':section,'tiles':len(self._genre_rows),
            'cards_hidden_until_selection':True,'focused_control':self.getFocusId(),'result':'success'})

    def _populate_genres(self,section):
        from .browse_tiles import genres,extras
        from .genre_type import assets as genre_type
        with self.lock:
            if self.genre_owner!={'movies':301,'tv':302,'extras':303}[section]:return
            listing=self._browse_list();selected=max(0,listing.getSelectedPosition())
            title=self._genre_rows[selected]['title'] if selected<len(self._genre_rows) else None
            rows=extras(self.service_rows) if section=='extras' else genres(section,self.genre_cache.get(section,[]))
            if rows==self._genre_rows:return
            if self._tile_items and [row['title'] for row in rows]==[row['title'] for row in self._genre_rows]:
                previous=self._tile_items
                self._tile_items={row['path']:previous[old['path']] for old,row in zip(self._genre_rows,rows)}
                for row in rows:self._tile_items[row['path']].setProperty('GridLabel',clean(row.get('grid_label') or ''))
                self._genre_rows=rows
                self.performance({'event':'browse_grid_metadata_updated','section':section,'tiles':len(rows),
                    'native_list_reset':False,'result':'success'})
                return
            self._genre_rows=rows
            selected=next((i for i,row in enumerate(rows) if row['title']==title),0)
            items=[];self._tile_items={}
            for row in rows:
                item=xbmcgui.ListItem(label=clean(row['title']))
                artwork=row.get('artwork') or self._tile_art.get(row['path'],'')
                if section!='extras' and self.genre_backdrops is not None and self.genre_image_cache is not None:
                    address=row.get('address') or row['path']
                    source=self.genre_backdrops.image(section,address) if address else ''
                    cached=self.genre_image_cache.local(source) if source else ''
                    if cached and cached!=source:
                        artwork=cached;row.update(artwork=source,artwork_source='provider_title_backdrop')
                if artwork:
                    fitted=_fit_art(artwork,_CARD_ART) if artwork.startswith('https://') else artwork
                    item.setArt({'thumb':self.image_cache.local(fitted) if self.image_cache else fitted,
                        'fanart':_fit_art(artwork,_HERO_ART) if artwork.startswith('https://') else artwork})
                    if section!='extras' and not artwork.startswith('https://'):item.setProperty('TileArt',artwork)
                item.setProperty('GridLabel',clean(row.get('grid_label') or (row['title'] if not artwork else '')))
                if section!='extras':
                    typography=genre_type(row['title'])
                    item.setProperty('PanelKey',row['title'])
                    item.setProperty('PanelTitle',typography.get('title',''))
                    item.setProperty('PanelSpine',typography.get('spine',''))
                item.setProperty('Caption',clean(row['caption']));items.append(item)
                self._tile_items[row['path']]=item
            listing.reset();listing.addItems(items);listing.selectItem(selected)
            self.performance({'event':'browse_grid_updated','section':section,'tiles':len(rows),
                'selection_preserved':bool(title and rows[selected]['title']==title),'result':'success'})

    def _browse_control_id(self):
        return 415 if self.genre_owner==303 else 410

    def _browse_list(self):
        return self.getControl(self._browse_control_id())

    def _observe_genre_panel(self):
        if self.genre_owner not in (301,302) or not self.getProperty('GenrePanel'):return
        from .genre_type import layout as genre_type_layout
        listing=self.getControl(410);index=listing.getSelectedPosition()
        row=self._genre_rows[index] if 0<=index<len(self._genre_rows) else {}
        item=self._tile_items.get(row.get('path'))
        dimensions=self.genre_image_cache.metadata.get(row.get('artwork')) if self.genre_image_cache else None
        signature=(self.generation,self.genre_owner,index,len(self._genre_rows),
            self.getFocusId(),xbmc.getInfoLabel('Container(410).NumItems'),
            bool(item and item.getProperty('TileArt')),tuple(dimensions or ()))
        if signature==self._genre_panel_reported:return
        previous=self._genre_panel_reported;self._genre_panel_reported=signature
        self.performance({'event':'genre_plane_focus','stage':'native_genre_presentation',
            'generation':self.generation,'section':self.nav,'selected_index':index,
            'previous_index':previous[2] if previous and previous[:2]==signature[:2] else None,
            'native_items':signature[5],
            'focused_control':signature[4],'previous_spine':index>0,
            'next_spine':index+1<len(self._genre_rows),
            'configured_plane_width':1760 if index in (0,len(self._genre_rows)-1) else 1600,
            'configured_spine_width':160,'configured_transition_ms':320,
            'configured_title_width':1440,'configured_title_height':600,
            'configured_typography':genre_type_layout(),'title_inherits_edge_stretch':False,
            'entry_animation_suppressed':bool(self.getProperty('GenreEntryFocus')),
            'background_pixel_dimensions':dimensions,'background_source':'provider_title_backdrop',
            'title_bound':bool(item and item.getProperty('PanelTitle')),
            'spine_title_bound':bool(item and item.getProperty('PanelSpine')),
            'image_bound':bool(item and item.getArt('fanart')),
            'catalog_request_for_presentation':False,'result':'configured'})

    def _hide_genres(self,restore=True):
        self._cancel_genre_selection('chooser_closed')
        owner=self.genre_owner;self.genre_owner=None;self.setProperty('GenrePanel','')
        if restore and owner is not None:self.setFocusId(owner)

    def _choose_genre(self):
        if self.genre_owner not in (301,302,303):return
        owner=self.genre_owner;section={301:'movies',302:'tv',303:'extras'}[owner]
        index=self._browse_list().getSelectedPosition()
        if not 0<=index<len(self._genre_rows):return
        row=self._genre_rows[index]
        if row.get('path'):path=row['path']
        elif not row.get('address'):path='__prime_movies__' if owner==301 else '__prime_tv__'
        else:
            from urllib.parse import quote_plus
            path='__genre__/'+section+'/'+quote_plus(row['address'])
            if self.loader is not None:self.loader.register_genre(path)
        if path.startswith(('__genre__/','__service__/')) and self.loader is not None:self.loader.register_genre(path)
        self._cancel_genre_selection('new_selection')
        pending={'generation':self.generation,'owner':owner,'section':section,'index':index,
            'path':path,'row':dict(row),'started':monotonic(),'future':None}
        pending['selection_id']='%d:%d'%(self.generation,int(pending['started']*1000000))
        with self.lock:
            self._genre_selection=pending
            self.setProperty('GenreMessage','')
        cached=(path,None) in self.views or self.loader is not None and self.loader.peek(path) is not None
        self.performance({'event':'browse_tile_selected','stage':'genre_selection','section':section,
            'selection_id':pending['selection_id'],'provider_route':True,'preload_hit':bool(cached),
            'chooser_retained_until_ready':not bool(cached),
            'prime_only':section in ('movies','tv') and not path.startswith('__purchases__/'),'result':'started'})
        if cached:self._open_genre_selection(pending)
        else:self._dispatch_genre_selection()

    def _cancel_genre_selection(self,reason):
        with self.lock:
            pending=self._genre_selection
            self._genre_selection=None
        if pending:self.performance({'event':'genre_selection_cancelled','stage':'genre_selection',
            'selection_id':pending['selection_id'],'reason':reason,'result':'cancelled'})

    def _dispatch_genre_selection(self):
        try:
            with self.lock:
                pending=self._genre_selection;loader=self.loader
                if not pending or pending['future'] is not None or loader is None:return
                if pending['generation']!=self.generation or pending['owner']!=self.genre_owner:
                    self._cancel_genre_selection('view_changed');return
                path=pending['path']
                if path.startswith(('__genre__/','__service__/')):loader.register_genre(path)
                if not loader.handles(path):raise ValueError('The selected category has no registered route')
                future=loader.request(path)
                pending['future']=future
            def ready(result):
                if self._genre_selection is not pending or self.stop.is_set() or loader is not self.loader:return
                try:
                    packet=result.result()
                    if packet is None:raise RuntimeError('The selected category returned no result')
                    if not loader.current():return
                    self._open_genre_selection(pending)
                except Exception as error:self._genre_selection_failed(pending,error)
            future.add_done_callback(ready)
        except Exception as error:self._genre_selection_failed(pending,error)

    def _genre_selection_failed(self,pending,error):
        with self.lock:
            if self._genre_selection is not pending:return
            self._genre_selection=None
            if pending['path'].startswith('__purchases__/'):
                from .browse_status import category_status
                self.setProperty('GenreMessage',category_status(pending['section'],pending['path'],unavailable=True)['message'])
            else:self.setProperty('GenreMessage',clean(pending['row']['title'])+' is temporarily unavailable. Choose another category or try again.')
        self.performance({'event':'genre_selection_failed','stage':'genre_selection',
            'selection_id':pending['selection_id'],'chooser_retained':True,
            'elapsed_ms':round((monotonic()-pending['started'])*1000),'exception':exception_details(error),'result':'failure'})

    def _open_genre_selection(self,pending):
        with self.lock:
            if (self._genre_selection is not pending or self.stop.is_set() or pending['generation']!=self.generation
                    or pending['owner']!=self.genre_owner):return
            if self._browse_list().getSelectedPosition()!=pending['index']:
                self._cancel_genre_selection('focus_changed');return
            self._genre_selection=None
            row=pending['row'];self._hide_genres(restore=False)
            self.nav=pending['section'];self.history.clear()
            self.navigate(pending['path'],remember=False,heading=row['title'],intro_art=row.get('artwork') or '')
            self._focus_first_card()
        self.performance({'event':'genre_selection_displayed','stage':'first_cards',
            'selection_id':pending['selection_id'],'cards':len(self.rows),'chooser_removed_after_result':True,
            'elapsed_ms':round((monotonic()-pending['started'])*1000),'result':'success'})

    def _track_tile_art(self,address):
        fresh=self._note_artwork({'thumb':address})
        if not fresh:return
        from .account_state import persist_if_current
        def write():
            with (Path(self.g.DATA_PATH)/'account-artwork.jsonl').open('a',encoding='utf-8') as output:
                output.write(''.join(dumps(value)+'\n' for value in fresh))
        try:persist_if_current(self.g.DATA_PATH,self.account_generation,write)
        except OSError as error:self.performance({'event':'tile_artwork_tracking_failed','stage':'tile_artwork','exception':exception_details(error),'result':'failure'})

    def _refresh_tile_art(self):
        loader=self.loader
        if loader is None or not loader.current():return
        owner=self.genre_owner
        if owner in (301,302):self._observe_genre_panel()
        if owner==303:
            if self._service_future is None:self._service_future=loader.request('__service_directory__')
            future=self._service_future
            if future is not False:
                try:
                    finished=future.done()
                    packet=loader.peek('__service_directory__');failure=None
                    if finished:
                        try:packet=future.result() or packet
                        except Exception as error:failure=error
                    if self.stop.is_set() or not loader.current():return
                    rows=list((packet or {}).get('tiles') or [])
                    if rows!=self.service_rows and (rows or (packet or {}).get('empty_confirmed')):
                        self.service_rows=rows
                        if self.genre_owner==303:self._populate_genres('extras')
                        self.setProperty('GenreMessage','')
                        self.performance({'event':'service_grid_batch_displayed','stage':'service_directory',
                            'services':len(rows),'request_finished':finished,
                            'complete':bool(packet.get('directory_complete')),
                            'subscription_filter_applied':False,'result':'displayed'})
                    if finished:
                        self._service_future=False
                    if failure is not None:raise failure
                    if finished and not rows:
                        if not (packet or {}).get('empty_confirmed'):raise RuntimeError('The service directory returned no service tiles')
                        from .browse_status import category_status
                        self.setProperty('GenreMessage',category_status('extras','__service_directory__')['message'])
                        self.performance({'event':'service_directory_empty','stage':'category_message','empty_confirmed':True,'result':'empty'})
                        return
                    if finished and rows:
                        self.performance({'event':'service_grid_ready','stage':'service_directory','services':len(rows),
                            'subscription_filter_applied':False,'complete':packet.get('directory_complete'),
                            'result':'success' if packet.get('directory_complete') else 'partial'})
                except Exception as error:
                    self._service_future=False
                    from .browse_status import category_status
                    self.setProperty('GenreMessage','Some services could not be retrieved. Reopen Extras to retry.' if self.service_rows else
                        category_status('extras','__service_directory__',unavailable=True)['message'])
                    self.performance({'event':'service_grid_failed','stage':'service_directory',
                        'displayed_services_retained':len(self.service_rows),'exception':exception_details(error),'result':'failure'})
        if owner!=self.genre_owner or self.stop.is_set():return
        # Both navigation genres and services already carry artwork. Kodi's
        # background texture loader owns downloads/cache. Do not duplicate them
        # in ImageCache or open title/genre pages just to obtain an image.
        updated=0
        for row in list(self._genre_rows):
            if owner!=self.genre_owner or self.stop.is_set():return
            path=row['path'];item=self._tile_items.get(path);artwork=row.get('artwork')
            if owner in (301,302):
                gallery,cache=self.genre_backdrops,self.genre_image_cache
                if gallery is None or cache is None or item is None:continue
                section='movies' if owner==301 else 'tv'
                address=row.get('address') or row['path']
                artwork=gallery.image(section,address) if address else ''
                if not artwork:continue
                original=source_image(artwork);local=cache.local(original)
                if local==original:continue
                row.update(artwork=original,artwork_source='provider_title_backdrop')
                if item.getProperty('TileArt')!=local:
                    item.setArt({'thumb':local,'fanart':local});item.setProperty('TileArt',local);updated+=1
                    self._tile_art[path]=original;self._track_tile_art(original);self._track_tile_art(local)
                    dimensions=cache.dimensions(original)
                    self.performance({'event':'genre_backdrop_bound','stage':'genre_artwork','section':section,
                        'genre':row['title'],'source':route(original),'pixel_dimensions':dimensions,
                        'original_source':True,'cover_fallback_used':False,'bound_to_local_original':True,
                        'native_upscaling_needed':bool(dimensions and dimensions[0]<1920),'result':'success'})
                continue
            if item is None or not artwork:continue
            if self._tile_art.get(path)!=artwork:
                self._tile_art[path]=artwork;self._track_tile_art(artwork)
            fitted=_fit_art(artwork,_CARD_ART) if artwork.startswith('https://') else artwork
            if self.image_cache:fitted=self.image_cache.local(fitted)
            if item.getProperty('TileArt')!=fitted:
                backdrop=_fit_art(artwork,_HERO_ART) if artwork.startswith('https://') else artwork
                item.setArt({'thumb':fitted,'fanart':backdrop});item.setProperty('TileArt',fitted);updated+=1
        if updated:self.performance({'event':'navigation_artwork_bound','stage':'tile_artwork','section':self.nav,
            'tiles_updated':updated,'source':'preloaded_amazon_title_backdrop' if owner in (301,302) else 'provider_navigation','catalog_requests':0,
            'resolution_policy':'provider_original_master','artificial_width_cap':False,
            'image_loader':'account_scoped_original_cache' if owner in (301,302) else 'kodi_background_texture_cache','result':'success'})

    def _genre_loop(self):
        from .genre_catalog import discover
        gateway=CatalogGateway(self.g);snapshot=None;received=0
        while not self.stop.is_set():
            try:section=self.genre_jobs.get(timeout=.15)
            except Empty:continue
            report=gateway._observe(lambda:not self.stop.is_set())
            try:
                self._genre_discovery='loading'
                if snapshot is None or monotonic()-received>900:
                    with gateway.session(lambda:not self.stop.is_set(),report) as client:
                        snapshot=discover(client,section,report)
                    received=monotonic();source='provider_categories'
                else:source='shared_navigation_memory'
                if self.stop.is_set() or not gateway._active(lambda:True):continue
                if not snapshot:raise RuntimeError('Amazon returned no recognized genre navigation')
                with self.lock:
                    for target in ('movies','tv'):
                        self.genre_cache[target]=sorted((dict(row) for row in snapshot),key=lambda row:row['title'])
                    self.genre_requested.update(('movies','tv'))
                    owner=self.genre_owner
                    self._genre_discovery='complete'
                if owner in (301,302):self._populate_genres('movies' if owner==301 else 'tv')
                self.performance({'event':'genres_ready','section':section,'genres':len(snapshot),
                    'artwork_supplied':sum(bool(row.get('artwork')) for row in snapshot),'source':source,
                    'shared_between_movies_and_tv':True,'catalog_pages_requested':0,'result':'success'})
            except Exception as error:
                self._genre_discovery='failed'
                self.genre_requested.discard(section)
                self.performance({'event':'genre_discovery_failed','section':section,'stage':'genre_grid',
                    'exception':exception_details(error),'result':'failure'})

    def onClick(self, control_id):
        if control_id==_ACCOUNT_READY_CONTROL:
            self._refresh_account_interface('account_notification');return
        if self.getProperty('StartupSplash'):return
        primary = {309: '__watching__', 301: '__prime_movies__', 302: '__prime_tv__',
                   303: '__extras__', 308: '__prime_watchlist__'}
        if control_id in primary:
            path = primary[control_id]
            self._activate_section(control_id,focus_cards=True)
        elif control_id in (410,415):
            self._choose_genre()
        elif control_id == 304:
            self._search()
        elif control_id == 307:
            self._choose_season()
        elif control_id == 305:
            self.g.addon.openSettings()
            self._refresh_account_interface('settings_closed')
        elif control_id == 306 and self.account_action is not None:
            try:
                self.account_action(self.pause_catalog)
            except Exception as error:
                self.g.dialog.ok('Smooth Prime', str(error) if isinstance(error, RuntimeError)
                                 else 'Account controls could not finish (' + type(error).__name__ + ').')
            finally:
                self._refresh_account_interface('account_action')
        elif control_id == 200:
            row = self._selected_row()
            if row:self._activate_row(row,self._capture_playback_origin())

    def _activate_row(self,row,origin,resume_mode='ask'):
        origin=dict(origin,url=row.get('url'),identity=row.get('identity'))
        path=_detail_path(self.g.pluginid,row)
        cached=self.detail_cache.get(path) if path else None
        if _tv_container(row) or cached and (cached.get('how')=='browse' or _tv_packet(cached.get('packet'))):
            self._open_series(row);return
        target=playback_row(row) or (cached or {}).get('playable')
        if target is not None:
            self.performance({'event':'title_click','action':'play','media_kind':_kind(target.get('info')),'result':'success'})
            self._start_playback(target,origin,resume_mode=resume_mode);return
        if not path:
            self.g.dialog.ok('Smooth Prime','This title has no available action. Open Diagnostics for the report.');return
        with self.lock:
            self._play_token+=1;token=self._play_token
            self.setProperty('Busy','1')
            self.play_jobs.put((token,self.generation,path,row.get('title') or 'Smooth Prime',origin,resume_mode))

    def _exit_to_kodi(self,reason):
        self._cancel_genre_selection('interface_closed')
        self.performance({'event':'interface_exit_requested','stage':'user_back',
            'reason':reason,'signed_out_view':self._signed_out_view,
            'focused_control':self.getFocusId(),'history_depth':len(self.history),'menu_back_ready':self._back_menu_ready,
            'workers_stopped':self.stop.is_set(),'generation':self.generation,'result':'requested'})
        self.stop.set()
        self._unregister_return()
        record(self.g,'window_exit_to_kodi_home')
        self.close()

    def onAction(self, action):
        # Left and right belong to the list. The hero reads Container(200),
        # so updating it from here only delays the move.
        action_id = action.getId()
        if self.getProperty('StartupSplash'):
            if action_id in _BACK:
                self.performance({'event':'startup_preload_cancelled','stage':'startup_preload',
                    'elapsed_ms':round((monotonic()-self._startup['started'])*1000) if self._startup else None,
                    'reason':'back_pressed','result':'cancelled'})
                self._startup=None;self.setProperty('StartupSplash','');self._exit_to_kodi('startup_cancelled')
            return
        # Back belongs to the window, even when authentication or a catalog
        # worker has ended. It must never depend on a provider/database request.
        if action_id in _BACK and (self._signed_out_view or self.stop.is_set()):
            self._exit_to_kodi('signed_out' if self._signed_out_view else 'workers_stopped')
            return
        focus=self.getFocusId()
        if action_id not in _BACK and focus in (200,410,415):self._back_menu_ready=False
        if focus==410 and action_id in (1,2):
            self.setProperty('GenreEntryFocus','');self._observe_genre_panel()
        elif self.genre_owner in (301,302) and action_id in (3,4):self.setProperty('GenreEntryFocus','1')
        if action_id in (1,2,3,4) and self.worker is not None and self.worker.is_alive():
            if focus in (309,301,302,303,308):
                self._focus_first_generation=None
                self.performance({'event':'menu_input_navigation','stage':'user_input',
                    'control_id':focus,'action_id':action_id,'generation':self.generation,
                    'section':self.nav,'result':'started'})
                self._activate_section(focus)
            elif focus==304:self._show_search()
        if focus==200 and action_id in (11,117):
            self._card_actions(info_only=action_id==11)
            return
        if action_id==4 and focus in (301,302,303):
            self._show_genres(focus)
            return
        if self.getProperty('GenrePanel') and action_id in _BACK and focus in (410,415):
            if self._genre_selection:
                self._cancel_genre_selection('back_pressed');return
            self._back_menu_ready=True;self.setFocusId(self.genre_owner)
            self.performance({'event':'back_to_main_menu','stage':'back_navigation','source':'genre_grid',
                'from_control':focus,'target_control':self.genre_owner,'generation':self.generation,'result':'success'})
            return
        if action_id not in _BACK:
            return
        if self.path not in _SECTION_PATHS and self.history:
            record(self.g, 'catalog_back')
            path, query, heading, nav, selected, series = self.history.pop()
            self.nav = nav
            self.navigate(path, remember=False, query=query, heading=heading, selection=selected, series=series)
        elif self.genre_owner is None and self.nav in ('movies','tv') and (self.path in ('__prime_movies__','__prime_tv__') or self.path.startswith(('__genre__/','__purchases__/'))):
            self._show_genres(301 if self.nav=='movies' else 302,focus_grid=True)
            self.performance({'event':'back_to_genre_grid','stage':'back_navigation','section':self.nav,'result':'success'})
        elif self.path=='search' and self.query is not None:
            self.path='search_results';self._show_search();self.setFocusId(304)
        elif self.genre_owner is None and (self.path.startswith('__service__/') or self.path=='__prime_sports__'):
            self._show_genres(303,focus_grid=True)
            self.performance({'event':'back_to_service_grid','stage':'back_navigation','result':'success'})
        else:
            menu_controls=(301,302,303,304,305,306,308,309)
            if not self._back_menu_ready or focus not in menu_controls:
                self._dismiss_pending_playback_refresh()
                self._focus_first_generation=None;self._focus_receipt=None;self._pending_progress_return=None
                self._back_menu_ready=True;target=self._menu_control()
                self.setProperty('GenreEntryFocus','1');self.setFocusId(target)
                self.performance({'event':'back_to_main_menu','stage':'back_navigation','source':'root_content',
                    'from_control':focus,'target_control':target,'section':self.nav,
                    'cards_retained':len(self.rows),'history_depth':len(self.history),
                    'generation':self.generation,'result':'success'})
            else:self._exit_to_kodi('root_back_from_menu')

    def _card_actions(self,info_only=False):
        row=self._selected_row()
        if not row:return
        origin=self._capture_playback_origin();generation=self.generation
        title=clean(row.get('title') or 'Title information');series=_tv_container(row)
        selected=2 if info_only else self.g.dialog.contextmenu(
            ['Browse seasons','More information'] if series else ['Play / resume','Play from beginning','More information'])
        if selected<0:
            self.performance({'event':'card_action','stage':'context_menu','result':'cancelled'});return
        if self.stop.is_set() or generation!=self.generation:
            self.performance({'event':'card_action','stage':'context_menu','reason':'view_changed','result':'cancelled'});return
        if info_only or selected==(1 if series else 2):
            info=dict(row.get('info') or {});saved=self.detail_cache.get(_detail_path(self.g.pluginid,row)) or {}
            info.update(saved.get('info') or {})
            self.g.dialog.textviewer(title,_meta(dict(row,info=info))+'\n\n'+_plain(info.get('plot') or 'No description is available for this title.'))
            action='information'
        elif series:self._open_series(row);action='seasons'
        elif selected==0:self._activate_row(row,origin);action='play'
        else:
            self._activate_row(row,origin,resume_mode='start')
            action='start_over'
        self.performance({'event':'card_action','stage':'context_menu','action':action,'media_kind':_kind(row.get('info')),'result':'success'})


def _playing(player):
    try:
        return bool(player.isPlaying())
    except Exception:
        return False


def _title_is_playing(playing, note, now, latched):
    """True while the Smooth Prime title just started is the active Kodi video."""
    if not playing or not isinstance(note, dict):
        return False
    if latched:
        return True
    try:
        armed = float(note.get('armed') or 0)
    except (TypeError, ValueError):
        return False
    return 0 <= now - armed <= _NOTE_WINDOW


class _PlaybackSignals(xbmc.Player):
    def __init__(self):
        super(_PlaybackSignals, self).__init__()
        self.terminal = ''

    def onAVStarted(self):
        self.terminal = ''

    def onPlayBackEnded(self):
        self.terminal = 'ended'

    def onPlayBackStopped(self):
        self.terminal = 'stopped'

    def onPlayBackError(self):
        self.terminal = 'error'


def watch_playback(profile):
    """Return the live catalog to its selected card when Kodi finishes the title.

    Playback itself stays with Kodi: this loop does not open a window and does
    not close the stock OSD, seek bar or player dialogs.
    """
    monitor = xbmc.Monitor()
    player = _PlaybackSignals()
    report = DiagnosticLog(profile, xbmcaddon.Addon(ADDON_ID).getAddonInfo('version'),
                           operation='playback_return')
    latched = False
    active_note = None
    last_position = last_duration = 0
    last_sample = 0
    while not monitor.abortRequested():
        playing = _playing(player)
        note = _read_note(profile)
        if note and (not active_note or note.get('request_id') != active_note.get('request_id')):
            active_note = note
            player.terminal = ''
            last_position = last_duration = last_sample = 0
            report({'event': 'playback_request_armed', 'stage': 'await_player',
                    'request_id': note.get('request_id'), 'has_return_target': bool(note.get('target')),
                    'current_window_id': xbmcgui.getCurrentWindowId()})
        if _title_is_playing(playing, note or active_note, time(), latched):
            if not latched:
                report({'event': 'playback_observed', 'stage': 'player_active',
                        'request_id': (active_note or {}).get('request_id'), 'result': 'success'})
            latched = True
            if time()-last_sample >= 1:
                try:
                    last_position,last_duration = player.getTime(),player.getTotalTime()
                    last_sample = time()
                except Exception as error:
                    last_sample = time()
                    report({'event':'watch_progress_failed','stage':'sample',
                        'exception':exception_details(error),'result':'failure'})
        if active_note and not playing:
            age = time() - float(active_note.get('armed') or 0)
            reason = player.terminal or 'stopped_or_ended'
            if latched or player.terminal == 'error' or age >= _NOTE_WINDOW:
                if not latched and player.terminal != 'error':
                    reason = 'start_timeout'
                report({'event': 'playback_finished', 'stage': 'return_to_card',
                        'request_id': active_note.get('request_id'), 'reason': reason,
                        'current_window_id': xbmcgui.getCurrentWindowId(),
                        'active_dialog_id': xbmcgui.getCurrentWindowDialogId(),
                        'last_position_seconds': round(last_position, 2),
                        'last_duration_seconds': round(last_duration, 2),
                        'since_request_ms': round(age * 1000),
                        'result': 'failure' if reason in ('error', 'start_timeout') else 'success'})
                _request_playback_return(active_note, reason, report)
                _clear_note(profile, active_note.get('request_id'))
                latched, active_note, player.terminal = False, None, ''
        if monitor.waitForAbort(0.1):
            break


def open_interface(globals_instance, settings_instance, account_name=None,
                   account_action=None, offer_signin=False, launch_diagnostics=None):
    record(globals_instance, 'window_creating')
    window = AuroraHome(_HOME_XML, globals_instance.PLUGIN_PATH, 'Default', '1080i',
                        globals_instance=globals_instance,
                        account_name=account_name, account_action=account_action,
                        offer_signin=offer_signin, launch_diagnostics=launch_diagnostics)
    try:
        if launch_diagnostics is not None:
            launch_diagnostics({'event': 'window_prepared', 'current_window_id': xbmcgui.getCurrentWindowId()})
        window.doModal()
    finally:
        window.stop.set()
        window.account_monitor.window = None
        window._unregister_return()
        for worker in (window.worker, getattr(window, 'detail_worker', None),
                       getattr(window, 'play_worker', None),
                       getattr(window, 'visual_worker', None), window.return_worker, window.genre_worker):
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
