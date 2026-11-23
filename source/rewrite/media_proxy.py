"""Playback-scoped loopback endpoints for native CDM requests and TTML subtitles."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import math
import re
from secrets import token_urlsafe
from threading import RLock,Thread
from time import monotonic
from urllib.parse import urljoin,urlsplit
import xml.etree.ElementTree as ET
from .http_transport import HttpTransport,https_address,shape,TransportFailure
from .prime_diagnostics import exception_details,route


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
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('Unsafe subtitle XML')
    root=ET.fromstring(data);rate=float(next((v for k,v in root.attrib.items() if k.endswith('frameRate')),'30'))
    tick=float(next((v for k,v in root.attrib.items() if k.endswith('tickRate')),'1'))
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
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('Unsafe manifest XML')
    root=ET.fromstring(data)
    if root.tag.rsplit('}',1)[-1]!='MPD':raise ValueError('Provider returned a non-DASH manifest')
    namespace=root.tag.split('}')[0]+'}' if root.tag.startswith('{') else ''
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
        self.manifest_bytes=None
        self.client=HttpTransport(api.g.DATA_PATH,api.epoch,report,headers=api.headers)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True;self.server.owner=self
        self.address='http://127.0.0.1:%d/%s/'%(self.server.server_port,self.key)
        self.thread=Thread(target=self.server.serve_forever,name='SmoothMedia',daemon=True)
        self.thread.start()
        report({'event':'playback_proxy_started','stage':'local_transport','port':self.server.server_port,'result':'success'})
    def manifest(self):return self.address+'manifest.mpd'
    def license(self):return self.address+'license'
    def prepare_manifest(self):
        started=monotonic()
        response=self.client.request(self.resources.manifest,stage='manifest_download')
        data=dash_document(response.data,response.address,self.report)
        with self.lock:self.manifest_bytes=data
        self.report({'event':'local_manifest_prepared','stage':'local_transport','response_bytes':len(data),
            'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
        return data
    def subtitles(self):
        result=[]
        for index,item in enumerate(self.resources.subtitles):
            language=re.sub(r'[^a-zA-Z0-9-]','-',str(item.get('languageCode') or 'und'))
            result.append(self.address+'subtitles/%d/%s%s.srt'%(index,language,'.forced' if item.get('forced') else ''))
        return result
    def close(self):
        started=monotonic()
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.client.headers.clear();self.key=''
        self.manifest_bytes=None
        self.resources.manifest='';self.resources.subtitles.clear();self.resources.transitions.clear()
        self.server.owner=None
        self.report({'event':'playback_proxy_closed','stage':'local_transport',
            'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})


class Handler(BaseHTTPRequestHandler):
    server_version='SmoothPrime';sys_version='';protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def do_HEAD(self):self.dispatch(head=True)
    def output(self,status,data,content_type,head=False):
        self.send_response(status);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('Connection','close');self.end_headers();self.close_connection=True
        if not head:self.wfile.write(data)
    def dispatch(self,head=False):
        owner=self.server.owner;started=monotonic();kind='validate'
        if owner is None:
            self.output(410,b'Session ended','text/plain',head);return
        try:
            path=urlsplit(self.path).path;prefix='/'+owner.key+'/'
            if not owner.key or not path.startswith(prefix):self.output(404,b'Not found','text/plain',head);return
            kind=path[len(prefix):]
            # Kodi probes the playing URL again during Stop. A HEAD request
            # needs local headers only, even while provider teardown is running.
            # It must never redownload a manifest or wait on account/provider I/O.
            if head:
                if kind=='manifest.mpd':
                    data=owner.manifest_bytes or b'';content_type='application/dash+xml'
                elif kind.startswith('subtitles/') and 0<=int(kind.split('/')[1])<len(owner.resources.subtitles):
                    data=b'';content_type='application/x-subrip; charset=utf-8'
                else:self.output(404,b'Not found','text/plain',True);return
                self.output(200,data,content_type,True)
                owner.report({'event':'local_media_response','stage':kind.split('/')[0],'method':'HEAD',
                    'status':200,'response_bytes':0,'content_length':len(data),'upstream_requests':0,
                    'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
                return
            if not owner.api.client.active():self.output(410,b'Session ended','text/plain',head);return
            if kind=='license' and self.command=='POST':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024*1024:raise ValueError('Invalid license request size')
                with owner.lock:
                    if owner.refused:raise TransportFailure('The provider already refused this playback license')
                    try:data=owner.api.license(self.rfile.read(length))
                    except Exception:
                        owner.refused=True;raise
                content_type='application/octet-stream'
            elif kind=='manifest.mpd' and self.command in ('GET','HEAD'):
                data=owner.manifest_bytes
                if data is None or owner.api.material=='LiveStreaming':data=owner.prepare_manifest()
                content_type='application/dash+xml'
            elif kind.startswith('subtitles/') and self.command in ('GET','HEAD'):
                index=int(kind.split('/')[1]);item=owner.resources.subtitles[index]
                response=owner.client.request(item['url'],stage='subtitle_download')
                data=ttml_to_srt(response.data);content_type='application/x-subrip; charset=utf-8'
            else:self.output(404,b'Not found','text/plain',head);return
            self.output(200,data,content_type,head)
            owner.report({'event':'local_media_response','stage':kind.split('/')[0],'method':self.command,
                'status':200,'response_bytes':len(data),
                'elapsed_ms':round((monotonic()-started)*1000),'result':'success'})
        except (BrokenPipeError,ConnectionResetError) as error:
            owner.report({'event':'local_media_cancelled','stage':kind.split('/')[0],
                'exception':exception_details(error),'result':'cancelled'})
        except Exception as error:
            owner.report({'event':'local_media_failed','stage':kind.split('/')[0],
                'method':self.command,'exception':exception_details(error),
                'elapsed_ms':round((monotonic()-started)*1000),'result':'failure'})
            try:self.output(502,b'Playback request failed; see Smooth Prime Diagnostics.','text/plain',head)
            except (BrokenPipeError,ConnectionResetError):pass
