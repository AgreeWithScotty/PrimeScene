"""Display-only progress labels from explicitly returned provider metadata."""
from math import ceil,isfinite

def progress(values):
    try:
        raw=values.get('percentplayed',values.get('progress',0))
        value=float(raw)
        return max(0.,min(100.,value)) if isfinite(value) else 0.
    except (TypeError,ValueError):return 0.

def duration_seconds(values):
    raw=values.get('duration')
    if isinstance(raw,bool):return 0.
    try:
        value=float(raw)
        return value if isfinite(value) and value>0 else 0.
    except (TypeError,ValueError):return 0.

def viewing_label(values):
    pct=progress(values)
    try:watched=float(values.get('playcount') or 0)>0
    except (TypeError,ValueError):watched=False
    if watched:return 'Watched'
    if not 0<pct<100:return ''
    seconds=duration_seconds(values)
    if seconds:return '%d min remaining'%max(1,ceil(seconds*(100-pct)/6000))
    return '%d%% watched'%int(pct)
