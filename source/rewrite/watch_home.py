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
    if node.get('isTrailer') is True or node.get('videoMaterialType') not in (None,'Feature','FEATURE','feature'):return {}
    result = {}
    status=node.get('playbackStatus')
    state={'FeatureFinished':'watched','FeatureNotStarted':'unwatched','FeatureInProgress':'in_progress'}.get(status) if isinstance(status,str) else None
    if state:
        result.update(watch_state=state,playcount=1 if state=='watched' else 0)
        if state in ('watched','unwatched'):result['percentplayed']=100 if state=='watched' else 0
    runtime=_number(node.get('runTime'))
    if runtime is not None and runtime>0:result['duration']=runtime
    for key in ('isWatched', 'watched', 'completed', 'isCompleted'):
        if node.get(key) is True:
            result.update(playcount=1,watch_state='watched')
    if not state and not result.get('playcount') and any(node.get(key) is False for key in ('isWatched','watched','completed','isCompleted')):
        result.update(playcount=0,watch_state='unwatched')
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
    # Follow declared playback wrappers only, never arbitrary related-title maps.
    for key in ('watchProgress','playbackProgress','viewingProgress','playbackActions',
                'primaryActions','payload','playback','main','children'):
        value=node.get(key)
        for child in value if isinstance(value,list) else (value,):
            if isinstance(child,dict):result.update(progress_info(child,depth+1))
    if result.get('watch_state')=='unwatched' and (result.get('resume_seconds',0)>0 or result.get('percentplayed',0)>0):
        result['watch_state']='in_progress'
    return result

def complete(info):
    from .card_presentation import progress,position_seconds,duration_seconds
    percentage=progress(info);position=position_seconds(info);duration=duration_seconds(info)
    if 0<percentage<100 or position is not None and duration and 0<position<duration:return False
    try:
        return float(info.get('playcount') or 0)>0 or percentage>=100
    except (TypeError,ValueError):
        return False
