"""Bounded HTTPS transactions, domain-scoped cookies and redacted diagnostics."""
from dataclasses import dataclass
import gzip
from io import BytesIO
import json
from secrets import token_hex
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler,HTTPCookieProcessor,Request,build_opener
from .account_state import current_account
from .prime_diagnostics import exception_details,route
from .regional_hosts import (PRIME_WEB_HOSTS,storefront_sso,storefront_relay,storefront_transport,
    strip_redirect_headers,POLICY_REVISION,https_host)


class TransportFailure(RuntimeError):pass


def https_address(value,hosts=None):
    try:
        parts=urlsplit(value)
        return bool(parts.scheme=='https' and parts.hostname and not parts.username and
            not parts.password and parts.port in (None,443) and (hosts is None or parts.hostname in hosts))
    except (TypeError,ValueError):return False


def shape(value):
    """Only protocol names/structure; values and dynamic identity keys are excluded."""
    known=set('error errors errorCode code message status statusCode messageType type body '
        'widevine2License license playbackUrls audioVideoUrls urlSets defaultUrlSetId '
        'avCdnUrlSets urls manifest subtitleUrls forcedNarratives transitionTimecodes '
        'errorsByResource requestId statusCallbackIntervalSeconds '
        'vodPlaylistedPlaybackUrls result ok sessionization sessionHandoffToken '
        'widevineServiceCertificate encodedServiceCertificate widevineLicense metadata '
        'keyMetadata keyType cdmUsageLevel hdcp playbackSettings manifestMetadata '
        'videoQuality intraTitlePlaylist failoverPolicy cdnOrder timedTextUrls'.split())
    stack=[('$',value,0)];out=[]
    while stack and len(out)<40:
        path,node,depth=stack.pop()
        if isinstance(node,dict):
            keys=sorted(k for k in node if k in known)
            out.append({'path':path,'fields':keys,'other_fields':len(node)-len(keys)})
            if depth<5:stack.extend((path+'.'+k,node[k],depth+1) for k in keys if isinstance(node[k],(dict,list)))
        elif isinstance(node,list):
            out.append({'path':path,'length':len(node)})
            if node and depth<5:stack.append((path+'[]',node[0],depth+1))
    return out


def server_errors(value):
    """Bounded provider error codes, omitting free-form private message text."""
    codes=[];stack=[value];visited=0
    while stack and visited<2000:
        node=stack.pop();visited+=1
        if isinstance(node,dict):
            for key,item in node.items():
                if key in ('errorCode','code','errorType','statusCode') and isinstance(item,(str,int)):
                    text=str(item)
                    if len(text)<=100 and all(c.isalnum() or c in '_.-' for c in text):codes.append(text)
                elif isinstance(item,(dict,list)):stack.append(item)
        elif isinstance(node,list):stack.extend(node[:100])
    return sorted(set(codes))[:30]


class Redirects(HTTPRedirectHandler):
    http_error_308=HTTPRedirectHandler.http_error_302
    def __init__(self,report,hosts,request_id,stage='http'):
        self.report,self.hosts,self.request_id,self.hops=report,hosts,request_id,0
        self.stage=stage
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        self.hops+=1
        catalog=storefront_transport(self.hosts)
        sso=catalog and storefront_sso(request.full_url,newurl,code,request.get_method(),request.data is not None)
        relay=catalog and storefront_relay(request.full_url,newurl,code,request.get_method(),request.data is not None)
        regular=bool(https_host(newurl,set(self.hosts)&PRIME_WEB_HOSTS)) if catalog else https_address(newurl,self.hosts)
        allowed=(regular or sso or relay) and self.hops<=6
        if catalog and code in (307,308) and request.data is not None and urlsplit(request.full_url).hostname!=urlsplit(newurl).hostname:
            allowed=False
        self.report({'event':'http_redirect','request_id':self.request_id,'stage':self.stage,'status':code,
            'from':route(request.full_url),'to':route(newurl),'hop':self.hops,'allowed':allowed,
            'policy_revision':POLICY_REVISION,'regional_sso':bool(sso),'regional_relay':bool(relay),
            'method':request.get_method(),'result':'allowed' if allowed else 'blocked'})
        if not allowed:raise TransportFailure('The provider returned an untrusted redirect')
        result=super().redirect_request(request,fp,code,msg,headers,newurl)
        if result:
            result._prime_request_id=self.request_id;result._prime_stage=self.stage
        if result and catalog:strip_redirect_headers(result)
        elif result and urlsplit(request.full_url).hostname!=urlsplit(newurl).hostname:
            for name in ('Authorization','Cookie'):result.remove_header(name)
        return result


@dataclass
class Response:
    data:bytes
    address:str
    status:int
    headers:object
    def json(self):return json.loads(self.data.decode('utf-8-sig'))


class HttpTransport:
    def __init__(self,profile,epoch,report,*,hosts=None,jar=None,headers=None):
        self.profile,self.epoch,self.report=profile,epoch,report
        self.hosts,self.jar,self.headers=hosts,jar,dict(headers or {})
    def active(self):return current_account(self.profile,self.epoch)
    def request(self,address,*,data=None,headers=None,method=None,limit=16*1024*1024,stage='http'):
        started=monotonic();identifier=token_hex(8);status=None
        if not self.active():raise TransportFailure('The account changed; the request was cancelled')
        if not https_address(address,self.hosts):raise TransportFailure('Invalid provider HTTPS address')
        combined=dict(self.headers);combined.update(headers or {})
        for key,value in combined.items():
            if key.lower() in ('authorization','cookie'):self.report.secret(value)
        if self.jar:
            for cookie in self.jar:self.report.secret(cookie.value)
        request=Request(address,data=data,headers=combined,method=method)
        request._prime_request_id=identifier;request._prime_stage=stage
        self.report({'event':'http_started','request_id':identifier,'stage':stage,
            'method':request.get_method(),'address':route(address),'request_bytes':len(data or b'')})
        handlers=[Redirects(self.report,self.hosts,identifier,stage)]
        if self.jar is not None:
            if storefront_transport(self.hosts):
                from .public_catalog import CatalogCookies
                handlers.append(CatalogCookies(self.jar,self.report))
            else:handlers.append(HTTPCookieProcessor(self.jar))
        try:
            with build_opener(*handlers).open(request,timeout=20) as reply:
                status=reply.status;final=reply.geturl()
                if storefront_transport(self.hosts) and not https_host(final,PRIME_WEB_HOSTS):
                    raise TransportFailure('Amazon did not return to its catalog after sign-on. Sign in again.')
                raw=reply.read(limit+1)
                if len(raw)>limit:raise TransportFailure('The provider response exceeds its size limit')
                if reply.headers.get('Content-Encoding','').lower()=='gzip':
                    raw=gzip.GzipFile(fileobj=BytesIO(raw)).read(limit+1)
                    if len(raw)>limit:raise TransportFailure('The decoded provider response exceeds its size limit')
                if not self.active():raise TransportFailure('The account changed during the request')
                if self.jar:
                    for cookie in self.jar:self.report.secret(cookie.value)
                self.report({'event':'http_completed','request_id':identifier,'stage':stage,'status':status,
                    'address':route(final),'response_bytes':len(raw),'content_type':reply.headers.get_content_type(),
                    'server_request_id':reply.headers.get('x-amzn-requestid') or reply.headers.get('x-amz-rid'),
                    'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
                return Response(raw,final,status,reply.headers)
        except Exception as error:
            details={'event':'http_failed','request_id':identifier,'stage':stage,'status':status,
                'address':route(address),'exception':exception_details(error),
                'elapsed_ms':round((monotonic()-started)*1000),'result':'failure' if self.active() else 'cancelled'}
            if isinstance(error,HTTPError):
                details.update(status=error.code,response_address=route(error.geturl()),
                    server_request_id=error.headers.get('x-amzn-requestid') or error.headers.get('x-amz-rid'))
                if stage in ('widevine_license','widevine_web_license'):
                    from .widevine_diagnostics import denial_details
                    # Amazon can expose the detailed refusal in response headers
                    # while keeping its JSON body generic. Record only known
                    # enums/requirement flags, never the raw header text.
                    error_type=error.headers.get('x-amzn-ErrorType','')
                    error_message=error.headers.get('x-amzn-ErrorMessage','')
                    details['drm_denial_header_presence']={
                        'error_type':bool(error_type),'error_message':bool(error_message)}
                    details['drm_denial_header_details']=denial_details({
                        'code':error_type.split(':',1)[0],
                        'errorType':error_type,'message':error_message,'reason':error_type})
                try:
                    body=error.read(65537);details['response_bytes']=len(body)
                    if len(body)<=65536:
                        value=json.loads(body);codes=server_errors(value)
                        error.server_error_codes=tuple(codes)
                        details.update(protocol_structure=shape(value),server_error_codes=codes)
                        if stage in ('widevine_license','widevine_web_license'):
                            from .widevine_diagnostics import denial_details
                            details['drm_denial_details']=denial_details(value)
                except Exception as decode_error:details['error_body_decode']=exception_details(decode_error)
                finally:error.close()
            self.report(details)
            raise
