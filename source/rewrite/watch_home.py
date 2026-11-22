"""Decode explicit provider watch-state fields; no local In Progress store."""

def progress_info(node):
    result = {}
    for key in ('isWatched', 'watched', 'completed', 'isCompleted'):
        if node.get(key) is True:
            result['playcount'] = 1
    for key in ('percentplayed', 'percentWatched', 'percentageWatched', 'progressPercentage'):
        value = node.get(key)
        if isinstance(value, (float, int)) and (not isinstance(value, bool)) and (0 <= value <= 100):
            result['percentplayed'] = value
    for key in ('watchProgress', 'playbackProgress', 'viewingProgress'):
        if isinstance(node.get(key), dict):
            result.update(progress_info(node[key]))
    return result

def complete(info):
    try:
        return float(info.get('playcount') or 0)>0 or float(info.get('percentplayed') or 0)>=100
    except (TypeError,ValueError):
        return False
