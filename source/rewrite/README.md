# Smooth Prime native backend: 0.2.23

CatalogGateway directly reads the native public_catalog/detail_catalog/tv_catalog
page state. No compatibility provider, renderer interception, shared provider
singleton, browser playback, external-app player or old backend import remains.
media_resources owns the actual selected-title Amazon resource/progress protocol;
http_transport owns bounded HTTPS/redirect/error handling; media_proxy owns a
playback-scoped loopback CDM license relay and native DASH/TTML handling;
playback_engine owns native Kodi/InputStream Adaptive lifecycle and skip UI;
playback_progress owns epoch-scoped SQLite resume. Authentication remains native.
No license decryption, key extraction or alternate content-source substitution.

All retired implementation modules and obsolete settings/assets are absent from
the current app/ZIP. Their canonical source mirror and obsolete preview/mock
scripts are removed. Historical release ZIPs and their original license notices
remain immutable. Current backend code is independently written; see NOTICE.md
for the Scott-owned Aurora interface and retained external font/dependency licenses.

Concrete evidence: Python/XML syntax and relative import/export closure; no old
backend import path or substantial exact function retained from retired derived
modules; all 0.2.22 skin/menu-glow bytes preserved; three saved real Amazon TV
responses parsed into 36 episodes; one fresh anonymous movie detail HTTP 200
parsed into a playable action; actual SQLite resume/completion/epoch guard checks;
ZIP content/CRC/hash and publication-download verification. Anonymous search
returned HTTP 503; signed-in catalog, CDM license and playback are unconfirmed.
No mocked provider/Kodi tests. Runtime diagnostics capture real failures completely
without passwords, codes, tokens, cookie values or private account identifiers.

The full installable ZIP is the authoritative complete source. Files in this flat
mirror map to resources/lib; default.py/gui.py/service.py/view_diagnostics.py are
app-root entries, and settings.xml/strings.po are application resource files.
