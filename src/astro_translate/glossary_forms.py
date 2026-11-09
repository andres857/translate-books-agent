"""Deterministic Spanish number/gender inflection, never synonym acceptance."""
import re
import unicodedata

WORD = re.compile(r'[^\W\d_]+', re.UNICODE)
INVARIABLE = frozenset('a al de del el la las los un una unos unas en con por para sin y o que se su sus es'.split())


def forms(word):
    word = unicodedata.normalize('NFC', word.casefold())
    # The approved noun ``luna`` is also used adjectivally in labels such as
    # ``nodos lunares``.  This lexical derivation is specific and does not
    # admit unrelated synonyms.
    if word == 'luna':
        return {'luna', 'lunas', 'lunar', 'lunares'}
    if word in INVARIABLE or len(word)<4:
        return {word}
    singular = word
    if word.endswith(('os','as')):
        singular=word[:-1]
    elif word.endswith('es') and len(word)>5:
        # A vowel-final approved lemma such as "planetas" uses -s above;
        # consonant-final plurals use -es. Do not strip short names (Aries).
        singular=word[:-2]
    elif word.endswith('s') and word[-2] in 'aeiou':
        singular=word[:-1]
    variants={word,singular}
    if re.search(r'(?:ic|ad|id|iv|os|ari|an|án|in|ólog)[oa]$',singular):
        stem=singular[:-1]
        variants.update(stem+ending for ending in ('o','a','os','as'))
    elif singular.endswith(('a','e','i','o','u')):
        variants.add(singular+'s')
    elif singular.endswith('z'):
        variants.add(singular[:-1]+'ces')
    elif not singular.endswith('s'):
        variants.add(singular+'es')
    return variants


def glossary_form_present(text, approved, related_terms=()):
    text=unicodedata.normalize('NFC',re.sub(r'\{v\d+\}', '', text))
    if _strict_form_present(text,approved):return True
    approved_words=[m.group() for m in WORD.finditer(approved)]
    # A second approved glossary item can be grammatically inserted inside a
    # translated multiword term: "recorrido de Júpiter por el zodíaco" keeps
    # the required phrase once the separately approved proper name is removed.
    for related in sorted((str(term) for term in related_terms if str(term).strip()),key=len,reverse=True):
        related=unicodedata.normalize('NFC',related)
        related_words=[m.group() for m in WORD.finditer(related)]
        overlap=0
        while overlap<min(len(approved_words),len(related_words)) and forms(approved_words[overlap])&forms(related_words[overlap]):
            overlap+=1
        patterns=[]
        if overlap and overlap<len(related_words):
            tail=r'\s+'.join(re.escape(w) for w in related_words[overlap:])
            patterns.append(r'(?<!\w)'+tail+r'(?:\s+y(?:\s+de)?)?(?!\w)')
        patterns.append(r'(?<!\w)(?:(?:de|del)\s+)?'+re.escape(related)+r'(?!\w)')
        for pattern in patterns:
            reduced=re.sub(pattern,' ',text,flags=re.I)
            if reduced!=text and _strict_form_present(reduced,approved):return True
    return False


def _strict_form_present(text,approved):
    pieces=[];cursor=0
    for match in WORD.finditer(approved):
        pieces.append(re.escape(approved[cursor:match.start()]))
        pieces.append('(?:'+'|'.join(re.escape(w) for w in sorted(forms(match.group())))+')')
        cursor=match.end()
    pieces.append(re.escape(approved[cursor:]))
    pattern=''.join(pieces).replace(r'\ ',r'\s+')
    return bool(re.search(r'(?<!\w)'+pattern+r'(?!\w)',text,re.I))
