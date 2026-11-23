"""Category-specific empty/temporarily unavailable display text."""
from .display_text import clean

def section_intro(section):
    if section=='home':return {'title':'In Progress','message':'Your in-progress movies or TV shows will display here.'}
    if section=='purchases':return {'title':'Purchases','message':'Your purchased movies and TV shows appear here. Visit Amazon to purchase media.'}
    return {'title':'Search Prime Video','message':"Select Search and enter a movie or TV show title. Search Amazon's full Prime Video catalogue, including Prime titles, rentals, purchases and add-on subscriptions."}

def browse_intro(section,path,heading='',query=None,series=None):
    """Own the destination before its first result; do not assert it is empty."""
    if path in ('__watching__','__prime_purchases__','search'):
        intro=section_intro('home' if path=='__watching__' else 'purchases' if path=='__prime_purchases__' else 'search')
        if query is not None:intro={'title':'Search results','message':"Movies and TV shows matching your search across Amazon's full Prime Video catalogue."}
        return intro
    if series:
        return {'title':clean(series.get('title') or heading),'message':
            'Choose a season to browse its episodes.' if series.get('view')=='seasons' else
            clean(series.get('season_label') or 'Episodes')}
    return {'title':clean(heading or ('Movies' if section=='movies' else 'TV' if section=='tv' else 'Extras')),
        'message':'Movies included with Prime.' if section=='movies' else 'TV shows included with Prime.' if section=='tv' else
        'Explore this service on Prime Video. A separate subscription or purchase may be required.'}

def category_status(section,path='',heading='',query=None,series=None,unavailable=False):
    heading=clean(heading or '');series=series or {}
    if series:
        show=clean(series.get('title') or heading) or 'this TV show'
        season=clean(series.get('season_label') or '')
        if series.get('view')=='seasons':
            title=show;kind='seasons'
            empty='No seasons are available for %s right now.'%show
            retry='Seasons for %s are unavailable right now. Please try again.'%show
        else:
            subject=show+(' (%s)'%season if season else '')
            title=subject;kind='episodes'
            empty='No episodes are available for %s right now.'%subject
            retry='Episodes for %s are unavailable right now. Please try again.'%subject
    elif query is not None or path=='search':
        title='Search';kind='search'
        empty='No movies or TV shows matched your search.'
        retry='Search results are unavailable right now. Please try again.'
    elif path=='__service_directory__':
        title='Extras';kind='services'
        empty='No subscription services are available to display right now.'
        retry='Subscription services are unavailable right now. Please try again.'
    elif path.startswith('__genres__/'):
        title='Movie genres' if section=='movies' else 'TV genres';kind='genres'
        empty='No %s genres are available to display right now.'%('movie' if section=='movies' else 'TV')
        retry='%s are unavailable right now. Please try again.'%title
    elif section=='home':
        title='In Progress';kind='progress'
        empty='Your in-progress movies or TV shows will display here.'
        retry='Your in-progress movies and TV shows are unavailable right now. Please try again.'
    elif section=='purchases':
        title='Purchases';kind='purchases'
        empty='This account has no purchased media to display. Visit Amazon to purchase media.'
        retry='Your Amazon purchase library could not be retrieved. Please try again.'
    elif section in ('movies','tv'):
        noun='movies' if section=='movies' else 'TV shows'
        genre=path.startswith('__genre__/')
        title=heading if genre and heading else 'Movies' if section=='movies' else 'TV'
        kind='genre_'+section if genre else section
        empty=('No Prime-included %s are available in %s right now.'%(noun,heading) if genre and heading else
            'No Prime-included %s are available right now.'%noun)
        retry=('%s %s are unavailable right now. Please try again.'%(heading,noun) if genre and heading else
            '%s are unavailable right now. Please try again.'%('Movies' if section=='movies' else 'TV shows'))
    elif path=='__prime_sports__' or heading.casefold()=='sports':
        title='Sports';kind='sports'
        empty='No sports events are available to display right now.'
        retry='Sports events are unavailable right now. Please try again.'
    else:
        title=heading or 'Extras';kind='service' if path.startswith('__service__/') else 'section'
        empty='No titles are available in %s right now.'%title
        retry='%s titles are unavailable right now. Please try again.'%title
    return {'title':clean(title),'message':clean(retry if unavailable else empty),'kind':kind,
        'state':'unavailable' if unavailable else 'empty'}
