"""Amazon media wire requests; independent from catalog and Kodi player state."""
import base64
from dataclasses import dataclass
from time import time
from urllib.parse import urlencode,urlsplit
from .accounts import loadUser
from .account_auth import renew_device_token
from .account_state import account_epoch,current_account
from .http_transport import HttpTransport,TransportFailure,https_address,shape,server_errors
from .prime_diagnostics import exception_details
from .prime_signin import has_authentication_cookie
from .session_store import load_session,persist_session,session_headers
from .regional_hosts import MEDIA_API_HOSTS,playback_catalog_context
from .media_quality import QUALITY_POLICY,preferred_quality,permitted_quality

WEB_DEVICE='AOAGZA014O5RE'


class MediaServiceError(TransportFailure):
    """A provider refusal with safe codes and an actionable, fixed UI message."""
    def __init__(self,codes,status,stage):
        self.server_error_codes=tuple(codes);self.status=status;self.stage=stage
        self.allowed_quality=permitted_quality(codes)
        self.category=('location' if 'PRS.NoRights.InvalidGeoIP' in codes else
            'quality' if self.allowed_quality is not None else 'provider_refusal')
        self.user_message=(
            'Amazon refused playback for this network location (PRS.NoRights.InvalidGeoIP). '
            'If a VPN or proxy is enabled, disconnect it and try again. '
            'If this continues on a normal connection, contact Amazon.'
            if self.category=='location' else
            'Amazon refused the HD license for this device/title. '
            'Open Settings > Diagnostics for the complete license and quality report.'
            if self.category=='quality' else
            'Amazon refused the playback request. Open Settings > Diagnostics for the provider error code and complete report.')
        super().__init__(self.user_message)


@dataclass
class MediaResources:
    manifest:str
    subtitles:list
    transitions:list
    cdn:str


class MediaAPI:
    def __init__(self,g,preferences,report,*,hardware_level='L3',device_platform='unknown',material='Feature',drm_system='widevine',quality_override=None):
        self.g,self.preferences,self.report=g,preferences,report
        self.epoch=account_epoch(g.DATA_PATH);self.account=loadUser()
        self.catalog_context=playback_catalog_context(self.account)
        self.material=material;self.hardware_level=hardware_level;self.device_platform=device_platform
        if drm_system!='widevine':raise ValueError('This add-on uses stock Kodi Widevine playback')
        self.drm_system=drm_system;self.web=None
        self.jar=load_session(g.DATA_PATH);self.auth='website';self.device_type=WEB_DEVICE
        endpoint=self.account.get('atvurl','').rstrip('/')
        hosts=MEDIA_API_HOSTS
        if not https_address(endpoint,hosts):
            self.jar.clear();raise TransportFailure('The verified account has no trusted media API endpoint')
        self.endpoint=endpoint
        headers=session_headers(g.DATA_PATH);headers.update({'Accept':'application/json','Accept-Encoding':'identity'})
        headers.setdefault('User-Agent',g.configured_user_agent())
        token=self.account.get('token') or {}
        if hardware_level=='L1' and token.get('access'):
            if float(token.get('expires') or 0)<=time()+60:
                token=renew_device_token(self.account,g=g,epoch=self.epoch,observer=report)
            if token:
                self.auth='device';self.device_type=g.dtid_android
                headers=dict(g.headers_android,Authorization='Bearer '+token['access'],Accept='application/json',**{'Accept-Encoding':'identity'})
        if self.auth=='website' and not has_authentication_cookie(self.jar):
            self.jar.clear();raise TransportFailure('Your Amazon session needs verification. Sign in again.')
        for value in (self.account.get('name'),self.account.get('deviceid'),self.account.get('cid'),self.account.get('mid')):
            report.secret(value)
        self.client=HttpTransport(g.DATA_PATH,self.epoch,report,hosts={urlsplit(endpoint).hostname},
            jar=self.jar if self.auth=='website' else None,headers=headers)
        self.headers={'User-Agent':headers.get('User-Agent','PrimeScene'),'Accept-Encoding':'identity'}
        # Saved opt-in values from older releases must not pin users to SD.
        # Resolution preference never changes the CDM's actual security level.
        if quality_override not in (None,'HD','SD'):raise ValueError('Unsupported quality retry')
        self.quality=quality_override or preferred_quality(hardware_level)
        report({'event':'media_authorization_selected','stage':'media_context','credential_type':self.auth,
            'hardware_level':hardware_level,'device_platform':device_platform,'requested_quality':self.quality,
            'drm_system':drm_system,
            'playback_catalog':self.catalog_context,
            'quality_policy':QUALITY_POLICY,'quality_mode':'automatic_fallback' if quality_override else 'automatic',
            'legacy_quality_settings_ignored':True,
            'quality_settings':{key:preferences.flag(key) for key in
                ('use_h265','enable_hdr10','enable_dovi')},
            'material':material,'result':'success'})

    def address(self,operation,identifier,resources=None,extra=None):
        if operation not in ('catalog/GetPlaybackResources','usage/UpdateStream'):raise ValueError('Invalid media operation')
        if not isinstance(identifier,str) or not identifier or len(identifier)>250:raise ValueError('Invalid provider title identity')
        params={'asin':identifier,'deviceTypeID':self.device_type,'firmware':'1',
            'deviceID':self.account['deviceid'],'marketplaceID':self.account['mid'],'format':'json','version':'2',
            'gascEnabled':str(self.catalog_context['gasc_enabled']).lower()}
        if resources:
            params.update(resourceUsage='ImmediateConsumption',consumptionType='Streaming',
                deviceDrmOverride='CENC',deviceStreamingTechnologyOverride='DASH',deviceProtocolOverride='Https',
                deviceBitrateAdaptationsOverride='CVBR,CBR',audioTrackId='all',languageFeature='MLFv2',
                videoMaterialType=self.material,desiredResources=resources,supportedDRMKeyScheme='DUAL_KEY',
                deviceVideoQualityOverride=self.quality)
            if self.hardware_level=='L1':
                if self.auth=='website' and self.device_platform in ('android','webos'):
                    # Amazon's website client on an L1 TV device needs the
                    # desktop playback profile. This does not change the CDM
                    # security level or claim hardware the device lacks.
                    params['operatingSystemName']='Windows'
                params['deviceVideoCodecOverride']='H264,H265' if self.preferences.flag('use_h265') else 'H264'
                hdr=[name for key,name in (('enable_hdr10','Hdr10'),('enable_dovi','DolbyVision')) if self.preferences.flag(key)]
                params['deviceHdrFormatsOverride']=','.join(hdr) or 'None'
            if 'SubtitleUrls' in resources:params['subtitleFormat']='TTMLv2'
            if self.material=='LiveStreaming':
                params.update(liveManifestType='accumulating,live',playerType='xp',
                    playerAttributes='{"frameRate":"HFR"}',deviceFrameRateOverride='High')
        params.update(extra or {})
        return self.endpoint+'/cdp/'+operation+'?'+urlencode(params)

    def json(self,address,*,data=b'',stage='media_request',headers=None):
        response=self.client.request(address,data=data,headers=headers,stage=stage)
        try:value=response.json()
        except (UnicodeError,ValueError) as error:
            self.report({'event':'media_json_decode_failed','stage':stage,'status':response.status,
                'content_type':response.headers.get_content_type(),'response_bytes':len(response.data),
                'exception':exception_details(error),'result':'failure'})
            raise TransportFailure('Amazon returned an unexpected media response format') from error
        if not isinstance(value,dict):raise TransportFailure('Amazon returned an unexpected media protocol')
        resource_errors=value.get('errorsByResource') or {}
        errors=value.get('error') or {key:error for key,error in resource_errors.items()
            if key in ('PlaybackUrls','AudioVideoUrls','Widevine2License','PlayReadyLicense')}
        self.report({'event':'media_response_parsed','stage':stage,'status':response.status,
            'requested_quality':self.quality,'hardware_level':self.hardware_level,
            'device_platform':self.device_platform,'credential_type':self.auth,
            'protocol_structure':shape(value),'server_error_codes':server_errors(value),
            'result':'failure' if errors else 'success'})
        if errors:raise MediaServiceError(server_errors(errors),response.status,stage)
        if current_account(self.g.DATA_PATH,self.epoch):persist_session(self.g.DATA_PATH,self.epoch,self.jar)
        return value

    def resources(self,identifier):
        self.identifier=identifier
        if (self.drm_system=='widevine' and self.device_platform=='windows' and self.hardware_level=='L3'
                and self.material=='Feature' and self.quality=='HD'):
            from .web_widevine import WebWidevine
            self.web=WebWidevine(self)
            return self.web.resources(identifier)
        wanted='PlaybackUrls,SubtitleUrls,ForcedNarratives,TransitionTimecodes'
        try:value=self.json(self.address('catalog/GetPlaybackResources',identifier,wanted),stage='playback_resources')
        except Exception as error:
            # A declared location refusal is not repaired by swapping this
            # account's credentials. Preserve Amazon's decision and exact URL.
            if isinstance(error,MediaServiceError) and error.category=='location':raise
            if self.auth!='device' or not has_authentication_cookie(self.jar) or not self.client.active():raise
            self.report({'event':'media_credential_retry','stage':'media_authorization','from':'device','to':'website',
                'same_account_and_title':True,'source_changed':False,'exception':exception_details(error),'result':'retry'})
            self.auth='website';self.device_type=WEB_DEVICE
            self.client.headers.clear();self.client.headers.update(session_headers(self.g.DATA_PATH))
            self.client.headers.update({'Accept':'application/json','Accept-Encoding':'identity'})
            self.client.headers.setdefault('User-Agent',self.g.configured_user_agent())
            self.client.jar=self.jar
            self.headers['User-Agent']=self.client.headers['User-Agent']
            value=self.json(self.address('catalog/GetPlaybackResources',identifier,wanted),stage='playback_resources_website')
        candidates=[];preferred=self.preferences.text('pref_host','Auto')
        playback=value.get('playbackUrls') or {}
        sets=playback.get('urlSets') or {};default=playback.get('defaultUrlSetId')
        if isinstance(sets,dict):
            ordered=([sets[default]] if default in sets else [])+[v for k,v in sets.items() if k!=default]
            for entry in ordered:
                manifest=(entry.get('urls') or {}).get('manifest',entry)
                if isinstance(manifest,dict) and https_address(manifest.get('url')):
                    candidates.append((manifest['url'],manifest.get('cdn') or entry.get('cdn','')))
        if not candidates:
            for entry in (value.get('audioVideoUrls') or {}).get('avCdnUrlSets') or []:
                for item in entry.get('avUrlInfoList') or []:
                    if https_address(item.get('url')):candidates.append((item['url'],entry.get('cdn','')))
        chosen=next((item for item in candidates if preferred!='Auto' and preferred.lower() in item[1].lower()),
            candidates[0] if candidates else None)
        if chosen is None:raise TransportFailure('Amazon returned no supported DASH manifest')
        subtitles=[]
        for key,forced in (('subtitleUrls',False),('forcedNarratives',True)):
            for item in value.get(key) or []:
                if isinstance(item,dict) and https_address(item.get('url')):
                    subtitles.append(dict(item,forced=forced))
        self.report({'event':'media_resources_ready','stage':'resource_selection','manifest_candidates':len(candidates),
            'requested_quality':self.quality,'hardware_level':self.hardware_level,
            'device_platform':self.device_platform,'credential_type':self.auth,
            'website_l1_profile':self.auth=='website' and self.hardware_level=='L1' and self.device_platform in ('android','webos'),
            'cdn':chosen[1],'subtitles':len(subtitles),'exact_provider_url':True,'result':'success'})
        return MediaResources(chosen[0],subtitles,(value.get('transitionTimecodes') or {}).get('skipElements') or [],chosen[1])

    def license(self,challenge):
        if self.web is not None:return self.web.license(challenge)
        if not challenge or len(challenge)>1024*1024:raise ValueError('Invalid CDM challenge size')
        payload=urlencode({'widevine2Challenge':base64.b64encode(challenge).decode('ascii')}).encode()
        value=self.json(self.address('catalog/GetPlaybackResources',self.identifier,'Widevine2License'),
            data=payload,headers={'Content-Type':'application/octet-stream'},stage='widevine_license')
        wrapped=value.get('widevine2License') or {}
        encoded=wrapped.get('license')
        if not isinstance(encoded,str) or not encoded:raise TransportFailure('Amazon did not grant a Widevine license. See Diagnostics.')
        self.report.secret(encoded)
        try:license_bytes=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError) as error:raise TransportFailure('Invalid provider license encoding') from error
        self.report({'event':'widevine_license_received','stage':'widevine_license','license_bytes':len(license_bytes),'result':'success'})
        return license_bytes

    def progress(self,event,position):
        if self.material=='Trailer':return None
        value=self.json(self.address('usage/UpdateStream',self.identifier,extra={'event':event,'timecode':str(round(max(0,position),3))}),stage='watch_progress')
        body=(value.get('message') or {}).get('body') or {}
        status=(value.get('message') or {}).get('statusCode')
        if status and status!='SUCCESS':raise TransportFailure('Amazon rejected playback progress')
        self.report({'event':'watch_progress_reported','stage':'watch_progress','playback_event':event,
            'position_seconds':round(position,2),'result':'success'})
        try:return max(10,min(300,float(body.get('statusCallbackIntervalSeconds',60))))
        except (TypeError,ValueError):return 60

    def close(self):
        try:
            if self.web is not None:self.web.close()
        finally:
            self.jar.clear();self.client.headers.clear();self.account.clear()
            self.headers.clear()
