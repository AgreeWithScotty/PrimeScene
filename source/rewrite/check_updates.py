"""Check Prime Scene's release and let Kodi handle repository installs."""
import re
import time
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui


ADDON_ID = 'plugin.video.primeinterface'
REPO_ID = 'repository.primescene'
INDEX = 'https://raw.githubusercontent.com/AgreeWithScotty/PrimeScene/main/addons.xml'
HEADING = 'Smooth Prime updates'


def version_key(version):
    if not version or not re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,6}){2}', version):
        raise ValueError('Invalid Prime Scene version')
    return tuple(map(int, version.split('.')))


def published_version():
    request = Request(INDEX, headers={'Cache-Control': 'no-cache'})
    with urlopen(request, timeout=8) as response:
        data = response.read(128 * 1024 + 1)
    if len(data) > 128 * 1024:
        raise ValueError('Prime Scene repository index is too large')
    index = ET.fromstring(data)
    versions = [node.get('version') for node in index.findall('addon')
                if node.get('id') == ADDON_ID]
    if index.tag != 'addons' or len(versions) != 1:
        raise ValueError('Prime Scene is missing from the repository index')
    version_key(versions[0])
    return versions[0]


def installed_version():
    return xbmcaddon.Addon(ADDON_ID).getAddonInfo('version')


def main():
    dialog = xbmcgui.Dialog()
    try:
        xbmcaddon.Addon(REPO_ID)
    except RuntimeError:
        dialog.ok(HEADING, 'Install or enable the Prime Scene Repository in Kodi to receive updates.')
        return

    progress = xbmcgui.DialogProgress()
    progress.create(HEADING, 'Checking for a newer version…')
    result = None
    try:
        installed = installed_version()
        latest = published_version()
        target = version_key(latest)
        if version_key(installed) >= target:
            result = 'Smooth Prime {} is up to date.'.format(installed)
        elif not progress.iscanceled():
            progress.update(0, 'Version {} is available. Waiting for Kodi…'.format(latest))
            xbmc.executebuiltin('UpdateAddonRepos', True)
            monitor = xbmc.Monitor()
            deadline = time.monotonic() + 25
            while not progress.iscanceled() and not monitor.abortRequested():
                try:
                    current = installed_version()
                except RuntimeError:
                    current = None  # Kodi can temporarily unload the add-on during installation.
                if current and version_key(current) >= target:
                    result = 'Smooth Prime updated to {}. Reopen Smooth Prime to use the new version.'.format(current)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Kodi may still be downloading. Do not label this a failed install.
                    result = ('Version {} is available. Kodi has not confirmed installation yet. '
                              'If automatic updates are off, install it from Kodi’s Available updates.').format(latest)
                    break
                if monitor.waitForAbort(min(0.5, remaining)):
                    break
    except (OSError, ValueError, ET.ParseError, RuntimeError) as error:
        xbmc.log('Prime Scene update check: {}'.format(type(error).__name__), xbmc.LOGWARNING)
        result = 'Could not verify the Smooth Prime update. Check your connection and try again.'
    finally:
        progress.close()
    if result:
        dialog.ok(HEADING, result)


if __name__=='__main__':
    main()
