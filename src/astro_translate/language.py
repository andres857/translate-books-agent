"""Local English/Spanish language evidence; short proper names remain ambiguous."""
from functools import lru_cache
import re

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
        if not text or text.casefold() in permitted or not re.search('[A-Za-z]',text): continue
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
