#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import re
import requests
from timeit import default_timer as timer
from bs4 import BeautifulSoup
from copy import deepcopy
from urllib.parse  import urlencode, quote_plus, urlparse, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import xbmcgui

from .common import Globals, Settings, sleep, get_key, findKey
from .account_auth import credentials
from .logging import Log, WriteLog, LogJSON
from .l10n import getString
from .configs import getConfig, writeConfig
from .metrics import addNetTime
from .prime_diagnostics import DiagnosticLog, exception_details, route

_session = None
_prime_scene_epoch = None
_g = Globals()
_s = Settings()


def _Error(data):
    code = data['errorCode'].lower()
    Log(f"{data['message']} ({code}) ", Log.ERROR)
    if 'invalidrequest' in code:
        return getString(30204)
    elif 'noavailablestreams' in code:
        return getString(30205)
    elif 'notowned' in code:
        return getString(30206)
    elif 'invalidgeoip' in code or 'dependency' in code:
        return getString(30207)
    elif 'temporarilyunavailable' in code:
        return getString(30208)
    else:
        return f"{data['message']} ({code}) "


def getUA(blacklist=False):
    Log('Switching UserAgent')
    UAlist = json.loads(getConfig('UAlist', json.dumps([])))
    UAcur = ''

    if blacklist:
        UAcur = getConfig('UserAgent')
        UAlist = [i for i in UAlist if i not in UAcur]
        writeConfig('UAlist', json.dumps(UAlist))
        Log(f'UA: {UAcur} blacklisted')

    if not UAlist:
        Log('Loading list of common UserAgents')
        result = getURL('https://microlink.io/user-agents.json')
        sorted_ua = result.get('user', {})
        UAlist = [ua for ua in sorted_ua if 'windows' in ua.lower() and ua not in UAcur]
        if not UAlist:
            UAlist = ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36']
        writeConfig('UAlist', json.dumps(UAlist))
    writeConfig('UserAgent', UAlist[0])
    Log('Using UserAgent: ' + UAlist[0])
    return


def mobileUA(content):
    soup = BeautifulSoup(content, 'html.parser')
    res = soup.find('html')
    res = res.get('class', '') if res else ''
    return True if 'a-mobile' in res or 'a-tablet' in res else False


def _get_session(retry=True):
    global _session

    if _session is not None and retry:
        return _session

    session = requests.Session()
    retries = Retry(
        total=2 if retry else 0,
        connect=1 if retry else 0,
        read=1 if retry else 0,
        status=1 if retry else 0,
        backoff_factor=0.5,
        # Do not automatically replay throttled requests.
        status_forcelist=[500, 502, 503, 504],
        respect_retry_after_header=False,
        raise_on_status=False
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    if retry:
        _session = session
    return session


def getURL(url, useCookie=False, silent=False, headers=None, rjson=True, check=False, postdata=None, binary=False, allow_redirects=True):
    getURL.lastResponseCode = 0
    retval = {} if rjson else ''
    from .account_state import account_epoch, current_account
    from . import configs
    global _prime_scene_epoch
    if _prime_scene_epoch is None:
        _prime_scene_epoch = account_epoch(_g.DATA_PATH)
    request_epoch = _prime_scene_epoch
    if configs._prime_scene_epoch is None:
        configs._prime_scene_epoch = request_epoch
    if not current_account(_g.DATA_PATH, request_epoch, allow_signed_out=not bool(useCookie)):
        return retval
    method = 'POST' if postdata is not None else 'HEAD' if check else 'GET'
    report = DiagnosticLog(_g.DATA_PATH, _g.addon.getAddonInfo('version'), operation='backend_request',
        active=lambda: current_account(_g.DATA_PATH, request_epoch, allow_signed_out=not bool(useCookie)))
    report({'event': 'backend_request_started', 'stage': 'request', 'method': method,
            'address': route(url), 'authenticated_request': bool(useCookie)})
    headers = {} if not headers else deepcopy(headers)
    session = _get_session(not check)

    if useCookie:
        cj = credentials() if isinstance(useCookie, bool) else useCookie
        if isinstance(cj, bool):
            report({'event': 'backend_credentials_missing', 'stage': 'credential_selection', 'result': 'failure'})
            return retval
        elif isinstance(cj, dict):
            headers.update(cj)
        else:
            session.cookies.update(cj)

    if (not silent) or _s.logging:
        dispurl = json.dumps(route(url))
        Log(f"{'check' if check else method.lower()}URL: {dispurl}")

    def_headers = {'User-Agent': getConfig('UserAgent'),
                   'Accept-Language': _g.userAcceptLanguages,
                   'Accept-Encoding': 'gzip, deflate, br',
                   'Upgrade-Insecure-Requests': '1',
                   'Connection': 'keep-alive'}

    if 'amazon' in url or 'primevideo' in url:
        for k, v in def_headers.items():
            if k not in headers:
                headers[k] = v

    """
    # This **breaks** redirections. Host header OVERRIDES the host in the URL:
    # if the URL is web.eu, but the Host is web.com, request will fetch web.com
    if 'Host' not in headers:
        headers['Host'] = host
    """
    if '/api/' in url:
        headers['X-Requested-With'] = 'XMLHttpRequest'

    r = None
    try:
        starttime = timer()
        r = session.request(method, url, data=postdata, verify=_s.ssl_verif, stream=True, allow_redirects=allow_redirects,
                            headers=headers, timeout=(10, 20))
        if not current_account(_g.DATA_PATH, request_epoch, allow_signed_out=not bool(useCookie)):
            return retval
        getURL.lastResponseCode = r.status_code  # Set last response code
        report({'event': 'backend_response_received', 'stage': 'response', 'status': r.status_code,
            'address': route(r.url), 'content_type': r.headers.get('Content-Type'),
            'server_request_id': r.headers.get('x-amzn-requestid') or r.headers.get('x-amz-rid'),
            'redirect_chain': [{'status': hop.status_code, 'address': route(hop.url),
                'location': route(hop.headers.get('Location',''))} for hop in r.history[:12]],
            'network_ms': round((timer()-starttime)*1000)})
        response = 'OK' if 400 > r.status_code >= 200 else ''
        if not check:
            response = r.content if binary else r.json() if rjson else r.text
            if _s.log_http or r.status_code >= 400:
                from .public_catalog import _protocol_shape
                from .auth_transport import error_details
                report({'event': 'backend_response_structure', 'stage': 'response_parse',
                    'status': r.status_code, 'bytes': len(r.content),
                    'format': 'json' if isinstance(response,(dict,list)) else 'binary' if binary else 'text',
                    'protocol_structure': _protocol_shape(response) if isinstance(response,(dict,list)) else [],
                    'server_errors': error_details(response,report) if isinstance(response,(dict,list)) else []})
        if useCookie and isinstance(response, str) and 'auth-cookie-warning-message' in response:
            report({'event': 'backend_credentials_rejected', 'stage': 'session_check', 'result': 'failure'})
            Log('Cookie invalid', Log.ERROR)
            _g.dialog.notification(_g.__plugin__, getString(30266), xbmcgui.NOTIFICATION_ERROR)
            return retval
        if useCookie and not isinstance(useCookie, dict):
            from .accounts import saveUserCookies
            saveUserCookies(session.cookies, request_epoch=request_epoch)
    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
            requests.exceptions.HTTPError,
            requests.packages.urllib3.exceptions.InsecurePlatformWarning,
            ValueError) as e:
        eType = e.__class__.__name__
        report({'event': 'backend_request_failed', 'stage': 'request_or_decode',
            'status': r.status_code if r is not None else None,
            'exception': exception_details(e), 'result': 'failure'})
        Log('Request failed: ' + eType, Log.ERROR)
        if 'InsecurePlatformWarning' in eType:
            Log('Using an outdated SSL module.', Log.ERROR)
            _g.dialog.ok('SSL module outdated', 'The SSL module for Python is outdated.',
                         'You can find a Linux guide on how to update Python and its modules for Kodi here: https://goo.gl/CKtygz',
                         'Additionally, follow this guide to update the required modules: https://goo.gl/ksbbU2')
            exit()
        return retval
    finally:
        if r is not None:
            r.close()
        if check:
            session.close()
    res = response
    duration = timer()
    duration -= starttime
    addNetTime(duration)
    report({'event': 'backend_request_complete', 'stage': 'complete',
        'elapsed_ms': round(duration*1000), 'status': getURL.lastResponseCode,
        'result': 'success' if 200 <= getURL.lastResponseCode < 400 else 'failure'})
    Log(f'Download Time: {duration}', Log.DEBUG)
    return res


def getURLData(mode, asin, retformat='json', devicetypeid=_g.dtid_web, version=2, firmware='1', opt='', extra=False,
               useCookie=False, retURL=False, vMT='Feature', dRes='PlaybackUrls,SubtitleUrls,ForcedNarratives',
               proxyEndpoint=None, silent=False):

    playback_req = 'PlaybackUrls' in dRes or 'Widevine2License' in dRes
    url = _g.ATVUrl + '/cdp/' + mode
    url += '?asin=' + asin
    url += '&deviceTypeID=' + devicetypeid
    url += '&firmware=' + firmware
    url += '&deviceID=' + _g.deviceID
    url += '&marketplaceID=' + _g.MarketID
    url += '&format=' + retformat
    url += '&version=' + str(version)
    url += '&gascEnabled=' + str(_g.UsePrimeVideo).lower()
    url += "&subtitleFormat=TTMLv2" if 'SubtitleUrls' in dRes else ''
    url += '&operatingSystemName=Windows' if playback_req and (_g.platform & _g.OS_ANDROID or _g.platform & _g.OS_WEBOS) and devicetypeid == _g.dtid_web and _s.wvl1_device else ''  # cookie auth on android
    if extra:
        url += '&resourceUsage=ImmediateConsumption&consumptionType=Streaming&deviceDrmOverride=CENC' \
               '&deviceStreamingTechnologyOverride=DASH&deviceProtocolOverride=Https' \
               '&deviceBitrateAdaptationsOverride=CVBR%2CCBR&audioTrackId=all'
        url += '&languageFeature=MLFv2'  # Audio Description tracks
        url += '&videoMaterialType=' + vMT
        url += '&desiredResources=' + dRes
        url += '&supportedDRMKeyScheme=DUAL_KEY' if playback_req else ''
        if _s.wvl1_device:
            url += '&deviceVideoCodecOverride=H264' + (',H265' if _s.use_h265 else '')
            url += '&deviceHdrFormatsOverride=' + supported_hdr()
            url += '&deviceVideoQualityOverride=' + ('UHD' if _s.enable_uhd else 'HD')
        elif playback_req and vMT in ('Feature', 'LiveStreaming') and not _s.l3_request_hd:
            # Since Oct 2026 Amazon refuses the HD Widevine license for devices without L1 (software CDM),
            # the license response then holds no license and IS.A retries endlessly. Request SD for both
            # the manifest and the license, so the keys Amazon grants match the streams in the manifest.
            url += '&deviceVideoQualityOverride=SD'

    if retURL:
        return url
    url += opt
    data = getURL(url if not proxyEndpoint else f"http://{getConfig('proxyaddress')}/{proxyEndpoint}/{quote_plus(url)}",
                  useCookie=useCookie, postdata='', silent=silent)
    if data:
        if 'error' in data.keys():
            return False, _Error(data['error'])
        elif 'AudioVideoUrls' in data.get('errorsByResource', ''):
            return False, _Error(data['errorsByResource']['AudioVideoUrls'])
        elif 'PlaybackUrls' in data.get('errorsByResource', ''):
            return False, _Error(data['errorsByResource']['PlaybackUrls'])
        else:
            return True, data
    return False, 'HTTP Error'


def supported_hdr():
    hdr = []
    if _s.enable_dovi == 'true':
        hdr.append('DolbyVision')
    if _s.enable_hdr10 == 'true':
        hdr.append('Hdr10')
    if len(hdr) == 0:
        hdr.append('None')
    return ','.join(hdr)


def getATVData(pg_mode, query='', version=2, useCookie=False, site_id=None):
    # ids: A28RQHJKHM2A2W - ps3 / AFOQV1TK6EU6O - ps4 / A1IJNVP3L4AY8B - samsung / A2E0SNTXJVT7WK - firetv1 /
    #      ADVBD696BHNV5 - montoya / A3VN4E5F7BBC7S - roku / A1MPSLFC7L5AFK - kindle / A2M4YX06LWP8WI - firetv2 /
    # PrimeVideo web device IDs:
    #      A63V4FRV3YUP9 / SILVERLIGHT_PC, A2G17C9GWLWFKO / SILVERLIGHT_MAC, AOAGZA014O5RE / HTML5
    # TypeIDs = {'GetCategoryList': 'firmware=fmw:15-app:1.1.23&deviceTypeID=A1MPSLFC7L5AFK',
    #            'GetSimilarities': 'firmware=fmw:15-app:1.1.23&deviceTypeID=A1MPSLFC7L5AFK',
    #                        'All': 'firmware=fmw:22-app:3.0.211.123001&deviceTypeID=A43PXU4ZN2AL1'}
    #                        'All': 'firmware=fmw:045.01E01164A-app:4.7&deviceTypeID=A3VN4E5F7BBC7S'}
    # TypeIDs = {'All': 'firmware=fmw:17-app:2.0.45.1210&deviceTypeID=A2RJLFEH0UEKI9'}
    _TypeIDs = {True: ['firmware=fmw:28-app:5.2.3&deviceTypeID=A3SSWQ04XYPXBH', 'firmware=fmw:26-app:3.0.265.20347&deviceTypeID=A1S15DUFSI8AUG',
                       'firmware=default&deviceTypeID=A1FYY15VCM5WG1'],
                False: ['firmware=fmw:28-app:5.2.3&deviceTypeID=A1C66CX2XD756O', 'firmware=fmw:26-app:3.0.265.20347&deviceTypeID=A12GXV8XMS007S',
                        'firmware=fmw:045.01E01164A-app:4.7&deviceTypeID=A3VN4E5F7BBC7S']}

    if '?' in query:
        query = query.split('?')[1]
    if query:
        query = '&IncludeAll=T&AID=1&' + query.replace('HideNum=T', 'HideNum=F')
    pg_mode = pg_mode.split('_')[0]
    if '/' not in pg_mode:
        pg_mode = 'catalog/' + pg_mode
    rem_pos = False if re.search('(?i)rolluptoseason=t|contenttype=tvseason', query) else _s.tld_episode_thumbnails

    if 'asinlist=&' not in query:
        titles = 0
        ids = len(_TypeIDs[rem_pos]) - 1
        att = 0
        while titles == 0 and att <= ids:
            deviceTypeID = _TypeIDs[rem_pos][att]
            parameter = f'{deviceTypeID}&deviceID={_g.deviceID}&format=json&version={version}&formatVersion=3&marketplaceId={_g.MarketID}'
            if site_id:
                parameter += '&id=' + site_id
            jsondata = getURL(f'{_g.ATVUrl}/cdp/{pg_mode}?{parameter}{query}', useCookie=useCookie)
            if not jsondata:
                return False
            if jsondata['message']['statusCode'] != "SUCCESS":
                Log('Error Code: ' + jsondata['message']['body']['code'], Log.ERROR)
                return None
            titles = len(jsondata['message']['body'].get('titles'))
            att += 1 if 'StartIndex=0' in query else ids + 1

        result = jsondata['message']['body']
        return _sortedResult(result, query) if 'asinlist' in query else result
    return {}


def _sortedResult(result, query):
    asinlist = parse_qs(query.upper(), keep_blank_values=True)['ASINLIST'][0].split(',')
    sorteditems = ['empty'] * len(asinlist)

    for item in result.get('titles', []):
        for index, asin in enumerate(asinlist):
            if asin in str(item):
                sorteditems[index] = item
                break
    if sorteditems.count('empty') > 0:
        Log(f"ASINs {[asinlist[n] for n, i in enumerate(sorteditems) if i == 'empty']} not found")

    result['titles'] = sorteditems
    return result


def FQify(URL):
    """ Makes sure to provide correct fully qualified URLs """
    base = _g.BaseUrl
    if '://' in URL:  # FQ
        return URL
    elif URL.startswith('//'):  # Specified domain, same schema
        return base.split(':')[0] + ':' + URL
    elif URL.startswith('/'):  # Relative URL
        return base + URL
    else:  # Hope and pray we never reach this ¯\_(ツ)_/¯
        return base + '/' + URL


def GrabJSON(url, postData=None):
    """ Extract JSON objects from HTMLs while keeping the API ones intact """
    from html import unescape

    def Unescape(text):
        if not text.startswith('{&#34;'):
            text = text.replace('&#34;', '\\"')
        text = unescape(text)

        try:
            text = text.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

        return text

    def Merge(o, n, keys=[]):
        """ Merge JSON objects with multiple multi-level collisions """
        if (not n) or (o == n):  # Nothing to do
            return
        elif (type(n) == list) or (type(n) == set):  # Insert into list/set
            for item in n:
                if item not in o:
                    if type(n) == list:
                        o.append(item)
                    else:
                        o.add(item)
        elif type(n) == dict:
            for k in list(n):  # list() instead of .keys() to avoid py3 iteration errors
                if k not in o:
                    o[k] = n[k]  # Insert into dictionary
                else:
                    Merge(o[k], n[k], keys + [k])  # Recurse
        else:
            # Ignore reporting collisions on metadata we don't care about
            if keys not in [
                ['csrfToken'],
                ['metadata', 'availability', 'description'],
                ['metadata', 'availability', 'severity'],
            ]:
                k = ' > '.join(keys)
                if _s.json_dump_collisions:
                    LogJSON(n, k, optionalName='CollisionNew')
                    LogJSON(o, k, optionalName='CollisionOld')
                Log('Collision detected during JSON objects merging{}, overwriting and praying (type: {})'.format(
                    ' on key “{}”'.format(k) if keys else '',
                    type(n)
                ), Log.WARNING)
            o = n

    def Prune(d):
        """ Prune some commonly found sensitive info from JSON response bodies """
        if not d:
            return

        l = d
        if isinstance(l, dict):
            for k in list(l):  # list() instead of .keys() to avoid py3 iteration errors
                if k == 'strings':
                    l[k] = {s: l[k][s] for s in ['AVOD_DP_season_selector'] if s in l[k]}
                if (not l[k]) or (k in ['context', 'params', 'playerConfig', 'refine']):
                    del l[k]
            l = d.values()
        for v in l:
            if isinstance(v, dict) or isinstance(v, list):
                Prune(v)

    def do(url, postData):
        GrabJSON.runs = True
        """ Wrapper to facilitate logging """
        headers = {'accept': 'application/json', "X-Requested-With": "XMLHttpRequest"}
        if re.match(r'/(?:gp/video/)?search(?:Default)?/', url):
            up = urlparse(url)
            qs = parse_qs(up.query)
            if 'from' in list(qs):  # list() instead of .keys() to avoid py3 iteration errors
                qs['startIndex'] = qs['from']
                del qs['from']
            up = up._replace(query=urlencode([(k, v) for k, l in qs.items() for v in l]))
            url = up.geturl()
        if '/api/storefront' in url:
            postData = ""
        if '/api/' in url:
            headers = None
        r = getURL(FQify(url), silent=True, useCookie=True, rjson=False, postdata=postData, headers=headers)
        if not r:
            return None
        r = r.strip()
        if '/api/' in url:
            o = json.loads(Unescape(r))
            if _s.json_dump_raw:
                Prune(o)
        else:
            m = HTMLdoc(r) if '<!doctype html>' in r else json.loads(r)
            if m is None:
                return None
            if ('widgets' in m) and ('Storefront' in m['widgets']):
                m = m['widgets']['Storefront']
            elif 'body' in m or 'props' in m or 'init' in m:
                bodies = findKey('body', m)
                sw = findKey('siteWide', m)
                m = m.get('props', m.get('init', m))
                if len(bodies) > 0:
                    if 'bodyStart' in sw and len(sw['bodyStart']) > 0:
                        for bs in sw['bodyStart']:
                            if 'name' in bs and bs['name'] == 'navigation-bar' and 'props' in bs:
                                m = bs['props']
                    else:
                        m = bodies['sitewide'].get('sitewide-navigation-bar', {}) if 'sitewide' in bodies else {}
                        if isinstance(bodies, dict):
                            bodies = [bodies]

                    if isinstance(bodies, list):
                        for body in bodies:
                            body = body.get('props', body)
                            for p in ['atf', 'btf', 'landingPage', 'browse', 'search', 'categories', 'genre']:
                                Merge(m, body.get(p, {}))
                            for p in ['content', 'containers', 'pagination']:
                                Merge(m, {p: body.get(p, {})})

                if _s.json_dump_raw:
                    # Prune useless/sensitive info
                    for k in list(m):  # list() instead of .keys() to avoid py3 iteration errors
                        if (not m[k]) or (k in ['copyright', 'links', 'logo', 'params', 'playerConfig', 'refine']):
                            del m[k]
                    if 'state' in m:
                        st = m['state']
                        for k in list(st):  # list() instead of .keys() to avoid py3 iteration errors
                            if not st[k]:
                                del st[k]
                            elif k in ['features', 'customerPreferences']:
                                del st[k]
            else:
                m = {}
            # Prune sensitive context info and merge into o
            if _s.json_dump_raw:
                Prune(m)
            o = m
        return o if o else None

    def HTMLdoc(r):
        matches = BeautifulSoup(r, 'html.parser').find_all('script', {'type': re.compile('(?:text/template|application/json)')})
        if not matches:
            matches = Captcha(r)
            if not matches:
                Log('No JSON objects found in the page', Log.ERROR)
                return None

        # Create a single object containing all the data from the multiple JSON objects in the page
        o = {}
        for m in matches:
            if not (m.id is None or 'hydration-data' in m.id):
                continue
            m = json.loads(Unescape(m.string.strip()))
            Merge(o, m)
        return o

    def Captcha(content):
        from .prime_signin import SignInDocument
        from .prime_login import finish_security_challenge
        document = SignInDocument(content)
        if document.forms or document.captcha_required or document.approval_required:
            if not finish_security_challenge(FQify(url), content, _prime_scene_epoch):
                return []
            content = getURL(FQify(url), useCookie=True, rjson=False, postdata=postData)
        return BeautifulSoup(content, 'html.parser').find_all('script',
            attrs={'type': re.compile('(?:text/template|application/json)')})

    if hasattr(GrabJSON, 'runs') and GrabJSON.runs:
        while GrabJSON.runs:
            sleep(1)
    try:
        j = do(url, postData)
    finally:
        GrabJSON.runs = False
    LogJSON(j, url)
    return j


def LocaleSelector():
    from .l10n import datetimeParser
    from .common import get_user_lang
    cj = credentials()
    if not cj:
        exit()

    if _g.UsePrimeVideo or _s.data_source == 1:
        from .accounts import loadUser
        langs = [(k, v['language']) for k, v in datetimeParser.items() if 'language' in v]
        l = get_user_lang(cj)
        presel = [i for i, x in enumerate(langs) if x[0] == l]
        '''
        resp = GrabJSON(_g.BaseUrl + '/api/getLanguageSettingsPage?subPage=language&widgetArgs=%7B%7D')
        for widget in resp['widgets']:
            if widget['widgetType'] == 'languages':
                langs = [(l['locale'], l['text'], l.get('selected') is not None) for l in widget['content']['languages']]
        '''
    else:
        # TLDs doesn't store locale in cookie by default
        markup = getURL(_g.BaseUrl + '/customer-preferences/edit', useCookie=True, rjson=False)
        page = BeautifulSoup(markup, 'html.parser')
        langs = [(entry.input.get('value'), entry.get_text(strip=True), entry.input.has_attr('checked'))
                 for entry in page.find_all('div', attrs={'data-a-input-name': 'lop'}) if entry.input]
        presel = [i for i, x in enumerate(langs) if x[2] is True]

    if len(langs) < 1:
        _g.dialog.notification(_g.__plugin__, getString(30270))
        exit()

    sel = _g.dialog.select(getString(30115), [x[1] for x in langs], preselect=presel[0] if presel else -1)
    if sel < 0:
        _g.addon.openSettings()
        exit()
    else:
        return langs[sel][0], langs[sel][1]
