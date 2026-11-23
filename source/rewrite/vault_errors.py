"""Safe credential-storage errors; never include native messages or secret values."""
class VaultError(RuntimeError):
    def __init__(self,reason='unavailable',status=None):
        self.reason,self.status=reason,status
        super().__init__('Secure credential storage could not finish ('+reason+').')

class VaultUnavailable(VaultError):pass
class VaultMissing(VaultError):pass
class VaultCleanupRequired(VaultError):pass


def storage_message(error):
    reason=getattr(error,'reason','')
    if reason.startswith(('private_credential_','credential_socket_')) or reason=='credential_service_not_running':
        return ('The secure sign-in service could not start. Restart Kodi and reopen Smooth Prime. '
                'If it persists, update Kodi and open Diagnostics for the failed stage.')
    if isinstance(error,VaultCleanupRequired):
        return ('Saved sign-in data could not be fully cleared. Use Finish sign out before signing in again. '
                'Close anything holding the profile files open; Diagnostics identifies the failed cleanup stage.')
    if reason=='legacy_credential_protection_failed_new_signin':
        return 'The saved sign-in could not be protected and must be verified again. Use Sign in. Open Diagnostics for details.'
    return ('Secure credential storage could not finish. Unlock your system keyring if it is locked, '
            'then retry. Use Finish sign out if cleanup is pending. Diagnostics contains the failed stage.')
