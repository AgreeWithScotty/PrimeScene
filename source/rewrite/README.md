# Prime Scene 0.2.6

This updates the existing Aurora Cinema add-on. Missing descriptions are loaded
for the current card, ten upcoming cards and two previous cards, before they
are selected. Descriptions already supplied by Amazon's catalog HTML/JSON are
merged into the matching title instead of discarded when duplicate cards occur.
Available longer descriptions replace shorter versions of the same title text.

One background metadata request runs at a time. Explicit Play jobs are checked
between requests; primary page readers and native scrolling stay independent.
Each card lookup is attempted once per window, with explicit unavailable/failure
diagnostics when Amazon returns no usable text. Saved descriptions are scoped
to the account generation, expire after 24 hours and are erased on sign-out.

Settings > Diagnostics reports catalog description coverage, read-ahead position,
cache hits/writes, real request timing and outcomes. It excludes description text,
credentials and private account identifiers. No simulated provider/Kodi tests
were run. Syntax and package checks do not establish real provider success.

Rebuild using tools/package_description_release.py and the verified 0.2.5 ZIP.
Source overlays are aurora.py, public_catalog.py and description_cache.py.
Playback selection, resolution and overlay source are preserved.
