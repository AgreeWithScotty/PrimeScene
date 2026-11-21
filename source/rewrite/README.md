# Smooth Prime 0.2.10

Same permanent add-on, based on verified 0.2.9. After a movie or episode ends
or stops, restore its originating selected card rather than navigating Home.
Capture the page, card identity/index, loaded view, season and Back history at
click time, including before asynchronous title resolution. Reuse the current
view and any appended pages where possible; otherwise restore the in-memory
origin snapshot without another catalog request. Clear account-owned origin
state on sign-out. Existing return-target and newer-playback guards remain.

Keep the original Kodi exit window, quiet navigation, top selectors, artwork
fitting, TV episode browser, season selector and description/artwork preloading.
Diagnostics correlate request IDs with expected/actual indices, card identity
restoration, restoration source, terminal reasons and actual window outcomes.
No simulated tests or real-device card-return success claims were made.

Rebuild using tools/package_card_return_release.py and the published 0.2.9 ZIP.
