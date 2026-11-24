"""Provider-led routes and artwork from category navigation/service branding."""
from urllib.parse import quote_plus
import json
from pathlib import Path

_ASSETS=Path(__file__).resolve().parent.parent/'skins/Default/media/genre-art'
_ART=None

def genre_art(section,title):
    global _ART
    if _ART is None:
        index=Path(__file__).with_name('genre_art.json')
        _ART=json.loads(index.read_text(encoding='utf-8'))['tiles'] if index.is_file() else {}
    # These are public release assets, never an account's catalog snapshot.
    names=_ART.get(section+'|'+title) or _ART.get(('tv' if section=='movies' else 'movies')+'|'+title) or []
    return [str(_ASSETS/name) for name in names if Path(name).name==name and (_ASSETS/name).is_file()][:1]

def genre_path(section,address):
    return '__genre__/'+section+'/'+quote_plus(address) if address else '__prime_movies__' if section=='movies' else '__prime_tv__'

def genres(section,rows):
    all_row={'title':'All movies' if section=='movies' else 'All TV shows','address':None,
        'path':genre_path(section,None),'caption':'Included with Prime',
        'grid_label':'ALL MOVIES' if section=='movies' else 'ALL TV SHOWS'}
    result=[all_row]+[dict(row,path=genre_path(section,row['address']),caption='Included with Prime',
        grid_label=row.get('artwork_label','')) for row in rows]
    for row in result:
        if row.get('artwork') and '/genre_background_' not in row['artwork']:
            row['artwork_source']='provider_navigation'
            continue
        images=genre_art(section,'*' if row is all_row else row['title'])
        if images:row.update(artwork=images[0],artwork_source='bundled_landscape',grid_label=row['title'])
    return result

def extras(services=()):
    return [{'title':'Sports','path':'__prime_sports__','caption':'Prime, subscriptions & pay-per-view','grid_label':'SPORTS'}]+[
        dict(row,path='__service__/'+quote_plus(row['address'])) for row in services]
