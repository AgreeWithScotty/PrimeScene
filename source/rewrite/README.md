# Smooth Prime 0.2.16

The existing WindowXML survives playback in Python while Kodi may free/recreate
its native resources. The previous ready guard skipped native onInit work,
leaving view 200 unselected/unbound and the window blank after video. Restore
the native view and rebind the retained card objects on each reactivation;
select/focus the originating card after the actual initialization callback.
Preserve the existing window's Kodi exit target, with no new catalog fetch.

Report actual native/logical card counts, view/focus/index and window IDs,
worker state, stage timings and exception frames. A correct window ID alone
no longer establishes successful return. All playback/resolver/overlay, sign-out,
episode, preloading and SMOOTH artwork paths are preserved. Source/artifact
checks do not establish a Kodi-device return success. No mocked tests were run.

Home/Continue Watching is a user design proposal, not a shipped feature in this
repair. Amazon supports per-profile watch progress and Continue Watching;
signed-in progress reading/availability must be established before claiming
cross-device history support.

Rebuild with tools/package_reactivation_release.py and the verified 0.2.15 ZIP.
