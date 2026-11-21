# Smooth Prime 0.2.12

Same permanent application. Movies opens first; Home is removed. Top selectors:
Movies, TV, Sports and Purchases, followed by Search, Settings and the account.
All SP icon and ultramarine assets from verified 0.2.11 are preserved.

Movies/TV read Amazon's published Included with Prime storefront and split its
actual typed cards. Positive Prime benefit/offer evidence is required; generic
entitlement, SVOD, ad-supported access and unknown access are not treated as
Prime. Current real anonymous response parsing identifies 40 Prime movies and
46 Prime TV titles. These counts do not prove a signed-in catalog's contents.

Sports uses the actual /sports storefront, with live event types retained.
Purchases discovers the Purchases & Rentals library through Amazon's actual
signed-in links and parses its main library collection. It does not substitute
Watchlist, recommendations or a guessed library URL. That signed-in flow has
not been observed with a live account; unknown structures fail with diagnostics.

Four account-scoped readers warm the first two actual pages. Cache schema 3
invalidates old mixed-title records. Sign-out still purges the account cache.
Card return/position/history, episode browsing, descriptions and artwork prefetch
and the existing player remain. Diagnostics record access/type coverage and the
library discovery/HTTP/parser chain. No simulated Kodi/provider tests were run.

Rebuild with tools/package_sections_release.py and the verified 0.2.11 ZIP.
