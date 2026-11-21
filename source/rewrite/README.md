# Smooth Prime 0.2.14

Based on verified 0.2.13, preserving its SMOOTH icon/fanart/launch splash, four
sections, Prime filtering, player and return-to-card behavior. Top category
clicks select/focus the first real card once, leaving later appends alone.

TV listing reads actual episode children even when cached season rendering
returns only folders or nothing. The chosen season is opened when episodes
are absent, with a direct actual-detail-state fallback using observed URLs and
real episode IDs. Production parsing of three current Amazon public responses
returned all 6, 22 and 8 declared episodes and their available season links.

Sign-out invalidates the local generation before worker shutdown, runs each
cleanup stage independently, purges account-owned files/caches, and audits the
remaining profile/settings. Neutral coordination state and the new sanitized
cleanup report may remain. Incomplete cleanup keeps a pending flag, blocks a
new login and exposes Finish sign out. Sign in again clears the old session
before prompting. Website login is stored/announced only after Amazon verifies
it; Amazon chooses any required verification challenges. UI/service notification
and guarded title rendering prevent stale account state being written back.

Diagnostics record real focus receipts, episode structure/relationship counts,
HTTP status/redirects/timings, purge stages and residual counts, fresh-cookie
state and Amazon verification outcomes without secrets or account identifiers.
Actual filesystem/SQLite cleanup primitives and schema upgrade were checked;
these do not establish Kodi-device cleanup or live provider login/playback.
No mocked Kodi/provider tests or device-success claims were made.

Rebuild with tools/package_repair_release.py and the verified 0.2.13 ZIP.
