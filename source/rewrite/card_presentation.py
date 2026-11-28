"""Display-only progress labels from explicitly returned provider metadata."""
from math import ceil,isfinite
import re

def progress(values):
    total=duration_seconds(values)
    position=_seconds(values.get('resume_seconds'))
    if total and position is not None and 0<position<=total:return position*100/total
    try:
        raw=values.get('percentplayed',values.get('progress',0))
        value=float(raw)
        return max(0.,min(100.,value)) if isfinite(value) else 0.
    except (TypeError,ValueError):return 0.

def duration_seconds(values):
    raw=values.get('duration') or values.get('runtime')
    value=_seconds(raw)
    if value is not None and value>0:return value
    if not isinstance(raw,str):return 0.
    raw=raw.strip()
    if re.fullmatch(r'\d+:\d{2}(?::\d{2})?',raw):
        parts=[int(p) for p in raw.split(':')]
        if all(p<60 for p in parts[1:]):return sum(p*60**i for i,p in enumerate(reversed(parts)))
    if raw.upper().startswith('PT'):
        iso=re.fullmatch(r'PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?',raw.upper())
        if iso:return sum(float(p or 0)*m for p,m in zip(iso.groups(),(3600,60,1)))
    units=re.findall(r'(\d+(?:\.\d+)?)\s*(h(?:ours?)?|m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?)\b',raw,re.I)
    return sum(float(n)*{'h':3600,'m':60,'s':1}[unit[0].lower()] for n,unit in units)


def _seconds(raw):
    if isinstance(raw,bool):return None
    try:
        value=float(raw)
        return value if isfinite(value) and value>=0 else None
    except (TypeError,ValueError):return None


def position_seconds(values):
    total=duration_seconds(values);position=_seconds(values.get('resume_seconds'))
    if total and position is not None and position>total:position=None
    if position is None or position==0 and progress(values)>0:
        position=total*progress(values)/100 if total and progress(values)>0 else None
    return min(total,position) if total and position is not None else position


def _clock(seconds):
    seconds=max(0,int(seconds));hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
    return '%d:%02d:%02d'%(hours,minutes,seconds) if hours else '%d:%02d'%(minutes,seconds)


def progress_label(values):
    if viewing_label(values)=='Watched':return 'Watched'
    position=position_seconds(values);total=duration_seconds(values)
    if position is None or position<=0:return ''
    raw=_seconds(values.get('resume_seconds'))
    estimated=raw in (None,0) or total and raw>total
    played=('~' if estimated else '')+_clock(position)+' played'
    if not total:return played
    left=_clock(total-position)+' left'
    return played+'  ·  '+left

def viewing_label(values):
    pct=progress(values)
    try:watched=float(values.get('playcount') or 0)>0
    except (TypeError,ValueError):watched=False
    if watched:return 'Watched'
    if values.get('watch_state')=='unwatched' and pct==0:return 'Unwatched'
    if pct>=100:return 'Watched'
    if not 0<pct<100:return ''
    seconds=duration_seconds(values)
    if seconds:return '%d min remaining'%max(1,ceil(seconds*(100-pct)/6000))
    return '%d%% watched'%int(pct)
