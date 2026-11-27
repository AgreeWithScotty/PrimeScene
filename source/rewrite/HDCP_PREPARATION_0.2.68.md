# Smooth Prime 0.2.68 HDCP preparation

- Check Windows effective HDCP protection while asynchronous preparation remains pending, in both the Python and owned native adapter waiters.
- Retain a pending request through playback only when the requested protection is independently verified; preserve genuine level-2 enforcement and treat unknown protection as unavailable.
- Record effective state, async status, HRESULT, wait checks and screen mode to diagnose NUC HDCP startup timeouts and display blanking.
- Retain native HD in stock Kodi, persistent pinned Chrome reuse, catalog and Watchlist speed improvements, and all accumulated fixes.

The matching NUC 0.2.67 report showed two eight-second HDCP timeouts. The updated waiters measure the effective state during pending operations and retain verified requests through playback. Actual local Windows API checks passed at level2; they did not reproduce the NUC pending-operation condition. Reduced NUC timeouts and elimination of the physical TV blackout remain unconfirmed until a new NUC run. No new playback tests were run for this version-only publication. Main/testing only; no public release promotion.
