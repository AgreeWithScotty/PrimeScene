"""Native Kodi streaming interface. One provider worker, generation-scoped updates."""
from queue import Empty, Queue
from os.path import join
from threading import Event, RLock, Thread
import xbmc
import xbmcgui

from .dedicated_bridge import ProviderBridge, is_title_card
from .interface_diagnostics import record


class StreamingWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        self.g = kwargs.pop('globals_instance')
        self.initialize_provider = kwargs.pop('initialize_provider', None)
        self.account_name = kwargs.pop('account_name', None)
        self.account_action = kwargs.pop('account_action', None)
        self.offer_signin = kwargs.pop('offer_signin', False)
        self.launch_diagnostics = kwargs.pop('launch_diagnostics', None)
        # Kodi constructs WindowXML in __new__; do not call object.__init__ with
        # XML filename/path arguments a second time.
        self.init_error = None
        self.jobs = Queue()
        self.stop = Event()
        self.lock = RLock()
        self.generation = 0
        self.rows = []
        self.poster_grid = False
        self.items = []
        self.lookup = {}
        self.identities = {}
        self.restore_selection = None
        self.history = []
        self.path = 'root'
        self.query = None
        self.heading = 'Your Prime Video library'
        self.next_path = None
        self.prefetch_at = 0
        self.busy = False
        self.result = None
        self.worker = None
        self.artwork = set()
        self.account_generation = None

    def onInit(self):
        if self.worker is not None:
            return
        record(self.g, 'window_initialized')
        if self.launch_diagnostics is not None:
            self.launch_diagnostics({'event': 'window_on_init', 'current_window_id': xbmcgui.getCurrentWindowId()})
        try:
            self._initialize_controls()
        except Exception as error:
            self.init_error = type(error).__name__
            record(self.g, 'window_init_failed', error)
            self.stop.set()
            self.close()

    def _initialize_controls(self):
        if self.getCurrentContainerId() != 200:
            xbmc.executebuiltin('Container.SetViewMode(200)', True)
        if self.getCurrentContainerId() != 200:
            raise RuntimeError('Kodi did not activate the Prime Scene catalog grid')
        self.setProperty('brandicon', join(self.g.PLUGIN_PATH, 'resources', 'art', 'prime-scene-icon-v2.png'))
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
                    self.onClick(104)
            return
        if self.account_name is not None:
            from .account_state import account_epoch
            self.account_generation = account_epoch(self.g.DATA_PATH)
        self.setProperty('status', 'Connecting to Amazonâ€¦')
        self.worker = Thread(target=self._work, name='PrimeCatalog', daemon=True)
        self.navigate('root', remember=False)
        self.setFocusId(200)
        self.worker.start()
        if self.launch_diagnostics is not None:
            self.launch_diagnostics({'event': 'interface_ready', 'signed_in': True,
                                     'current_window_id': xbmcgui.getCurrentWindowId()})

    def update_account_button(self):
        name = self.account_name() if self.account_name is not None else ''
        self.setProperty('accountlabel', name or 'Sign in')

    def show_signed_out(self):
        with self.lock:
            self.generation += 1
            self.rows, self.items, self.lookup, self.identities = [], [], {}, {}
            self.history.clear()
            self.artwork.clear()
            self.clearList()
            self.path, self.query, self.next_path = 'root', None, None
            self.busy = False
            self.clearProperty('hascontent')
            self.clearProperty('postergrid')
            self.setProperty('rootview', 'true')
            self.setProperty('heading', '')
            self.setProperty('accountlabel', 'Sign in')
            self.setProperty('status', 'Sign in to Prime Video to browse and watch.')
            self.setFocusId(104)

    def pause_catalog(self):
        self.stop.set()
        self.setProperty('accountlabel', 'Signing out...')
        self.setProperty('status', 'Signing out...')
        with self.lock:
            self.generation += 1
            self.rows, self.items, self.lookup, self.identities = [], [], {}, {}
            self.history.clear()
            self.clearList()
            self.next_path = None
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
            control = self.getControl(200)
            if remember and (path, query) != (self.path, self.query):
                self.history.append((self.path, self.query, self.heading, self.getCurrentListPosition()))
            self.path = path
            self.query = query
            self.heading = heading or 'Your Prime Video library'
            self.setProperty('heading', self.heading)
            self.setProperty('rootview', 'true' if path == 'root' else 'false')
            # Move the actual panel, including its native clipping and hit area.
            control.setPosition(264, 60 if path == 'root' else 310)
            control.setHeight(960 if path == 'root' else 700)
            self.generation += 1
            self.rows, self.items, self.lookup = [], [], {}
            self.identities = {}
            self.restore_selection = selection or None
            self.poster_grid = False
            self.clearProperty('postergrid')
            self.clearList()
            self.clearProperty('hascontent')
            self.setProperty('status', 'Connecting to Amazonâ€¦')
            self.next_path = None
            self.prefetch_at = 0
            self.busy = True
            self.jobs.put((self.generation, path, query, selection))

    def _publish(self, token, packet):
        with self.lock:
            if self.stop.is_set() or token != self.generation:
                return
            if self.account_generation is not None:
                from .account_state import current_account
                if not current_account(self.g.DATA_PATH, self.account_generation):
                    return
            additions = []
            if not self.rows and self.path != 'root' and packet['rows'] and not packet.get('mixed_grid'):
                # Choose one grid for the whole page, even as later batches arrive.
                self.poster_grid = all(is_title_card(row['info'], row['video']) for row in packet['rows'])
                if self.poster_grid:
                    self.setProperty('postergrid', 'true')
            for row in packet['rows']:
                key = row['url']
                if key in self.lookup:
                    continue
                identity = row.get('identity')
                existing = self.identities.get(identity)
                if existing is not None:
                    previous = self.rows[existing]
                    if previous['info'].get('mediatype') == 'storefront' and row['info'].get('mediatype') != 'storefront':
                        # Keep the card and selection in place as signed-in metadata arrives.
                        self.rows[existing] = row
                        self.lookup[key] = existing
                        item = self.items[existing]
                        item.setProperty('summary', str(row['info'].get('plot', '')))
                        item.setProperty('year', str(row['info'].get('year') or ''))
                        item.setProperty('action', 'Watch' if row['video'] else 'Explore')
                    continue
                item = xbmcgui.ListItem(label=row['title'])
                info = row['info'].copy()
                title_card = is_title_card(info, row['video'])
                if not title_card and not info.get('thumb'):
                    info['thumb'] = info.get('poster') or info.get('fanart') or join(
                        self.g.PLUGIN_PATH, 'resources', 'skins', 'Default', 'media', 'category-cinema.webp')
                art = {k: info[k] for k in ('thumb', 'poster', 'fanart') if info.get(k)}
                new_art = {value for value in art.values() if value.startswith('https://')} - self.artwork
                if new_art and self.account_name is not None:
                    self.artwork.update(new_art)
                    from pathlib import Path
                    import json
                    try:
                        with (Path(self.g.DATA_PATH) / 'account-artwork.jsonl').open('a', encoding='utf-8') as output:
                            for value in new_art:
                                output.write(json.dumps(value) + '\n')
                    except OSError:
                        pass
                if title_card:
                    artwork = art.get('poster') or art.get('thumb') or art.get('fanart')
                    if artwork:
                        art['titlecard'] = artwork
                    item.setProperty('contentcard', 'true')
                item.setArt(art)
                item.setProperty('summary', str(info.get('plot', '')))
                item.setProperty('year', str(info.get('year') or ''))
                item.setProperty('action', 'Watch' if row['video'] else 'Explore')
                self.lookup[key] = len(self.rows)
                if identity:
                    self.identities[identity] = len(self.rows)
                self.rows.append(row)
                self.items.append(item)
                additions.append(item)
            if additions:
                # WindowXML binds the items and current selection together.
                # ControlList.addItems queues a separate bind that selects item zero.
                self.addItems(additions)
                if self.restore_selection is not None and self.restore_selection < len(self.rows):
                    self.setCurrentListPosition(self.restore_selection)
                    self.restore_selection = None
            self.next_path = packet['next']
            if self.rows:
                self.setProperty('hascontent', 'true')
                self.setProperty('status', '')

    def _work(self):
        try:
            record(self.g, 'provider_initializing')
            if self.initialize_provider is not None:
                self.initialize_provider()
            bridge = ProviderBridge(self.g.pv, self.g)
            record(self.g, 'provider_ready')
        except Exception as error:
            record(self.g, 'provider_init_failed', error)
            if not self.stop.is_set():
                self.setProperty('status', 'Could not open the catalog. Your sign-in is saved. Reopen Prime Scene to retry.')
            self.busy = False
            return
        monitor = xbmc.Monitor()
        while not self.stop.is_set() and not monitor.abortRequested():
            try:
                token, path, query, selection = self.jobs.get(timeout=0.15)
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
                            self.setProperty('status', 'No titles were returned. Try Home or Search.')
                        self.restore_selection = None
            except BaseException as error:
                record(self.g, 'catalog_request_failed', error)
                # No request URLs, tokens or exception bodies enter this log.
                xbmc.log('[Prime Interface] Catalog operation failed: ' + type(error).__name__, xbmc.LOGERROR)
                with self.lock:
                    if token == self.generation and not self.stop.is_set():
                        self.setProperty('status', 'Unable to finish loading. Return Home to retry.')
            finally:
                with self.lock:
                    if token == self.generation:
                        self.busy = False
                        self._prefetch()

    def _prefetch(self):
        with self.lock:
            if self.busy or not self.next_path or self.stop.is_set():
                return
            selected = max(0, self.getCurrentListPosition())
            if selected >= self.prefetch_at:
                path = self.next_path
                self.next_path = None
                self.busy = True
                self.jobs.put((self.generation, path, None, 0))

    def onClick(self, controlId):
        if controlId == 101:
            self.history.clear()
            self.navigate('root', remember=False)
        elif controlId == 102:
            phrase = self.g.dialog.input('Search Prime Video').strip()
            if phrase:
                self.navigate('search', query=phrase, heading='Search results')
        elif controlId == 103:
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
        elif controlId == 104 and self.account_action is not None:
            changed = False
            try:
                changed = self.account_action(self.pause_catalog)
            except Exception as error:
                self.g.dialog.ok('Prime Scene', str(error) if isinstance(error, RuntimeError)
                                 else 'Account controls could not finish (' + type(error).__name__ + ').')
            finally:
                self.stop = Event()
                self.update_account_button()
                if self.account_name() and changed:
                    self._initialize_controls()
                elif not self.account_name():
                    self.show_signed_out()
        elif controlId == 200:
            with self.lock:
                index = self.getCurrentListPosition()
                if not 0 <= index < len(self.rows):
                    return
                row = dict(self.rows[index])
            if row['video']:
                xbmc.Player().play(row['url'])
            elif row['url'].startswith(self.g.pluginid + 'pv/browse/'):
                path = row['url'].split('pv/browse/', 1)[1]
                self.navigate(path, heading=row['title'])
            elif '?mode=Search' in row['url']:
                self.onClick(102)
            else:
                self.g.dialog.ok('Prime Video', 'This menu item is unavailable here. Open Settings for account options.')

    def onAction(self, action):
        action_id = action.getId()
        if action_id not in (9, 10, 92):
            return
        if self.stop.is_set():
            return
        # Keep catalog Back/Escape in this window. At Home the XML previouswindow
        # setting sends Kodi directly home instead of refreshing its Videos list.
        if self.path != 'root' and self.history:
            record(self.g, 'catalog_back')
            path, query, heading, selected = self.history.pop()
            self.navigate(path, remember=False, query=query, heading=heading, selection=selected)
        else:
            self.stop.set()
            record(self.g, 'window_exit_to_kodi_home')
            self.close()


def open_interface(globals_instance, settings_instance, initialize_provider=None, account_name=None, account_action=None,
                   offer_signin=False, launch_diagnostics=None):
    if settings_instance.data_source != 0:
        globals_instance.dialog.ok('Prime Video', 'This preview currently supports the Website data source. Opening Classic view.')
        return 'classic'
    record(globals_instance, 'window_creating')
    window = StreamingWindow('PrimeStreaming.xml', globals_instance.PLUGIN_PATH,
                             'Default', '1080i', globals_instance=globals_instance,
                             initialize_provider=initialize_provider, account_name=account_name,
                             account_action=account_action, offer_signin=offer_signin,
                             launch_diagnostics=launch_diagnostics)
    try:
        window.doModal()
    finally:
        window.stop.set()
        # Finish the current bounded batch before restoring provider bindings.
        # In-flight HTTP calls retain their existing timeout; no thread is killed.
        if window.worker is not None:
            while window.worker.is_alive():
                window.worker.join(0.1)
    if window.init_error is not None:
        if launch_diagnostics is not None:
            launch_diagnostics({'event': 'interface_initialization_failed', 'error_type': window.init_error,
                                'current_window_id': xbmcgui.getCurrentWindowId()})
        globals_instance.dialog.ok('Prime Scene', 'The interface could not initialize (' +
                                   window.init_error + '). Your sign-in is saved.')
        return False
    return window.result
