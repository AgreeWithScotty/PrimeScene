"""Separate script entry for the Prime Scene window."""
import sys
from traceback import format_exc
import xbmc
import xbmcgui

ID = 'plugin.video.primeinterface'


def main(launch_diagnostics=None):
    # Provide the original backend with our native plugin URI for playback links.
    sys.argv[:] = ['plugin://' + ID + '/', '-1', '']
    from resources.lib.common import Globals, Settings
    from resources.lib.accounts import loadUser, loadUsers
    from resources.lib.aurora import failure_detail, open_interface
    from resources.lib.interface_diagnostics import record
    g, settings = Globals(), Settings()
    record(g, 'gui_script_started')
    from resources.lib.account_state import is_signed_out
    offer_signin = not loadUsers() and not is_signed_out(g.DATA_PATH)
    def initialize_provider():
        users = loadUsers()
        if not users:
            return False
        from resources.lib.configs import getConfig
        if not getConfig('UserAgent'):
            from resources.lib.network import getUA
            getUA()
        g.InitialiseProvider(*(loadUser(key, cachedUsers=users) for key in
                               ('mid', 'baseurl', 'atvurl', 'pv', 'deviceid')))

    def account_name():
        users = loadUsers()
        return loadUser('name', cachedUsers=users) if users else ''

    def account_action(before_sign_out):
        from resources.lib.prime_login import LogIn
        from resources.lib.account_state import sign_out, needs_cleanup
        if needs_cleanup(g.DATA_PATH):
            sign_out(g, before_cleanup=before_sign_out)
            return True
        name = account_name()
        if name:
            choice = g.dialog.select(name, ['Sign out', 'Sign in again', 'Cancel'])
            if choice == 0:
                sign_out(g, before_cleanup=before_sign_out)
                return True
            if choice != 1:
                return False
            sign_out(g, before_cleanup=before_sign_out)
        from resources.lib.configs import getConfig
        if not getConfig('UserAgent'):
            from resources.lib.network import getUA
            getUA()
        return LogIn() is not False

    try:
        result = open_interface(g, settings, initialize_provider=initialize_provider,
                                account_name=account_name, account_action=account_action,
                                offer_signin=offer_signin, launch_diagnostics=launch_diagnostics)
        if result is False:
            return False
    except Exception as error:
        record(g, 'window_open_failed', error)
        xbmc.log('[Prime Scene] The interface could not open\n' + format_exc(), xbmc.LOGERROR)
        if launch_diagnostics is not None:
            from resources.lib.prime_diagnostics import exception_details
            launch_diagnostics({'event': 'window_open_failed', 'exception': exception_details(error)})
        xbmcgui.Dialog().ok('Smooth Prime', 'The interface could not open (' +
                           failure_detail(error) + '). Your sign-in is saved.')
        return False


if __name__ == '__main__':
    main()
