"""Preserve supported text; handle missing glyphs without displaying tofu boxes."""
from html import unescape
import re,unicodedata
from collections import Counter
from pathlib import Path
from threading import RLock
from .font_coverage import ranges,contains,active_body_font
_observed=Counter();_replaced=Counter();_lock=RLock();_coverage=None;_coverage_source='unresolved'
_MAP={'\u30fb':' \u00b7 ','\u2010':'-','\u2011':'-','\u2012':'-','\u2013':'-','\u2014':' - ',
    '\u2015':' - ','\u2212':'-','\u2018':"'",'\u2019':"'",'\u201a':"'",'\u201b':"'",
    '\u201c':'"','\u201d':'"','\u201e':'"','\u2026':'...','\u2022':' \u00b7 ',
    '\u2023':' \u00b7 ','\u25cf':' \u00b7 ','\u25a0':' ','\u25a1':' ','\ufffd':' ','\ufffc':' '}

def configure(observe):
    global _coverage,_coverage_source
    found=active_body_font(observe)
    if found is not None:
        with _lock:_coverage=found;_coverage_source='active_skin_font13'
        return
    try:
        found=ranges((Path(__file__).resolve().parents[1]/'skins/Default/fonts/Inter-Regular.ttf').read_bytes())
        with _lock:_coverage=found;_coverage_source='bundled_inter_fallback'
        observe({'event':'display_font_fallback_ready','stage':'font_coverage','unicode_ranges':len(found),'result':'success'})
    except Exception as error:
        from .prime_diagnostics import exception_details
        with _lock:_coverage=((32,126),);_coverage_source='ascii_fallback'
        observe({'event':'display_font_fallback_failed','stage':'font_coverage','exception':exception_details(error),'result':'failure'})

def observations():
    with _lock:return dict(_observed)

def replacement_report():
    with _lock:return {'coverage_source':_coverage_source,'replacements':dict(_replaced)}

def reset_account_observations():
    with _lock:_observed.clear();_replaced.clear()

def clean(value):
    if not isinstance(value,str):return ''
    for _ in range(3):
        decoded=unescape(value)
        if decoded==value:break
        value=decoded
    original=value;value=unicodedata.normalize('NFKC',value);out=[];changes=Counter()
    with _lock:
        _observed.update('U+%04X'%ord(c) for c in original if ord(c)>127)
        coverage=_coverage
    for char in value:
        category=unicodedata.category(char);code='U+%04X'%ord(char)
        if char in _MAP:
            mapped=_MAP[char]
            if coverage is not None:mapped=''.join(c if contains(coverage,c) else ' ' for c in mapped)
            out.append(mapped);changes[code+':punctuation']+=1
        elif char in '\n\t':out.append(char)
        elif category.startswith('Z'):out.append(' ')
        elif category in ('Cc','Cf','Co','Cs','Cn'):
            out.append(' ');changes[code+':non_display']+=1
        elif coverage is None or contains(coverage,char):out.append(char)
        else:
            folded=''.join(c for c in unicodedata.normalize('NFKD',char)
                if unicodedata.category(c)[0]!='M' and (32<=ord(c)<127 or contains(coverage,c)))
            out.append(folded if folded and folded!=char else '' if category.startswith('M') else ' ')
            changes[code+':missing_glyph']+=1
    with _lock:_replaced.update(changes)
    return re.sub(r'[ \t]+',' ',''.join(out)).strip()
