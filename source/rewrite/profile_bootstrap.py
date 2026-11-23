"""Initialize only this add-on profile and migrate obsolete backend artifacts."""
from pathlib import Path
from .account_runtime import AccountRuntime
from .account_state import account_epoch,persist_if_current
from .prime_diagnostics import DiagnosticLog,exception_details
from .private_storage import private_directory,read_secret,storage_status,legacy_credentials_remaining
from .vault_errors import VaultMissing

def prepare_profile():
    g=AccountRuntime();root=Path(g.DATA_PATH).resolve()
    if root.name!='plugin.video.primeinterface':raise ValueError('Invalid application profile')
    private_directory(root)
    security=DiagnosticLog(root,g.addon.getAddonInfo('version'),operation='credential_storage')
    from .credential_runtime import ensure_service
    try:ensure_service(g)
    except Exception as error:
        security({'event':'credential_service_unavailable','stage':'vault_startup',
            'legacy_plaintext_files_remaining':legacy_credentials_remaining(root),
            'exception':exception_details(error),'result':'failure'})
        raise
    epoch=account_epoch(root)
    def protect_saved():
        migrated=0
        for relative in ('config/accounts.lst','prime-video-session.cookies'):
            path=root/relative
            if path.exists():read_secret(path);migrated+=1
        security({'event':'credential_storage_ready','stage':'profile_protection','files_checked':migrated,
            **storage_status(root),
            'result':'success'})
    try:persist_if_current(root,epoch,protect_saved)
    except VaultMissing as error:
        # A prior memory session has ended (or an OS item was removed). Purge
        # its complete account state before showing a new sign-in screen.
        from .account_state import sign_out
        security({'event':'credential_session_expired','stage':'profile_protection','reason':error.reason,
            'legacy_plaintext_files_remaining':legacy_credentials_remaining(root),'result':'expired'})
        sign_out(g)
        # Sign-out removes the former profile report. Preserve a complete,
        # accessible account-neutral explanation in the newly clean profile.
        previous_attempt=security.attempt
        security=DiagnosticLog(root,g.addon.getAddonInfo('version'),operation='credential_storage')
        security({'event':'credential_session_reset','stage':'profile_protection','reason':error.reason,
            'exception':exception_details(error),'previous_attempt':previous_attempt,
            'legacy_plaintext_files_remaining':legacy_credentials_remaining(root),
            'account_cleanup_completed':True,'result':'new_signin_required'})
        epoch=account_epoch(root)
    except Exception as error:
        security({'event':'credential_storage_failed','stage':'profile_protection',
            'legacy_plaintext_files_remaining':legacy_credentials_remaining(root),
            'exception':exception_details(error),'result':'failure'})
        raise
    marker=root/'native-backend-schema'
    if marker.is_file() and marker.read_text(encoding='ascii')=='1':return
    report=DiagnosticLog(root,g.addon.getAddonInfo('version'),operation='backend_migration')
    def migrate():
        removed=0
        for relative in ('resume.db','config/proxyaddress','config/proxyaddress.lock'):
            target=(root/relative).resolve()
            if root not in target.parents:raise ValueError('Migration escaped application profile')
            if target.is_file():target.unlink();removed+=1
        marker.write_text('1',encoding='ascii')
        report({'event':'native_backend_migrated','stage':'profile_migration','removed_files':removed,'account_preserved':True,'result':'success'})
    try:persist_if_current(root,epoch,migrate)
    except Exception as error:
        report({'event':'native_backend_migration_failed','stage':'profile_migration','exception':exception_details(error),'result':'failure'})
        raise
