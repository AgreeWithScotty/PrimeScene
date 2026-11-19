"""Dedicated Kodi window for the independent Prime Scene storefront."""

from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Thread
import json
from urllib.error import HTTPError

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from prime_signin import SignInError, authenticate, catalog_opener, diagnostic_host, load_cookies, save_cookies
from public_catalog import PublicCatalogError, fetch_cards


TITLE = 'Prime Scene Rewrite Preview'
GRID = 200
ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / 'resources' / 'skins' / 'Default' / 'media'


@dataclass(frozen=True)
class Section:
    label: str
    key: str
    artwork: str


SECTIONS = (
    Section('Prime Video Home', 'home', 'category-home.webp'),
    Section('Movies', 'movies', 'category-cinema.webp'),
    Section('TV Shows', 'tv', 'category-tv.webp'),
    Section('Sign in to Prime Video', 'signin', 'category-profile.webp'),
)
HEADINGS = {section.key: section.label for section in SECTIONS}


def ask_kodi(label, masked):
    keyboard = xbmc.Keyboard('', label, masked)
    keyboard.doModal()
    return keyboard.getText() if keyboard.isConfirmed() else None


def approve_kodi(message):
    return xbmcgui.Dialog().yesno(TITLE, message, yeslabel='Continue', nolabel='Cancel')


class StorefrontWindow(xbmcgui.WindowXML):
    def __init__(self, *_):
        self.changed = Condition()
        self.closed = False
        self.pending = None
        self.generation = 0
        self.page = None
        self.cards = ()
        self.cache = {}
        self.root_selection = 0
        self.account_file = None
        self.account_cookies = None
        self.account_generation = 0
        self.report_path = None

    def record_test_event(self, event):
        if self.report_path is None:
            return
        try:
            with self.changed:
                self.report_path.parent.mkdir(parents=True, exist_ok=True)
                with self.report_path.open('a', encoding='utf-8') as output:
                    output.write(json.dumps(event) + '\n')
        except OSError:
            pass

    def onInit(self):
        if self.getCurrentContainerId() != GRID:
            xbmc.executebuiltin('Container.SetViewMode(200)', True)
        self.setProperty('brandicon', str(ROOT / 'resources' / 'icon.png'))
        self.show_home()
        Thread(target=self.fetch_sections, daemon=True, name='PrimeSceneStorefront').start()

    def replace_grid(self, rows):
        self.clearList()
        items = []
        for label, artwork in rows:
            item = xbmcgui.ListItem(label=label)
            item.setArt({'thumb': artwork})
            items.append(item)
        if items:
            self.addItems(items)

    def position_grid(self, home):
        grid = self.getControl(GRID)
        grid.setPosition(250, 45 if home else 132)
        grid.setHeight(986 if home else 924)

    def focused_control(self):
        try:
            return self.getFocusId()
        except RuntimeError:
            return None

    def show_home(self):
        with self.changed:
            self.generation += 1
            self.pending = None
            self.page = None
            self.cards = ()
            self.setProperty('home', 'true')
            self.clearProperty('status')
            self.position_grid(home=True)
            self.replace_grid((section.label, str(MEDIA / section.artwork)) for section in SECTIONS)
            self.setCurrentListPosition(self.root_selection)
            self.setFocusId(GRID)

    def show_section(self, key):
        with self.changed:
            self.generation += 1
            self.pending = None
            self.page = key
            self.cards = ()
            self.clearProperty('home')
            self.setProperty('heading', HEADINGS[key])
            self.position_grid(home=False)
            focus = self.focused_control()
            self.clearList()
            if key in self.cache:
                self.render_cards(self.cache[key], focus)
            else:
                self.setProperty('status', 'Loading titles…')
                self.pending = (self.generation, key, focus)
                self.changed.notify()

    def render_cards(self, cards, focus):
        self.cards = cards
        self.replace_grid((card.title, card.artwork_url) for card in cards)
        self.clearProperty('status')
        current_focus = self.focused_control()
        if current_focus is None or current_focus == focus:
            self.setFocusId(GRID)

    def fetch_sections(self):
        while True:
            with self.changed:
                self.changed.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                generation, key, focus = self.pending
                self.pending = None
                cookies, account_generation = self.account_cookies, self.account_generation
            try:
                cards = tuple(fetch_cards(key, opener=catalog_opener(cookies))) if cookies else tuple(fetch_cards(key))
                self.record_test_event({'event': 'catalog', 'page': key, 'cards': len(cards)})
            except HTTPError as error:
                self.record_test_event({'event': 'catalog_http_error', 'page': key,
                                        'status': error.code, 'host': diagnostic_host(error.url)})
                cards = ()
            except (PublicCatalogError, OSError, LookupError):
                self.record_test_event({'event': 'catalog_reader_error', 'page': key})
                cards = ()
            with self.changed:
                if self.closed:
                    return
                if account_generation != self.account_generation:
                    continue
                if cards:
                    self.cache[key] = cards
                if generation != self.generation:
                    continue
                if cards:
                    self.render_cards(cards, focus)
                else:
                    self.setProperty('status', 'Could not load titles. Choose a category to retry.')
                    if self.focused_control() in (None, GRID):
                        self.setFocusId(102 + next(i for i, section in enumerate(SECTIONS) if section.key == key))

    def sign_in(self):
        cookies = load_cookies(self.account_file)
        dialog = xbmcgui.Dialog()
        try:
            if not authenticate(cookies, ask_kodi, approve=approve_kodi, observer=self.record_test_event):
                return
        except SignInError as error:
            self.record_test_event({'event': 'signin_failed', 'message': str(error)})
            dialog.ok(TITLE, str(error))
            return
        except (OSError, LookupError):
            dialog.ok(TITLE, 'Could not complete the connection to Amazon. Try signing in again.')
            return

        message = 'Signed in to Prime Video.'
        try:
            save_cookies(cookies, self.account_file)
        except OSError:
            message = 'Signed in to Prime Video. Kodi could only keep this session until the preview closes.'
        with self.changed:
            self.account_cookies = cookies
            self.account_generation += 1
            self.cache.clear()
            self.show_home()
        dialog.ok(TITLE, message)

    def onClick(self, control_id):
        sign_in = False
        card = None
        if control_id == 101:
            self.show_home()
        elif control_id in (102, 103, 104):
            self.show_section(SECTIONS[control_id - 102].key)
        elif control_id == 105:
            sign_in = True
        elif control_id == GRID:
            with self.changed:
                position = self.getCurrentListPosition()
                if self.page is None and 0 <= position < len(SECTIONS):
                    self.root_selection = position
                    section = SECTIONS[position]
                    if section.key == 'signin':
                        sign_in = True
                    else:
                        self.show_section(section.key)
                elif self.page is not None and 0 <= position < len(self.cards):
                    card = self.cards[position]
        if sign_in:
            self.sign_in()
        elif card is not None:
            xbmcgui.Dialog().ok(card.title, 'Playback inside Kodi is not available in this preview.')

    def onAction(self, action):
        if action.getId() not in (9, 10, 92):
            return
        if self.page is None:
            self.stop()
            self.close()
        else:
            self.show_home()

    def stop(self):
        with self.changed:
            self.closed = True
            self.pending = None
            self.changed.notify_all()


def main():
    window = StorefrontWindow('PrimeSceneRewrite.xml', str(ROOT), 'Default', '1080i')
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    window.account_file = Path(profile) / 'prime-video-session.cookies'
    window.report_path = Path(profile) / 'prime-scene-test-report.jsonl'
    window.account_cookies = load_cookies(window.account_file)
    try:
        window.doModal()
    finally:
        window.stop()


if __name__ == '__main__':
    main()
