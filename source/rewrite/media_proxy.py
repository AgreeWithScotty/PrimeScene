"""Playback-scoped loopback endpoints for native CDM requests and TTML subtitles."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from hmac import compare_digest
import math
import re
from secrets import token_hex,token_urlsafe
from threading import BoundedSemaphore,Event,RLock,Thread
from time import monotonic
from urllib.parse import urljoin,urlsplit
import xml.etree.ElementTree as ET
from .http_transport import HttpTransport,https_address,shape,TransportFailure
from .prime_diagnostics import exception_details,route


class LocalRequestError(ValueError):
    def __init__(self,status,reason):self.status,self.reason=status,reason;super().__init__(reason)


def local_request(path,headers,method,key,port):
    """Only Kodi's exact loopback endpoint can use a playback capability."""
    if headers.get_all('Host',[])!=['127.0.0.1:%d'%port]:raise LocalRequestError(400,'invalid_host')
    if headers.get('Origin') is not None or headers.get('Sec-Fetch-Site','none') not in ('none','same-origin'):
        raise LocalRequestError(403,'browser_origin')
    if headers.get('Transfer-Encoding') is not None:raise LocalRequestError(400,'unsupported_transfer_encoding')
    parts=urlsplit(path)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:raise LocalRequestError(400,'invalid_target')
    segments=parts.path.split('/',2)
    if len(segments)!=3 or not key or not segments[1].isascii() or not compare_digest(segments[1],key):raise LocalRequestError(404,'invalid_capability')
    kind=segments[2]
    if kind=='license':
        if method!='POST':raise LocalRequestError(405,'invalid_method')
    elif kind=='manifest.mpd' or re.fullmatch(r'subtitles/[0-9]{1,4}/[a-zA-Z0-9.-]+\.srt',kind):
        if method not in ('GET','HEAD'):raise LocalRequestError(405,'invalid_method')
    else:raise LocalRequestError(404,'invalid_endpoint')
    values=headers.get_all('Content-Length',[])
    if len(values)>1 or values and not re.fullmatch('[0-9]{1,8}',values[0]):raise LocalRequestError(400,'invalid_content_length')
    length=int(values[0]) if values else 0
    if (kind=='license' and not 0<length<=1024*1024) or (kind!='license' and length):
        raise LocalRequestError(400,'invalid_body_size')
    return kind,length


class LocalMediaServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self.slots=BoundedSemaphore(6)
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(False):self.shutdown_request(request);return
        request.settimeout(5)
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


def media_xml(data):
    if len(data)>16*1024*1024:raise ValueError('Media XML exceeds its size limit')
    # NUL removal catches the declaration in UTF-16/32 as well as UTF-8.
    declarations=data.replace(b'\x00',b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:raise ValueError('Unsafe media XML')
    parser=ET.XMLPullParser(events=('start','end'));depth=count=0;root=None
    for index in range(0,len(data),16384):
        parser.feed(data[index:index+16384])
        for event,node in parser.read_events():
            if event=='start':
                if root is None:root=node
                depth+=1;count+=1
                if depth>48 or count>100000:raise ValueError('Media XML exceeds its structural limit')
            else:depth-=1
    parser.close()
    if root is None:raise ValueError('Empty media XML')
    return root


def stamp(value,rate=30):
    value=str(value or '').strip()
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(h|m|s|ms|f|t)',value)
    if match:
        number=float(match[1]);unit=match[2]
        if unit=='t':raise ValueError('TTML tick units need the declared tick rate')
        return number*{'h':3600,'m':60,'s':1,'ms':.001,'f':1/rate}[unit]
    parts=value.split(':')
    if len(parts)==4:return int(parts[0])*3600+int(parts[1])*60+int(parts[2])+float(parts[3])/rate
    if len(parts)==3:return int(parts[0])*3600+int(parts[1])*60+float(parts[2])
    raise ValueError('Unsupported TTML timestamp')


def srt_time(seconds):
    total=max(0,round(seconds*1000));h,tail=divmod(total,3600000);m,tail=divmod(tail,60000);s,ms=divmod(tail,1000)
    return '%02d:%02d:%02d,%03d'%(h,m,s,ms)


def ttml_to_srt(data):
    root=media_xml(data);rate=float(next((v for k,v in root.attrib.items() if k.endswith('frameRate')),'30'))
    tick=float(next((v for k,v in root.attrib.items() if k.endswith('tickRate')),'1'))
    if not math.isfinite(rate) or not math.isfinite(tick) or rate<=0 or tick<=0:raise ValueError('Invalid subtitle clock')
    def timing(value):return float(value[:-1])/tick if str(value).endswith('t') else stamp(value,rate)
    def text(node):
        return (node.text or '')+''.join(('\n' if child.tag.rsplit('}',1)[-1]=='br' else text(child))+(child.tail or '') for child in node)
    cues=[]
    def visit(node,offset=0):
        begin=offset+timing(node.attrib['begin']) if node.get('begin') else offset
        if node.tag.rsplit('}',1)[-1]=='p':
            end=offset+timing(node.attrib['end']) if node.get('end') else begin+timing(node.attrib.get('dur','0s'))
            caption=text(node).strip()
            if caption and end>begin:cues.append((begin,end,caption))
        else:
            for child in node:visit(child,begin)
    visit(root)
    return '\n\n'.join('%d\n%s --> %s\n%s'%(i,srt_time(a),srt_time(b),text) for i,(a,b,text) in enumerate(cues,1)).encode('utf-8')


def dash_document(data,address,report):
    """Preserve every provider track and URL; resolve relative media bases only."""
    root=media_xml(data)
    if root.tag.rsplit('}',1)[-1]!='MPD':raise ValueError('Provider returned a non-DASH manifest')
    namespace=root.tag.split('}')[0]+'}' if root.tag.startswith('{') else ''
    for node in root.iter():
        references=([node.text] if node.tag.rsplit('}',1)[-1] in ('BaseURL','Location','PatchLocation') and node.text else [])
        references.extend(value for key,value in node.attrib.items() if key.rsplit('}',1)[-1] in ('href','media','initialization','sourceURL','index'))
        for value in references:
            if not https_address(urljoin(address,value.strip())):raise ValueError('Manifest contains an unsafe media reference')
    ET.register_namespace('',namespace[1:-1]) if namespace else None
    bases=[node for node in root if node.tag==namespace+'BaseURL']
    if not bases:
        base=ET.Element(namespace+'BaseURL');base.text=urljoin(address,'.');root.insert(0,base)
    else:
        for base in bases:base.text=urljoin(address,base.text or '')
    for location in root.findall(namespace+'Location'):
        location.text=urljoin(address,location.text or '')
    audio=0
    for adaptation in root.iter(namespace+'AdaptationSet'):
        if adaptation.get('contentType')=='audio' or adaptation.get('mimeType','').startswith('audio/'):
            audio+=1
            # Each track remains selectable. Do not prune codecs/bitrates or
            # alter signed provider URLs to force another media source.
            language=adaptation.get('lang')
            if language:adaptation.set('lang',language.replace('_','-'))
    report({'event':'dash_prepared','stage':'manifest_parse','audio_sets':audio,
        'tracks_removed':0,'media_source_urls_rewritten':False,'result':'success'})
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


class PlaybackProxy:
    def __init__(self,api,resources,report):
        self.api,self.resources,self.report=api,resources,report
        self.key=token_urlsafe(24);self.lock=RLock();self.refused=None
        self.closed=Event()
        self.client=HttpTransport(api.g.DATA_PATH,api.epoch,report,headers=api.headers)
        self.server=LocalMediaServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True;self.server.owner=self
        self.address='http://127.0.0.1:%d/%s/'%(self.server.server_port,self.key)
        self.thread=Thread(target=self.server.serve_forever,name='SmoothMedia',daemon=True)
        self.thread.start()
        report({'event':'playback_proxy_started','stage':'local_transport','port':self.server.server_port,'result':'success'})
    def manifest(self):return self.address+'manifest.mpd'
    def license(self):return self.address+'license'
    def subtitles(self):
        result=[]
        for index,item in enumerate(self.resources.subtitles):
            language=re.sub(r'[^a-zA-Z0-9-]','-',str(item.get('languageCode') or 'und'))
            result.append(self.address+'subtitles/%d/%s%s.srt'%(index,language,'.forced' if item.get('forced') else ''))
        return result
    def close(self):
        self.closed.set()
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.client.headers.clear();self.key=''
        self.resources.manifest='';self.resources.subtitles.clear();self.resources.transitions.clear()
        self.server.owner=None
        self.report({'event':'playback_proxy_closed','stage':'local_transport','result':'success'})


class Handler(BaseHTTPRequestHandler):
    server_version='SmoothPrime';sys_version='';protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def do_HEAD(self):self.dispatch(head=True)
    def output(self,status,data,content_type,head=False):
        self.send_response(status);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Connection','close');self.end_headers();self.close_connection=True
        if not head:self.wfile.write(data)
    def body(self,length):
        chunks=[];remaining=length;deadline=monotonic()+5
        while remaining:
            available=deadline-monotonic()
            if available<=0:raise LocalRequestError(408,'body_timeout')
            self.connection.settimeout(available)
            try:part=self.rfile.read1(min(remaining,65536))
            except TimeoutError as error:raise LocalRequestError(408,'body_timeout') from error
            if not part:raise LocalRequestError(400,'incomplete_body')
            chunks.append(part);remaining-=len(part)
        return b''.join(chunks)
    def dispatch(self,head=False):
        owner=self.server.owner;started=monotonic();kind='validate';request_id=token_hex(8)
        if owner is None:
            self.output(410,b'Session ended','text/plain',head);return
        try:
            kind,length=local_request(self.path,self.headers,self.command,owner.key,self.server.server_port)
            if owner.closed.is_set() or not owner.api.client.active():
                owner.report({'event':'local_media_request_cancelled','stage':'local_request_guard','request_id':request_id,
                    'status':410,'reason':'session_ended','result':'cancelled'})
                self.output(410,b'Session ended','text/plain',head);return
            if kind=='license' and self.command=='POST':
                challenge=self.body(length)
                with owner.lock:
                    if owner.closed.is_set():raise LocalRequestError(410,'session_ended')
                    if owner.refused:raise TransportFailure('The provider already refused this playback license')
                    try:data=owner.api.license(challenge)
                    except Exception:
                        owner.refused=True;raise
                content_type='application/octet-stream'
            elif kind=='manifest.mpd' and self.command in ('GET','HEAD'):
                response=owner.client.request(owner.resources.manifest,stage='manifest_download')
                data=dash_document(response.data,response.address,owner.report);content_type='application/dash+xml'
            elif kind.startswith('subtitles/') and self.command in ('GET','HEAD'):
                index=int(kind.split('/')[1])
                if index>=len(owner.resources.subtitles):raise LocalRequestError(404,'invalid_subtitle')
                item=owner.resources.subtitles[index]
                response=owner.client.request(item['url'],stage='subtitle_download')
                data=ttml_to_srt(response.data);content_type='application/x-subrip; charset=utf-8'
            else:self.output(404,b'Not found','text/plain',head);return
            self.output(200,data,content_type,head)
            owner.report({'event':'local_media_response','stage':kind.split('/')[0],'response_bytes':len(data),
                'request_id':request_id,'status':200,'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
        except LocalRequestError as error:
            owner.report({'event':'local_media_request_rejected','stage':'local_request_guard',
                'request_id':request_id,'status':error.status,'reason':error.reason,'elapsed_ms':round((monotonic()-started)*1000),'result':'refused'})
            self.output(error.status,b'Request refused','text/plain',head)
        except (BrokenPipeError,ConnectionResetError) as error:
            owner.report({'event':'local_media_cancelled','stage':kind.split('/')[0],
                'request_id':request_id,'exception':exception_details(error),'result':'cancelled'})
        except Exception as error:
            owner.report({'event':'local_media_failed','stage':kind.split('/')[0],
                'request_id':request_id,'exception':exception_details(error),'elapsed_ms':round((monotonic()-started)*1000),'result':'failure'})
            try:self.output(502,b'Playback request failed; see Smooth Prime Diagnostics.','text/plain',head)
            except (BrokenPipeError,ConnectionResetError):pass
