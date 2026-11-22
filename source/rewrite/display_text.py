"""Normalize provider display text without changing title identities or URLs."""
from html import unescape
import unicodedata
from collections import Counter
from threading import RLock
_observed=Counter()
_lock=RLock()

def observations():
    with _lock:return dict(_observed)

def clean(value):
    if not isinstance(value,str):return ''
    value=unescape(value)
    with _lock:
        _observed.update('U+%04X'%ord(c) for c in value if ord(c)>127)
    return ''.join(' ' if c in ('\ufffd','\u00a0','\u202f') else c for c in value
        if unicodedata.category(c) not in ('Cf','Co','Cs') and
        (unicodedata.category(c)!='Cc' or c in '\n\t'))
