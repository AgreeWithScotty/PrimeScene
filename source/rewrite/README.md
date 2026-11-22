# Smooth Prime 0.2.28: navigation metadata, not catalog previews

The first-title instruction is superseded. Amazon Categories returns all 12 genre
cover images and overlay-name presentation in one response. genre_catalog parses
the native nested link and cover fields. Current public metadata is seeded, then
one session request refreshes the shared Movies/TV snapshot. No genre catalog is
opened for artwork. Actual metadata response: HTTP 200, 321,877 bytes, 500 ms in
this public observation. All 12 actual sized image URLs returned images, 29,977
bytes combined in 750 ms. These timings are observations, not Kodi UI guarantees.

The per-genre request queue and preview pool are removed. Unrelated section warming
is removed. A selected category still warms its first two actual pages and arms
the existing early lookahead. Grid images use Kodi's asynchronous background texture
cache rather than a duplicate application downloader. Current public art binds
without waiting for the navigation refresh. Metadata updates reuse existing tile
objects and preserve focus. The app-created All/Sports choices use clear text
tiles; actual genre covers use the provider's title overlays, as its native UI does.
Genre art fills each existing grid cell. Only the in-graphic name is shown;
the duplicated below-art label/caption is removed. Extras service presentation
retains its existing layout. The clear focused outline remains.

Startup selects Movies (301), the first menu item, and opens its genre grid. It
does not call In Progress or open a content catalog before a genre/All selection.
Empty native-view restoration targets the active section's menu, not always 309.
No redesign of the menu/grid, original glow, Extras directory or card layout.

50 Python/4 XML and 174 relative import edges checked. Ten established season,
episode, publish/progress, playback-origin and lookahead methods are unchanged;
21 named native/auth/media/font/service modules and all 41 skin assets retain
their 27 bytes. Logs identify category-art sources/counts, zero catalog-image
requests, startup selection, metadata updates and failures without secrets.
No simulated Kodi/provider tests and no remote visual-success claim.
