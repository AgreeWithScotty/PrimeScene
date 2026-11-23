"""Dedicated native application and independent account controls."""
from resources.lib.app_runtime import AppRuntime,Preferences
from resources.lib.accounts import loadUser,loadUsers
from resources.lib.account_state import is_signed_out,sign_out,needs_cleanup
from resources.lib.aurora import open_interface
from resources.lib.interface_diagnostics import record

def main(launch_diagnostics=None):
    g=AppRuntime()
    storage_warning=[]
    def account_name():
        from resources.lib.vault_errors import VaultError
        try:users=loadUsers()
        except VaultError as error:
            if not storage_warning:
                storage_warning.append(True);record(g,'credential_storage_unavailable',error)
                g.dialog.ok('Smooth Prime','Secure credential storage is unavailable. Unlock your system keyring or use Finish sign out, then retry. Open Diagnostics for details.')
            return ''
        return loadUser('name',cachedUsers=users) if users else ''
    def account_action(before_sign_out):
        from resources.lib.prime_login import LogIn
        if needs_cleanup(g.DATA_PATH):
            sign_out(g,before_cleanup=before_sign_out);return True
        name=account_name()
        if name:
            sign_out(g,before_cleanup=before_sign_out)
            return True
        return LogIn() is not False
    record(g,'gui_script_started')
    return open_interface(g,Preferences(g.addon),account_name=account_name,account_action=account_action,
        offer_signin=not account_name() and not is_signed_out(g.DATA_PATH),launch_diagnostics=launch_diagnostics)

if __name__=='__main__':main()
