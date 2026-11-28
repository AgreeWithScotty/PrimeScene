# Smooth Prime 0.2.70 Watchlist and HDCP fixes

- Retain rendered Watchlist cards across menu visits and warm saved account cards and original artwork at startup.
- Refresh Watchlist in the background, reuse unchanged native items and preserve loaded artwork and descriptions missing from thinner listings.
- Preserve regional pagination revalidation, confirmed additions/removals and empty lists, partial responses, account isolation and fresh In Progress.
- Request the best Amazon resources before optional Windows HDCP preparation; retry the same title at the same quality with genuine protection when the provider, manifest or content license requires it.
- Preserve explicit CDM protection enforcement and record read-only display settings, protection stages and Watchlist retention diagnostics.
- Retain native HD in stock Kodi, persistent pinned Chrome reuse and all accumulated fixes.

Prior actual signed-in Watchlist reads returned 20 cards with artwork and descriptions in 1859/1609 ms; cached lookup was immediate and three original images downloaded in 641 ms. The prior real stock Kodi run re-requested the same title after verified Windows HDCP 2.2 and played 1920x800 HD for 12 seconds. No new playback tests were run for publication. The matching NUC 0.2.69 run kept 1920x1040 HD and returned to the catalog in 1031 ms, but both physical flashes persisted. The draft removes optional protection setup until required; physical NUC flash elimination and Watchlist rendering remain unconfirmed until a user run. Native binaries and the pinned Google runtime inventory are unchanged. Main/testing only; no public release promotion.
