# Smooth Prime 0.2.9

Same permanent application, based on the checksum-verified 0.2.8 ZIP. Preserve
Smooth Prime branding, landscape cards, TV episode/season browsing, description
and artwork read-ahead and all player/resolver backend bytes.

The playback watcher correlates requests across the UI and resolver, retains
the pending note before video starts, observes playback termination and queues
Home restoration only after the overlay closes. The original window is activated
by its registered ID, preserving its Kodi exit target. Exiting or changing the
account invalidates the return target; new playback cancels stale commands.
Primary Home/Movies/TV views are retained in memory during detail-view eviction.

Navigation keeps existing cards/hero until replacement cards arrive and does
not display loading text or a title-resolution busy dialog. Duplicate primary
navigation cards and section headings are removed; top selectors remain.
Genuine errors retain actionable reports. Real-run diagnostics include correlated
request IDs, terminal reasons, actual window IDs, timing and explicit outcomes.
No simulated tests or device-return success claims were made.

Rebuild using tools/package_return_release.py with the published 0.2.8 ZIP.
