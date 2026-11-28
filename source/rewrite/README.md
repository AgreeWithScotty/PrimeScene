# Smooth Prime 0.2.73 scoped Stop display continuity

- Suppress a redundant Windows Stop display reset when settled playback uses the same reported mode as the pre-play menu and the original refresh policy is on start/stop.
- Restore the original refresh policy after fullscreen closes; preserve different playback modes, other user policies and manual preference changes.
- Use an owned journal, named mutex, live heartbeat and service recovery to prevent cleanup races and recover interrupted temporary settings.
- Keep the non-account recovery journal through sign-out so account cleanup cannot strand the temporary display preference.
- Retain season-owned artwork, watched/unwatched episode states, exact played/remaining clocks, native HD and all accumulated fixes.

The full supplied 0.2.71 report confirms that the flash persists with native catalog return in 172 ms and no HDCP output-close work. The exact installed Kodi source permits a Stop resolution reset under the observed policy 2. This update temporarily uses policy 3 only for settled Windows playback in the same reported menu mode, preserving start/cadence behavior and restoring the original preference after native fullscreen closes. Actual stock Kodi settings checks passed ownership, live-lease protection, interrupted recovery, manual-change preservation and original-setting restoration, without media playback. The physical NUC flash outcome remains unverified. No Kodi/ISA/native/Google binaries or Amazon DRM requests changed. Main/testing only; no public release promotion.
