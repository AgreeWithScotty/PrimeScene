"""Dedicated native application and independent account controls."""
from resources.lib.app_runtime import AppRuntime,Preferences
from resources.lib.accounts import loadUser,loadUsers
from resources.lib.account_state import is_signed_out,sign_out,needs_cleanup
from resources.lib.aurora import open_interface
from resources.lib.interface_diagnostics import record

def main(launch_diagnostics=None):
    g=AppRuntime()
    def account_name():
        users=loadUsers()
        return loadUser('name',cachedUsers=users) if users else ''
    def account_action(before_sign_out):
        from resources.lib.prime_login import LogIn
        if needs_cleanup(g.DATA_PATH):
            sign_out(g,before_cleanup=before_sign_out);return True
        name=account_name()
        if name:
            choice=g.dialog.select(name,['Sign out','Sign in again','Cancel'])
            if choice not in (0,1):return False
            sign_out(g,before_cleanup=before_sign_out)
            if choice==0:return True
        return LogIn() is not False
    record(g,'gui_script_started')
    return open_interface(g,Preferences(g.addon),account_name=account_name,account_action=account_action,
        offer_signin=not loadUsers() and not is_signed_out(g.DATA_PATH),launch_diagnostics=launch_diagnostics)

if __name__=='__main__':main()
