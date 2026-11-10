# Prime Scene for Amazon Prime Video

Standalone Kodi video add-on with progressive browsing and its own account,
catalog, playback and proxy code. Not affiliated with Amazon.

## Install and update

Install the Prime Scene Repository ZIP from `packages/repository.primescene/`
once through Kodi's **Add-ons -> Install from ZIP file**. Open **Install from
repository -> Prime Scene Repository -> Video add-ons -> Prime Scene**.
Existing Prime Scene users can update without uninstalling or clearing data.

Later releases appear in Kodi's update manager. In Prime Scene use **Settings ->
Updates -> Check for updates** to refresh repositories and open available updates.
Kodi can also install updates automatically if that option is enabled.

## Identity and source

Add-on ID: `plugin.video.primeinterface` (kept unchanged across versions).
Repository ID: `repository.primescene`.

Full Python/XML source and the GPL license are included in each release ZIP.
The backend is derived from Sandmann79/xbmc, credited in the included NOTICE.md.
No installed Amazon VOD add-on is required or queried. No account data is bundled.

This is a preview: actual interface rendering and playback still need device
testing. Repository index and package files are kept in the same Git commit.
