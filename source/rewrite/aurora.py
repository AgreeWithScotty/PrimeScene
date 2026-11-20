"""Aurora Cinema windows for Prime Scene.

Layout, type and charcoal tint follow SmoothFlix Aurora Cinema. Python fills
window properties from this add-on's own catalogue and plays through the
existing Prime Scene playback URL. No separate Kodi skin is required.
"""
from json import dumps, load
from pathlib import Path
from queue import Empty, Queue
from re import fullmatch
from threading import Event, RLock, Thread
from time import time
from traceback import format_exc

import xbmc
import xbmcaddon
import xbmcgui

from .dedicated_bridge import CatalogSigninRequired, ProviderBridge
from .interface_diagnostics import record

_BACK = (9, 10, 92)
_LATERAL = {1: -1, 2: 1}
# Cool slate washes. The visible scrim stays on one matte tone; it does not
# cycle, and it never stands in for missing artwork.
_TINTS = ('66181C22', '661C1916', '66141A1C', '661A1814', '66121618')
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
_TITLE_XML = 'PrimeSceneTitle.xml'
_PLAYER_XML = 'PrimeScenePlayer.xml'


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
    if not names:
        return ''
    return ' '.join(names[0].upper().split())


def _synopsis(text, limit=320):
    text = ' '.join((text or '').split())
    if len(text) > limit:
        return text[:limit - 1].rstrip() + '…'
    return text


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


def _kicker_for(row):
    return {
        'movie': 'FILM',
        'film': 'FILM',
        'episode': 'EPISODE',
        'tvshow': 'SERIES',
        'show': 'SERIES',
        'series': 'SERIES',
        'season': 'SEASON',
    }.get(_kind(row.get('info')), '')


def _browse_path(pluginid, url):
    marker = pluginid + 'pv/browse/'
    if isinstance(url, str) and url.startswith(marker):
        return url.split('pv/browse/', 1)[1]
    return None


def _item(row):
    info = row.get('info') or {}
    title = (row.get('title') or 'Untitled').upper()
    item = xbmcgui.ListItem(label=title)
    thumb = _http(info.get('thumb')) or _http(info.get('poster')) or _http(info.get('fanart'))
    poster = _http(info.get('poster')) or thumb
    fanart = _http(info.get('fanart'))
    art = {}
    if thumb:
        art['thumb'] = thumb
        art['landscape'] = thumb
    if poster:
        art['poster'] = poster
        art['icon'] = poster
    if fanart:
        art['fanart'] = fanart
    if art:
        item.setArt(art)
    meta = _meta(row)
    plot = _synopsis(info.get('plot') or '')
    kicker = _kicker_for(row)
    item.setProperty('Meta', meta)
    item.setProperty('Watched', '%s%% WATCHED' % _progress(info) if _progress(info) else meta)
    item.setProperty('Plot', plot)
    item.setProperty('Kicker', kicker)
    item.setProperty('Year', _year_label(info))
    item.setProperty('Runtime', _runtime_label(info.get('duration') if info.get('duration') not in (None, '') else info.get('runtime')))
    item.setProperty('Rating', _rating_label(info))
    item.setProperty('Genre', _genre_label(info))
    item.setProperty('MediaType', _kind(info))
    item.setProperty('Id', str(row.get('url') or ''))
    item.setProperty('Identity', str(row.get('identity') or ''))
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
        'title': title or 'Prime Scene',
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
    note_playback(profile, row.get('title') or 'Prime Scene', _detail_meta(row))
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
        self.prefetch_at = 0
        self.busy = False
        self.result = None
        self.worker = None
        self.artwork = set()
        self.account_generation = None
        self._ready = False
        self._followed = None
        self._restore = None

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
        self.update_account_button()
        if self.account_name is not None and not self.account_name():
            self.show_signed_out()
            if self.launch_diagnostics is not None:
                self.launch_diagnostics({'event': 'interface_ready', 'signed_in': False,
                                         'current_window_id': xbmcgui.getCurrentWindowId()})
            if self.offer_signin:
                self.offer_signin = False
                if self.g.dialog.yesno('Prime Scene', 'Sign in to Amazon Prime Video to start watching.',
                                       nolabel='Cancel', yeslabel='Sign in'):
                    self.onClick(306)
            return
        if self.worker is not None:
            return
        if self.account_name is not None:
            from .account_state import account_epoch
            self.account_generation = account_epoch(self.g.DATA_PATH)
        self._set_hero_status('PRIME SCENE', 'CONNECTING')
        self.worker = Thread(target=self._work, name='PrimeCatalog', daemon=True)
        self.nav = 'home'
        self.navigate('root', remember=False, heading='Home')
        self.setFocusId(200)
        self.worker.start()
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
            self.path, self.query, self.next_path = 'root', None, None
            self.busy = False
            self.nav = ''
        control = self.getControl(200)
        if hasattr(control, 'reset'):
            control.reset()
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
        control = self.getControl(200)
        if hasattr(control, 'reset'):
            control.reset()
        if self.worker is not None:
            while self.worker.is_alive():
                self.worker.join(0.1)
        self.worker = None
        self.jobs = Queue()

    def navigate(self, path, remember=True, query=None, selection=0, heading=None):
        if self.account_name is not None and not self.account_name():
            self.show_signed_out()
            return
        with self.lock:
            if remember and (path, query) != (self.path, self.query):
                self.history.append((self.path, self.query, self.heading, self.nav, self._selected_index()))
            self.path = path
            self.query = query
            self.heading = heading or 'Home'
            self.generation += 1
            self.rows = []
            self.next_path = None
            self.prefetch_at = 0
            self.busy = True
            self._restore = selection or None
            token = self.generation
        self.setProperty('Nav', self.nav if self.nav in ('home', 'movies', 'tv') else '')
        self.setProperty('Section', (self.heading or 'Home').upper())
        self._set_hero_status((self.heading or 'Prime Scene').upper(), 'CONNECTING')
        control = self.getControl(200)
        if hasattr(control, 'reset'):
            control.reset()
        self._followed = None
        self.jobs.put((token, path, query, selection))

    def _selected_index(self):
        control = self.getControl(200)
        position = 0
        if hasattr(control, 'getSelectedPosition'):
            position = control.getSelectedPosition()
        if position is None or position < 0:
            position = 0
        return position

    def _note_artwork(self, info):
        art = {key: info[key] for key in ('thumb', 'poster', 'fanart') if info.get(key)}
        fresh = {value for value in art.values() if isinstance(value, str) and value.startswith('https://')} - self.artwork
        if not fresh or self.account_name is None:
            return art
        self.artwork.update(fresh)
        try:
            with (Path(self.g.DATA_PATH) / 'account-artwork.jsonl').open('a', encoding='utf-8') as output:
                for value in fresh:
                    output.write(dumps(value) + '\n')
        except OSError:
            pass
        return art

    def _publish(self, token, packet):
        with self.lock:
            if self.stop.is_set() or token != self.generation:
                return
            if self.account_generation is not None:
                from .account_state import current_account
                if not current_account(self.g.DATA_PATH, self.account_generation):
                    return
            had = bool(self.rows)
            additions = []
            for row in packet.get('rows') or []:
                key = row.get('url')
                if any(existing.get('url') == key for existing in self.rows):
                    continue
                info = dict(row.get('info') or {})
                self._note_artwork(info)
                stored = dict(row, info=info)
                self.rows.append(stored)
                additions.append(_item(stored))
            self.next_path = packet.get('next')
            rows = list(self.rows)
        if additions:
            control = self.getControl(200)
            if not had and hasattr(control, 'reset'):
                control.reset()
            for item in additions:
                control.addItem(item)
            focus = 0
            if not had and self._restore and self._restore < len(rows):
                focus = self._restore
                if hasattr(control, 'selectItem'):
                    control.selectItem(focus)
                self._restore = None
            if rows:
                self._apply_hero(rows[focus], focus)
                self._followed = focus
            self.setProperty('Section', (self.heading or 'Home').upper())

    def _work(self):
        try:
            # The landing dashboard does not need the playback provider. Movies,
            # TV and legacy sections ask the bridge for it when they are opened.
            bridge = ProviderBridge(None, self.g, initialize_provider=self.initialize_provider)
            record(self.g, 'catalog_worker_ready')
        except Exception as error:
            record(self.g, 'provider_init_failed', error)
            if not self.stop.is_set():
                self._set_hero_status('PRIME SCENE', 'COULD NOT OPEN THE CATALOG. YOUR SIGN-IN IS SAVED.')
            self.busy = False
            return
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
            try:
                record(self.g, 'catalog_request_started')
                bridge.browse_progressive(
                    path, lambda packet: self._publish(token, packet),
                    lambda: token == self.generation and not self.stop.is_set(), query=query)
                record(self.g, 'catalog_request_finished')
                with self.lock:
                    if token == self.generation and not self.stop.is_set():
                        if path.endswith('/nextPage') and len(self.rows) > page_start:
                            self.prefetch_at = page_start
                        if not self.rows:
                            self._set_hero_status((self.heading or 'Prime Scene').upper(),
                                                  'NO TITLES WERE RETURNED. TRY HOME OR SEARCH.')
            except BaseException as error:
                record(self.g, 'catalog_request_failed', error)
                xbmc.log('[Prime Scene] Catalog operation failed: ' + type(error).__name__, xbmc.LOGERROR)
                with self.lock:
                    if token == self.generation and not self.stop.is_set():
                        message = ('USE THE ACCOUNT BUTTON AND CHOOSE SIGN IN AGAIN.'
                                   if isinstance(error, CatalogSigninRequired) else
                                   'UNABLE TO FINISH LOADING. RETURN HOME OR OPEN DIAGNOSTICS.')
                        self._set_hero_status((self.heading or 'Prime Scene').upper(), message)
            finally:
                with self.lock:
                    if token == self.generation:
                        self.busy = False
                        self._prefetch()

    def _prefetch(self):
        with self.lock:
            if self.busy or not self.next_path or self.stop.is_set():
                return
            if self._selected_index() >= self.prefetch_at:
                path = self.next_path
                self.next_path = None
                self.busy = True
                self.jobs.put((self.generation, path, None, 0))

    def _apply_hero(self, row, index):
        info = row.get('info') or {}
        fanart = _http(info.get('fanart')) or _http(info.get('poster')) or _http(info.get('thumb'))
        self.setProperty('HeroKicker', _kicker_for(row))
        self.setProperty('HeroTitle', (row.get('title') or '').upper())
        self.setProperty('HeroMeta', _meta(row))
        self.setProperty('HeroSynopsis', _synopsis(info.get('plot') or ''))
        self.setProperty('HeroTag', _meta(row))
        self.setProperty('HeroFanart', fanart)
        self.setProperty('HeroTint', _SCRIM)
        wash = self.getControl(101)
        if hasattr(wash, 'setColorDiffuse'):
            wash.setColorDiffuse(_SCRIM)

    def _hero_from_list(self):
        with self.lock:
            if not self.rows:
                return
            position = self._selected_index()
            if position >= len(self.rows):
                position = 0
            row = self.rows[position]
        self._apply_hero(row, position)
        self._followed = position

    def _selected_row(self):
        with self.lock:
            index = self._selected_index()
            if not 0 <= index < len(self.rows):
                return None, []
            row = dict(self.rows[index])
            related = [dict(other) for other in self.rows
                       if other.get('video') and other.get('url') != row.get('url')][:8]
        return row, related

    def _open_browse(self, row):
        path = _browse_path(self.g.pluginid, row.get('url'))
        if path:
            self.navigate(path, heading=row.get('title') or 'Prime Scene')
            return True
        if '?mode=Search' in (row.get('url') or ''):
            self._search()
            return True
        return False

    def _focus_id(self):
        getter = getattr(self, 'getFocusId', None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        return getattr(self, 'focus', None)

    def _search(self):
        phrase = self.g.dialog.input('Search Prime Video')
        if not isinstance(phrase, str) or not phrase.strip():
            return
        self.nav = ''
        self.navigate('search', query=phrase.strip(), heading='Search')

    def onFocus(self, control_id):
        if control_id == 200:
            self._hero_from_list()

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
                self.g.dialog.ok('Prime Scene', str(error) if isinstance(error, RuntimeError)
                                 else 'Account controls could not finish (' + type(error).__name__ + ').')
            finally:
                self.stop = Event()
                self.update_account_button()
                if self.account_name and self.account_name() and changed:
                    self._initialize_controls()
                elif self.account_name is not None and not self.account_name():
                    self.show_signed_out()
        elif control_id in (200, 320, 321):
            row, related = self._selected_row()
            if not row:
                return
            if control_id == 320 and row.get('video'):
                play_row(row, self.g.DATA_PATH)
                return
            if row.get('video') and control_id in (200, 321):
                open_title(row, related, self.g.PLUGIN_PATH, self.g.DATA_PATH)
                return
            if not self._open_browse(row):
                self.g.dialog.ok('Prime Scene', 'This menu item is unavailable here. Open Settings for account options.')

    def onAction(self, action):
        action_id = action.getId()
        # The skin paints the hero from Container(200) on every move. This
        # updates the same fields on the main thread for the scrim and for
        # hosts that read window properties. Left/right are predicted because
        # Kodi delivers onAction before the list selection changes.
        if action_id in _LATERAL and self._focus_id() in (None, 200):
            with self.lock:
                if self.rows:
                    position = self._selected_index() + _LATERAL[action_id]
                    position = max(0, min(len(self.rows) - 1, position))
                    row = self.rows[position]
                else:
                    row = None
                    position = 0
            if row is not None:
                self._apply_hero(row, position)
                self._followed = position
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


class TitleWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        _prepare(self, args)
        self.video = {}
        self.related = []
        self.profile = ''

    def onInit(self):
        video = self.video or {}
        info = video.get('info') or {}
        self.setProperty('Kicker', _kicker_for(video))
        self.setProperty('HeroTitle', (video.get('title') or '').upper())
        self.setProperty('Meta', _detail_meta(video))
        self.setProperty('Synopsis', _synopsis(info.get('plot') or '', 600))
        self.setProperty('HeroFanart', _http(info.get('fanart')) or _http(info.get('poster')) or _http(info.get('thumb')))
        control = self.getControl(210)
        if hasattr(control, 'reset'):
            control.reset()
        for entry in self.related or []:
            control.addItem(_item(entry))
        self.setFocusId(310)

    def onAction(self, action):
        if action.getId() in _BACK:
            self.close()

    def onClick(self, control_id):
        if control_id == 310 and self.video:
            play_row(self.video, self.profile)
        elif control_id == 210 and self.related:
            control = self.getControl(210)
            position = control.getSelectedPosition() if hasattr(control, 'getSelectedPosition') else 0
            if position is None or position < 0 or position >= len(self.related):
                position = 0
            play_row(self.related[position], self.profile)


class PlayerOverlay(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        _prepare(self, args)
        self.info = {}

    def onInit(self):
        info = self.info or {}
        self.setProperty('Title', (info.get('title') or 'PRIME SCENE').upper())
        self.setProperty('Meta', info.get('meta') or '')
        self.apply_times(info.get('position') or 0, info.get('duration') or 0)
        volume = self.getControl(520)
        if hasattr(volume, 'setPercent'):
            volume.setPercent(int(info.get('volume') or 40))

    def onAction(self, action):
        if action.getId() in _BACK:
            self.close()

    def onClick(self, control_id):
        if control_id == 500:
            xbmc.executebuiltin('PlayerControl(Previous)')
        elif control_id == 501:
            xbmc.executebuiltin('PlayerControl(Play)')
        elif control_id == 502:
            xbmc.executebuiltin('PlayerControl(Next)')

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


def open_title(video, related, plugin_path, profile):
    window = TitleWindow(_TITLE_XML, plugin_path, 'Default', '1080i')
    window.video = video
    window.related = list(related or [])
    window.profile = profile
    window.doModal()
    return window


def playback_overlay(profile):
    """Modeless player chrome. The service calls tick() while a title plays."""
    note = _read_note(profile) or {}
    window = PlayerOverlay(_PLAYER_XML, _addon_path(), 'Default', '1080i')
    window.info = {
        'title': note.get('title') or '',
        'meta': note.get('meta') or '',
        'volume': 40,
    }
    window.show()
    window.onInit()
    return window


def watch_playback(profile):
    """Show Aurora player chrome only for a title this add-on just started."""
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    overlay = None
    latched = False
    while not monitor.abortRequested():
        playing = False
        try:
            playing = bool(player.isPlaying())
        except Exception:
            playing = False
        due = overlay_due(playing, _read_note(profile), time(), latched)
        if due:
            latched = True
            if overlay is None:
                overlay = playback_overlay(profile)
            else:
                overlay.tick(player)
        elif overlay is not None:
            overlay.close()
            overlay = None
            latched = False
            _clear_note(profile)
        if monitor.waitForAbort(0.5):
            break
    if overlay is not None:
        overlay.close()


def open_interface(globals_instance, settings_instance, initialize_provider=None, account_name=None,
                   account_action=None, offer_signin=False, launch_diagnostics=None):
    if settings_instance.data_source != 0:
        globals_instance.dialog.ok('Prime Scene', 'Choose Website under Settings > Data Source to browse Prime Scene.')
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
        if window.worker is not None:
            while window.worker.is_alive():
                window.worker.join(0.1)
    if window.init_error is not None:
        if launch_diagnostics is not None:
            launch_diagnostics({'event': 'interface_initialization_failed',
                                'error_type': getattr(window, 'init_error_type', None) or window.init_error,
                                'error': window.init_error,
                                'current_window_id': xbmcgui.getCurrentWindowId()})
        globals_instance.dialog.ok('Prime Scene', 'The interface could not initialize (' +
                                   window.init_error + '). Your sign-in is saved.')
        return False
    return window.result
