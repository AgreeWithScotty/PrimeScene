"""Presentation-only tiles; provider addresses remain in discovered navigation."""
ART={'Action & adventure':'action','Action':'action','Adventure':'action','Anime':'popular',
    'Animation':'family','Comedy':'comedy','Documentary':'documentary','Drama':'drama',
    'Fantasy':'home','Historical':'my-stuff','Horror':'horror','Kids & family':'family',
    'Mystery':'mystery','Mystery & thriller':'mystery','Suspense':'mystery','Thriller':'mystery',
    'Romance':'romance','Romantic comedy':'romance','Science fiction':'sci-fi',
    'Science fiction & fantasy':'sci-fi','Western':'action','Musicals':'tv','Music':'tv','Crime':'mystery'}

def genres(section,rows):
    all_row={'title':'All movies' if section=='movies' else 'All TV shows','address':None,
        'art':'cinema' if section=='movies' else 'tv','caption':'Included with Prime'}
    return [all_row]+[dict(row,art=ART.get(row['title'],'cinema'),caption='Included with Prime') for row in rows]

def extras():
    return [
        {'title':'Sports','path':'__prime_sports__','art':'sports',
            'caption':'Prime, subscriptions & pay-per-view'},
        {'title':'Add-on subscriptions','path':'__prime_channels__','art':'new-releases',
            'caption':'Additional subscription required'},
    ]
