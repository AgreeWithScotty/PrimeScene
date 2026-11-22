# Smooth Prime 0.2.20: authentication replacement

The old login.py and users.py modules are deleted from the current app/archive.
Native sign-in, account reads/writes, region discovery, credentials and device
token renewal use independent modules, with no login/users/common/network
imports in their conservative import closure. Existing-session challenges use
the native form engine rather than MechanicalSoup or the former MFA UI.
Only the canonical scoped CookieJar supplies website authentication. Token
renewal uses the registered Amazon device wire protocol; DeviceLegacy is a
provider protocol value, not a legacy module fallback. Background requests do
not initiate a password login. Explicit account switching purges before login.

Account writes and token renewal respect generation invalidation; concurrent
authorization is serialized with the native sign-in lock. Reports capture real
request/attempt IDs, stages/timings, status/redacted redirects, protocol/error
structure and exception locations, excluding passwords/codes/tokens/cookies
and private account identifiers. Raw auth-page HTTP dumps are removed.

Concrete evidence covers all Python/XML parsing, relative import/export closure,
absence of old modules/symbols, unchanged player logic apart from credential
adapter names, unchanged other distributed bytes, ZIP CRC/hash, and an actual
anonymous Amazon startup response. None establishes signed-in authentication
or playback success. No mocked provider/Kodi tests were run.

Remaining catalog compatibility, network, player/proxy and shared backend code
is still derived and attributed in NOTICE.md. The entire backend is NOT yet
original. Historical published ZIPs are immutable upgrade/rollback evidence.
