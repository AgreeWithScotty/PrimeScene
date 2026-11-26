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
source/native-chrome-cdm. Google Chrome and Google Widevine are not bundled.

The Aurora Cinema interface is adapted from Scott's SmoothFlix:
https://github.com/AgreeWithScotty/SmoothFlix
The distributed application source is GPL-3.0-only; see LICENSE. The complete
Python/XML source is included in the installable ZIP.

Inter and Bebas Neue fonts retain their SIL Open Font License notices under
resources/skins/Default/fonts. InputStream Helper and Kodi InputStream Adaptive
are separate external dependencies maintained and licensed by their respective
projects. Widevine is provided through Kodi's supported platform/CDM mechanism;
no Widevine binary or external Python dependency implementation is bundled.

This source notice applies to the current package. Historical published releases
retain their original source, license and notices and are not rewritten.
