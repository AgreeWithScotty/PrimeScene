# Smooth Prime 0.2.67 catalog and HD startup

- Display cached catalog and Watchlist cards immediately while refreshing provider data in the background; reconcile confirmed additions, removals and empty lists.
- Reuse account-scoped HTTPS connections for catalog, artwork and playback, retain confirmed regional Watchlist navigation, and use bounded gzip transfers.
- Overlap the selected title request with Windows HDCP preparation and reduce DASH manifest processing while preserving every provider track, DRM field and signed media URL.
- Preserve native HD in unmodified Kodi, genuine HDCP enforcement, automatic persistent Chrome reuse, and accumulated regional, sign-in, Back, resume, artwork and title fixes.
- Record actionable connection reuse, preparation overlap, transfer size, XML processing, cancellation and failure diagnostics.

Actual prior local stock-Kodi validation played H264 1920x800 for 12 seconds, first HD at 6625ms versus an earlier separate 9563ms run. Network and HDCP timing vary; NUC latency is not yet measured. Same actual MPD processing was approximately halved with equal XML semantics and all track/DRM counts preserved. Actual signed-in Watchlist reads and original-artwork connection reuse passed. No additional playback tests were run for this version-only publication. Main/testing only; no public release promotion.
