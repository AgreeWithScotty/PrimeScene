"""Native Kodi/Widevine playback with one bounded, account-scoped media session."""
import json
import re
from threading import Event
from time import monotonic
from urllib.parse import urlencode
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
from .app_runtime import AppRuntime,Preferences
from .account_state import account_epoch,current_account
from .media_resources import MediaAPI
from .media_proxy import PlaybackProxy
from .playback_progress import ResumePoints
from .prime_diagnostics import DiagnosticLog,exception_details


class PlaybackLog(DiagnosticLog):
    def __init__(self,g,request_id):
        epoch=account_epoch(g.DATA_PATH)
        super().__init__(g.DATA_PATH,g.addon.getAddonInfo('version'),operation='playback',
            active=lambda:current_account(g.DATA_PATH,epoch))
        self.request_id=request_id
    def __call__(self,event):super().__call__(dict(event,playback_request_id=self.request_id))


class PlayerEvents(xbmc.Player):
    def __init__(self,path,on_terminal=None):
        super().__init__();self.path=path;self.started=Event();self.terminal='';self.on_terminal=on_terminal
    def _finished(self,reason):
        self.terminal=reason
        if self.on_terminal is not None:self.on_terminal(reason,'native_player_callback')
    def owned(self):
        try:return self.isPlaying() and self.getPlayingFile()==self.path
        except RuntimeError:return False
    def onAVStarted(self):
        if self.owned():self.started.set()
    def onPlayBackStopped(self):
        if self.started.is_set():self._finished('stopped')
    def onPlayBackEnded(self):
        if self.started.is_set():self._finished('ended')
    def onPlayBackError(self):self._finished('error')


def security_level(report):
    level='L3'
    try:
        import xbmcdrm
        session=xbmcdrm.CryptoSession('edef8ba9-79d6-4ace-a3c8-27dcd51d21ed','AES/CBC/NoPadding','HmacSHA256')
        declared=session.GetPropertyString('securityLevel')
        if declared in ('L1','L2','L3'):level=declared
        report({'event':'widevine_security_level','stage':'device_capability','level':level,'result':'success'})
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


def list_item(name,proxy,report,resume,preferences,origin_url):
    item=xbmcgui.ListItem(label=name,path=proxy.manifest(),offscreen=True)
    item.setMimeType('application/dash+xml');item.setContentLookup(False)
    item.setProperty('original_listitem_url',origin_url)
    item.getVideoInfoTag().setTitle(name)
    item.setProperty('inputstream','inputstream.adaptive')
    headers=urlencode(proxy.api.headers)
    item.setProperty('inputstream.adaptive.manifest_headers',headers)
    item.setProperty('inputstream.adaptive.stream_headers',headers)
    if preferences.number('stream_quality')==0:item.setProperty('inputstream.adaptive.stream_selection_type','fixed-res')
    item.setSubtitles(proxy.subtitles())
    if resume:item.setProperty('StartOffset',str(resume))
    version=xbmcaddon.Addon('inputstream.adaptive').getAddonInfo('version')
    numbers=tuple(int(v) for v in re.findall(r'\d+',version)[:3])
    if numbers>=(22,1,5):
        item.setProperty('inputstream.adaptive.drm',json.dumps({'com.widevine.alpha':{
            'force_single_session':True,'license':{'server_url':proxy.license(),
                'req_headers':urlencode({'Content-Type':'application/octet-stream'})}}}))
    else:
        item.setProperty('inputstream.adaptive.license_type','com.widevine.alpha')
        item.setProperty('inputstream.adaptive.license_key',proxy.license()+'|Content-Type=application%2Foctet-stream|R{SSM}|')
        build=xbmc.getInfoLabel('System.BuildVersion')
        if int(re.match(r'\d+',build).group())<21:item.setProperty('inputstream.adaptive.manifest_type','mpd')
    report({'event':'kodi_playback_item_ready','stage':'inputstream_configuration','inputstream_version':version,
        'drm_configuration':'json' if numbers>=(22,1,5) else 'compatibility_properties',
        'subtitles':len(proxy.resources.subtitles),'resume_seconds':round(resume,2),'result':'success'})
    return item


def play(identifier,name,material='Feature',handle=-1,resume_mode='ask'):
    g=AppRuntime();preferences=Preferences(g.addon)
    from .aurora import _read_note,_request_playback_return
    note=_read_note(g.DATA_PATH) or {};report=PlaybackLog(g,note.get('request_id'))
    report.secret(identifier);report.secret(name)
    started=monotonic();api=proxy=prompt=None;terminal='error';position=duration=0;player=None
    return_sent=False
    def restore_card(reason,source):
        nonlocal return_sent
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
        'resume_mode':resume_mode,
        'kodi':xbmc.getInfoLabel('System.BuildVersion'),'result':'started'})
    try:
        from inputstreamhelper import Helper
        helper=Helper('mpd',drm='com.widevine.alpha')
        if not helper.check_inputstream():
            terminal='cancelled';report({'event':'inputstream_unavailable','stage':'inputstream_check','result':'cancelled'});return False
        api=MediaAPI(g,preferences,report,hardware_level=security_level(report),material=material)
        resources=api.resources(identifier);proxy=PlaybackProxy(api,resources,report)
        points=ResumePoints(g.DATA_PATH,api.epoch,report)
        previous=points.read(identifier) if material=='Feature' else None
        resume=0
        if resume_mode not in ('ask','start'):raise ValueError('Unsupported resume mode')
        if resume_mode=='ask' and previous and previous['position']>30:
            selected=g.dialog.contextmenu(['Resume from %d:%02d'%(previous['position']//60,previous['position']%60),'Play from beginning'])
            if selected<0:terminal='cancelled';return False
            if selected==0:resume=previous['position']
        proxy.prepare_manifest()
        origin_url=g.pluginid+'?'+urlencode({'mode':'PlayVideo','asin':identifier,'trailer':{'Feature':'0','Trailer':'1','LiveStreaming':'2'}[material]})
        item=list_item(name,proxy,report,resume,preferences,origin_url);player=PlayerEvents(proxy.manifest(),restore_card)
        if handle>=0:xbmcplugin.setResolvedUrl(handle,True,item)
        else:player.play(proxy.manifest(),item)
        deadline=monotonic()+45;next_report=0;last_saved=0;first_report=True;display_reported=False;skip_seen=set();prompt=SkipPrompt(player,report)
        while not g.monitor.abortRequested():
            if not current_account(g.DATA_PATH,api.epoch):
                terminal='account_changed'
                if player.owned():player.stop()
                break
            if player.terminal:terminal=player.terminal;break
            if player.started.is_set():
                if not display_reported:
                    display_reported=True
                    report({'event':'playback_display_mode','stage':'av_started',
                        'screen_mode':xbmc.getInfoLabel('System.ScreenMode') or None,'result':'observed'})
                if not player.owned():
                    terminal=player.terminal or ('replaced' if player.isPlaying() else 'stopped');break
                try:position,duration=player.getTime(),player.getTotalTime()
                except RuntimeError:
                    if g.monitor.waitForAbort(.1):break
                    continue
                now=monotonic()
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
                    elif xbmcgui.getCurrentWindowId() in (12005,12901):prompt.show_element(active)
                elif not active:prompt.dismiss()
            elif monotonic()>deadline:
                terminal='start_timeout';raise RuntimeError('Kodi did not start the authorized media within its deadline')
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
    except Exception as error:
        report({'event':'playback_failed','stage':'native_playback','exception':exception_details(error),'result':'failure'})
        g.dialog.ok('Smooth Prime','Playback could not start. Open Settings > Diagnostics for the complete report.')
        if handle>=0 and player is None:xbmcplugin.setResolvedUrl(handle,False,xbmcgui.ListItem())
        return False
    finally:
        restore_card(terminal,'cleanup_entry')
        cleanup_started=monotonic()
        for resource in (prompt,proxy,api):
            if resource is None:continue
            try:resource.dismiss() if resource is prompt else resource.close()
            except Exception as error:report({'event':'playback_cleanup_failed','stage':'cleanup',
                'exception':exception_details(error),'result':'failure'})
        report({'event':'playback_session_finished','stage':'terminal','reason':terminal,
            'cleanup_elapsed_ms':round((monotonic()-cleanup_started)*1000),'ui_return_requested_before_cleanup':return_sent,
            'position_seconds':round(position,2),'duration_seconds':round(duration,2),
            'elapsed_ms':round((monotonic()-started)*1000),
            'result':'cancelled' if terminal in ('cancelled','account_changed','shutdown','replaced') else 'failure' if terminal in ('error','start_timeout') else 'success'})
        # The observer can restore the card immediately. A fresh In Progress
        # request must follow the final provider progress attempt, not race it.
        _request_playback_return(note,terminal,report,progress_ready=True)
