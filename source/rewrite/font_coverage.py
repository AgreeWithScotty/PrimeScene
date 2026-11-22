"""Read real TrueType/OpenType Unicode coverage without extra dependencies."""
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from bisect import bisect_right
from hashlib import sha256
from .prime_diagnostics import exception_details

def ranges(data):
    if len(data)<12:raise ValueError('Truncated font header')
    count=struct.unpack_from('>H',data,4)[0];tables={}
    if count>512 or 12+count*16>len(data):raise ValueError('Invalid font tables')
    for i in range(count):
        tag,_,offset,size=struct.unpack_from('>4sIII',data,12+i*16)
        if offset+size>len(data):raise ValueError('Font table exceeds file')
        tables[tag]=(offset,size)
    base,size=tables[b'cmap'];count=struct.unpack_from('>H',data,base+2)[0];points=set();groups=[]
    for i in range(count):
        platform,encoding,offset=struct.unpack_from('>HHI',data,base+4+i*8)
        if platform not in (0,3) or platform==3 and encoding not in (1,10):continue
        p=base+offset;form=struct.unpack_from('>H',data,p)[0]
        if form==4:
            length,_,segments=struct.unpack_from('>HHH',data,p+2);segments//=2
            if p+length>base+size or segments>32768:raise ValueError('Invalid font segments')
            end=p+14;start=end+2*segments+2;delta=start+2*segments;index=delta+2*segments
            for j in range(segments):
                a=struct.unpack_from('>H',data,start+2*j)[0];b=struct.unpack_from('>H',data,end+2*j)[0]
                d=struct.unpack_from('>h',data,delta+2*j)[0];distance=struct.unpack_from('>H',data,index+2*j)[0]
                for c in range(a,min(b,0xfffe)+1):
                    if distance:
                        address=index+2*j+distance+2*(c-a)
                        glyph=struct.unpack_from('>H',data,address)[0] if address+2<=p+length else 0
                        glyph=(glyph+d)&0xffff if glyph else 0
                    else:glyph=(c+d)&0xffff
                    if glyph:points.add(c)
        elif form in (12,13):
            length,_,number=struct.unpack_from('>III',data,p+4)
            if p+length>base+size or number>100000 or 16+number*12>length:raise ValueError('Invalid font groups')
            for j in range(number):
                a,b,glyph=struct.unpack_from('>III',data,p+16+j*12)
                if a>b or b>0x10ffff:raise ValueError('Invalid Unicode range')
                if not glyph and form==12:a+=1
                if glyph or form==12:groups.append((a,b))
    groups.extend((c,c) for c in points);merged=[]
    for a,b in sorted(groups):
        if a>b:continue
        if merged and a<=merged[-1][1]+1:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    if not merged:raise ValueError('No supported Unicode map')
    return tuple(merged)

def contains(coverage,character):
    value=ord(character);i=bisect_right(coverage,(value,0x10ffff))-1
    return i>=0 and coverage[i][0]<=value<=coverage[i][1]

def active_body_font(observe):
    import xbmc,xbmcaddon,xbmcvfs
    skin=xbmc.getSkinDir();selected=xbmc.getInfoLabel('Skin.Font')
    def read(path):
        f=xbmcvfs.File(str(path))
        try:return bytes(f.readBytes())
        finally:f.close()
    try:
        base=Path(xbmcaddon.Addon(skin).getAddonInfo('path'))
        xml=next((base/res/'Font.xml' for res in ('1080i','720p','1080p','xml') if xbmcvfs.exists(str(base/res/'Font.xml'))),None)
        if xml is None:raise ValueError('Active skin font definitions were not found')
        tree=ET.fromstring(read(xml));sets=tree.findall('fontset')
        chosen=next((n for n in sets if n.get('id')==selected),sets[0] if sets else tree)
        definition=next((n for n in chosen.findall('font') if n.findtext('name')=='font13'),None)
        if definition is None:raise ValueError('Active skin does not declare font13')
        filename=definition.findtext('filename') or ''
        candidates=([filename] if '://' in filename else [str(base/'fonts'/filename),
            'special://home/media/Fonts/'+filename,'special://xbmc/media/Fonts/'+filename])
        path=next((p for p in candidates if xbmcvfs.exists(p)),None)
        if path is None:raise ValueError('Active skin font file could not be read')
        data=read(path);coverage=ranges(data)
        observe({'event':'display_font_resolved','stage':'font_coverage','skin':skin,'fontset':selected,
            'font_name':'font13','font_file':Path(filename).name,'font_sha256':sha256(data).hexdigest(),
            'unicode_ranges':len(coverage),'down_triangle_supported':contains(coverage,'\u25be'),'result':'success'})
        return coverage
    except Exception as error:
        observe({'event':'display_font_resolution_failed','stage':'font_coverage','skin':skin,
            'exception':exception_details(error),'fallback':'bundled_body_font_coverage','result':'failure'})
        return None
