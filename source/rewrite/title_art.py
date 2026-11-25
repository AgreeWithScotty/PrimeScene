"""Hero titles with two uniform sizes from the bundled Bebas Neue glyphs.

WindowXML uses fonts registered by the active skin. The add-on instead
assembles its existing OFL glyphs off the GUI thread, on equal native-sized
transparent canvases. Long continuations wrap at the same smaller size and
advance through native multiimage frames while the large first line stays put.
"""
from base64 import b64decode
from hashlib import sha1
from pathlib import Path
from time import monotonic
from zlib import decompress
import json
import math
import re
import struct
import zlib

try:
    from .title_glyphs import GLYPHS
    from .prime_diagnostics import exception_details
except ImportError:
    from title_glyphs import GLYPHS
    from prime_diagnostics import exception_details

_DIR = Path(__file__).resolve().parent
_META = json.loads((_DIR / 'title_glyphs.json').read_text(encoding='utf-8'))
_BLOB = decompress(b64decode(''.join(GLYPHS)))
_GLYPHS = _META['glyphs']
_CAP_TOP = _GLYPHS['A']['oy']
_CAP_H = _GLYPHS['A']['h']
_MAX_W = 1240
_MAX_H = 268
_CANVAS_W = 1280
_PRIMARY_SCALE = 100. / _CAP_H
_SECONDARY_SCALE = 50. / _CAP_H
_INK_TOP = min(g['oy'] - _CAP_TOP for g in _GLYPHS.values() if g['n'])
_PRIMARY_TOP = math.ceil(-_INK_TOP * _PRIMARY_SCALE) + 2
_LEFT = 2
_LAYOUT_REVISION = 'uniform-levels-v4'
_BREAK = re.compile(r'(?<!\d):|:(?!\d)|\s+-\s+')
_JOINERS = {'A', 'AN', 'THE', 'OF', 'AND', 'OR', 'TO', 'FOR', 'IN', 'ON',
            'AT', 'WITH', 'FROM', 'BY', 'AS', 'BUT', 'VS', 'VS.'}
_REPLACEMENTS = {
    '’': "'", '‘': "'", '‛': "'", '´': "'", '`': "'",
    '“': '"', '”': '"', '–': '-', '—': '-', '…': '...',
}


def normalize(text):
    raw = ' '.join((text or '').split())
    raw = ''.join(_REPLACEMENTS.get(char, char) for char in raw)
    return raw.upper()


def _glyph(char):
    return _GLYPHS.get(char) or _GLYPHS['?']


def _width(text):
    return sum(_glyph(char)['adv'] for char in text)


def _span(text,scale):
    """Use the same rounded advances and ink bounds as the actual blitter."""
    cursor=0;left=0;right=0;top=0;bottom=0
    for char in text:
        glyph=_glyph(char)
        if glyph['n']:
            x=cursor+round(glyph['ox']*scale)
            y=round((glyph['oy']-_CAP_TOP)*scale)
            left=min(left,x);right=max(right,x+max(1,round(glyph['w']*scale)))
            top=min(top,y);bottom=max(bottom,y+max(1,round(glyph['h']*scale)))
        cursor+=round(glyph['adv']*scale)
    return left,max(right,cursor),top,bottom


def _fits(text,scale):
    left,right,_,_=_span(text,scale)
    return right-min(0,left)<=_MAX_W-3


def _wrap(text,scale):
    """Wrap words without changing letter size; split an overlong single word."""
    lines=[];line=''
    for word in text.split():
        candidate=(line+' '+word).strip()
        if line and not _fits(candidate,scale):lines.append(line);line=''
        if _fits(word,scale):line=(line+' '+word).strip();continue
        part=''
        for char in word:
            if part and not _fits(part+char,scale):lines.append(part);part=''
            part+=char
        line=part
    if line:lines.append(line)
    return lines


def _split_primary(text):
    """Move overflow into the smaller level without splitting a fitting word."""
    count=0
    while count<len(text) and _fits(text[:count+1],_PRIMARY_SCALE):count+=1
    boundary=text.rfind(' ',0,count+1)
    if boundary>0:count=boundary
    first,remainder=text[:count].strip(),text[count:].strip()
    words=first.split()
    while len(words)>1 and words[-1] in _JOINERS:
        remainder=words.pop()+' '+remainder
    return ' '.join(words),remainder


def _title_parts(text):
    title = normalize(text) or 'SMOOTH PRIME'
    for boundary in _BREAK.finditer(title):
        colon = boundary.group() == ':'
        primary = title[:boundary.end() if colon else boundary.start()].strip()
        continuation = title[boundary.end() if colon else boundary.start():].strip()
        if primary and continuation:
            return primary, continuation, 'colon' if colon else 'dash'
    words = title.split()
    split = 1
    while split < len(words) and _fits(' '.join(words[:split + 1]),_PRIMARY_SCALE):
        split += 1
    # Keep short titles intact. Wrap longer ones without leaving an article or
    # preposition dangling at the end of the large line.
    if split == len(words):
        return title, '', 'single_line'
    while split > 1 and words[split - 1] in _JOINERS:
        split -= 1
    return ' '.join(words[:split]), ' '.join(words[split:]), 'word_wrap'


def arrange(text):
    if len(normalize(text))>4096:raise ValueError('Title exceeds the bounded rendering limit')
    first, continuation, _rule = _title_parts(text)
    if not _fits(first,_PRIMARY_SCALE):
        first,overflow=_split_primary(first)
        continuation=' '.join(part for part in (overflow,continuation) if part)
    boxes=[{'text':first,'scale':_PRIMARY_SCALE,'top':_PRIMARY_TOP,'height':100,'frame':0}]
    if not continuation:return boxes
    primary_bottom=_PRIMARY_TOP+_span(first,_PRIMARY_SCALE)[3]
    frame=0;top=_PRIMARY_TOP+100+14;bottom=primary_bottom
    for line in _wrap(continuation,_SECONDARY_SCALE):
        _,_,above,below=_span(line,_SECONDARY_SCALE)
        placed=max(top,bottom+8-above)
        if placed+below>_MAX_H-2:
            frame+=1;top=_PRIMARY_TOP+100+14;bottom=primary_bottom
            placed=max(top,bottom+8-above)
        if frame>=64 or placed+below>_MAX_H-2:
            raise ValueError('Title continuation exceeds the bounded rendering area')
        boxes.append({'text':line,'scale':_SECONDARY_SCALE,'top':placed,'height':50,'frame':frame})
        top=placed+50+8;bottom=placed+below
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


def _render_frame(boxes,frame):
    # Equal full-size transparent canvases match both native controls exactly.
    # Short names cannot be enlarged by native keep-aspect fitting.
    width,height=_CANVAS_W,_MAX_H
    canvas=bytearray(width*height*4)
    for box in [boxes[0]]+[box for box in boxes[1:] if box['frame']==frame]:
        left,_,_,_=_span(box['text'],box['scale'])
        cursor=_LEFT-min(0,left)
        for char in box['text']:
            glyph=_glyph(char)
            _blit(canvas,width,glyph,cursor,box['top'],box['scale'])
            cursor+=int(round(glyph['adv']*box['scale']))
    return width,height,bytes(canvas)


def render_rgba(text,frame=0):
    boxes=arrange(text)
    if frame<0 or frame>max(box['frame'] for box in boxes):raise ValueError('Invalid title frame')
    return _render_frame(boxes,frame)


def _png(width, height, rgba):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    raw = b''.join(b'\x00' + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 6))
            + chunk(b'IEND', b''))


def render_title(text, cache_dir, report=None):
    """Return a PNG, or a native multiimage folder for wrapped overflow."""
    started = monotonic()
    details = {'layout_revision': _LAYOUT_REVISION,'font_family':'Bebas Neue',
        'primary_cap_height':100,'secondary_cap_height':50,
        'canvas_width':_CANVAS_W,'canvas_height':_MAX_H,'configured_native_fit_scale':1}
    try:
        key = sha1((_LAYOUT_REVISION + '|' + normalize(text)).encode('utf-8')).hexdigest()[:20]
        details.update(title_key=key,normalized_length=len(normalize(text)))
        boxes = arrange(text)
        frames=1+max(box['frame'] for box in boxes)
        details.update(title_key=key, break_rule=_title_parts(text)[2], line_count=len(boxes),
                       line_word_counts=[len(box['text'].split()) for box in boxes],
                       line_scales=[round(box['scale'], 4) for box in boxes],
                       line_widths=[_span(box['text'],box['scale'])[1]-_span(box['text'],box['scale'])[0] for box in boxes],
                       line_tops=[box['top'] for box in boxes],
                       line_heights=[box['height'] for box in boxes],
                       line_frames=[box['frame'] for box in boxes],frame_count=frames,
                       overflow='continuation_frames' if frames>1 else 'none',
                       unsupported_codepoints=sorted({ord(c) for c in normalize(text) if c not in _GLYPHS}),
                       unsupported_placeholder='?',
                       max_width=_MAX_W, max_height=_MAX_H)
        folder = Path(cache_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path=folder/('title-%s%s'%(key,'.png' if frames==1 else ''))
        marker=path/'complete.json' if frames>1 else None
        cached=path.is_file() if frames==1 else (marker.is_file() and
            all((path/('%04d.png'%index)).is_file() for index in range(frames)))
        if not cached:
            if frames>1:path.mkdir(parents=True,exist_ok=True)
            for index in range(frames):
                target=path if frames==1 else path/('%04d.png'%index)
                pending=target.with_suffix('.png.new')
                width,height,rgba=_render_frame(boxes,index)
                pending.write_bytes(_png(width,height,rgba));pending.replace(target)
            if marker:
                pending=marker.with_suffix('.json.new')
                pending.write_text(json.dumps({'layout_revision':_LAYOUT_REVISION,'frames':frames}),encoding='utf-8')
                pending.replace(marker)
        if report:
            report(dict(details, event='title_layout_ready', stage='hero_title_render',
                        cache_hit=cached, elapsed_ms=round((monotonic() - started) * 1000), result='success'))
        return str(path)
    except Exception as error:
        if report:
            report(dict(details, event='title_layout_failed', stage='hero_title_render',
                        elapsed_ms=round((monotonic() - started) * 1000),
                        exception=exception_details(error), result='failure'))
        raise


def plate_size(text):
    width, height, _rgba = render_rgba(text)
    return width, height
