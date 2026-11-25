"""Original Amazon image identities and declared image-resolution ordering."""
import re
import struct
from urllib.parse import parse_qsl,urlsplit,urlunsplit

POLICY_REVISION='amazon-originals-everywhere-v1'
_PUBLIC_IMAGE=re.compile(r'^(https://(?:m\.media-amazon\.com|images-na\.ssl-images-amazon\.com)(?::443)?/images/.+?)(?:\._[^./]+_)?(\.(?:jpe?g|png|webp|gif|avif|svg))$',re.I)
_SIZE=re.compile(r'_(?:SX|SY|UX|UY|SL|UR|SR)(\d+)(?:,(\d+))?')

def source_image(address):
    """Remove CDN delivery transforms, preserving the exact asset and query."""
    if not isinstance(address,str):return ''
    try:
        parts=urlsplit(address)
        if parts.scheme!='https' or parts.username is not None or parts.password is not None or parts.port not in (None,443):return address
    except ValueError:return address
    base=urlunsplit((parts.scheme,parts.netloc,parts.path,'',''));match=_PUBLIC_IMAGE.fullmatch(base)
    if not match:return address
    return match[1]+match[2]+('?' + parts.query if parts.query else '')

def source_facts(address):
    """Describe the source without claiming unknown provider URLs are masters."""
    original=source_image(address)
    try:
        parts=urlsplit(original);known=bool(_PUBLIC_IMAGE.fullmatch(original.split('?',1)[0]))
        bounded_query=bool({key.lower() for key,_ in parse_qsl(parts.query)} &
            {'width','height','quality','w','h','q','resize','fit'})
    except (ValueError,TypeError):known=False;bounded_query=False
    return {'artwork_policy':POLICY_REVISION,'artificial_width_cap':False,
        'resolution_policy':'provider_original_master' if known and not bounded_query else 'provider_declared_source',
        'delivery_transform_removed':original!=address,'provider_delivery_query_present':bounded_query}


def image_candidates(value,validate,depth=0,width=None,height=None):
    """Keep declared dimensions until selection, before removing delivery caps."""
    if depth>6:return
    if isinstance(value,str):
        address=validate(value)
        if address:yield declared_resolution(address,width,height),address
    elif isinstance(value,dict):
        width=value.get('width',width);height=value.get('height',height)
        for key,child in value.items():
            if isinstance(child,(str,dict,list)):
                size=re.fullmatch(r'(\d{1,5})[xX](\d{1,5})',str(key))
                w,h=size.groups() if size else (width,height)
                yield from image_candidates(child,validate,depth+1,w,h)
    elif isinstance(value,list):
        for child in value:yield from image_candidates(child,validate,depth+1,width,height)


def choose_image(value,validate,depth=0,original=True):
    candidates=list(image_candidates(value,validate,depth))
    if not candidates:return ''
    selected=max(candidates,key=lambda item:item[0])[1]
    return source_image(selected) if original else selected


def html_image(attributes,validate):
    """Prefer the largest declared responsive image, then ordinary sources."""
    choices=[]
    for name in ('srcset','data-srcset'):
        # CDN transforms can contain commas; use descriptor boundaries instead.
        for match in re.finditer(r'(?:^|,\s*)(https?://\S+)\s+(\d+(?:\.\d+)?)(w|x)(?=\s*(?:,|$))',attributes.get(name) or ''):
            address=validate(match[1])
            if address:choices.append((float(match[2]),address))
    if choices:return source_image(max(choices,key=lambda item:item[0])[1])
    return choose_image([attributes.get('src'),attributes.get('data-src')],validate)


def declared_resolution(address,width=None,height=None):
    def positive(value):
        try:return max(0,min(65536,int(value or 0)))
        except (TypeError,ValueError):return 0
    w,h=positive(width),positive(height)
    if not (w or h):
        sizes=_SIZE.findall(address)
        if sizes:w,h=positive(sizes[-1][0]),positive(sizes[-1][1])
    # Prefer an uncropped master when no explicit resolution accompanies it.
    original=bool(address and source_image(address)==address and _PUBLIC_IMAGE.fullmatch(address.split('?',1)[0]))
    return (1 if original and not (w or h) else 0,w*h if w and h else max(w,h)**2,max(w,h))

def pixel_dimensions(data):
    """Read bounded PNG/JPEG/WebP headers for diagnostics; do not decode pixels."""
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24:return struct.unpack('>II',data[16:24])
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        if data[12:16]==b'VP8X' and len(data)>=30:return (1+int.from_bytes(data[24:27],'little'),1+int.from_bytes(data[27:30],'little'))
        if data[12:16]==b'VP8 ' and len(data)>=30 and data[23:26]==b'\x9d\x01\x2a':return (int.from_bytes(data[26:28],'little')&16383,int.from_bytes(data[28:30],'little')&16383)
        if data[12:16]==b'VP8L' and len(data)>=25 and data[20]==47:
            bits=int.from_bytes(data[21:25],'little');return ((bits&16383)+1,((bits>>14)&16383)+1)
    if data.startswith(b'\xff\xd8'):
        position=2
        while position+4<=len(data):
            if data[position]!=255:break
            while position<len(data) and data[position]==255:position+=1
            if position>=len(data):break
            marker=data[position];position+=1
            if marker in (216,217) or 208<=marker<=215:continue
            if marker==218 or position+2>len(data):break
            length=int.from_bytes(data[position:position+2],'big')
            if length<2 or position+length>len(data):break
            if marker in (192,193,194,195,197,198,199,201,202,203,205,206,207) and length>=7:
                return (int.from_bytes(data[position+5:position+7],'big'),int.from_bytes(data[position+3:position+5],'big'))
            position+=length
    return None
