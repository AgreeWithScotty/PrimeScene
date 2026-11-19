# Prime Scene replacement components

Prime Scene 0.1.26 adds real-run diagnostics to the existing plugin.video.primeinterface application. Every sign-in attempt records its release/environment, correlated attempt and sequence, stage and timing, redacted routes and redirect chain, HTTP statuses and server request IDs, form/hidden-field structure, cookie metadata without values, authorization result and redacted server errors, and exception locations. Credentials and tokens are excluded. Account sign-out erases the previous account reports along with its data.

The report can be opened in Kodi through Settings > Diagnostics > View diagnostic report. The complete file is prime-scene-diagnostics.jsonl in this add-on's own userdata profile. The older prime-scene-signin.jsonl is retained for compatibility.

No simulated sign-in tests were run for this logging release. Syntax/package construction and published artifacts can be checked directly. Full live Amazon login/2FA success remains unverified; diagnosis must use the actual run. Other inherited backend components remain present. No separate add-on or preview is distributed.
