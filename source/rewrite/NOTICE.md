# Attribution and source

Smooth Prime is maintained by AgreeWithScotty. It is an independent fork and
is not an Amazon product. Smooth Prime modifications were made in November 2026.
Remaining catalog compatibility, network and playback code is derived from Sandmann79/xbmc,
Amazon VOD1.2.4+matrix.1, by Sandmann79, Varstahl and its other contributors:
https://github.com/Sandmann79/xbmc

Their GPL-3.0-or-later license and existing notices are retained. The complete
Python source and XML interface are included in this installable ZIP. Local
changes add a dedicated progressive interface, bounded category enrichment,
request/registration repairs, an isolated identity/profile/service namespace,
and its own first-launch sign-in flow. The original installed add-on's code is
never imported. Existing external Kodi Python modules remain dependencies.

In version 0.2.20 the retained login.py and users.py implementations were
removed. Native authentication, account storage, region discovery, credential
selection, token renewal and security-challenge handling now use independently
written Smooth Prime modules. These no longer import the former login module
or its MFA/CAPTCHA UI. The remaining backend components above have not yet all
been replaced; this release is not represented as a fully original backend.

Upstream dependency version attributes are compatibility minimums, not pins to
old dependency releases. Kodi resolves installed/current repository dependencies.
No old dependency package or Widevine binary is bundled.

Aurora Cinema, the self-contained WindowXML layout used for the home, title
and player screens, is adapted from SmoothFlix 0.3.0 by Scott:
https://github.com/AgreeWithScotty/SmoothFlix
Its interface code is GPL-3.0-only. Inter and Bebas Neue are included under
the SIL Open Font License; see resources/skins/Default/fonts/.
