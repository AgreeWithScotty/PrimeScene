# Prime Scene replacement components

Prime Scene 0.1.25 updates the existing plugin.video.primeinterface application. The bottom sidebar account button shows Sign in or the signed-in account name and a Sign out option. Signing out invalidates the account generation, clears local account files, cookies, caches, history and account artwork, and blocks stale writes. Anonymous playback-service infrastructure contains no Amazon account data.

Native authorization callbacks are consumed directly rather than requested as HTML pages. HTTP failures report their status and stage; usable verification forms returned with an error allow code correction.

Validation: 48 automated tests, packaged startup checks and progressive catalog regressions passed. The supplied diagnostic report shows the prior attempt reached verification and then redirects, but its old format does not include the failing HTTP status. Full live-account 2FA success is not yet verified. Other inherited backend components remain present.

The complete application source and license are in the release ZIP. No separate application or preview track is distributed.
