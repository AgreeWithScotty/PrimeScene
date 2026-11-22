"""Large hero titles as pictures.

Kodi 21 WindowXML only resolves font names the active skin already defined.
Anything else, including this add-on's Font.xml, is drawn as font13, so a
168px Bebas Neue label shows up as small plain text. The glyphs below are
Bebas Neue (OFL, shipped beside the skin) rasterized once; each title is
assembled off the GUI thread and cached. The home window binds the picture,
so moving along a row does not run Python.
"""
from base64 import b64decode
from hashlib import sha1
from pathlib import Path
from zlib import decompress
import json
import struct
import zlib

try:
    from .title_glyphs import GLYPHS
except ImportError:
    from title_glyphs import GLYPHS

_DIR = Path(__file__).resolve().parent
_META = json.loads((_DIR / 'title_glyphs.json').read_text(encoding='utf-8'))
_BLOB = decompress(b64decode(''.join(GLYPHS)))
_GLYPHS = _META['glyphs']
_CAP_TOP = _GLYPHS['A']['oy']
_CAP_H = _GLYPHS['A']['h']
_LINE = int(round(_CAP_H * 0.92))
_MAX_W = 1240
_MAX_H = 268
_REPLACEMENTS = {
    '’': "'", '‘': "'", '‛': "'", '´': "'", '`': "'",
    '“': '"', '”': '"', '–': '-', '—': '-', '…': '...',
}


def normalize(text):
    raw = ' '.join((text or '').split())
    raw = ''.join(_REPLACEMENTS.get(char, char) for char in raw)
    return raw.upper()


def _glyph(char):
    return _GLYPHS.get(char) or _GLYPHS.get(' ')


def _width(text):
    return sum(_glyph(char)['adv'] for char in text)


def arrange(text):
    words=normalize(text).split() or ['SMOOTH','PRIME']
    first=words[0]
    primary=min(1.,_MAX_W/float(max(1,_width(first))))
    primary_height=max(1,round(_CAP_H*primary))
    boxes=[{'text':first,'scale':primary,'top':0,'height':primary_height}]
    if len(words)==1:return boxes
    gap=max(14,round(_CAP_H*.09));available=max(1,_MAX_H-primary_height-gap)
    secondary=min(primary*.5,available/float(_CAP_H))
    for _ in range(5):
        lines=[];line=''
        for word in words[1:]:
            candidate=(line+' '+word).strip()
            if line and _width(candidate)*secondary>_MAX_W:
                lines.append(line);line=word
            else:line=candidate
        if line:lines.append(line)
        total=len(lines)*_CAP_H*secondary+max(0,len(lines)-1)*8
        widest=max(_width(line)*secondary for line in lines)
        if total<=available and widest<=_MAX_W:break
        secondary*=min(available/max(1,total),_MAX_W/max(1,widest))*.98
    top=primary_height+gap
    for line in lines:
        height=max(1,round(_CAP_H*secondary))
        boxes.append({'text':line,'scale':secondary,'top':top,'height':height})
        top+=height+8
    return boxes

def layout(text):
    boxes=arrange(text)
    return [box['text'] for box in boxes],boxes[0]['scale']


def _scale_span(raw, width, height, scale):
    if scale >= 0.999:
        return raw, width, height
    out_w = max(1, int(round(width * scale)))
    out_h = max(1, int(round(height * scale)))
    out = bytearray(out_w * out_h * 4)
    for y in range(out_h):
        src_y = min(height - 1, int(y / scale))
        row = src_y * width * 4
        dest = y * out_w * 4
        for x in range(out_w):
            src_x = min(width - 1, int(x / scale))
            start = row + src_x * 4
            out[dest + x * 4:dest + x * 4 + 4] = raw[start:start + 4]
    return bytes(out), out_w, out_h


def _blit(canvas, canvas_w, glyph, x, y, scale):
    if glyph['n'] <= 0:
        return
    raw = _BLOB[glyph['off']:glyph['off'] + glyph['n']]
    width, height = glyph['w'], glyph['h']
    raw, width, height = _scale_span(raw, width, height, scale)
    ox = int(round(glyph['ox'] * scale))
    oy = int(round((glyph['oy'] - _CAP_TOP) * scale))
    left, top = x + ox, y + oy
    for row in range(height):
        dest_y = top + row
        if dest_y < 0 or dest_y >= len(canvas)//(canvas_w*4):
            continue
        src = row * width * 4
        for col in range(width):
            dest_x = left + col
            if dest_x < 0 or dest_x >= canvas_w:
                continue
            pixel = raw[src + col * 4:src + col * 4 + 4]
            if pixel[3] == 0:
                continue
            at = (dest_y * canvas_w + dest_x) * 4
            canvas[at:at + 4] = pixel


def render_rgba(text):
    boxes=arrange(text)
    width=min(_MAX_W,max(1,max(round(_width(box['text'])*box['scale'])+3 for box in boxes)))
    height=min(_MAX_H,max(box['top']+box['height'] for box in boxes))
    canvas=bytearray(width*height*4)
    for box in boxes:
        cursor=0
        for char in box['text']:
            glyph=_glyph(char)
            _blit(canvas,width,glyph,cursor,box['top'],box['scale'])
            cursor+=int(round(glyph['adv']*box['scale']))
    return width,height,bytes(canvas)


def _png(width, height, rgba):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    raw = b''.join(b'\x00' + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 6))
            + chunk(b'IEND', b''))


def render_title(text, cache_dir):
    """Write one cached PNG and return its path. Empty titles still get a plate."""
    lines, _scale = layout(text)
    key = sha1(('hierarchy-v2|'+'|'.join(lines)).encode('utf-8')).hexdigest()[:20]
    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('title-%s.png' % key)
    if not path.is_file():
        width, height, rgba = render_rgba(text)
        path.write_bytes(_png(width, height, rgba))
    return str(path)


def plate_size(text):
    width, height, _rgba = render_rgba(text)
    return width, height
