# Smooth Prime 0.2.25: observed browse corrections

The original 0.2.22 blue glow textures and animation style are restored, with
18% more horizontal extent. No replacement text-layer glow or bitmap edit.
Genre discovery accepts actual genre/kids links, excludes top-level Sports,
always follows the Categories navigation and merges refreshes without discarding
the verified genre list. Unchanged drawer labels are not reset while scrolling.

Card-edge Left/Right actions are noop. Kodi's native container code confirms that
explicit noop prevents both edge focus transfer and wraparound; Up retains the
current content-tab target. The real page-pagination-button rel=next was wrongly
classified as a nested carousel link. It is now retained. Saved truncated pages
invalidate with schema 5, the first two pages still preload, and subsequent pages
request twelve cards before their start, retaining selection on append.

Amazon's observed U+30FB separator maps to the U+00B7 middle dot that the interface
already displays. In Progress keeps live same-account UI objects while fetching
a new provider snapshot, then reconciles list additions/removals/order in place.
It does not reuse old server results or save progress lists. Current loaded art
is retained during native rebinding. Account invalidation clears that frame and
all transient state. Fresh rows register their exact detail URLs and artwork for
playback and full sign-out purge.

Title layout keeps the first word at the original font scale and uses half-size
lower words with a positive gap. Actual production PNG output confirms separate
ink bands; cache keys version the new layout. Source/import/layout checks, saved
real provider responses, actual renderer output, archive bytes and publication
downloads are evidence. No simulated Kodi/provider tests or device-success claim.
All 13 authentication/media/playback files remain byte-identical to 0.2.24.
