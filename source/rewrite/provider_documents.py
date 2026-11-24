"""Extract provider JSON, collection navigation and visible empty-state evidence."""
import json,re
from html.parser import HTMLParser
from time import monotonic

LIBRARY_LABELS={'purchases','purchasesrentals','purchasesandrentals','yourpurchases','purchasedmedia'}
WATCHLIST_LABELS={'watchlist','yourwatchlist','mywatchlist'}
_VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

def library_label(value):return isinstance(value,str) and re.sub('[^a-z]','',value.casefold()) in LIBRARY_LABELS
def watchlist_label(value):return isinstance(value,str) and re.sub('[^a-z]','',value.casefold()) in WATCHLIST_LABELS

def empty_copy(text,collection):
    """Only an explicit absence statement in the rendered main content qualifies."""
    text=' '.join(text.lower().replace('\u2019',"'").split())
    subject=r'(?:watch\s*list)' if collection=='watchlist' else r'(?:purchases?(?: (?:and|or) rentals?)?|purchased (?:titles|videos|movies|media)|library)'
    patterns=(
        r'(?:your |this |the )?'+subject+r' (?:is|are) (?:currently )?empty\b',
        r"(?:you (?:have|currently have)|you've got|there are) no (?:items? (?:in|on) (?:your |the )?)?"+subject+r'\b',
        r"(?:you (?:haven't|have not)) (?:yet )?(?:purchased|bought|rented)(?: or (?:purchased|bought|rented))? (?:any )?(?:titles|videos|movies|tv shows|media|anything)\b" if collection=='purchases' else
            r"(?:you (?:haven't|have not)) (?:yet )?added (?:any )?(?:titles|videos|movies|tv shows|anything) to (?:your |the )?watch\s*list\b",
        r'no (?:items?|titles?|videos?|movies?|tv shows?) (?:in|on) (?:your |the )?'+subject+r'\b',
        r"you (?:don't|do not) have (?:any )?"+subject+r'\b',
        r'no '+subject+r' (?:yet|to display|found)\b',
        r'\b(?:nothing here yet|nothing to (?:display|show)|no (?:items|titles|videos|movies)(?: here)? (?:yet|to display|to show|found))\b',
    )
    return next(('visible_main_absence_'+str(i+1) for i,pattern in enumerate(patterns) if re.search(pattern,text)),None)

class PageDocuments(HTMLParser):
    def __init__(self,label_matches=library_label):
        super().__init__();self.capture=False;self.parts=[];self.scripts=[];self.anchor=None;self.library_links=[]
        self.label_matches=label_matches;self.signin=False;self.verification=False
        self.frames=[];self.main_text=[];self.main_characters=0;self.selected_links=0;self.script_types={}
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        hidden=('hidden' in attributes or attributes.get('aria-hidden','').lower()=='true'
            or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',attributes.get('style',''),re.I)))
        if tag not in _VOID:self.frames.append((tag,hidden,tag=='main' or attributes.get('role')=='main'))
        if tag=='script':
            kind=(attributes.get('type') or '').split(';')[0].lower()
            if kind in ('application/json','application/ld+json','text/template'):
                self.capture=True;self.parts=[]
                self.script_types[kind]=self.script_types.get(kind,0)+1
        elif tag=='a':
            self.anchor=[attributes.get('href'),attributes.get('aria-label') or '','',
                attributes.get('aria-current') in ('page','true') or attributes.get('aria-selected')=='true']
        elif tag=='input':
            self.signin=self.signin or attributes.get('type','').lower()=='password'
            self.verification=self.verification or bool(re.search('otp|verificationcode',(attributes.get('name','')+' '+attributes.get('id','')).casefold()))
    def handle_data(self,data):
        if self.capture:self.parts.append(data)
        else:
            if self.anchor is not None:self.anchor[2]+=data
            if (self.main_characters<65536 and any(f[2] for f in self.frames)
                    and not any(f[1] or f[0] in ('script','style','noscript','template') for f in self.frames)):
                self.main_text.append(data);self.main_characters+=len(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.capture:self.scripts.append(''.join(self.parts));self.parts=[];self.capture=False
        elif tag=='a' and self.anchor is not None:
            if self.label_matches(self.anchor[1]) or self.label_matches(self.anchor[2]):
                self.library_links.append(self.anchor[0]);self.selected_links+=bool(self.anchor[3])
            self.anchor=None
        for index in range(len(self.frames)-1,-1,-1):
            if self.frames[index][0]==tag:
                del self.frames[index:];break

def read_documents(data,content_type,label_matches=library_label,collection=None):
    started=monotonic();parser=None
    if content_type.endswith('json') or content_type.endswith('+json'):scripts=[data]
    else:
        parser=PageDocuments(label_matches);parser.feed(data);parser.close();scripts=parser.scripts
    extracted=monotonic();documents=[];failures=0;locations=[]
    for value in scripts:
        try:documents.append(json.loads(value))
        except (ValueError,RecursionError) as error:
            failures+=1
            if len(locations)<3:locations.append({'type':type(error).__name__,'line':getattr(error,'lineno',None),'column':getattr(error,'colno',None)})
    details={'document_extract_ms':round((extracted-started)*1000),
        'document_decode_ms':round((monotonic()-extracted)*1000),'json_documents':len(documents),
        'json_decode_failures':failures,'html_title_cards_parsed':0,'json_failure_locations':locations,
        'sign_in_form_present':bool(parser and parser.signin),'verification_form_present':bool(parser and parser.verification)}
    if collection:
        rendered=' '.join(parser.main_text) if parser else ''
        normalized=' '.join(rendered.lower().replace('\u2019',"'").split())
        subject=r'(?:watch\s*list|purchases?|rentals?|library)'
        details.update(selected_collection_navigation=bool(parser and parser.selected_links),
            rendered_main_present=bool(parser and any(text.strip() for text in parser.main_text)),
            rendered_empty_proof=empty_copy(rendered,collection) if parser else None,
            rendered_error_cue=bool(re.search(r"something went wrong|unable to|could(?:n't| not) (?:load|retrieve)|temporarily unavailable|try again",normalized)),
            rendered_signin_required=bool(re.search(r'sign in to (?:view|see|access) (?:your |the )?'+subject,normalized)),
            rendered_absence_cues={word:bool(re.search(r'\b'+word+r'\b',normalized)) for word in
                ('empty','nothing','no','yet','purchased','rented','purchases','rentals','watchlist','library','videos','items')},
            script_type_inventory=parser.script_types if parser else {})
    return documents,details,list(parser.library_links) if parser else []
