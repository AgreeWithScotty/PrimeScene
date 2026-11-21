"""Personal viewing state. Never infer viewing history from recommendations."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
from time import time
from .account_state import account_epoch, current_account, persist_if_current


def progress_info(node):
    result = {}
    for key in ('isWatched', 'watched', 'completed', 'isCompleted'):
        if node.get(key) is True:
            result['playcount'] = 1
    for key in ('percentplayed', 'percentWatched', 'percentageWatched', 'progressPercentage'):
        value = node.get(key)
        if isinstance(value, (float, int)) and not isinstance(value, bool) and 0 <= value <= 100:
            result['percentplayed'] = value
    for key in ('watchProgress', 'playbackProgress', 'viewingProgress'):
        if isinstance(node.get(key), dict):
            result.update(progress_info(node[key]))
    return result


def complete(info):
    return bool(info.get('playcount')) or float(info.get('percentplayed') or 0) >= 100


def key(row):
    return str(row.get('identity') or row.get('detail_url') or row.get('url') or '')


def profile_scope(profile):
    # Caller owns the account guard; avoid recursively acquiring its SQLite
    # writer transaction when this runs inside persist_if_current.
    from .session_store import COOKIE_FILE
    from .prime_signin import load_cookies
    jar=load_cookies(Path(profile)/COOKIE_FILE)
    try:
        values=sorted((c.domain,c.name,c.value) for c in jar if 'profile' in c.name.lower())
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()
    finally:
        jar.clear()


def read(profile, epoch):
    path = Path(profile)/'watch-progress.json'
    if not current_account(profile, epoch) or not path.exists():
        return {}
    if path.stat().st_size > 8*1024*1024:
        raise ValueError('Viewing progress exceeds its size limit')
    value = json.loads(path.read_text(encoding='utf-8'))
    return value.get('titles', {}) if value.get('epoch') == epoch and value.get('scope') == profile_scope(profile) else {}


def save(profile, note, position, duration, ended, report):
    context = note.get('watch_context') or {}
    row = context.get('row') or {}
    epoch = context.get('epoch')
    if not epoch or not row or duration <= 0 or position <= 0:
        return
    if not current_account(profile,epoch):
        return
    scope=profile_scope(profile)
    if scope != context.get('scope'):
        return
    def write():
        titles = read(profile, epoch)
        titles[key(row)] = dict(row=deepcopy(row), position=position, duration=duration,
            complete=bool(ended), updated=time(), series=context.get('series'),
            episodes=context.get('episodes') or [])
        titles = dict(sorted(titles.items(), key=lambda item:item[1]['updated'], reverse=True)[:500])
        path=Path(profile)/'watch-progress.json'
        temporary=path.with_suffix('.new')
        temporary.write_text(json.dumps({'epoch':epoch,'scope':scope,'titles':titles}),encoding='utf-8')
        temporary.replace(path)
    stored=persist_if_current(profile,epoch,write)
    report({'event':'watch_progress_saved','stage':'progress_persist','request_id':note.get('request_id'),
        'position_seconds':round(position),'duration_seconds':round(duration),'completed':bool(ended),
        'result':'success' if stored else 'cancelled'})


def decorate(rows, profile, epoch, unfinished=False):
    titles=read(profile,epoch)
    result=[]
    for original in rows:
        row=deepcopy(original)
        saved=titles.get(key(row))
        if saved:
            row.setdefault('info',{}).update(percentplayed=100 if saved['complete'] else
                min(99,100*saved['position']/saved['duration']),playcount=int(saved['complete']),
                resume_seconds=0 if saved['complete'] else saved['position'])
        if not unfinished or not complete(row.get('info') or {}):
            result.append(row)
    return result


def home_rows(profile, epoch):
    titles=read(profile,epoch)
    result=[]
    series_seen=set()
    for saved in sorted(titles.values(),key=lambda item:item['updated'],reverse=True):
        row=deepcopy(saved['row']); context=saved.get('series')
        if context:
            root=context.get('root_path')
            if not root or root in series_seen:
                continue
            series_seen.add(root)
            row.update(title=context.get('title') or row['title'],video=False,
                url='plugin://plugin.video.primeinterface/pv/browse/'+root,identity=root,
                watching_only=True)
            row.setdefault('info',{}).update(mediatype='tvshow')
            result.append(row)
        elif not saved['complete'] and (row.get('info') or {}).get('mediatype') in ('movie','video'):
            result.extend(decorate([row],profile,epoch,True))
    return result
