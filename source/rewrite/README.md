# Smooth Prime 0.2.72 season artwork and episode viewing state

- Use each season's own Amazon artwork and metadata; prepare missing sibling artwork from its declared page in the background instead of copying the selected season.
- Map identity-specific Amazon episode playback actions to Watched/Unwatched states, native playcount/resume tags and exact played/remaining times.
- Keep watched episodes visible inside seasons opened through In Progress and refresh episode state on entry and after provider STOP settlement.
- Preserve selected cards during refresh, invalidate older episode prefetch and add passive display-mode diagnostics during playback and at Stop.
- Retain working Watchlist, In Progress preload, genre reset, native HD, startup-flash and persistent Chrome fixes. The remaining physical Stop flash is still unresolved.

Actual signed-in Amazon validation confirmed different original artwork for Seasons 1 and 2, and correctly mapped all eight Season 1 episodes: two in progress, one finished and five unstarted. Final labels were validated from the unmodified real responses, including 0:33 played / 50:09 left, 43:23 played / 4:18 left, Watched and Unwatched. Completed episodes remain visible. No new Kodi playback tests were run for publication. The physical NUC GUI outcome is not verified. The user reports the Stop flash persists on 0.2.71, but the current available NUC report ends with 0.2.70; current Stop diagnostics are still needed. This version adds passive display samples without changing display/HDCP settings or Stop behavior. Main/testing only; no public release promotion.
