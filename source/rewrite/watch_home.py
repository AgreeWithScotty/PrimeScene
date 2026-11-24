"""Decode explicit provider watch-state fields; no local In Progress store."""
from math import isfinite


def _number(value):
    if isinstance(value,bool):return None
    try:
        value=float(value)
        return value if isfinite(value) and value>=0 else None
    except (TypeError,ValueError):return None

def progress_info(node,depth=0):
    if not isinstance(node,dict) or depth>6:return {}
    result = {}
    for key in ('isWatched', 'watched', 'completed', 'isCompleted'):
        if node.get(key) is True:
            result['playcount'] = 1
    for key in ('percentplayed', 'percentWatched', 'percentageWatched', 'progressPercentage'):
        value = node.get(key)
        value=_number(value)
        if value is not None and value<=100:
            result['percentplayed'] = value
    # DVWebClient's progress.percentage is a fraction, unlike percentWatched.
    progress=node.get('progress')
    if isinstance(progress,dict) and not progress.get('isLive'):
        value=_number(progress.get('percentage'))
        if value is not None and value<=1:result['percentplayed']=value*100
    for key,scale in (('resumeTime',1),('resumePositionSeconds',1),('playbackPositionSeconds',1),
                      ('positionSeconds',1),('positionMillis',.001),('resumePositionMillis',.001)):
        value=_number(node.get(key))
        if value is not None:
            result['resume_seconds']=value*scale
            break
    for key in ('watchProgress', 'playbackProgress', 'viewingProgress'):
        if isinstance(node.get(key), dict):
            result.update(progress_info(node[key],depth+1))
    # Playback actions carry the resume time even when the card has no percentage.
    for action in node.get('playbackActions') or []:
        if (isinstance(action,dict) and action.get('videoMaterialType') in (None,'Feature','FEATURE')
                and 'resume_seconds' not in result):
            extra=progress_info(action,depth+1)
            if 'resume_seconds' in extra:result['resume_seconds']=extra['resume_seconds']
    return result

def complete(info):
    from .card_presentation import progress,position_seconds,duration_seconds
    percentage=progress(info);position=position_seconds(info);duration=duration_seconds(info)
    if 0<percentage<100 or position is not None and duration and 0<position<duration:return False
    try:
        return float(info.get('playcount') or 0)>0 or percentage>=100
    except (TypeError,ValueError):
        return False
