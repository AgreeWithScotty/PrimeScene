"""Validate DASH once and patch only required XML bytes; never rebuild its tracks."""
from html import escape
import re
from time import monotonic
from urllib.parse import urljoin
from xml.parsers import expat
from .prime_diagnostics import exception_details


class UnsupportedLayout(ValueError):pass


def _tag(data,start):
    index=start+1
    while index<len(data) and data[index] not in b' \t\r\n/>':index+=1
    name=data[start+1:index];quote=None
    while index<len(data):
        value=data[index]
        if quote is not None:
            if value==quote:quote=None
        elif value in (34,39):quote=value
        elif value==62:return name,index+1
        index+=1
    raise ValueError('Incomplete DASH start tag')


def _attribute(data,start,end,name):
    # Skip the element name, then walk actual attributes so text inside another
    # quoted attribute cannot be mistaken for the language attribute.
    index=start+1
    while data[index] not in b' \t\r\n/>':index+=1
    while index<end:
        while index<end and data[index] in b' \t\r\n':index+=1
        if data[index:index+1] in (b'/',b'>'):break
        begin=index
        while data[index] not in b' \t\r\n=':index+=1
        key=data[begin:index]
        while data[index] in b' \t\r\n=':index+=1
        quote=data[index];value_start=index+1;index=data.index(bytes((quote,)),value_start)
        if key==name:return value_start,index
        index+=1
    raise UnsupportedLayout('language_attribute_layout')


class _Reader:
    def __init__(self,data,address):
        self.data,self.address=data,address;self.names=[];self.adaptations=[];self.edits=[]
        self.root_name=None;self.root_end=None;self.prefix=b'';self.base_count=0;self.text=None
        self.namespace='';self.audio=0;self.video_sets=0;self.video=[]
        self.protection={};self.other_protection=0;self.init_data_nodes=0
        self.compatible=not (data.startswith((b'\xff\xfe',b'\xfe\xff')) or b'\x00' in data[:64])
        self.parser=expat.ParserCreate(namespace_separator='}')
        self.parser.StartElementHandler=self.start;self.parser.EndElementHandler=self.end
        self.parser.XmlDeclHandler=self.declaration
        self.parser.StartDoctypeDeclHandler=self.unsafe
        self.parser.ExternalEntityRefHandler=self.unsafe
    def unsafe(self,*args):raise ValueError('Unsafe manifest XML')
    def declaration(self,version,encoding,standalone):
        if encoding and encoding.lower().replace('-','')!='utf8':
            self.compatible=False
    def mpd(self,name):return self.namespace+name
    def start(self,name,attributes):
        if not self.compatible:return
        parent=self.names[-1] if self.names else None
        self.names.append(name);depth=len(self.names)
        if depth>128:raise ValueError('DASH structure is too deep')
        position=self.parser.CurrentByteIndex
        if depth==1:
            if name.rsplit('}',1)[-1]!='MPD':raise ValueError('Provider returned a non-DASH manifest')
            self.namespace=name.rsplit('}',1)[0]+'}' if '}' in name else ''
            self.root_name,self.root_end=_tag(self.data,position)
            if self.data[self.root_end-2:self.root_end]==b'/>':raise UnsupportedLayout('self_closing_root')
            self.prefix=self.root_name.rsplit(b':',1)[0]+b':' if b':' in self.root_name else b''
        if self.text is not None:raise UnsupportedLayout('nested_root_url')
        if depth==2 and name in (self.mpd('BaseURL'),self.mpd('Location')):
            qname,end=_tag(self.data,position)
            self.text={'name':name,'qname':qname,'start':position,'end':end,'parts':[],
                'empty':self.data[end-2:end]==b'/>'}
            self.parser.CharacterDataHandler=self.text['parts'].append
            if name==self.mpd('BaseURL'):self.base_count+=1
        if name==self.mpd('AdaptationSet'):
            self.adaptations.append({'depth':depth,'attributes':attributes,'representations':[],'start':position})
        elif name==self.mpd('Representation') and parent==self.mpd('AdaptationSet'):
            self.adaptations[-1]['representations'].append(attributes)
        if name==self.mpd('ContentProtection'):
            systems={'urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95':'playready',
                'urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed':'widevine',
                'urn:mpeg:dash:mp4protection:2011':'common_cenc'}
            system=systems.get(attributes.get('schemeIdUri','').lower())
            if system:self.protection[system]=self.protection.get(system,0)+1
            else:self.other_protection+=1
        if parent==self.mpd('ContentProtection') and name.rsplit('}',1)[-1] in ('pssh','pro'):
            self.init_data_nodes+=1
    def end(self,name):
        if not self.compatible:return
        if self.text is not None and name==self.text['name']:
            item=self.text;value=''.join(item['parts']);resolved=urljoin(self.address,value or '')
            encoded=escape(resolved,quote=False).encode('utf-8')
            if item['empty']:
                replacement=self.data[item['start']:item['end']-2]+b'>'+encoded+b'</'+item['qname']+b'>'
                self.edits.append((item['start'],item['end'],replacement))
            elif resolved!=value:
                self.edits.append((item['end'],self.parser.CurrentByteIndex,encoded))
            self.text=None;self.parser.CharacterDataHandler=None
        if name==self.mpd('AdaptationSet'):
            adaptation=self.adaptations.pop();values=adaptation['attributes'];representations=adaptation['representations']
            if (values.get('contentType')=='video' or values.get('mimeType','').startswith('video/')
                    or any(r.get('mimeType','').startswith('video/') for r in representations)):
                self.video_sets+=1
                for representation in representations:
                    item={}
                    for key in ('width','height','bandwidth'):
                        value=representation.get(key) or values.get(key)
                        if value and len(value)<=12 and value.isdigit():item[key]=int(value)
                    codec=representation.get('codecs') or values.get('codecs') or ''
                    if re.fullmatch(r'[A-Za-z0-9.,_-]{1,128}',codec):item['codecs']=codec
                    self.video.append(item)
            if values.get('contentType')=='audio' or values.get('mimeType','').startswith('audio/'):
                self.audio+=1;language=values.get('lang')
                if language and '_' in language:
                    _,end=_tag(self.data,adaptation['start'])
                    begin,finish=_attribute(self.data,adaptation['start'],end,b'lang')
                    self.edits.append((begin,finish,escape(language.replace('_','-'),quote=True).encode('utf-8')))
        self.names.pop()
    def result(self):
        self.parser.Parse(self.data,True)
        if not self.compatible:raise UnsupportedLayout('non_utf8_encoding')
        if self.root_name is None:raise ValueError('Provider returned no DASH document')
        if not self.base_count:
            value=escape(urljoin(self.address,'.'),quote=False).encode('utf-8')
            self.edits.append((self.root_end,self.root_end,b'<'+self.prefix+b'BaseURL>'+value+b'</'+self.prefix+b'BaseURL>'))
        if not self.edits:return self.data
        parts=[];position=0
        for begin,end,value in sorted(self.edits):
            if begin<position:raise ValueError('Overlapping DASH edits')
            parts.extend((self.data[position:begin],value));position=end
        parts.append(self.data[position:]);return b''.join(parts)


def prepare(data,address,report):
    started=monotonic()
    try:
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('Unsafe manifest XML')
        reader=_Reader(data,address);result=reader.result()
    except UnsupportedLayout:raise
    except Exception as error:
        report({'event':'dash_preparation_failed','stage':'manifest_parse',
            'exception':exception_details(error),'elapsed_ms':round((monotonic()-started)*1000),'result':'failure'})
        raise
    video=reader.video;bitrates=[v['bandwidth'] for v in video if 'bandwidth' in v]
    report({'event':'dash_prepared','stage':'manifest_parse','processing_revision':'sparse-xml-edits-v1',
        'audio_sets':reader.audio,'video_sets':reader.video_sets,'drm_protection_counts':reader.protection,
        'other_drm_protection_count':reader.other_protection,'drm_init_data_nodes':reader.init_data_nodes,
        'video_representations':len(video),'video_heights':sorted({v['height'] for v in video if 'height' in v}),
        'video_widths':sorted({v['width'] for v in video if 'width' in v}),
        'video_codecs':sorted({v['codecs'] for v in video if 'codecs' in v}),
        'video_bitrate_range':[min(bitrates),max(bitrates)] if bitrates else [],
        'source_bytes':len(data),'output_bytes':len(result),'xml_edits':len(reader.edits),
        'elapsed_ms':round((monotonic()-started)*1000),'tracks_removed':0,
        'media_source_urls_rewritten':False,'result':'success'})
    return result
