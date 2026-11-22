# Smooth Prime 0.2.21

In Progress requests a new Amazon snapshot on every visit, including return
from playback. Memory/disk/bridge caches are bypassed; the obsolete local
watch-progress store and saved Home page are deleted under the account epoch
guard. No saved playback-card ledger is merged. Repeated actual product IDs
are collapsed without guessing that unrelated same-title editions are equal.
Provider completion fields are read fresh for In Progress series/season views.
Normal Movies/TV/Sports/Purchases caching and preloading remain.

Initial focus is the In Progress top tab; clicking a category still moves to
its first card. Native playback return waits for Kodi's natural restoration
and activates the catalog only if it is still inactive. No catalog window
fade animations remain. Every app control is in a group hidden during native
fullscreen video. The optional skip dialog closes once, only if shown.
Normal section return preserves the existing originating card/view/history;
In Progress return selects the matching card from the new provider snapshot.

Diagnostics cover cleanup, cache bypass, request/attempt IDs, real HTTP status
and redacted redirects/structure, window/view/card counts, focus/selection,
active dialog and elapsed times. Failures retain exception locations without
private account identifiers, passwords, codes, tokens or cookie values.

Concrete checks cover syntax, relative imports/exports, exact baseline byte
preservation, unchanged authentication and unchanged player code except the
skip-dialog lifecycle, archive CRC/hash and actual publication downloads.
The supplied pre-change recording and Kodi window-manager source informed the
repair. No simulated provider/Kodi tests or post-change device-success claims.
Full backend original-code replacement remains unfinished; keep NOTICE.md.
