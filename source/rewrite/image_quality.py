"""Original Amazon image identities and declared image-resolution ordering."""
import re
import struct
from urllib.parse import urlsplit,urlunsplit

_PUBLIC_IMAGE=re.compile(r'^(https://(?:m\.media-amazon\.com|images-na\.ssl-images-amazon\.com)/images/.+?)(?:\._[^./]+_)?(\.(?:jpe?g|png|webp))$',re.I)
_SIZE=re.compile(r'_(?:SX|SY|UX|UY|SL|UR|SR)(\d+)(?:,(\d+))?')

def source_image(address):
    """Remove CDN delivery transforms, preserving the exact asset and query."""
    if not isinstance(address,str):return ''
    parts=urlsplit(address)
    if parts.scheme!='https' or parts.username is not None or parts.password is not None or parts.port not in (None,443):return address
    base=urlunsplit((parts.scheme,parts.netloc,parts.path,'',''));match=_PUBLIC_IMAGE.fullmatch(base)
    if not match:return address
    return match[1]+match[2]+('?' + parts.query if parts.query else '')

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
