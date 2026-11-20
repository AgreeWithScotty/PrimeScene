# Prime Scene for Prime Video

Standalone Kodi application with permanent ID `plugin.video.primeinterface`.
Current release: 0.2.4. Install updates through the ScottyWins repository; do not
uninstall between versions. Account data belongs only to this add-on.

The Aurora Cinema window opens directly from Video add-ons: a matte charcoal
and cool slate hero, horizontal title cards, and a focus state you can read
from the couch. No separate Kodi skin is required. Home, Movies and TV load
real Prime Video titles from the signed-in storefront reader. The focused
title is drawn as large condensed type, with its metadata and full synopsis
under it, including when the rail card did not arrive with those details.
Choosing a card plays it. A movie or episode resolves its playable id and
starts. A series plays the next, in-progress, or earliest episode when one
can be resolved, and opens the episode list only when it cannot. Results append
as each server page arrives.
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

The player overlay is the only control scheme during Prime Scene playback.
It stays up for the whole title: pause, OK, info and seek bring it back after
it hides, and Back hides it once before a second Back stops playback. Subtitles
and audio are offered when the stream has them. Other videos keep Kodi's own OSD.

The release ZIP contains complete runnable source and the GPL license. No
separately installed Amazon add-on is required. See NOTICE.md for inherited
backend credits and the SmoothFlix Aurora Cinema attribution. Not affiliated
with Amazon.
