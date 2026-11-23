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

On non-Windows systems, profile directories use mode 0700 and secret files mode
0600. These are filesystem access controls, not encrypted storage. No claim is
made that application storage prevents access by root, an administrator, malware,
or another program/add-on running as the same OS user. Python can also retain
immutable credential copies in process memory while a session is active.

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

Security regression checks exercise Windows DPAPI, filesystem migration,
redaction, real loopback request rejection, local-address rejection, public TLS
transport and malicious XML inputs. They do not establish Amazon login,
subscription entitlement, DRM playback or a completed independent security audit.

For a security report, provide the affected release, a minimal reproduction and
a redacted failure stage. Do not put tokens, cookies, credentials, license
payloads or an entire account profile in a public issue.

References: [Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata),
[Python URL transport](https://docs.python.org/3/library/urllib.request.html).
