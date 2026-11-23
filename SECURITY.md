# Smooth Prime security boundaries

Credentials stay on the user's Kodi system. Passwords and verification codes
are not written to the saved account. Website cookies and device access/refresh
tokens are credentials and must not be shared, including in support reports.

On Windows, account metadata and cookie files are encrypted using the current
Windows user's DPAPI protection, without a bundled encryption key or a
machine-wide decryption flag. Existing plaintext files migrate atomically during
application/service startup or their next read. Decrypted cookie text is parsed
in memory. Protection errors fail the operation; there is no plaintext fallback.
Moving protected files to another user/computer or using an older add-on version
that cannot read the new format requires signing in again.

On macOS, the native adapter uses current SecItem Keychain APIs. On Linux, it
uses libsecret/Secret Service with an existing, unlocked default collection.
Those native records hold the credentials; the add-on profile contains only
scoped random references. Linux keyring encryption/access policy depends on the
OS keyring implementation and its configuration; Secret Service alone does not
mandate either. No automatic Python keyring backend or plaintext file backend
is accepted. Native adapters have not been exercised on a macOS/Linux host in
this workspace; successful writes require native read-back verification and
report the actual backend. Locked existing vault entries fail without downgrading.

If no usable native store is available, credentials remain only in a dedicated
Kodi-lifetime memory service. This includes Android/Fire TV: no verified native
Android Keystore bridge ships in this release, and hardware-backed protection
is not claimed. Session-only sign-in survives closing/reopening the add-on and
playback calls while that service remains alive, but requires fresh sign-in
after Kodi/service restart. Its profile files contain no credential payload or
encryption key. There is no new vault passphrase prompt or plaintext fallback.

The memory service listens on loopback with a capability stored only in Kodi
memory. A single socket mutually authenticates client/server using fresh
challenges and HMAC-SHA256 before sending any credential data. Request/response
JSON frames are size-limited and MAC-authenticated; pickle is never accepted.
Authentication and credential delivery use the same socket without reconnection.
This is local IPC, not TLS encryption; it relies on the local OS against traffic
capture by other users. A process with the same Kodi privileges is outside the
isolation boundary. Connections, memory and record counts are bounded.

Native entries are journaled before creation, read back before migration commits,
and deleted/verified on sign-out. Failed deletion retains a non-secret retry
journal and prevents another sign-in. Migration shares the account transaction,
so a stale reader cannot recreate purged credential files. Expired memory sessions
trigger full account cleanup. Imported references require fresh sign-in when their
platform/profile no longer matches. The service lease is retained until native
writes finish, preventing abandoned writes from racing a replacement service.
An unsuccessful legacy migration preserves the original instead of destroying
the only session copy; access fails and diagnostics explicitly count any remaining
legacy plaintext credential files. Sign-out can purge those originals. They are
never used as an operational plaintext fallback.

Non-Windows profile directories still use mode 0700 and private files mode 0600.
No claim is made that any storage scheme prevents access by root, an administrator,
malware or another add-on running with Kodi's privileges. Python can retain
immutable credential copies in memory while a session is active.

Account sign-out invalidates the captured account epoch and removes private
account files and runtime state. A new account cannot adopt an older invocation's
credentials. This is local sign-out; it does not revoke every Amazon device or
session belonging to the account.

Sign-in, catalogue and device services use bounded HTTPS requests and explicit
trusted hosts. TLS verification remains enabled. Cross-origin redirects cannot
replay submitted bodies or authorization headers. Provider errors and untrusted
redirects remain failures rather than silently choosing another source.

The media relay listens on 127.0.0.1 only. Each playback has a fresh random URL
capability. Host, browser Origin/Fetch headers, endpoint, method, body length and
session state are checked before an upstream request. Connections/concurrent
handlers are bounded; incomplete/oversized bodies are refused. Local capability
URLs and CDM challenges/licenses must not be logged or published.

Relay downloads resolve media hosts to public IP addresses and pin connections
to those addresses while retaining normal TLS hostname verification. Environment
proxies are disabled specifically for these public media downloads so they cannot
bypass address checks. Users who require such a proxy may be unable to use this
transport. Stream segments delivered directly by Kodi InputStream Adaptive remain
under that component's network/TLS implementation. DASH/subtitle XML disallows
DTDs/entities, including UTF-16/32 encodings, and limits size/depth/node count;
unsafe manifest URL schemes are rejected.

Diagnostics remove registered secrets and centrally redact credential fields,
authorization text, URLs, email addresses and JWTs. Reports are bounded, retain
explicit trimming markers, and expose logging failures. Do not publish a report
without reviewing it: watched-title metadata and third-party/Kodi logging may
have privacy implications beyond this add-on's own structured report.

Updates use the normal Kodi repository installer. The in-app metadata check
requires its fixed HTTPS repository address and refuses redirects/unsafe XML.
SHA-256 release inventories establish byte consistency; they are not release
signatures. Repository/GitHub account compromise remains an update trust risk.
Maintainers should protect publishing accounts and review repository access.
This change does not configure GitHub account 2FA, branch protection, package
signing, or private vulnerability reporting on the user's behalf.

Security regression checks exercise Windows DPAPI, real session-vault IPC,
secret-free references/migration, restart expiry, deletion/retry journals, rogue
server rejection, redaction, real loopback request rejection, local-address
rejection, public TLS transport and malicious XML inputs. They do not establish
native macOS/Linux vault success, Android Keystore support, Amazon login,
subscription entitlement, DRM playback or a completed independent security audit.

For a security report, provide the affected release, a minimal reproduction and
a redacted failure stage. Do not put tokens, cookies, credentials, license
payloads or an entire account profile in a public issue.

References: [Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata),
[Python URL transport](https://docs.python.org/3/library/urllib.request.html).
Native storage APIs: [Apple SecItem](https://developer.apple.com/documentation/security/secitemcopymatching(_:_:)),
[libsecret](https://gnome.pages.gitlab.gnome.org/libsecret/),
[Secret Service policy limits](https://specifications.freedesktop.org/secret-service/latest-single/#non-goals).
