# Smooth Prime 0.2.27: actual title previews and individual services

Movies/TV keep the full grid, with the first matching title's Amazon artwork
instead of the old premade illustrations. Preview requests use the same catalog
reader and shared cache as selection, first page only, with two preview workers.
Selecting a queued preview promotes it to the foreground pool; running requests
are shared. Images update existing tile objects without resetting grid selection.
Only nearby Extras logos warm. Artwork/state is account-epoch guarded and purged.

Extras now contains Sports plus individual services from Amazon's actual Browse
all subscriptions directory, not a generic mixed-title subscriptions feed. It
lists all available services, without checking subscription ownership (the user's
latest correction). Only actual channel/subscription catalog links qualify;
sign-up offer/checkout links are excluded. Real directory data contains 102 services
including HBO Max, Apple TV, Paramount+, MGM+, STARZ and AMC+. The selected service
opens its own provider catalog. The actual STARZ browse link returned 91 public
cards and a next-page link. This is not a signed-in access or playback claim.

Native WindowXML labels resolve skin font names. Simply editing a bundled TTF
or its unused Font.xml would not change those labels. font_coverage.py reads the
active skin's font13 TrueType/OpenType cmap and logs fontset/name/hash/coverage.
Its map reader matched the actual maps of all four bundled font files. Supported
letters remain, common punctuation normalizes, unsupported glyphs transliterate
where possible or become whitespace, and invalid/control/private-use characters
cannot render tofu. Every display path uses cleanup; source IDs/URLs stay intact.
The missing U+25BE season symbol is replaced by native geometric chevron art.
Bundled font/image bytes and the original blue glow style are unchanged.

Logs distinguish active-font failure/fallback, codepoints/replacement reasons,
directory HTTP/parser stages, preview source/index/pages and exceptions. They
exclude passwords, codes, cookies, tokens and private account identifiers.
No simulated provider/Kodi test, no remote visual-success claim. Authentication,
playback, first-two-page/early paging, title hierarchy, sign-out purge and fresh
in-place In Progress behavior remain. Package bytes and public download verified
separately during publication; previous releases remain immutable.
