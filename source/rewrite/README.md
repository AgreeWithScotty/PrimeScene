# Smooth Prime 0.2.8

The current complete application remains plugin.video.primeinterface, with the
0.2.7 Smooth Prime name/icon and existing player. Series/season cards open the
show episode browser and a season selector when multiple actual linked seasons
are present. Movie and individual-episode cards play their own item. No series
card auto-selects a resume/first episode. Unknown title types resolve before the
action is chosen, and an unavailable listing is reported instead of guessed.

Selected title metadata is read from the provider's selected node, keeping parent
show data separate from child episode data. Cached list packets and parsed title
nodes are reused. Show/season view state preserves Back and the original catalog
selection. Normal card/description/image prefetching remains in place.

Card imagery prefers declared landscape art. Each card has the same black canvas
and independent focus frame; portrait-only fallback images fit completely without
stretching or clipping. Hero artwork and poster fields keep their separate roles.

Real-run diagnostics record click action/type, season and episode counts, artwork
coverage, cache outcomes and existing request/parser diagnostics. No simulated
provider/Kodi tests or real-device success claims were made. The original player
and backend code/branding assets were preserved in the actual ZIP.

Rebuild using tools/package_tv_artwork_release.py and the verified published
0.2.7 ZIP. Source overlays are listed in that builder.
