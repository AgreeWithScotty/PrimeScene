"""Check Prime Scene's release and let Kodi handle repository installs."""
import re
import time
from urllib.request import HTTPRedirectHandler,Request
from secrets import token_hex
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from resources.lib.prime_diagnostics import DiagnosticLog,exception_details,route
from resources.lib.tls_policy import secure_opener


ADDON_ID = 'plugin.video.primeinterface'
REPO_ID = 'repository.primescene'
INDEX = 'https://raw.githubusercontent.com/AgreeWithScotty/PrimeScene/main/addons.xml'
HEADING = 'Smooth Prime updates'

class RepositoryRedirect(HTTPRedirectHandler):
    def __init__(self,report,request_id):self.report,self.request_id=report,request_id
    def redirect_request(self,request,response,status,message,headers,new_url):
        self.report({'event':'update_redirect_refused','stage':'repository_metadata','request_id':self.request_id,
            'status':status,'from':route(request.full_url),'to':route(new_url),'result':'refused'})
        raise ValueError('The update index redirected away from its configured address')


def version_key(version):
    if not version or not re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,6}){2}', version):
        raise ValueError('Invalid Prime Scene version')
    return tuple(map(int, version.split('.')))


def published_version(report):
    request_id=token_hex(8);started=time.monotonic()
    request = Request(INDEX, headers={'Cache-Control': 'no-cache'})
    report({'event':'update_index_started','stage':'repository_metadata','request_id':request_id,'address':route(INDEX),'result':'started'})
    with secure_opener(RepositoryRedirect(report,request_id)).open(request, timeout=8) as response:
        data = response.read(128 * 1024 + 1)
        status=response.status
    if len(data) > 128 * 1024:
        raise ValueError('Prime Scene repository index is too large')
    if b'<!DOCTYPE' in data.replace(b'\x00',b'').upper() or b'<!ENTITY' in data.replace(b'\x00',b'').upper():
        raise ValueError('Unsafe repository XML')
    index = ET.fromstring(data)
    versions = [node.get('version') for node in index.findall('addon')
                if node.get('id') == ADDON_ID]
    if index.tag != 'addons' or len(versions) != 1:
        raise ValueError('Prime Scene is missing from the repository index')
    version_key(versions[0])
    report({'event':'update_index_verified','stage':'repository_metadata','request_id':request_id,'status':status,
        'bytes':len(data),'elapsed_ms':round((time.monotonic()-started)*1000),'result':'success'})
    return versions[0]


def installed_version():
    return xbmcaddon.Addon(ADDON_ID).getAddonInfo('version')


def main():
    dialog = xbmcgui.Dialog()
    addon=xbmcaddon.Addon(ADDON_ID)
    report=DiagnosticLog(xbmcvfs.translatePath(addon.getAddonInfo('profile')),addon.getAddonInfo('version'),operation='update')
    try:
        xbmcaddon.Addon(REPO_ID)
    except RuntimeError:
        dialog.ok(HEADING, 'Install or enable the Prime Scene Repository in Kodi to receive updates.')
        return

    progress = xbmcgui.DialogProgress()
    progress.create(HEADING, 'Checking for a newer version…')
    result = None
    outcome='complete'
    try:
        installed = installed_version()
        latest = published_version(report)
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
        outcome='failure'
        report({'event':'update_check_failed','stage':'update','exception':exception_details(error),
            'status':getattr(error,'code',None),'result':'failure'})
        xbmc.log('Prime Scene update check: {}'.format(type(error).__name__), xbmc.LOGWARNING)
        result = 'Could not verify the Smooth Prime update. Check your connection and try again.'
    finally:
        if progress.iscanceled():outcome='cancelled'
        progress.close()
        report({'event':'update_check_finished','stage':'update',
            'result':outcome})
    if result:
        dialog.ok(HEADING, result)


if __name__=='__main__':
    main()
