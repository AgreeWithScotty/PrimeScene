"""Direct application entry with compatibility for existing playback URLs."""
import sys
from urllib.parse import parse_qs, urlsplit

ID = 'plugin.video.primeinterface'
ROOT_URL = 'plugin://' + ID + '/'


def own_url(value):
    try:
        parts = urlsplit(value)
        return parts.scheme == 'plugin' and parts.netloc == ID
    except ValueError:
        return False


def main():
    incoming = list(sys.argv)
    resolver = (len(incoming) >= 3 and own_url(incoming[0])
                and incoming[1].lstrip('-').isdigit())
    if resolver:
        uri = incoming[0]
        options = incoming[2]
        parts = urlsplit(uri)
        parameters = parse_qs(parts.query or options.lstrip('?'))
        root_request = parts.path in ('', '/') and not parameters.get('mode')
        if root_request:
            # A saved old plugin:// Home shortcut has already asked Kodi to open
            # Videos. Cancel that directory rather than returning an empty one.
            import xbmc
            import xbmcaddon
            import xbmcgui
            import xbmcplugin
            import xbmcvfs
            previous_window = xbmcgui.getCurrentWindowId()
            xbmcplugin.endOfDirectory(int(incoming[1]), succeeded=False, cacheToDisc=False)
            xbmc.executebuiltin('RunScript(' + ID + ')')
            from resources.lib.prime_diagnostics import DiagnosticLog
            addon = xbmcaddon.Addon(ID)
            report = DiagnosticLog(xbmcvfs.translatePath(addon.getAddonInfo('profile')),
                                   addon.getAddonInfo('version'), operation='launch')
            report({'event': 'plugin_shortcut_redirected', 'launch_kind': 'plugin_shortcut',
                    'current_window_id': previous_window, 'resolver_handle': int(incoming[1]),
                    'empty_directory_returned': False, 'redirected_to_direct_script': True})
            return
        kind = 'playback_action'
        sys.argv[:] = incoming
    else:
        uri = next((value for value in incoming[1:] if own_url(value)), ROOT_URL)
        parts = urlsplit(uri)
        root_request = parts.path in ('', '/') and not parse_qs(parts.query).get('mode')
        sys.argv[:] = [uri, '-1', '?' + parts.query if parts.query else '']
        kind = 'direct_application' if root_request else 'script_action'

    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcvfs
    from resources.lib.prime_diagnostics import DiagnosticLog, exception_details, route
    addon = xbmcaddon.Addon(ID)
    report = DiagnosticLog(xbmcvfs.translatePath(addon.getAddonInfo('profile')),
                           addon.getAddonInfo('version'), operation='launch')
    report({'event': 'launch_started', 'launch_kind': kind, 'address': route(uri),
            'resolver_handle': int(sys.argv[1]), 'current_window_id': xbmcgui.getCurrentWindowId(),
            'kodi': xbmc.getInfoLabel('System.BuildVersion')})
    try:
        from resources.lib.profile_bootstrap import prepare_profile
        prepare_profile()
        if root_request:
            # No plugin-directory request, startup.py import, or queued second
            # script sits between Kodi's app tile and the dedicated window.
            from gui import main as open_application
            if open_application(launch_diagnostics=report) is False:
                report({'event': 'launch_failed', 'reason': 'interface_reported_failure'})
                return
        else:
            from resources.lib.actions import dispatch
            dispatch(sys.argv)
    except Exception as error:
        report({'event': 'launch_failed', 'exception': exception_details(error)})
        raise
    else:
        report({'event': 'launch_finished', 'launch_kind': kind})


if __name__ == '__main__':
    main()
