"""Remove obsolete In Progress snapshots; only fresh provider lists are shown."""
import hashlib
import json
from pathlib import Path
from .account_state import persist_if_current
from .prime_diagnostics import exception_details


def discard_stored(profile, epoch, report):
    root=Path(profile).resolve()
    if root.name!='plugin.video.primeinterface':
        raise ValueError('Invalid In Progress profile boundary')
    digest=hashlib.sha256(b'__watching__').hexdigest()
    targets=[root/'watch-progress.json',root/'watch-progress.new',
        root/'navigation-cache'/(digest+'.json'),root/'navigation-cache'/(digest+'.new')]
    removed=[];failures=[]
    def discard():
        for target in targets:
            try:
                if root not in target.resolve().parents:
                    raise ValueError('In Progress cleanup escaped its profile')
                if target.exists():
                    target.unlink();removed.append(target.name)
            except Exception as error:
                failures.append(target.name)
                report({'event':'progress_cache_delete_failed','stage':'discard_old_progress',
                    'file_kind':target.suffix,'exception':exception_details(error),'result':'failure'})
        note=root/'aurora-playback.json'
        if note.is_file():
            try:
                if note.stat().st_size>1024*1024:raise ValueError('Oversized playback note')
                value=json.loads(note.read_text(encoding='utf-8'))
                if 'watch_context' in value:
                    value.pop('watch_context')
                    temporary=note.with_suffix('.new')
                    temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(note)
            except Exception as error:
                failures.append('playback_note_context')
                report({'event':'progress_note_cleanup_failed','stage':'discard_old_progress',
                    'exception':exception_details(error),'result':'failure'})
    stored=persist_if_current(root,epoch,discard)
    report({'event':'progress_cache_discarded','stage':'discard_old_progress',
        'removed_files':len(removed),'failed_files':len(failures),
        'result':'cancelled' if not stored else 'failure' if failures else 'success'})
    if failures:raise RuntimeError('Old In Progress data could not be completely cleared. See Diagnostics.')
