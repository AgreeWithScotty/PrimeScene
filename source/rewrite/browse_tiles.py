"""Provider-led routes and artwork from category navigation/service branding."""
from urllib.parse import quote_plus

def genre_path(section,address):
    return '__genre__/'+section+'/'+quote_plus(address) if address else '__prime_movies__' if section=='movies' else '__prime_tv__'

def genres(section,rows):
    all_row={'title':'All movies' if section=='movies' else 'All TV shows','address':None,
        'path':genre_path(section,None),'caption':'Included with Prime',
        'grid_label':'ALL MOVIES' if section=='movies' else 'ALL TV SHOWS'}
    return [all_row]+[dict(row,path=genre_path(section,row['address']),caption='Included with Prime',
        grid_label=row.get('artwork_label','')) for row in rows]

def extras(services=()):
    return [{'title':'Sports','path':'__prime_sports__','caption':'Prime, subscriptions & pay-per-view','grid_label':'SPORTS'}]+[
        dict(row,path='__service__/'+quote_plus(row['address'])) for row in services]
