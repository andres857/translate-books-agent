"""Editorial ordinal rules for The Secrets of Astrology, source-hash bound."""
import re
from .core import SYMBOL_FONT
from dataclasses import dataclass

SOURCE_SHA256='517746c22d1897c06c6f5efde58564956c4389321f72b068494a25c5e7858a9c'
EN_ORDINAL=re.compile(r'(?<!\w)(\d+)\s*(st|nd|rd|th)(?!\w)',re.I)
ES_SUFFIX=re.compile(r'\.(?:er|º|ª)(?!\w)')
SIGN=re.compile(r'THE (\d+)(ST|ND|RD|TH) SIGN OF THE ZODIAC')
HOUSE=re.compile(r'(\d+)(?:st|nd|rd|th) HOUSE',re.I)

def suffix(ordinal,gender='m'):
    return '.ª' if gender=='f' else '.er' if int(ordinal) in (1,3) else '.º'

def lines(page):
    return [line for b in page.get_text('dict')['blocks'] for line in b.get('lines', [])]

def _diagram_pair(line):
    """Digit + raised English suffix opening a horizontal line, or None.

    Trailing whitespace belongs to the suffix span («rd »). Further spans on the
    same PDF line (an adjacent label such as «Friendships») stay ordinary units.
    """
    raw=[s for s in line['spans'] if s['text'].strip()]
    if len(raw)<2 or tuple(line['dir'])!=(1.,0.):return None
    digit,ending=raw[:2]
    if (not digit['text'].isdigit() or not 1<=int(digit['text'])<=12
        or not re.fullmatch(r'st|nd|rd|th',ending['text'].strip(),re.I)
        or not .35<ending['size']/digit['size']<.85
        or ending['origin'][1]>=digit['origin'][1]-digit['size']*.1
        or abs(ending['origin'][0]-digit['bbox'][2])>digit['size']*.15
        or any(SYMBOL_FONT.search(s['font']) for s in raw[:2])):return None
    if any(abs(s['size']-ending['size'])<.01 and s['font']==ending['font'] for s in raw[2:]):return None
    return digit,ending

def _cohort(page):
    return [line for line in lines(page) if _diagram_pair(line)]

def _context(text):
    return bool(re.search(r'\bhouse(?:s)?\b',text,re.I) and re.search(r'\bMC\b',text) and re.search(r'\bIC\b',text))

def _spread(values,extent,ratio):
    return bool(values) and max(values)-min(values)>=extent*ratio

def facing_page(page):
    """Facing leaf of a two-page spread, using the same parity as editorial_margins."""
    doc=page.parent;index=page.number+1 if page.number%2 else page.number-1
    return doc[index] if doc is not None and 0<=index<len(doc) else None

def house_diagram_lines(page):
    """Secrets-specific MC/IC house-diagram cohort detector.

    The wheel may be split over a spread (p138/139, 170/171, 172/173): then the
    house/MC/IC context and the two-dimensional distribution are those of the
    joined wheel, whose houses must not repeat across the two leaves.
    """
    candidates=_cohort(page)
    digits=[line['spans'][0]['text'] for line in candidates]
    if len(set(digits))<3 or len(set(digits))!=len(digits) or not (page.get_drawings() or page.get_images()):return []
    text=' '.join(''.join(s['text'] for s in line['spans']) for line in lines(page))
    xs=[line['bbox'][0] for line in candidates];ys=[line['bbox'][1] for line in candidates]
    alone=_spread(xs,page.rect.width,.15) and _spread(ys,page.rect.height,.15)
    if alone and _context(text):return candidates
    facing=facing_page(page)
    if facing is None:return []
    other=_cohort(facing);other_digits=[line['spans'][0]['text'] for line in other]
    if not other or set(other_digits)&set(digits) or len(set(other_digits))!=len(other_digits):return []
    if len(set(digits)|set(other_digits))<6:return []
    # Each leaf carries a genuine 2-D part of the wheel, not a list column.
    if not (_spread(xs,page.rect.width,.05) and _spread(ys,page.rect.height,.05)):return []
    joined=text+' '+' '.join(''.join(s['text'] for s in line['spans']) for line in lines(facing))
    return candidates if _context(joined) else []

@dataclass(frozen=True)
class SecretsOrdinalPolicy:
    source_sha256:str=SOURCE_SHA256
    def diagram_lines(self,page):return house_diagram_lines(page)
    def diagram_pair(self,line):return _diagram_pair(line)
    def kind(self,line,diagram_lines):
        text=''.join(s['text'] for s in line['spans']).strip()
        return ('house_diagram' if line in diagram_lines else 'sign' if SIGN.fullmatch(text)
                else 'house' if HOUSE.fullmatch(text) else None)
    def inventory_treatment(self,line,text,match,diagram_lines,translated_units):
        if line in diagram_lines:treatment='local_house_diagram'
        elif SIGN.fullmatch(text):treatment='local_sign_heading'
        elif HOUSE.fullmatch(text) and len(line['spans'])==3:treatment='local_house_heading'
        elif re.search(r'\bhouse\b',text,re.I):treatment='house_feminine_or_existing_casa_number'
        else:treatment='pending_context'
        if treatment not in ('local_house_diagram','local_sign_heading','local_house_heading'):
            owners=[u for u in translated_units if any(s['source']==raw['text'] and list(s['bbox'])==list(raw['bbox'])
                    for s in u.get('spans',[]) for raw in line['spans'])]
            if any(not EN_ORDINAL.search(u.get('target','')) and re.search(r'\bcasa\s+'+re.escape(match.group(1))+r'\b',u.get('target',''),re.I) for u in owners):
                treatment='existing_casa_number'
        return treatment
    def run_text(self,kind,raw,digit,ending,style):
        if kind=='sign':
            return [{'text':'EL ','style':style(raw[0])},{'text':digit['text'],'style':style(digit)},
                    {'text':suffix(digit['text']),'style':style(ending)},
                    {'text':' SIGNO DEL ZODÍACO','style':style(raw[-1])}]
        if kind=='house_diagram':
            return [{'text':digit['text'],'style':style(digit)},
                    {'text':suffix(digit['text'],'f'),'style':style(ending)}]
        return [{'text':'CASA ','style':style(raw[-1])},{'text':digit['text'],'style':style(digit)}]
    def target(self,kind,digit):
        return (f'EL {digit["text"]}{suffix(digit["text"])} SIGNO DEL ZODÍACO' if kind=='sign'
                else digit['text']+'.ª' if kind=='house_diagram' else f'CASA {digit["text"]}')
    def rule(self,kind):
        return 'I3_house_feminine_delegated_2026-11-07' if kind=='house_diagram' else 'I3_local_pending_G4'
    def count_masculine_marker(self,kind,digit):return kind=='sign' and suffix(digit['text'])=='.º'

SECRETS_POLICY=SecretsOrdinalPolicy()
