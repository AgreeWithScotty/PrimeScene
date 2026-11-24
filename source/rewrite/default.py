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


def launch_cover(xbmcgui, addon_path):
    """Cover Kodi's native directory handoff with the existing application art."""
    class Cover(xbmcgui.WindowXMLDialog):
        def __init__(self, *args):
            self.cancelled = False

        def onAction(self, action):
            if action.getId() in (9, 10, 92):
                self.cancelled = True
                self.close()

    cover = Cover('PrimeSceneLaunch.xml', addon_path, 'Default', '1080i')
    cover.show()
    return cover


def main():
    incoming = list(sys.argv)
    directory_handle = None
    resolver = (len(incoming) >= 3 and own_url(incoming[0])
                and incoming[1].lstrip('-').isdigit())
    if resolver:
        uri = incoming[0]
        options = incoming[2]
        parts = urlsplit(uri)
        parameters = parse_qs(parts.query or options.lstrip('?'))
        root_request = parts.path in ('', '/') and not parameters.get('mode')
        if root_request:
            directory_handle = int(incoming[1]) if int(incoming[1]) >= 0 else None
            kind = 'native_plugin_application' if directory_handle is not None else 'legacy_application'
            sys.argv[:] = [uri, '-1', '']
        else:
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
    from time import monotonic
    from resources.lib.prime_diagnostics import DiagnosticLog, exception_details, route
    addon = xbmcaddon.Addon(ID)
    report = DiagnosticLog(xbmcvfs.translatePath(addon.getAddonInfo('profile')),
                           addon.getAddonInfo('version'), operation='launch')
    report({'event': 'launch_started', 'launch_kind': kind, 'address': route(uri),
            'resolver_handle': int(sys.argv[1]), 'current_window_id': xbmcgui.getCurrentWindowId(),
            'native_directory_handle': directory_handle,
            'kodi': xbmc.getInfoLabel('System.BuildVersion')})
    directory_released = False
    cover = None
    cover_id = None
    cover_closing = False
    interface_initialized = False

    def release_directory(succeeded):
        nonlocal directory_released
        if directory_handle is None or directory_released:
            return
        import xbmcplugin
        xbmcplugin.endOfDirectory(directory_handle, succeeded=succeeded, cacheToDisc=False)
        directory_released = True
        report({'event': 'native_directory_released', 'stage': 'plugin_window_handoff',
                'current_window_id': xbmcgui.getCurrentWindowId(),
                'interface_initialized': interface_initialized, 'directory_success': succeeded,
                'second_script_launched': False,
                'result': 'success' if succeeded else 'cancelled'})

    def launch_event(event):
        nonlocal cover, cover_closing, interface_initialized
        report(event)
        if event.get('event') == 'window_prepared' and cover is not None and not cover_closing:
            # The directory must finish before activating a non-dialog window.
            # Kodi refuses activation while its native busy dialog is modal.
            started = monotonic()
            monitor = xbmc.Monitor()
            while xbmc.getCondVisibility('Window.IsVisible(10138)'):
                if monitor.waitForAbort(.01) or monotonic() - started > 2:
                    raise RuntimeError('Kodi did not finish the native directory handoff')
            if cover.cancelled:
                raise InterruptedError('Application opening was cancelled')
            cover.close()
            cover_closing = True
            report({'event': 'launch_cover_closing', 'stage': 'plugin_window_handoff',
                    'native_busy_closed': True, 'elapsed_ms': round((monotonic() - started) * 1000),
                    'opaque_handoff_max_ms': 60000, 'fade_out': False,
                    'result': 'success'})
        elif event.get('event') == 'window_on_init':
            interface_initialized = True
        elif event.get('event') == 'interface_ready':
            if cover is not None:
                # Keep the closing cover fully opaque until the application's
                # own splash and controls are ready underneath it.
                xbmc.executebuiltin('Dialog.Close(%d,true)' % cover_id, True)
                closed = not xbmc.getCondVisibility('Window.IsVisible(%d)' % cover_id)
                report({'event': 'launch_cover_closed', 'stage': 'plugin_window_handoff',
                        'current_window_id': xbmcgui.getCurrentWindowId(),
                        'cover_closed': closed, 'application_controls_ready': True,
                        'signed_in': event.get('signed_in'),
                        'result': 'success' if closed else 'failure'})
                if not closed:
                    raise RuntimeError('The application launch overlay did not close')
                cover = None

    try:
        if root_request and directory_handle is None:
            # Old script/favourite entry points use the normal native route too,
            # so Kodi records this application's actual use for every UI launch.
            xbmc.executebuiltin('RunAddon(' + ID + ')')
            report({'event': 'legacy_launch_redirected', 'stage': 'native_application_entry',
                    'result': 'success'})
            return
        if root_request:
            cover = launch_cover(xbmcgui, addon.getAddonInfo('path'))
            cover_id = xbmcgui.getCurrentWindowDialogId()
            report({'event': 'launch_cover_opened', 'stage': 'plugin_window_handoff',
                    'dialog_id': xbmcgui.getCurrentWindowDialogId(), 'result': 'success'})
            release_directory(True)
        from resources.lib.profile_bootstrap import prepare_profile
        prepare_profile()
        if root_request:
            # The dedicated UI opens in this same invocation; there is no second
            # RunScript interpreter or unfinished native directory behind it.
            from gui import main as open_application
            if open_application(launch_diagnostics=launch_event) is False:
                report({'event': 'launch_failed', 'reason': 'interface_reported_failure'})
                return
        else:
            from resources.lib.actions import dispatch
            dispatch(sys.argv)
    except InterruptedError:
        report({'event': 'launch_cancelled', 'stage': 'plugin_window_handoff', 'result': 'cancelled'})
        xbmc.executebuiltin('ActivateWindow(Home)')
    except Exception as error:
        report({'event': 'launch_failed', 'exception': exception_details(error)})
        raise
    else:
        report({'event': 'launch_finished', 'launch_kind': kind})
    finally:
        release_directory(False)
        if cover is not None:
            xbmc.executebuiltin('Dialog.Close(%d,true)' % cover_id, True)
            report({'event': 'launch_cover_closed', 'stage': 'plugin_window_handoff',
                    'result': 'cancelled' if cover.cancelled else 'success'})


if __name__ == '__main__':
    main()
