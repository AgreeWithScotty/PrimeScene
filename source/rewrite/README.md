# Smooth Prime 0.2.38 app-wide security hardening

One shared verified HTTPS policy covers every outbound Python opener, with TLS1.2+, no traffic-key debug files and no inherited environment proxy. Non-Windows credential exchange uses owner-only filesystem IPC with mutual HMAC authentication; no credential TCP listener/fallback remains. Invalid/unprotectable legacy plaintext is removed or refused with an explicit cleanup failure. Private file identity/ownership/link checks, a public-header allowlist and fail-safe normalized log redaction close further app-level gaps.

Actual Windows DPAPI, filesystem lock/link, public/local TLS certificate rejection, redaction, loopback HTTP and XML checks establish their observed boundaries. New Unix IPC/native macOS/Linux vault behavior has not been exercised on a POSIX host here. No Amazon/Kodi success was simulated. UI/art/fonts/navigation/provider choices remain. Restart Kodi after updating. Read SECURITY.md.
