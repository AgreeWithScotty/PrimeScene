"""Bounded HTTPS transactions, domain-scoped cookies and redacted diagnostics."""
from dataclasses import dataclass
import ipaddress
import re
import gzip
from io import BytesIO
from http.client import HTTPSConnection
import json
from secrets import token_hex
from time import monotonic
import socket
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler,HTTPCookieProcessor,HTTPSHandler,ProxyHandler,Request,build_opener
from .account_state import current_account
from .prime_diagnostics import exception_details,route


class TransportFailure(RuntimeError):pass


def public_addresses(host,port):
    """Resolve once and pin the connection to a public address, preventing rebinding."""
    addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    if not addresses:raise TransportFailure('The media host did not resolve')
    for family,kind,protocol,_,target in addresses:
        address=ipaddress.ip_address(target[0])
        if not address.is_global or address.is_multicast:
            raise TransportFailure('The media destination resolved to a non-public address')
    return addresses


def _public_connect(destination,timeout=socket._GLOBAL_DEFAULT_TIMEOUT,source_address=None):
    addresses=public_addresses(*destination);last=None
    for family,kind,protocol,_,target in addresses:
        connection=socket.socket(family,kind,protocol)
        try:
            connection.settimeout(socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout)
            if source_address:connection.bind(source_address)
            connection.connect(target);return connection
        except OSError as error:last=error;connection.close()
    raise last or TransportFailure('The media host could not be reached')


class _PublicHTTPSConnection(HTTPSConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._create_connection=_public_connect


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self,request):
        options={'context':self._context}
        if hasattr(self,'_check_hostname'):options['check_hostname']=self._check_hostname
        return self.do_open(_PublicHTTPSConnection,request,**options)


def https_address(value,hosts=None):
    try:
        if not isinstance(value,str) or len(value)>65536 or '\\' in value or any(ord(c)<=32 or ord(c)==127 for c in value):return False
        parts=urlsplit(value)
        host=parts.hostname or ''
        if hosts is None:
            try:
                address=ipaddress.ip_address(host)
                public=address.is_global and not address.is_multicast
            except ValueError:
                public=bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',host) and '.' in host
                    and not host.endswith(('.localhost','.local','.internal','.test','.invalid','.example')))
            if not public:return False
        return bool(parts.scheme=='https' and host and parts.username is None and
            parts.password is None and parts.port in (None,443) and (hosts is None or host in hosts))
    except (TypeError,ValueError):return False


def shape(value):
    """Only protocol names/structure; values and dynamic identity keys are excluded."""
    known=set('error errors errorCode code message status statusCode messageType type body '
        'widevine2License license playbackUrls audioVideoUrls urlSets defaultUrlSetId '
        'avCdnUrlSets urls manifest subtitleUrls forcedNarratives transitionTimecodes '
        'errorsByResource requestId statusCallbackIntervalSeconds'.split())
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
    def __init__(self,report,hosts,request_id):
        self.report,self.hosts,self.request_id,self.hops=report,hosts,request_id,0
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        self.hops+=1
        allowed=https_address(newurl,self.hosts) and self.hops<=6
        changed=urlsplit(request.full_url).hostname!=urlsplit(newurl).hostname
        if changed and request.data is not None and code in (307,308):allowed=False
        self.report({'event':'http_redirect','request_id':self.request_id,'status':code,
            'from':route(request.full_url),'to':route(newurl),'hop':self.hops,'allowed':allowed})
        if not allowed:raise TransportFailure('The provider returned an untrusted redirect')
        result=super().redirect_request(request,fp,code,msg,headers,newurl)
        if result:
            for name in ('Cookie','Host'):result.remove_header(name)
            if changed:
                for name in ('Authorization','Proxy-authorization','Origin','Referer'):result.remove_header(name)
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
        self.report({'event':'http_started','request_id':identifier,'stage':stage,
            'method':request.get_method(),'address':route(address),'request_bytes':len(data or b'')})
        handlers=[Redirects(self.report,self.hosts,identifier)]
        if self.hosts is None:
            # Public media URLs are pinned directly; an environment proxy must
            # not bypass local-address rejection. TLS still verifies the host.
            handlers.extend((ProxyHandler({}),_PublicHTTPSHandler()))
        if self.jar is not None:handlers.append(HTTPCookieProcessor(self.jar))
        try:
            with build_opener(*handlers).open(request,timeout=20) as reply:
                status=reply.status;final=reply.geturl();raw=reply.read(limit+1)
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
                try:
                    body=error.read(65537);details['response_bytes']=len(body)
                    if len(body)<=65536:
                        value=json.loads(body);details.update(protocol_structure=shape(value),server_error_codes=server_errors(value))
                except Exception as decode_error:details['error_body_decode']=exception_details(decode_error)
                finally:error.close()
            self.report(details)
            raise
