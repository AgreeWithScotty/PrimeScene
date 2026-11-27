# Smooth Prime 0.2.66 persistent runtime

- Keep pinned Chrome and its integrity receipt in Kodi persistent add-on data so add-on updates reuse the runtime.
- Reuse a matching existing download when available; otherwise automatically download once into persistent storage without a separate browser installation or manual migration step.
- Share the persistent runtime across immutable owned adapter versions and recover prior scoped CDM paths safely.
- Keep the catalog visible while in-add-on playback prepares, then request fullscreen when the owned native player reports audio/video ready.
- Reduce local proxy shutdown polling and record slow audio, decoder, video-frame and protection/adapter shutdown stages.
- Preserve native stock-Kodi HD, genuine output protection and all accumulated regional, sign-in, Watchlist, Back, resume, artwork and title fixes.

The existing genuine runtime was verified/migrated and reused with zero downloads after the legacy location was unavailable. The genuine Chrome CDM service broker connected from the new persistent layout. The new fullscreen behavior and remaining start/stop latency await NUC playback testing; no new playback test was run for this publication. An optional preservation script can avoid the final transition download, but installation and first use require no manual migration step. Main/testing only; no public release promotion.
