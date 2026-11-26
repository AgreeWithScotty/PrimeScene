"""Amazon's session-bound web Widevine protocol, using Kodi's existing CDM.

Wire names come from Amazon's published WebPlayerSDK (2026-11-24).
Only URLs and feature envelopes actually returned for this title are used.
Certificates, envelopes, handoffs and license/challenge contents stay in memory.
"""
import base64
import json
import platform
import re
from time import monotonic
from urllib.parse import quote, urlencode, urlsplit
from urllib.error import HTTPError
import xbmc
from .http_transport import HttpTransport, TransportFailure, https_address, shape, server_errors
from .provider_documents import read_documents
from .regional_hosts import PRIME_WEB_HOSTS, AMAZON_WEB_HOSTS

PROTOCOL_REVISION = 'windows-web-widevine-10'


def walk(documents):
    stack = [(document, (), 0) for document in reversed(documents)]
    count = 0
    while stack:
        node, path, depth = stack.pop(); count += 1
        if count > 100000 or depth > 40:
            raise TransportFailure('Amazon playback state exceeds its structure limit')
        if isinstance(node, dict):
            yield node, path
            stack.extend((v, path + (k,), depth + 1) for k, v in reversed(list(node.items())) if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend((v, path + (i,), depth + 1) for i, v in reversed(list(enumerate(node))) if isinstance(v, (dict, list)))


class WebWidevine:
    def __init__(self, api):
        self.api = api; self.report = api.report
        self.envelope = self.handoff = self.certificate = self.title = ''
        self.server_certificate=''
        self.output_protection=None;self.hdcp_level=None;self.hdcp_protection_level=None
        self.origin = api.account.get('baseurl', '').rstrip('/')
        if not https_address(self.origin, PRIME_WEB_HOSTS | AMAZON_WEB_HOSTS):
            raise TransportFailure('The account has no verified Amazon website origin')
        self.headers = {'Origin': self.origin, 'Referer': self.origin + '/',
                        'Content-Type': 'text/plain;charset=UTF-8', 'Accept': 'application/json'}

    def address(self, route):
        if route not in ('prs/GetVodPlaybackResources', 'drm-vod/GetWidevineLicense'):
            raise ValueError('Invalid web playback operation')
        account = self.api.account
        return self.api.endpoint + '/playback/' + route + '?' + urlencode({
            'deviceID': account['deviceid'], 'deviceTypeID': self.api.device_type,
            'gascEnabled': str(self.api.catalog_context['gasc_enabled']).lower(),
            'marketplaceID': account['mid'], 'firmware': '1', 'titleId': self.title})

    def feature(self, identifier):
        client = HttpTransport(self.api.g.DATA_PATH, self.api.epoch, self.report,
            hosts=PRIME_WEB_HOSTS | {urlsplit(self.origin).hostname}, jar=self.api.jar, headers=self.api.client.headers)
        path = ('/detail/' if self.api.account.get('pv') else '/gp/video/detail/') + quote(identifier, safe='')
        reply = client.request(self.origin + path,
            headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}, stage='widevine_feature_envelope')
        final=urlsplit(reply.address)
        self.headers.update(Origin='https://'+final.hostname, Referer=reply.address)
        text = reply.data.decode(reply.headers.get_content_charset() or 'utf-8', errors='replace')
        documents, extraction, _ = read_documents(text, reply.headers.get_content_type())
        candidates = [];inventory=[]
        for node, location in walk(documents):
            envelope = node.get('playbackEnvelope')
            if isinstance(envelope, dict):
                envelope = next((envelope[k] for k in ('envelope', 'playbackEnvelope', 'value')
                                 if isinstance(envelope.get(k), str)), None)
            title = node.get('playbackID') or node.get('titleID')
            feature = node.get('videoMaterialType') == 'Feature' or node.get('isTrailer') is False
            # The selected ASIN detail response may key its above-the-fold
            # action by the canonical GTI instead. Require that exact main-page
            # action scope and the provider's matching key; recommendations and
            # trailer/secondary actions cannot establish this alias.
            canonical_page_alias = (isinstance(title, str) and title.startswith('amzn1.dv.gti.')
                and location[:5] == ('body','atf','state','action','atf')
                and len(location) > 6 and location[5] == title and location[6] == 'primaryActions')
            if (not feature or node.get('isTrailer') is True or node.get('videoMaterialType') == 'Trailer'
                    or not isinstance(envelope, str) or not 0 < len(envelope) <= 1024*1024
                    or not isinstance(title, str) or not (title == identifier or identifier in location or canonical_page_alias)
                    or 'secondaryActions' in location or 'autoplayTrailerHero' in location):
                continue
            if title != identifier and not title.startswith('amzn1.dv.gti.'):
                continue
            score = ('primaryActions' not in location, title != identifier,
                     node.get('entitlementType') not in ('PRIME', 'PURCHASE', 'RENTAL', 'SUBSCRIPTION', 'FREE'))
            ordinal=len(inventory)
            entitlement=node.get('entitlementType')
            inventory.append({'ordinal':ordinal,'primary_action':not score[0],
                'canonical_page_alias':canonical_page_alias,
                'exact_title_match':not score[1],'known_playback_entitlement':not score[2],
                'entitlement_type':entitlement if entitlement in ('PRIME','PURCHASE','RENTAL','SUBSCRIPTION','FREE') else 'other',
                'action_indices':[value for value in location if isinstance(value,int)][:8]})
            candidates.append((score, envelope, title, ordinal))
        candidates.sort(key=lambda item: item[0])
        self.report({'event': 'widevine_feature_parsed', 'stage': 'widevine_feature_envelope',
            'protocol_revision': PROTOCOL_REVISION, 'documents': len(documents), 'extraction': extraction,
            'feature_candidates': len(candidates), 'status': reply.status,
            'candidate_inventory':inventory[:20],
            'result': 'success' if candidates else 'failure'})
        if not candidates:
            raise TransportFailure('Amazon did not provide a feature playback envelope for the selected title. See Diagnostics.')
        best = candidates[0]
        # A page may offer several valid envelopes for the SAME feature (e.g.
        # distinct primary actions). Follow Amazon's primary action order;
        # distinct envelopes alone are not an ambiguous content identity.
        matching=[item for item in candidates if item[0]==best[0]]
        same_title=all(item[2]==best[2] for item in matching)
        self.report({'event':'widevine_feature_selected','stage':'widevine_feature_envelope',
            'protocol_revision':PROTOCOL_REVISION,'selected_ordinal':best[3],
            'equally_ranked_actions':len(matching),'same_title':same_title,
            'selection':'provider_primary_action_order','result':'success' if same_title else 'failure'})
        if not same_title:
            raise TransportFailure('Amazon returned conflicting feature title identities. See Diagnostics.')
        self.envelope, self.title = best[1:3]
        self.report.secret(self.envelope); self.report.secret(self.title)

    def body(self):
        # A resolution preference is not a security/HDCP claim. The actual
        # Widevine CDM supplies its own signed capabilities in the challenge.
        # Do not invent an HDCP level, L1 support, or a browser host signature.
        dimensions = {}
        for name, label in (('width', 'System.ScreenWidth'), ('height', 'System.ScreenHeight')):
            value = xbmc.getInfoLabel(label)
            if value.isdigit() and 0 < int(value) <= 16384: dimensions[name] = int(value)
        discriminators = {'operatingSystem': {'name': 'Windows', 'version': platform.version()},
            'nativeApplication': {'name': 'Kodi', 'version': xbmc.getInfoLabel('System.BuildVersion')},
            'hfrControlMode': 'Legacy'}
        if len(dimensions) == 2: discriminators['displayResolution'] = dimensions
        body={'globalParameters': {'deviceCapabilityFamily': 'WebPlayer',
            'playbackEnvelope': self.envelope, 'capabilityDiscriminators': discriminators},
            'auditPingsRequest': {}, 'widevineServiceCertificateRequest': {}, 'playbackDataRequest': {},
            'timedTextUrlsRequest': {'supportedTimedTextFormats': ['TTMLv2', 'DFXP']},
            'transitionTimecodesRequest': {},
            'vodPlaylistedPlaybackUrlsRequest': {'device': {
                'maxVideoResolution': '1080p', 'supportedStreamingTechnologies': ['DASH'],
                'streamingTechnologies': {'DASH': {
                    'bitrateAdaptations': ['CBR', 'CVBR'], 'codecs': ['H264'],
                    'drmKeyScheme': 'DualKey', 'drmType': 'Widevine', 'drmStrength': 'L10',
                    'dynamicRangeFormats': ['None'],
                    'edgeDeliveryAuthorizationSchemes': ['PVExchangeV1', 'Transparent'],
                    'fragmentRepresentations': ['ByteOffsetRange', 'SeparateFile'], 'frameRates': ['Standard'],
                    'stitchType': 'MultiPeriod', 'segmentInfoType': 'Base',
                    'timedTextRepresentations': ['NotInManifestNorStream', 'SeparateStreamInManifest'],
                    'trickplayRepresentations': ['NotInManifestNorStream'], 'variableAspectRatio': 'supported'}}}}}
        if self.hdcp_level:
            body['vodPlaylistedPlaybackUrlsRequest']['device']['hdcpLevel']=self.hdcp_level
        return body

    def prepare_output_protection(self):
        """Keep a real Windows HDCP session alive through the HD playback."""
        if self.output_protection is not None:return
        self.report({'event':'windows_hdcp_preparation','stage':'hd_output_protection',
            'requested_quality':self.api.quality,'result':'started'})
        try:
            from .windows_hdcp import HdcpSession
            self.output_protection=HdcpSession()
            before=self.output_protection.status()
            # Keep the verified type-1 capability: omitting the measured 2.2
            # capability caused Amazon to offer only 960x400 in a real HD test.
            desired=2
            result=self.output_protection.request(desired,self.api.g.monitor.waitForAbort)
            attempts=[dict(result,desired_protection=desired)]
            if result.get('result_code')==0 and result.get('hdcp_on'):self.hdcp_protection_level=desired
            if (result.get('result_code')==0 and result.get('effective_protection')==2
                    and result.get('hdcp_on') and result.get('type_enforcement')):
                # Windows type-1 enforcement requires HDCP2.2 or later. This
                # value is sent only after the OS reports successful enforcement.
                self.hdcp_level='2.2'
            elif desired==2 and result.get('result_code')==2 and not self.api.g.monitor.abortRequested():
                # Generic HDCP-on does not establish a specific version. Enable
                # the real protection, but never advertise an unmeasured1.4/2.2.
                result=self.output_protection.request(1,self.api.g.monitor.waitForAbort)
                attempts.append(dict(result,desired_protection=1))
                if result.get('result_code')==0 and result.get('hdcp_on'):self.hdcp_protection_level=1
            if self.api.g.monitor.abortRequested():
                self.report({'event':'windows_hdcp_prepared','stage':'hd_output_protection',
                    'windows_attempts':attempts,'result':'cancelled'})
                raise InterruptedError('Windows output protection preparation cancelled')
            self.report({'event':'windows_hdcp_prepared','stage':'hd_output_protection',
                'before':before,'windows_attempts':attempts,
                'windows_enforced_hdcp_level':self.hdcp_level,
                'required_protection':self.hdcp_protection_level,
                'advertised_hdcp_level':self.hdcp_level,
                'cdm_security_level_changed':False,'session_held_for_playback':True,
                'result':'success' if result.get('result_code')==0 else 'unavailable'})
        except InterruptedError:raise
        except Exception as error:
            from .prime_diagnostics import exception_details
            if self.api.g.monitor.abortRequested():
                self.report({'event':'windows_hdcp_prepared','stage':'hd_output_protection',
                    'exception':exception_details(error),'result':'cancelled'})
                raise InterruptedError('Windows output protection preparation cancelled') from error
            self.hdcp_level=None
            self.report({'event':'windows_hdcp_prepared','stage':'hd_output_protection',
                'exception':exception_details(error),'advertised_hdcp_level':None,'result':'unavailable'})

    def verify_output_protection(self,record=False):
        if self.hdcp_protection_level is None:return
        try:state=self.output_protection.current_status()
        except Exception as error:
            from .prime_diagnostics import exception_details
            self.report({'event':'windows_hdcp_verified','stage':'hd_output_protection',
                'exception':exception_details(error),'result':'failure'})
            raise
        protected=state['hdcp_on'] and (self.hdcp_protection_level==1 or state['type_enforcement'])
        if record or not protected:
            self.report({'event':'windows_hdcp_verified','stage':'hd_output_protection',
                'windows_state':state,'windows_enforced_hdcp_level':self.hdcp_level,
                'advertised_hdcp_level':self.hdcp_level,
                'required_protection':self.hdcp_protection_level,
                'result':'success' if protected else 'failure'})
        if not protected:
            error=TransportFailure('Windows could not keep the negotiated HDCP output protection active')
            error.stage='hd_output_protection';raise error

    def request(self, operation, body, stage, required):
        from .media_resources import MediaServiceError
        self.report({'event':'widevine_playback_catalog_selected','stage':stage,
            'protocol_revision':PROTOCOL_REVISION,'playback_catalog':self.api.catalog_context,
            'website_host':urlsplit(self.origin).hostname,'result':'prepared'})
        if operation=='prs/GetVodPlaybackResources':
            device=body['vodPlaylistedPlaybackUrlsRequest']['device']
            self.report({'event':'widevine_web_requested_capabilities','stage':stage,
                'protocol_revision':PROTOCOL_REVISION,'requested_quality':self.api.quality,
                'max_video_resolution':device['maxVideoResolution'],'drm_type':'Widevine',
                'requested_codecs':device['streamingTechnologies']['DASH']['codecs'],
                'drm_strength':device['streamingTechnologies']['DASH'].get('drmStrength'),
                'drm_strength_supplied':'drmStrength' in device['streamingTechnologies']['DASH'],
                'hdcp_level':device.get('hdcpLevel'),
                'hdcp_source':'windows_enforced_type1' if self.hdcp_level else 'unmeasured',
                'windows_enforced_hdcp_level':self.hdcp_level,
                'cdm_request_profile':self.api.hardware_level,'result':'prepared'})
        try:
            reply = self.api.client.request(self.address(operation), data=json.dumps(body).encode('utf-8'),
                headers=self.headers, stage=stage)
        except HTTPError as error:
            codes=getattr(error, 'server_error_codes', ())
            if codes:raise MediaServiceError(codes, error.code, stage) from error
            raise
        try: value = reply.json()
        except (ValueError, UnicodeError) as error:
            raise TransportFailure('Amazon returned an invalid web playback response') from error
        if not isinstance(value, dict): raise TransportFailure('Invalid Amazon web playback protocol')
        # Register opaque response values BEFORE reporting any structure.
        for node, _ in walk([value]):
            for key in ('license', 'sessionHandoffToken', 'encodedServiceCertificate', 'sessionToken'):
                if isinstance(node.get(key), str): self.report.secret(node[key])
        result = value.get(required)
        failed = bool(value.get('error') or value.get('errors') or not isinstance(result, dict)
                      or result.get('error') or result.get('errors') or result.get('ok') is False)
        if required=='vodPlaylistedPlaybackUrls' and isinstance(result, dict) and not isinstance(result.get('result'), dict):
            failed=True
        self.report({'event': 'widevine_web_response', 'stage': stage, 'status': reply.status,
            'protocol_revision': PROTOCOL_REVISION, 'requested_quality': self.api.quality,
            'protocol_structure': shape(value), 'server_error_codes': server_errors(value),
            'result': 'failure' if failed else 'success'})
        if failed:
            raise MediaServiceError(server_errors(value), reply.status, stage)
        from .session_store import persist_session
        if self.api.client.active():persist_session(self.api.g.DATA_PATH, self.api.epoch, self.api.jar)
        return value

    def resources(self, identifier):
        from .media_resources import MediaResources
        self.prepare_output_protection()
        self.feature(identifier)
        value = self.request('prs/GetVodPlaybackResources', self.body(), 'widevine_web_resources',
                             'vodPlaylistedPlaybackUrls')
        result = (value['vodPlaylistedPlaybackUrls'].get('result') or {})
        playback = result.get('playbackUrls') or {}
        playlist = playback.get('intraTitlePlaylist') or []
        if len(playlist) != 1 or playlist[0].get('type') != 'Main':
            raise TransportFailure('Amazon returned an unsupported web playback playlist')
        main = playlist[0]; choices = main.get('urls') or []
        order = (playback.get('failoverPolicy') or {}).get('cdnOrder') or []
        preferred = self.api.preferences.text('pref_host', 'Auto')
        choices = [item for item in choices if isinstance(item, dict) and https_address(item.get('url'))]
        choices.sort(key=lambda item: (preferred != 'Auto' and preferred.lower() not in str(item.get('cdn', '')).lower(),
            order.index(item.get('cdn')) if item.get('cdn') in order else len(order)))
        self.handoff = (value.get('sessionization') or {}).get('sessionHandoffToken')
        self.certificate = ((value.get('widevineServiceCertificate') or {}).get('result') or {}).get('encodedServiceCertificate', '')
        if not choices or not isinstance(self.handoff, str) or not self.handoff:
            raise TransportFailure('Amazon returned incomplete web playback session data')
        if self.certificate:
            try: certificate = base64.b64decode(self.certificate, validate=True)
            except (ValueError, TypeError) as error: raise TransportFailure('Invalid Widevine service certificate') from error
            if not 0 < len(certificate) <= 65536: raise TransportFailure('Invalid Widevine service certificate size')
            from .widevine_certificate import cdm_certificate
            payload,facts=cdm_certificate(certificate)
            self.server_certificate=base64.b64encode(payload).decode('ascii')
            self.report.secret(self.server_certificate)
            self.report(dict(facts,event='widevine_server_certificate_prepared',stage='widevine_certificate',
                protocol_revision=PROTOCOL_REVISION,result='prepared'))
        timed = ((value.get('timedTextUrls') or {}).get('result') or {})
        subtitles = []
        for key, forced in (('subtitleUrls', False), ('forcedNarrativeUrls', True)):
            subtitles.extend(dict(item, forced=forced) for item in timed.get(key) or []
                             if isinstance(item, dict) and https_address(item.get('url')))
        transitions = []
        mapping = {'SKIP_INTRO': 'INTRO', 'SKIP_RECAP': 'RECAP', 'SKIP_INTRO_RECAP': 'INTRO_RECAP'}
        for event in ((value.get('transitionTimecodes') or {}).get('result') or {}).get('events') or []:
            if event.get('eventType') in mapping and isinstance(event.get('endTimeMs'), (int, float)):
                transitions.append({'elementType': mapping[event['eventType']],
                    'startTimecodeMs': event.get('startTimeMs') or 0, 'endTimecodeMs': event['endTimeMs']})
        declared = (main.get('manifestMetadata') or {}).get('videoQuality')
        self.report({'event': 'widevine_web_resources_ready', 'stage': 'resource_selection',
            'protocol_revision': PROTOCOL_REVISION, 'requested_quality': self.api.quality,
            'provider_declared_quality': declared if declared in ('SD', 'HD', 'UHD') else None,
            'manifest_candidates': len(choices), 'certificate_present': bool(self.certificate),
            'session_handoff_present': bool(self.handoff), 'exact_provider_url': True,
            'subtitles': len(subtitles), 'result': 'success'})
        return MediaResources(choices[0]['url'], subtitles, transitions, choices[0].get('cdn', ''))

    def license(self, challenge):
        if not challenge or len(challenge) > 1024*1024: raise ValueError('Invalid Widevine challenge size')
        self.verify_output_protection(record=True)
        certificate_request=challenge==b'\x08\x04'
        self.report({'event':'widevine_cdm_message','stage':'widevine_challenge',
            'protocol_revision':PROTOCOL_REVISION,'challenge_bytes':len(challenge),
            'message_type':'service_certificate_request' if certificate_request else
                'license_request' if challenge[:2]==b'\x08\x01' else 'other_cdm_message',
            'certificate_available':bool(self.certificate),'requested_quality':self.api.quality,'result':'observed'})
        if certificate_request:
            # Amazon's LicenseAcquirerV2 resolves this exact two-byte CDM
            # message with its cached service certificate, not a license call.
            # ISA can send it even when a certificate property was supplied.
            if not self.certificate:raise TransportFailure('Amazon supplied no certificate for the Widevine CDM request')
            certificate=base64.b64decode(self.certificate,validate=True)
            self.report({'event':'widevine_service_certificate_delivered','stage':'widevine_certificate',
                'certificate_bytes':len(certificate),'license_requested':False,'result':'success'})
            return certificate
        try:
            from .widevine_diagnostics import challenge_facts
            self.report(dict(challenge_facts(challenge),event='widevine_cdm_proof_observed',
                stage='widevine_client_proof',protocol_revision=PROTOCOL_REVISION,result='observed'))
        except Exception as error:
            from .prime_diagnostics import exception_details
            self.report({'event':'widevine_cdm_proof_observed','stage':'widevine_client_proof',
                'protocol_revision':PROTOCOL_REVISION,'exception':exception_details(error),
                'challenge_altered':False,'result':'unavailable'})
        value = self.request('drm-vod/GetWidevineLicense', {'includeHdcpTestKey': True,
            'playbackEnvelope': self.envelope, 'sessionHandoffToken': self.handoff,
            'licenseChallenge': base64.b64encode(challenge).decode('ascii')},
            'widevine_web_license', 'widevineLicense')
        wrapped = value['widevineLicense']; encoded = wrapped.get('license')
        if not isinstance(encoded, str) or not encoded: raise TransportFailure('Amazon did not grant a web Widevine license')
        try: data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error: raise TransportFailure('Invalid web Widevine license encoding') from error
        if not 0 < len(data) <= 1024*1024: raise TransportFailure('Invalid web Widevine license size')
        restrictions = []
        for item in (wrapped.get('metadata') or {}).get('keyMetadata') or []:
            safe = {}
            for key in ('keyType', 'cdmUsageLevel'):
                enum = item.get(key)
                if isinstance(enum, str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}', enum): safe[key] = enum
            hdcp = (item.get('hdcp') or {}).get('hdcp')
            if isinstance(hdcp, (int, float)) or isinstance(hdcp, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', hdcp):
                safe['hdcp'] = hdcp
            if safe: restrictions.append(safe)
        self.report({'event': 'widevine_web_license_received', 'stage': 'widevine_web_license',
            'license_bytes': len(data), 'key_restrictions': restrictions[:30],
            'include_hdcp_test_key': True, 'result': 'success'})
        return data

    def close(self):
        started=monotonic()
        try:
            if self.output_protection is not None:
                self.output_protection.close()
                self.report({'event':'windows_hdcp_closed','stage':'hd_output_protection',
                    'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
        except Exception as error:
            from .prime_diagnostics import exception_details
            self.report({'event':'windows_hdcp_closed','stage':'hd_output_protection',
                'exception':exception_details(error),'result':'failure'})
            raise
        finally:
            self.output_protection=None;self.hdcp_level=None;self.hdcp_protection_level=None
            self.envelope = self.handoff = self.certificate = self.title = ''
            self.server_certificate=''
