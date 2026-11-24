"""Pre-rendered bundled-font genre lettering; no drawing on the input thread."""
import json
from pathlib import Path

_ROOT=Path(__file__).resolve().parent
_MEDIA=_ROOT.parent/'skins/Default/media/genre-type'
_LABELS=None


def assets(title):
    global _LABELS
    if _LABELS is None:
        _LABELS=json.loads((_ROOT/'genre_type.json').read_text(encoding='utf-8'))['titles']
    row=_LABELS.get(title) or {}
    result={}
    for key in ('title','spine'):
        name=row.get(key)
        if isinstance(name,str) and Path(name).name==name and (_MEDIA/name).is_file():
            result[key]=str(_MEDIA/name)
    return result
