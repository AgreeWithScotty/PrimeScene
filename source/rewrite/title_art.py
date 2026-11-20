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


def layout(text):
    """Up to two lines. Scale so the block stays inside the hero box."""
    words = [word for word in normalize(text).split(' ') if word]
    if not words:
        words = ['SMOOTH PRIME']
    lines = [' '.join(words)]
    if _width(lines[0]) > _MAX_W and len(words) > 1:
        best = None
        for split in range(1, len(words)):
            pair = [' '.join(words[:split]), ' '.join(words[split:])]
            wide = max(_width(line) for line in pair)
            if best is None or wide < best[0]:
                best = (wide, pair)
        lines = best[1]
    scale = 1.0
    widest = max(_width(line) for line in lines)
    height = _CAP_H + _LINE * (len(lines) - 1)
    if widest > _MAX_W:
        scale = min(scale, _MAX_W / float(widest))
    if height * scale > _MAX_H:
        scale = min(scale, _MAX_H / float(height))
    return lines, scale


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
        if dest_y < 0 or dest_y >= _MAX_H:
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
    lines, scale = layout(text)
    line_step = max(1, int(round(_LINE * scale)))
    cap = max(1, int(round(_CAP_H * scale)))
    width = min(_MAX_W, max(1, int(round(max(_width(line) for line in lines) * scale)) + 2))
    height = min(_MAX_H, cap + line_step * (len(lines) - 1))
    canvas = bytearray(width * height * 4)
    for index, line in enumerate(lines):
        cursor = 0
        top = index * line_step
        for char in line:
            glyph = _glyph(char)
            _blit(canvas, width, glyph, cursor, top, scale)
            cursor += int(round(glyph['adv'] * scale))
    return width, height, bytes(canvas)


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
    key = sha1(('|'.join(lines)).encode('utf-8')).hexdigest()[:20]
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
