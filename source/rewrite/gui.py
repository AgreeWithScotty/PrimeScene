"""Separate script entry for the Prime Scene window."""
import sys
import xbmc
import xbmcgui

ID = 'plugin.video.primeinterface'


def main():
    # Provide the original backend with our native plugin URI for playback links.
    sys.argv[:] = ['plugin://' + ID + '/', '-1', '']
    from resources.lib.common import Globals, Settings
    from resources.lib.users import loadUser, loadUsers
    from resources.lib.dedicated_ui import open_interface
    from resources.lib.interface_diagnostics import record
    g, settings = Globals(), Settings()
    record(g, 'gui_script_started')
    def initialize_provider():
        users = loadUsers()
        if not users:
            return False
        g.InitialiseProvider(*(loadUser(key, cachedUsers=users) for key in
                               ('mid', 'baseurl', 'atvurl', 'pv', 'deviceid')))

    def account_name():
        users = loadUsers()
        return loadUser('name', cachedUsers=users) if users else ''

    def account_action(before_sign_out):
        from resources.lib.prime_login import LogIn
        from resources.lib.account_state import sign_out
        name = account_name()
        if name:
            if g.dialog.select(name, ['Sign out', 'Cancel']) == 0:
                before_sign_out()
                sign_out(g)
                return True
            return False
        else:
            return LogIn() is not False

    try:
        result = open_interface(g, settings, initialize_provider=initialize_provider,
                                account_name=account_name, account_action=account_action)
        if result == 'classic':
            xbmc.executebuiltin('ActivateWindow(Videos,"plugin://' + ID + '/?mode=ClassicUI",return)')
    except Exception as error:
        record(g, 'window_open_failed', error)
        xbmcgui.Dialog().ok('Prime Scene', 'The interface could not open (' +
                           type(error).__name__ + '). Your sign-in is saved.')


if __name__ == '__main__':
    main()
