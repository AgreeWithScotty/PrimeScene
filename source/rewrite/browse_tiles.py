"""Provider-led routes and artwork from category navigation/service branding."""
from urllib.parse import quote_plus



def genre_path(section,address):
    return '__genre__/'+section+'/'+quote_plus(address) if address else '__prime_movies__' if section=='movies' else '__prime_tv__'

def genres(section,rows):
    all_row={'title':'All movies' if section=='movies' else 'All TV shows','address':None,
        'path':genre_path(section,None),'caption':'Included with Prime',
        'grid_label':'ALL MOVIES' if section=='movies' else 'ALL TV SHOWS'}
    result=[dict(row,path=genre_path(section,row['address']),caption='Included with Prime',
        grid_label=row.get('artwork_label','')) for row in rows]+[all_row,
        {'title':'Purchases','address':None,'path':'__purchases__/'+section,
         'caption':'Your purchased movies' if section=='movies' else 'Your purchased TV shows','grid_label':'PURCHASES'}]
    for row in result:
        if row.get('artwork_source')!='provider_title_backdrop':
            row.pop('artwork',None)
    return result

def extras(services=()):
    return [{'title':'Sports','path':'__prime_sports__','caption':'Prime, subscriptions & pay-per-view','grid_label':'SPORTS'}]+[
        dict(row,path='__service__/'+quote_plus(row['address'])) for row in services]
