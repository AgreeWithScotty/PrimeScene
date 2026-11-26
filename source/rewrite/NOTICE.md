# Source and asset notices

Smooth Prime is maintained by AgreeWithScotty. Its catalog, account/authentication,
HTTPS transport, media-resource and license relay, subtitle handling, resume,
progress and playback lifecycle implementations are independently written for
this application. No former compatibility-provider implementation is included
or imported. Provider protocol identifiers, request fields and Kodi integration
properties describe interoperable wire/API contracts; they are not software
dependencies or claims of affiliation with Amazon.

The owned Windows CDM adapter and IPC helper are under resources/native/windows-x64/chrome-cdm.
They include Chromium and related permissive open-source components; full notices
are in that directory's THIRD_PARTY_NOTICES.txt. Corresponding adapter/helper
source and exact build-input provenance accompany this main-branch update under
source/native-chrome-cdm. Google Chrome and Google Widevine binaries are not
distributed in this add-on. On first Windows x64 HD playback, the exact pinned
regular Chrome 158.0.8127.89 payload is downloaded directly from Google and its
SHA256 is verified. Its installer and updater are never run. The private runtime
retains the original Google executables, Widevine notice and host signatures.
Google's executable terms are at https://www.google.com/chrome/terms/ and
https://policies.google.com/terms.

The unmodified 7zr.exe 26.06 archive extractor is included with its full license
notices in 7zip-LICENSE.txt and corresponding exact source in 7z2606-src.tar.xz in
the native directory. It does not require a separate installed extraction tool.

The Aurora Cinema interface is adapted from Scott's SmoothFlix:
https://github.com/AgreeWithScotty/SmoothFlix
The distributed application source is GPL-3.0-only; see LICENSE. The complete
Python/XML source is included in the installable ZIP.

Inter and Bebas Neue fonts retain their SIL Open Font License notices under
resources/skins/Default/fonts. InputStream Helper and Kodi InputStream Adaptive
are separate external dependencies maintained and licensed by their respective
projects. On Windows x64, the private runtime is provisioned automatically and used
through the owned standard CDM adapter in stock Kodi. It does not require a system
Chrome installation, register a browser updater, or modify Kodi/InputStream
Adaptive. Other supported platforms retain their normal CDM integration. No
external Python dependency implementation is bundled.

This source notice applies to the current package. Historical published releases
retain their original source, license and notices and are not rewritten.
