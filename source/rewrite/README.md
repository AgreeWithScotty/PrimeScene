# Prime Scene 0.2.5 navigation

The active runtime is the existing plugin.video.primeinterface application, based
on published 0.2.4 Aurora Cinema. Its playback and overlay components are preserved.
Rebuild this package using tools/package_navigation_release.py and the verified
published 0.2.4 ZIP. All six source overlays are in source/rewrite; the full ZIP
contains the remaining source and required licenses. No extra add-on is created.

Screens retain their card objects and selection while navigating back or between
visited tabs. Home/Movies/TV share bounded in-flight storefront work, warm their
first two actual server pages, and cache first pages inside the active account
profile. A cached page displays before a stale refresh completes. Explicit sign-out
invalidates the account generation, closes workers and clears account-owned caches.

Cards bind once per page through WindowXML rather than one complete rebind per
ControlList.addItem. Expensive title rasterization is performed for focused/nearby
cards after the rail is usable. Public image read-ahead uses exact existing sized
URLs and bounded background workers. Metadata lookup waits for settled focus and
cannot hold the primary catalog lane. Explicit playback still uses the prior
resolution and overlay scheme.

Settings > Diagnostics records real navigation generations and stage timings for
cache hits, requests, card building/binding, title drawing and image downloads.
Syntax, actual archive/source preservation and published bytes are concrete checks;
they are not claims about measured device latency or live provider success. No
simulated provider or Kodi tests were run for this release.
