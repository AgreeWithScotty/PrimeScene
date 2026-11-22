"""Initialize only this add-on profile and migrate obsolete backend artifacts."""
from pathlib import Path
from .account_runtime import AccountRuntime
from .account_state import account_epoch,persist_if_current
from .prime_diagnostics import DiagnosticLog,exception_details
from .private_storage import private_directory,read_secret

def prepare_profile():
    g=AccountRuntime();root=Path(g.DATA_PATH).resolve()
    if root.name!='plugin.video.primeinterface':raise ValueError('Invalid application profile')
    private_directory(root)
    security=DiagnosticLog(root,g.addon.getAddonInfo('version'),operation='credential_storage')
    epoch=account_epoch(root)
    def protect_saved():
        migrated=0
        for relative in ('config/accounts.lst','prime-video-session.cookies'):
            path=root/relative
            if path.exists():read_secret(path);migrated+=1
        import os
        security({'event':'credential_storage_ready','stage':'profile_protection','files_checked':migrated,
            'protection':'windows_current_user_dpapi' if os.name=='nt' else 'posix_owner_permissions',
            'result':'success'})
    try:persist_if_current(root,epoch,protect_saved)
    except Exception as error:
        security({'event':'credential_storage_failed','stage':'profile_protection','exception':exception_details(error),'result':'failure'})
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
