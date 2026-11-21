# Smooth Prime 0.2.18

Built on verified 0.2.17, retaining stock Kodi playback controls and branding.
Home opens first and reads only explicitly labelled Amazon Continue Watching
collections, never a general recommendation catalog. Empty history differs from
unrecognized protocol. Actual account history retrieval is not yet observed.

The service samples actual player position/duration and saves unfinished movies
and active TV contexts. End-of-playback marks completion. Home show browsing
hides completed episodes and follows observed next-season routes when needed.
Local writes are account-generation guarded, partitioned by website profile
context, and erased by the full sign-out purge. Completion is taken from explicit
Amazon watched flags/100 percent progress or an actual playback-ended event.
No guessed provider endpoints or mocked provider/Kodi tests are used.

Reports include version/environment, request/attempt IDs, real HTTP routes/status,
parser structure and collection counts, progress timing/outcomes, episode filter
counts and exception locations. Secrets and account identifiers are excluded.
No live signed-in history, cross-device sync or Kodi UI success is claimed.
