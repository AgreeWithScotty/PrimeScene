"""Explicit native actions; no provider routing or dynamic evaluation."""
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
import xbmc
from .app_runtime import AppRuntime
from .prime_diagnostics import DiagnosticLog,exception_details

def dispatch(argv):
    parts=urlsplit(argv[0]);arguments=parse_qs(parts.query or (argv[2] if len(argv)>2 else '').lstrip('?'))
    value=lambda name,default='':arguments.get(name,[default])[0]
    mode=value('mode');g=AppRuntime()
    report=DiagnosticLog(g.DATA_PATH,g.addon.getAddonInfo('version'),operation='action')
    report({'event':'action_started','stage':'dispatch','action':mode,'result':'started'})
    playback_outcome={}
    try:
        if mode=='PlayVideo':
            from .account_state import current_account
            if value('account_epoch') and not current_account(g.DATA_PATH,value('account_epoch')):
                report({'event':'action_finished','stage':'account_guard','action':mode,
                    'reason':'originating_account_signed_out','result':'cancelled'})
                return False
            from .playback_engine import play
            material={'0':'Feature','1':'Trailer','2':'LiveStreaming','3':'LiveStreaming'}.get(value('trailer','0'))
            if material is None:raise ValueError('Unsupported playback material')
            resume=value('resume','ask')
            if resume not in ('ask','start'):raise ValueError('Unsupported resume mode')
            result=play(value('asin'),value('name','Smooth Prime'),material,int(argv[1]),resume_mode=resume,outcome=playback_outcome)
        elif mode=='LogIn':
            from .prime_login import LogIn
            result=LogIn() is not False
        elif mode in ('remLoginData','removeUser','SignOut'):
            from .account_state import sign_out
            sign_out(g);result=True
        elif mode=='renameUser':
            from .accounts import renameUser
            renameUser();result=True
        elif mode in ('CheckUpdates','Diagnostics'):
            filename='check_updates.py' if mode=='CheckUpdates' else 'view_diagnostics.py'
            xbmc.executebuiltin('RunScript("'+str(Path(g.PLUGIN_PATH)/filename)+'")');result=True
        elif mode=='openSettings':g.addon.openSettings();result=True
        else:raise ValueError('Unknown Smooth Prime action')
        report({'event':'action_finished','stage':'dispatch','action':mode,
            'result':playback_outcome.get('result') or ('success' if result else 'cancelled'),
            'playback_terminal_reason':playback_outcome.get('reason')})
        return result
    except Exception as error:
        report({'event':'action_failed','stage':'dispatch','action':mode,'exception':exception_details(error),'result':'failure'})
        g.dialog.ok('Smooth Prime','The action could not finish. Open Diagnostics for its complete report.')
        return False
