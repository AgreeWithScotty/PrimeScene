"""Safe credential-storage errors; never include native messages or secret values."""
class VaultError(RuntimeError):
    def __init__(self,reason='unavailable',status=None):
        self.reason,self.status=reason,status
        super().__init__('Secure credential storage could not finish ('+reason+').')

class VaultUnavailable(VaultError):pass
class VaultMissing(VaultError):pass
class VaultCleanupRequired(VaultError):pass
