# Prime Scene for Prime Video

Standalone Kodi application with permanent ID `plugin.video.primeinterface`.
Current release: 0.2.2. Install updates through the ScottyWins repository; do not
uninstall between versions. Account data belongs only to this add-on.

The Aurora Cinema window opens directly from Video add-ons: a matte charcoal
and cool slate hero, horizontal title cards, and a focus state you can read
from the couch. No separate Kodi skin is required. Home, Movies and TV load
real Prime Video titles from the signed-in storefront reader. The focused
title's artwork, metadata and synopsis are bound to that card, so the hero
updates as you move. Results append as each server page arrives.
When Amazon provides a page continuation, the next page starts as you reach
the newest cards.

Website sign-in uses native Kodi prompts for credentials, verification methods,
email/SMS codes, approval and security images. It preserves complete cookie
scope and browser headers. Device registration is separate from website login.
The account button displays the saved name and offers Sign out or Sign in again.
Older accounts without a full website session can reconnect there. Signing out
clears account sessions, lists, history, profiles and artwork.

Settings > Diagnostics > View diagnostic report opens one combined report.
It records installed release, operation IDs, stages, timings, redacted
redirects, HTTP statuses, parser structure and explicit outcomes. Passwords,
verification codes, tokens, cookie values and private account identifiers are
excluded. Current-attempt sign-in diagnostics survive successful account
replacement; prior-account files are erased.

Search, My Stuff, profiles, Sports, News, Live TV and playback use the existing
implementations and start only when requested. Watch Now plays through the
existing playback URL. The player overlay is Aurora chrome on top of that player.

The release ZIP contains complete runnable source and the GPL license. No
separately installed Amazon add-on is required. See NOTICE.md for inherited
backend credits and the SmoothFlix Aurora Cinema attribution. Not affiliated
with Amazon.
