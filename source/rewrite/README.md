# Prime Scene for Prime Video

Standalone Kodi application with permanent ID `plugin.video.primeinterface`.
Current release:0.1.28. Install updates through the ScottyWins repository; do not
uninstall between versions. Account data belongs only to this add-on.

The dedicated window opens directly from Video add-ons. Its eight category
tiles use bundled cinematic artwork and appear without a catalog request.
Home, Movies and TV use Prime Scene's independent reader with the authenticated
website session. Results append as each actual server page arrives. When Amazon
provides a page continuation, the second page starts immediately; entering the
newest page requests the following page without resetting the selected card.
Ambiguous carousel continuations and token-only responses are reported rather
than treated as complete page links.

Website sign-in uses native Kodi prompts for credentials, verification methods,
email/SMS codes, approval and security images. It preserves complete cookie
scope and browser headers. Device registration is separate from website login.
The bottom account button displays the saved name and offers Sign out or Sign
in again. Older accounts without a full website session can reconnect there.
Signing out clears account sessions, lists, history, profiles and artwork.

Settings > Diagnostics > View diagnostic report opens one combined report.
It records installed release/environment, operation IDs, stages, timings,
redacted redirects, HTTP statuses, parser structure and explicit outcomes.
Passwords, verification codes, tokens, cookie values and private account
identifiers are excluded. Current-attempt sign-in diagnostics survive successful
account replacement; prior-account files are erased.

The existing title/episode, playback, Search, My Stuff, profile, Sports, News
and Live TV implementations are initialized only when requested. Their complete
replacement remains unfinished. Website session verification, live signed-in
catalog formats and playback after these integration changes are not established
by syntax or package checks. No simulated provider tests were run for 0.1.28.

The release ZIP contains complete runnable source and GPL license. No separately
installed Amazon add-on is required. See NOTICE.md for inherited backend credits.
Not affiliated with Amazon.
