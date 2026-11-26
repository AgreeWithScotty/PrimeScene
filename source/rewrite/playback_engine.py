"""Native Kodi DRM playback with bounded, account-scoped media sessions."""
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from threading import Event
from time import monotonic
from urllib.parse import urlencode
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs
from .app_runtime import AppRuntime,Preferences
from .account_state import account_epoch,current_account
from .media_resources import MediaAPI,MediaServiceError
from .media_quality import lower_quality
from .media_proxy import PlaybackProxy
from .playback_progress import ResumePoints
from .prime_diagnostics import DiagnosticLog,exception_details
from .chrome_runtime import PreparationCancelled
STARTUP_REVISION='reply-before-async-stop-v1'


class PlaybackLog(DiagnosticLog):
    def __init__(self,g,request_id):
        epoch=account_epoch(g.DATA_PATH)
        super().__init__(g.DATA_PATH,g.addon.getAddonInfo('version'),operation='playback',
            active=lambda:current_account(g.DATA_PATH,epoch))
        self.request_id=request_id
    def __call__(self,event):super().__call__(dict(event,playback_request_id=self.request_id))


class PlayerEvents(xbmc.Player):
    def __init__(self,path,on_terminal=None):
        super().__init__();self.path=path;self.started=Event();self.finished=Event();self.stop_requested=Event()
        self.terminal='';self.on_terminal=on_terminal
    def _finished(self,reason):
        self.terminal=reason;self.finished.set()
        if self.on_terminal is not None:self.on_terminal(reason,'native_player_callback')
    def owned(self):
        try:return self.isPlaying() and self.getPlayingFile()==self.path
        except RuntimeError:return False
    def onAVStarted(self):
        if self.owned():self.started.set()
    def onPlayBackStopped(self):
        if self.started.is_set() or self.stop_requested.is_set():self._finished('stopped')
    def onPlayBackEnded(self):
        if self.started.is_set():self._finished('ended')
    def onPlayBackError(self):self._finished('error')


def security_level(report):
    level='L3'
    try:
        import xbmcdrm
        session=xbmcdrm.CryptoSession('edef8ba9-79d6-4ace-a3c8-27dcd51d21ed','AES/CBC/NoPadding','HmacSHA256')
        declared=session.GetPropertyString('securityLevel')
        if declared in ('L1','L2','L3'):
            level=declared
            report({'event':'widevine_security_level','stage':'device_capability','level':level,'measured':True,'result':'success'})
        else:
            report({'event':'widevine_security_level_unavailable','stage':'device_capability','requested_mode':level,
                'property_present':bool(declared),'measured':False,'reason':'no_reported_native_security_level','result':'unavailable'})
    except Exception as error:
        report({'event':'widevine_security_level_unavailable','stage':'device_capability','requested_mode':level,
            'exception':exception_details(error),'result':'unavailable'})
    return level


class SkipPrompt(xbmcgui.WindowDialog):
    def __init__(self,player,report):
        super().__init__();self.player,self.report=player,report;self.shown=False;self.element=None
        self.button=xbmcgui.ControlButton(max(0,self.getWidth()-340),max(0,self.getHeight()-140),300,60,
            'Skip intro',textColor='FFFFFFFF',focusedColor='FFFFFFFF')
    def show_element(self,element):
        if self.shown:return
        self.element=element;self.addControl(self.button);self.button.setLabel('Skip '+str(element.get('elementType','scene')).replace('_',' ').lower())
        self.shown=True;self.show();self.setFocus(self.button)
    def dismiss(self):
        if not self.shown:return
        self.shown=False;self.removeControl(self.button);self.close()
    def onControl(self,control):
        if control.getId()==self.button.getId() and self.player.owned():
            try:self.player.seekTime(float(self.element['endTimecodeMs'])/1000)
            except Exception as error:self.report({'event':'skip_scene_failed','stage':'player_seek','exception':exception_details(error),'result':'failure'})
            self.dismiss()
    def onAction(self,action):
        if action.getId() in (9,10,92):self.dismiss()


def inputstream_settings(addon,report):
    """Read actual installed chooser limits without changing global settings."""
    try:
        path=Path(xbmcvfs.translatePath(addon.getAddonInfo('path')))/'resources/settings.xml'
        with path.open('rb') as source:data=source.read(256*1024+1)
        if len(data)>256*1024:raise ValueError('InputStream settings schema exceeds its limit')
        result={}
        for setting in ET.fromstring(data).iter('setting'):
            key=setting.get('id') or ''
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',key):continue
            if not any(part in key.lower() for part in ('resolution','bandwidth','chooser','ignore_display','stream_selection')):continue
            value=addon.getSetting(key)
            result[key]=(value if re.fullmatch(r'[-A-Za-z0-9_. x]{0,48}',value or '') else '[unreported value]')
        return result
    except Exception as error:
        report({'event':'inputstream_settings_unavailable','stage':'quality_configuration',
            'exception':exception_details(error),'result':'unavailable'})
        return None


def stream_snapshot(player,api,report):
    """Observe selected native codecs/dimensions; never log media URLs or keys."""
    error_code=None
    try:
        if not player.owned():return None
        def rpc(method,params=None):
            nonlocal error_code
            request={'jsonrpc':'2.0','id':1,'method':method}
            if params is not None:request['params']=params
            response=json.loads(xbmc.executeJSONRPC(json.dumps(request)))
            if 'error' in response:
                error_code=(response.get('error') or {}).get('code')
                raise RuntimeError('Kodi could not report native stream properties')
            return response['result']
        active=rpc('Player.GetActivePlayers')
        identifier=next((p['playerid'] for p in active if p.get('type')=='video'),None)
        if identifier is None:return None
        values=rpc('Player.GetProperties',{'playerid':identifier,
            'properties':['currentvideostream','currentaudiostream','videostreams']})
        def safe(stream):
            return {key:value for key,value in (stream or {}).items()
                if key in ('width','height','codec','hdrtype','bitrate','channels','samplerate','fps','interlaced')
                and (type(value) in (int,float,bool) or isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._/+ -]{0,64}',value))}
        if not player.owned():return None
        return {'requested_quality':api.quality,
            'current_window_id':xbmcgui.getCurrentWindowId(),
            'fullscreen_video':xbmc.getCondVisibility('Window.IsVisible(fullscreenvideo)'),
            'hardware_level':api.hardware_level,'selected_video':safe(values.get('currentvideostream')),
            'selected_audio':safe(values.get('currentaudiostream')),
            'native_video_choices':[safe(item) for item in (values.get('videostreams') or [])[:64]]}
    except Exception as error:
        report({'event':'playback_stream_observation_failed','stage':'native_stream_quality',
            'jsonrpc_error_code':error_code,'exception':exception_details(error),'result':'unavailable'})
        return None


def configure_drm(item,key_system,license_url,inputstream_version,certificate=''):
    """Select the requested DRM as well as supplying its license transport."""
    numbers=tuple(int(v) for v in re.findall(r'\d+',inputstream_version)[:3])
    if key_system!='com.widevine.alpha':raise ValueError('This add-on uses stock Kodi Widevine playback')
    if numbers>=(22,1,5):
        configuration={
            # Select Widevine explicitly on manifests advertising several DRMs.
            'priority':1,'force_single_session':True,'license':{'server_url':license_url,
                'req_headers':urlencode({'Content-Type':'application/octet-stream'})}}
        if certificate:configuration['license']['server_certificate']=certificate
        item.setProperty('inputstream.adaptive.drm',json.dumps({key_system:configuration}))
        return 'json'
    item.setProperty('inputstream.adaptive.license_type',key_system)
    item.setProperty('inputstream.adaptive.license_key',license_url+'|Content-Type=application%2Foctet-stream|R{SSM}|')
    if certificate:item.setProperty('inputstream.adaptive.server_certificate',certificate)
    build=xbmc.getInfoLabel('System.BuildVersion')
    if int(re.match(r'\d+',build).group())<21:item.setProperty('inputstream.adaptive.manifest_type','mpd')
    return 'compatibility_properties'


def list_item(name,proxy,report,resume,preferences,origin_url):
    item=xbmcgui.ListItem(label=name,path=proxy.manifest(),offscreen=True)
    item.setMimeType('application/dash+xml');item.setContentLookup(False)
    item.setProperty('original_listitem_url',origin_url)
    item.getVideoInfoTag().setTitle(name)
    item.setProperty('inputstream','inputstream.adaptive')
    headers=urlencode(proxy.api.headers)
    item.setProperty('inputstream.adaptive.manifest_headers',headers)
    item.setProperty('inputstream.adaptive.stream_headers',headers)
    selection='fixed-res' if preferences.number('stream_quality')==0 else 'adaptive'
    item.setProperty('inputstream.adaptive.stream_selection_type',selection)
    item.setSubtitles(proxy.subtitles())
    if resume:item.setProperty('StartOffset',str(resume))
    adaptive=xbmcaddon.Addon('inputstream.adaptive');version=adaptive.getAddonInfo('version')
    key_system='com.widevine.alpha'
    certificate=proxy.api.web.server_certificate if proxy.api.web is not None else ''
    drm_configuration=configure_drm(item,key_system,proxy.license(),version,certificate)
    report({'event':'kodi_playback_item_ready','stage':'inputstream_configuration','inputstream_version':version,
        'drm_configuration':drm_configuration,
        'stream_selection_type':selection,'inputstream_chooser_settings':inputstream_settings(adaptive,report),
        'requested_quality':proxy.api.quality,
        'key_system':key_system,'key_system_priority':1,'service_certificate_present':bool(certificate),
        'playback_protocol':'web_widevine' if proxy.api.web is not None else 'classic_widevine',
        'subtitles':len(proxy.resources.subtitles),'resume_seconds':round(resume,2),'result':'success'})
    return item


def play(identifier,name,material='Feature',handle=-1,resume_mode='ask',outcome=None):
    state={};quality=None
    # At most UHD -> HD -> SD; no retry can increase the requested quality.
    for _ in range(3):
        result=_play_attempt(identifier,name,material,-1 if state.get('resolved') else handle,
            resume_mode,outcome,state,quality_override=quality)
        quality=state.pop('retry_quality',None)
        if quality is None:return result
    return False


def _play_attempt(identifier,name,material,handle,resume_mode,outcome,retry_state,quality_override=None):
    g=AppRuntime();preferences=Preferences(g.addon)
    from .aurora import _read_note,_request_playback_return
    # Kodi's separate return observer can clear the on-disk note after the HD
    # error. Keep this request's original target and ID through the SD attempt.
    note=retry_state.setdefault('return_note',_read_note(g.DATA_PATH) or {})
    report=PlaybackLog(g,note.get('request_id'))
    report.secret(identifier);report.secret(name)
    started=monotonic();api=proxy=prompt=cdm_lease=None;terminal='error';position=duration=0;player=None
    return_sent=False;retrying=False
    def restore_card(reason,source):
        nonlocal return_sent
        if retrying:return
        if (source=='native_player_callback' and player is not None and not player.started.is_set()
                and (player.stop_requested.is_set() or reason=='error' and api is not None and api.quality in ('UHD','HD'))):return
        if return_sent:return
        try:
            return_sent=bool(_request_playback_return(note,reason,report))
            report({'event':'playback_return_signalled','stage':'immediate_return','source':source,
                'current_window_id':xbmcgui.getCurrentWindowId(),'screen_mode':xbmc.getInfoLabel('System.ScreenMode') or None,
                'cleanup_pending':True,'result':'success' if return_sent else 'cancelled'})
        except Exception as error:
            report({'event':'playback_return_signal_failed','stage':'immediate_return','source':source,
                'exception':exception_details(error),'result':'failure'})
    report({'event':'playback_started','stage':'prepare','material':material,'resolver_handle':handle,
        'resume_mode':resume_mode,'startup_revision':STARTUP_REVISION,
        'kodi':xbmc.getInfoLabel('System.BuildVersion'),'result':'started'})
    try:
        from inputstreamhelper import Helper
        device_platform=next((name for name,condition in (
            ('android','System.Platform.Android'),('webos','System.Platform.WebOS'),
            ('windows','System.Platform.Windows'),('linux','System.Platform.Linux'),
            ('osx','System.Platform.OSX'),('ios','System.Platform.IOS')) if xbmc.getCondVisibility(condition)),'unknown')
        chrome_bridge=False
        if device_platform=='windows' and material=='Feature' and quality_override is None:
            from .chrome_cdm_bridge import available as chrome_bridge_available
            chrome_bridge=chrome_bridge_available(g,report)
        helper=Helper('mpd',drm=None if chrome_bridge else 'com.widevine.alpha')
        if not helper.check_inputstream():
            terminal='cancelled';report({'event':'inputstream_unavailable','stage':'inputstream_check','result':'cancelled'});return False
        adaptive=xbmcaddon.Addon('inputstream.adaptive')
        from .widevine_diagnostics import record_environment
        record_environment(device_platform,report)
        report({'event':'widevine_playback_environment','stage':'inputstream_check',
            'inputstream_version':adaptive.getAddonInfo('version'),
            'inputstream_helper_version':xbmcaddon.Addon('script.module.inputstreamhelper').getAddonInfo('version'),
            'device_platform':device_platform,'key_system':'com.widevine.alpha',
            'cdm_initialization_owner':'chrome_bridge' if chrome_bridge else 'inputstream.adaptive',
            'experimental_host_verification':False,
            'inputstream_chooser_settings':inputstream_settings(adaptive,report),'result':'success'})
        api=MediaAPI(g,preferences,report,hardware_level=security_level(report),
            device_platform=device_platform,material=material,drm_system='widevine',quality_override=quality_override)
        if quality_override is not None:
            report({'event':'playback_quality_fallback_started','stage':'quality_authorization',
                'same_account_and_title':True,'drm_system':'widevine','requested_quality':api.quality,
                'previous_quality':retry_state.get('previous_quality'),
                'previous_attempt':retry_state.get('quality_attempt'),'result':'started'})
        resources=api.resources(identifier);proxy=PlaybackProxy(api,resources,report)
        points=ResumePoints(g.DATA_PATH,api.epoch,report)
        previous=points.read(identifier) if material=='Feature' else None
        resume=0
        if resume_mode not in ('ask','start'):raise ValueError('Unsupported resume mode')
        if 'resume' in retry_state:resume=retry_state['resume']
        elif resume_mode=='ask' and previous and previous['position']>30:
            selected=g.dialog.contextmenu(['Resume from %d:%02d'%(previous['position']//60,previous['position']%60),'Play from beginning'])
            if selected<0:terminal='cancelled';return False
            if selected==0:resume=previous['position']
        retry_state['resume']=resume
        origin_url=g.pluginid+'?'+urlencode({'mode':'PlayVideo','asin':identifier,'trailer':{'Feature':'0','Trailer':'1','LiveStreaming':'2'}[material]})
        proxy.prepare_manifest()
        item=list_item(name,proxy,report,resume,preferences,origin_url)
        if chrome_bridge:
            from .chrome_cdm_bridge import Lease
            cdm_lease=Lease(g,adaptive,report)
            cdm_lease.activate()
        player=PlayerEvents(proxy.manifest(),restore_card)
        report({'event':'playback_open_dispatch','stage':'player_open','resolver_handle':handle,'result':'started'})
        if handle>=0:xbmcplugin.setResolvedUrl(handle,True,item)
        else:player.play(proxy.manifest(),item)
        retry_state['resolved']=True
        report({'event':'playback_open_dispatched','stage':'player_open','result':'queued'})
        def stop_opening(reason):
            # Player.stop() synchronously waits on Kodi's application thread.
            # During DRM startup that thread may need this Python proxy to run.
            # Queue Stop instead, then yield to the real terminal callback.
            stop_started=monotonic();retry_state['startup_stop_complete']=False
            try:
                if player.finished.is_set():
                    retry_state['startup_stop_complete']=True
                    report({'event':'playback_startup_stop_finished','stage':'player_shutdown','reason':reason,
                        'terminal_callback':player.terminal,'stop_message_required':False,'elapsed_ms':0,'result':'success'})
                    return
                latest_note=_read_note(g.DATA_PATH) or {}
                if (note.get('request_id') and latest_note.get('request_id') and
                        note['request_id']!=latest_note['request_id']):
                    report({'event':'playback_startup_stop_skipped','stage':'player_shutdown','reason':reason,'result':'replaced'})
                    return
                report({'event':'playback_startup_ownership_check','stage':'player_shutdown','result':'started'})
                if player.isPlaying() and not player.owned():
                    report({'event':'playback_startup_stop_skipped','stage':'player_shutdown','reason':reason,'result':'replaced'})
                    return
                player.stop_requested.set()
                report({'event':'playback_startup_stop_requested','stage':'player_shutdown',
                    'reason':reason,'transport':'asynchronous_builtin','result':'started'})
                xbmc.executebuiltin('PlayerControl(Stop)',False)
                report({'event':'playback_startup_stop_queued','stage':'player_shutdown','result':'queued'})
                stop_deadline=monotonic()+8
                while not player.finished.is_set() and monotonic()<stop_deadline:
                    if g.monitor.waitForAbort(.05):break
                retry_state['startup_stop_complete']=player.finished.is_set()
                report({'event':'playback_startup_stop_finished','stage':'player_shutdown','reason':reason,
                    'terminal_callback':player.terminal or None,'elapsed_ms':round((monotonic()-stop_started)*1000),
                    'result':'success' if player.finished.is_set() else 'cancelled' if g.monitor.abortRequested() else 'timeout'})
            except Exception as error:
                report({'event':'playback_failed_start_stop_error','stage':'player_shutdown',
                    'elapsed_ms':round((monotonic()-stop_started)*1000),'exception':exception_details(error),'result':'failure'})
        deadline=monotonic()+45;next_report=0;last_saved=0;first_report=True;display_reported=False;skip_seen=set()
        next_snapshot=0;stream_signature=None
        while not g.monitor.abortRequested():
            if not current_account(g.DATA_PATH,api.epoch):
                terminal='account_changed'
                stop_opening('account_changed')
                break
            if proxy.failed.is_set():
                error=proxy.refused
                report({'event':'playback_license_failure_observed','stage':getattr(error,'stage','license'),
                    'server_error_codes':list(getattr(error,'server_error_codes',())),
                    'license_response_finished':True,'result':'failure'})
                stop_opening('license_refused')
                raise error
            if player.terminal:
                terminal=player.terminal
                if terminal=='error':raise RuntimeError('Kodi rejected the selected stream before playback could continue')
                break
            if player.started.is_set():
                if cdm_lease is not None:cdm_lease.restore('native_player_started')
                if not display_reported:
                    display_reported=True
                    report({'event':'playback_display_mode','stage':'av_started',
                        'screen_mode':xbmc.getInfoLabel('System.ScreenMode') or None,
                        'current_window_id':xbmcgui.getCurrentWindowId(),
                        'fullscreen_video':xbmc.getCondVisibility('Window.IsVisible(fullscreenvideo)'),
                        'result':'observed'})
                if not player.owned():
                    terminal=player.terminal or ('replaced' if player.isPlaying() else 'stopped');break
                try:position,duration=player.getTime(),player.getTotalTime()
                except RuntimeError:
                    if g.monitor.waitForAbort(.1):break
                    continue
                now=monotonic()
                if now>=next_snapshot:
                    if api.web is not None:
                        try:api.web.verify_output_protection()
                        except Exception:
                            stop_opening('hdcp_protection_lost');raise
                    snapshot=stream_snapshot(player,api,report);next_snapshot=now+15
                    signature=json.dumps(snapshot,sort_keys=True) if snapshot else None
                    if snapshot and signature!=stream_signature:
                        stream_signature=signature
                        report(dict(snapshot,event='playback_stream_observed',stage='native_stream_quality',
                            result='observed' if snapshot.get('selected_video',{}).get('height') else 'unavailable'))
                if material=='Feature' and position>0 and now-last_saved>=10:
                    points.write(identifier,position,duration);last_saved=now
                send=preferences.number('send_vp',1)
                percent=position*100/duration if duration else 0
                if send>0 and percent>=send and now>=next_report:
                    try:
                        interval=api.progress('START' if first_report else 'PLAY',position) or 60
                        first_report=False;next_report=now+interval
                    except Exception as error:
                        next_report=now+60
                        report({'event':'progress_update_failed','stage':'watch_progress','exception':exception_details(error),'result':'failure'})
                mode=preferences.number('skip_scene')
                active=next((e for e in resources.transitions if isinstance(e,dict) and
                    isinstance(e.get('startTimecodeMs'),(int,float)) and isinstance(e.get('endTimecodeMs'),(int,float)) and
                    e['startTimecodeMs']/1000<=position<e['endTimecodeMs']/1000-3),None)
                if active and mode and active.get('endTimecodeMs') not in skip_seen:
                    if mode==2:
                        player.seekTime(active['endTimecodeMs']/1000);skip_seen.add(active['endTimecodeMs'])
                    elif xbmcgui.getCurrentWindowId() in (12005,12901):
                        if prompt is None:
                            report({'event':'skip_prompt_create_started','stage':'playing_overlay','av_started':True,'result':'started'})
                            prompt=SkipPrompt(player,report)
                            report({'event':'skip_prompt_ready','stage':'playing_overlay','result':'success'})
                        prompt.show_element(active)
                elif not active and prompt is not None:prompt.dismiss()
            elif monotonic()>deadline:
                terminal='start_timeout';stop_opening('start_timeout')
                raise RuntimeError('Kodi did not start the authorized media within its deadline')
            if g.monitor.waitForAbort(.2):terminal='shutdown';break
        restore_card(terminal,'session_terminal')
        if material=='Feature' and position>0:
            points.write(identifier,position,duration,completed=terminal=='ended' or duration>0 and position/duration>=.9)
        if position>0 and preferences.number('send_vp',1)>0 and current_account(g.DATA_PATH,api.epoch):
            progress_started=monotonic()
            report({'event':'progress_stop_started','stage':'watch_progress','ui_return_requested':return_sent,'result':'started'})
            try:
                api.progress('STOP',position)
                report({'event':'progress_stop_finished','stage':'watch_progress',
                    'elapsed_ms':round((monotonic()-progress_started)*1000),'result':'success'})
            except Exception as error:report({'event':'progress_stop_failed','stage':'watch_progress','exception':exception_details(error),'result':'failure'})
        return terminal not in ('error','start_timeout','account_changed')
    except PreparationCancelled as error:
        terminal='cancelled'
        report({'event':'playback_cancelled','stage':'private_chrome_runtime',
            'exception':exception_details(error),'result':'cancelled'})
        return False
    except Exception as error:
        if g.monitor.abortRequested():
            terminal='shutdown'
            report({'event':'playback_cancelled','stage':'prepare_or_play',
                'exception':exception_details(error),'result':'cancelled'})
            return False
        details={'event':'playback_failed','stage':getattr(error,'stage','native_playback'),
            'exception':exception_details(error),'result':'failure'}
        if isinstance(error,MediaServiceError):
            details.update(provider_refusal=error.category,status=error.status,server_error_codes=list(error.server_error_codes))
        if api is not None:
            details.update(requested_quality=api.quality,hardware_level=api.hardware_level,
                device_platform=api.device_platform,credential_type=api.auth,quality_downgraded=False)
        report(details)
        next_quality=(lower_quality(api.quality,error.allowed_quality)
            if api is not None and isinstance(error,MediaServiceError) and error.category=='quality' else None)
        if next_quality is not None and not retry_state.get('startup_stop_complete',True):
            report({'event':'playback_quality_retry_withheld','stage':'player_shutdown',
                'fallback_quality':next_quality,'reason':'previous_player_shutdown_not_acknowledged','result':'failure'})
        if (next_quality is not None
                and isinstance(error,MediaServiceError) and error.category=='quality'
                and retry_state.get('startup_stop_complete',True)
                and not g.monitor.abortRequested() and (player is None or not player.started.is_set())
                and current_account(g.DATA_PATH,api.epoch)):
            retrying=True;retry_state['retry_quality']=next_quality
            retry_state['previous_quality']=api.quality;retry_state['quality_attempt']=report.attempt
            report({'event':'widevine_quality_refused_fallback','stage':'quality_authorization',
                'higher_quality_succeeded':False,'same_account_and_title':True,'requested_quality':api.quality,
                'fallback_quality':next_quality,'server_error_codes':list(error.server_error_codes),
                'exception':exception_details(error),'result':'retry'})
            return False
        restore_card(terminal,'playback_failure')
        g.dialog.ok('Smooth Prime',error.user_message if isinstance(error,MediaServiceError) else
            'Playback could not start. Open Settings > Diagnostics for the complete report.')
        if handle>=0 and player is None:xbmcplugin.setResolvedUrl(handle,False,xbmcgui.ListItem())
        return False
    finally:
        restore_card(terminal,'cleanup_entry')
        cleanup_started=monotonic()
        for resource in (prompt,proxy,api,cdm_lease):
            if resource is None:continue
            try:resource.dismiss() if resource is prompt else resource.close()
            except Exception as error:report({'event':'playback_cleanup_failed','stage':'cleanup',
                'exception':exception_details(error),'result':'failure'})
        result='cancelled' if terminal in ('cancelled','account_changed','shutdown','replaced') else 'failure' if terminal in ('error','start_timeout') else 'success'
        if outcome is not None and not retrying:outcome.update(result=result,reason=terminal)
        report({'event':'playback_session_finished','stage':'terminal','reason':terminal,
            'cleanup_elapsed_ms':round((monotonic()-cleanup_started)*1000),'ui_return_requested_before_cleanup':return_sent,
            'position_seconds':round(position,2),'duration_seconds':round(duration,2),
            'elapsed_ms':round((monotonic()-started)*1000),
            'result':result})
        # The observer can restore the card immediately. A fresh In Progress
        # request must follow the final provider progress attempt, not race it.
        if not retrying:_request_playback_return(note,terminal,report,progress_ready=True)
