"""Local English/Spanish language evidence; short proper names remain ambiguous."""
from functools import lru_cache
import re

SPANISH_FUNCTION_WORDS=frozenset('a al de del el la las los y e o u en con por para un una'.split())
# Valid Spanish words spelt exactly like an English one. Lingua cannot tell a
# one-word list item («• Romance», p139) apart, so these never count as
# residual English on their own. Lexical evidence only, never a translation map.
SPANISH_HOMOGRAPHS=frozenset('''
actor animal capital central color control cultural drama error final
general global honor horror hotel humor ideal idea interior exterior
local mental moral motor natural normal original personal piano plural popular
radio real regular romance sensual similar social solar lunar superior inferior
terror total universal visual vital festival particular familiar'''.split())

# Latin technical names of astrology. A text may cite them unchanged from its
# source (p23 «IC is “Imum Coeli”» → «… (“Imum Coeli”, …)»); they are not
# English, but an EN/ES detector gives the unknown words to English. Closed
# list of whole multi-word names: a single word («medium») never qualifies, and
# Lingua is not trusted to tell Latin from English («The Ascendant» → Latin).
LATIN_ASTROLOGICAL_TERMS=('Medium Coeli','Imum Coeli','Pars Fortunae','Caput Draconis',
                          'Cauda Draconis','Via Combusta','Anima Mundi')

def source_identical_latin_terms(units):
    """Listed Latin names copied identically (same spelling) from the unit's own source."""
    found=[]
    for unit in units:
        source=unit.get('source','');target=unit.get('target','')
        for term in LATIN_ASTROLOGICAL_TERMS:
            pattern=r'(?<!\w)'+re.escape(term)+r'(?!\w)'
            hits=[m.group() for m in re.finditer(pattern,source,re.I)]
            for text in dict.fromkeys(hits):
                if re.search(r'(?<!\w)'+re.escape(text)+r'(?!\w)',target):
                    found.append({'code':'source_identical_latin_term','unit_id':unit.get('id'),
                                  'text':text,'source_identical':True})
    return found

@lru_cache(maxsize=1)
def _detector():
    from lingua import Language, LanguageDetectorBuilder
    return LanguageDetectorBuilder.from_languages(Language.ENGLISH,Language.SPANISH).build()

def residual_english(texts,allowed=()):
    from lingua import Language
    permitted={x.casefold().strip() for x in allowed}
    residual=[]
    for text in texts:
        text=text.strip()
        words=re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+',text.casefold())
        # Bibliographic abbreviation after protected page numbers are removed.
        if re.fullmatch(r'pp?\.\s*[\d\s,–—−-]*',text,re.I):continue
        if not text or text.casefold() in permitted or not re.search('[A-Za-z]',text): continue
        if words and all(word in SPANISH_FUNCTION_WORDS or word in SPANISH_HOMOGRAPHS for word in words):continue
        scores=_detector().compute_language_confidence_values(text)
        if scores and scores[0].language==Language.ENGLISH and scores[0].value>=.70:
            residual.append(text)
    return residual

def glossary_matches(text,rows,key='source'):
    """Choose longest overlapping phrases first, retaining separate occurrences."""
    candidates=[]
    for row in rows:
        pattern=re.escape(row[key])
        if key=='source':pattern=re.sub(r'\\ ',r'[\\s\-‐‑]+',pattern)
        for match in re.finditer(r'(?<!\w)'+pattern+r'(?!\w)',text,re.I):
            candidates.append((match.start(),match.end(),row))
    occupied=[]; selected=[]
    for start,end,row in sorted(candidates,key=lambda x:(-(x[1]-x[0]),x[0])):
        if any(start<b and end>a for a,b in occupied): continue
        occupied.append((start,end))
        if row not in selected: selected.append(row)
    return selected

def qa_texts(page,extras=()):
    """Local-only prose inspection includes mixed protected/text spans.

    Removing protected values here does not authorize editing their PDF spans
    or transmitting the original text to any model.
    """
    from .core import PROTECTED,SYMBOL_FONT
    texts=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                if SYMBOL_FONT.search(span['font']): continue
                text=PROTECTED.sub(' ',span['text'])
                for word in extras:
                    text=re.sub(r'(?<!\w)'+re.escape(word)+r'(?!\w)',' ',text)
                if re.search('[A-Za-z]',text): texts.append(text)
    return texts
