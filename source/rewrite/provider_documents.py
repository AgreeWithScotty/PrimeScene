"""Extract provider JSON and explicit library navigation without parsing title cards."""
import json,re
from html.parser import HTMLParser
from time import monotonic

LIBRARY_LABELS={'purchases','purchasesrentals','purchasesandrentals','yourpurchases','purchasedmedia'}
def library_label(value):return isinstance(value,str) and re.sub('[^a-z]','',value.casefold()) in LIBRARY_LABELS

class PageDocuments(HTMLParser):
    def __init__(self):
        super().__init__();self.capture=False;self.parts=[];self.scripts=[];self.anchor=None;self.library_links=[]
        self.signin=False;self.verification=False
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if tag=='script' and (attributes.get('type') or '').split(';')[0].lower() in ('application/json','application/ld+json','text/template'):
            self.capture=True;self.parts=[]
        elif tag=='a':self.anchor=[attributes.get('href'),attributes.get('aria-label') or '','']
        elif tag=='input':
            self.signin=self.signin or attributes.get('type','').lower()=='password'
            self.verification=self.verification or bool(re.search('otp|verificationcode',(attributes.get('name','')+' '+attributes.get('id','')).casefold()))
    def handle_data(self,data):
        if self.capture:self.parts.append(data)
        elif self.anchor is not None:self.anchor[2]+=data
    def handle_endtag(self,tag):
        if tag=='script' and self.capture:self.scripts.append(''.join(self.parts));self.parts=[];self.capture=False
        elif tag=='a' and self.anchor is not None:
            if library_label(self.anchor[1]) or library_label(self.anchor[2]):self.library_links.append(self.anchor[0])
            self.anchor=None

def read_documents(data,content_type):
    started=monotonic();parser=None
    if content_type.endswith('json') or content_type.endswith('+json'):scripts=[data]
    else:
        parser=PageDocuments();parser.feed(data);parser.close();scripts=parser.scripts
    extracted=monotonic();documents=[];failures=0;locations=[]
    for value in scripts:
        try:documents.append(json.loads(value))
        except (ValueError,RecursionError) as error:
            failures+=1
            if len(locations)<3:locations.append({'type':type(error).__name__,'line':getattr(error,'lineno',None),'column':getattr(error,'colno',None)})
    return (documents,{'document_extract_ms':round((extracted-started)*1000),
        'document_decode_ms':round((monotonic()-extracted)*1000),'json_documents':len(documents),
        'json_decode_failures':failures,'html_title_cards_parsed':0,
        'json_failure_locations':locations,
        'sign_in_form_present':bool(parser and parser.signin),'verification_form_present':bool(parser and parser.verification)},
        list(parser.library_links) if parser else [])
