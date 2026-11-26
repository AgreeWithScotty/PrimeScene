# Smooth Prime 0.2.65 performance update

- Reuse verified unchanged private Chrome runtime files instead of repeating the full runtime hash scan on every playback.
- Signal native helper shutdown independently of media traffic, wake cancelled waits and bound helper exit before owned-job cleanup.
- Avoid redundant Windows output-protection requests while checking real protection and retaining the measured HDCP 2.2 capability for HD.
- Record runtime verification work, adapter revision, output-protection release and native shutdown timings in the complete diagnostics report.
- Retain automatic pinned Google runtime download and all accumulated regional, sign-in, Watchlist, Back, resume, artwork and title fixes.

Prior local stock-Kodi HD measurement: 1920x800, 9.6 seconds to first HD observation, 157 ms native adapter teardown. The Windows NUC was unavailable, so its reported freeze remains pending user validation. No further tests were run for this requested publication. Main/testing only; no public release promotion.
