# Smooth Prime 0.2.69 stop cleanup

- Discard unsuccessful Windows HDCP sessions before playback instead of retaining failed sessions until Stop.
- After failed preparation, answer native status queries as unknown without repeating an automatic HDCP probe; preserve explicit protection requests from the genuine CDM.
- Queue the return to the catalog asynchronously and retain the existing readiness acknowledgement.
- Scope failed-preparation policy to the selected playback and release its owned DLL reference safely; record request origin, desired protection mask and display environment.
- Retain native HD in stock Kodi, persistent pinned Chrome reuse, catalog and Watchlist improvements, and all accumulated fixes.

The matching NUC 0.2.68 run still showed two eight-second HDCP timeouts with protection unverified, failed session cleanup after the catalog returned, and a 20-second synchronous catalog activation. This update changes the failed-session lifecycle and return dispatch. Actual local native API checks confirmed a nonintrusive unknown status response and preserved genuine explicit protection, plus DLL ownership/cleanup. Elimination of the NUC stop blackout, HD playback under the new failure policy, and reduction of the full stop delay remain unconfirmed until a new NUC run. The first Python HDCP probe is still performed and may take eight seconds or blank the TV. No new playback tests were run for this publication. Main/testing only; no public release promotion.
