# Prime Scene Rewrite Preview 0.0.5

Install or update through Prime Scene Repository, Video add-ons, Prime Scene Rewrite Preview. The preview ID is script.primescene.rewrite.

This directory contains the three independently implemented runtime modules included in the ZIP. They use Python standard-library HTTP, cookies, and HTML parsing with native Kodi dialogs.

The login handles regional redirects, verification-method selection, code entry, email approval, approval-to-code transitions, and manual security-image entry. Authentication requires an authentication cookie together with account-page data. Redacted stages are written to prime-scene-test-report.jsonl in the preview profile.

25 local tests passed. Exact AST comparison found no identical substantial function bodies among 39 functions in the runtime and 257 upstream functions. This is a structural check, not a full similarity analysis.

The complete live verification flow remains to be confirmed. Signed-in Home is under investigation. Playback inside Kodi, device-token integration, search, watchlists, profiles, and episode navigation are unfinished.
