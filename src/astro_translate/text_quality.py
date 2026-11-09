"""Conservative, local I6 diagnostics. Never translate or repair lexical text.

The small unambiguous accent lexicon below supplements approved glossary/guide
words. It is a detection vocabulary, not a replacement map. Ambiguous accent
pairs (e.g. EL/ÉL, PUBLICO/PÚBLICO, PRACTICA/PRÁCTICA) are deliberately excluded.
Grammar rules cover only the observed article + asterismo and encontrar + clitic
+ explicit zodiac-name constructions; this is not a general Spanish parser.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re
import unicodedata

import pymupdf as fitz

ACCENT_WORDS = ('EDUCACIÓN COMUNICACIÓN ZODÍACO TRANSFORMACIÓN INTUICIÓN '
                'PERCEPCIÓN EMOCIÓN EMOCIONES CONEXIÓN RELACIÓN EXPRESIÓN '
                'INSPIRACIÓN IMAGINACIÓN PROFESIÓN VOCACIÓN SÍMBOLO SÍMBOLOS '
                'ASTROLOGÍA ENERGÍA FILOSOFÍA').split()
AMBIGUOUS = set('PUBLICO PRACTICA PRACTICO HABITO DEPOSITO ANIMO TERMINO '
                'NUMERO CONTINUO MEDICO CRITICO CALCULO TRANSITO TITULO '
                'ARTICULO CAPITULO AMPLIO CIRCULO MODULO ESTIMULO'.split())
WORD = re.compile(r'[^\W\d_]+', re.UNICODE)
STYLE = re.compile(r'\{v\d+\}')
GLYPH = re.compile(r'[\u2600-\u27ff\ue000-\uf8ff]')


def _issue(identifier, code, action='retranslate', **evidence):
    return dict(unit_id=identifier, code=code, action=action, **evidence)


def _fold(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.upper())
                   if unicodedata.category(c) != 'Mn')


def _is_upper_text(text):
    # Feminine/masculine ordinal markers are protected lexical glyphs, not
    # lowercase editorial letters.
    return re.sub(r'[ªº]', '', text).isupper()


def _repetitions(text):
    """Immediate n>=2 lexical repetitions, never across sentence/token barriers."""
    found = []
    for segment in re.finditer(r'[^.!?;{}\d]+', text):
        words = list(WORD.finditer(segment.group()))
        lower = [w.group().casefold() for w in words]
        covered = set()
        for n in range(len(words)//2, 1, -1):
            for i in range(len(words)-2*n+1):
                if lower[i:i+n] != lower[i+n:i+2*n] or any(j in covered for j in range(i, i+2*n)):
                    continue
                covered.update(range(i, i+2*n))
                found.append((n, segment.start()+words[i].start(),
                              segment.start()+words[i+2*n-1].end()))
    return sorted(found, key=lambda item: item[1])


CONTENT_STOP = frozenset("a an the of to in on at and or is are was were be it its this that with for from by de del la las el los un una unos unas y o en al por para con sin es son se su sus que como lo nos me te mi tu tus nuestro nuestros nuestra nuestras estos estas esos esas más mas menos muy tan".split())


def _separated_repetitions(text, *, source=False):
    """Repeated content phrases within one sentence, with an intervening gap."""
    found=[]
    for segment in re.finditer(r'[^.!?;{}\d]+',text):
        words=list(WORD.finditer(segment.group()));lower=[w.group().casefold() for w in words]
        covered=set()
        for n in range(len(words)//2,0 if source else 1,-1):
            for i in range(len(words)-2*n):
                phrase=lower[i:i+n]
                content=sum(w not in CONTENT_STOP for w in phrase)
                if content<(1 if source else 2):continue
                if n==1 and len(phrase[0])<4:continue
                for j in range(i+n+1,len(words)-n+1):
                    if phrase!=lower[j:j+n] or any(k in covered for k in range(j,j+n)):continue
                    covered.update(range(i,i+n));covered.update(range(j,j+n))
                    found.append((content,segment.start()+words[i].start(),segment.start()+words[j+n-1].end()))
    return sorted(found,key=lambda row:row[1])


def _case_issues(unit):
    from .dates import looks_like_date
    if unit.get('native_ordinal') or unit.get('translation_method')=='deterministic_date' or looks_like_date(unit['source']):return []
    source=unit['source'];runs=_source_runs(unit)
    if _is_upper_text(source) and sum(c.isalpha() for c in source)>1:
        return [] if _is_upper_text(unit['target']) else [_issue(unit['id'],'source_case','local')]
    expected={}
    for run in runs:
        key=tuple(sorted(run['style'].items()))
        expected[key]=expected.get(key,'')+source[run['start']:run['end']]
    issues=[]
    for run in unit.get('target_runs',[]):
        original=expected.get(tuple(sorted(run['style'].items())),'')
        if _is_upper_text(original) and sum(c.isalpha() for c in original)>1 and not _is_upper_text(run['text']) and WORD.search(run['text']):
            issues.append(_issue(unit['id'],'source_case','local'))
    return issues


def _punctuation_issues(unit):
    return [_issue(unit['id'],'punctuation_spacing','local',start=m.start(),end=m.end())
            for m in re.finditer(r'(?<=[^\d\s])\s+(?=[,.;:!?])',unit['target'])]


def _display_text_issues(unit):
    """Catch whitespace and one-letter abbreviation debris in display copy."""
    quoted_display=bool(re.search(r'(?:^|_)quote(?:_|$)',unit.get('id','')) or
                        unit.get('display_quote') or
                        re.match(r'\s*["“‘«]',unit.get('source','')) or
                        re.match(r'\s*["“‘«]',unit.get('target','')))
    inferred_display=((unit.get('size',0)>=16 or 'display' in unit.get('scale_group','').lower())
                      and unit.get('source','').isupper() and not quoted_display)
    display=bool(unit.get('display_title') or unit.get('display_lines') or inferred_display)
    target=unit['target'];issues=[]
    if display and re.match(r'^\s{2,}',target):
        issues.append(_issue(unit['id'],'display_leading_space','recompose',start=0,
                             end=len(target)-len(target.lstrip())))
    if display and (re.match(r'^\s{2,}',target) or re.search(r'(?<=\S)\s{2,}',target)):
        m=re.search(r'^\s{2,}|(?<=\S)\s{2,}',target)
        issues.append(_issue(unit['id'],'display_double_space','recompose',start=m.start(),end=m.end()))
    # A translated “vs.” commonly becomes “frente a.”. In an all-caps
    # display heading the period is an orphan abbreviation marker, not prose.
    if (re.search(r'\bvs\.',unit.get('source',''),re.I) and target.upper()==target
            and re.search(r'\bFRENTE\s+A\.(?=\s+[A-ZÁÉÍÓÚÑ])',target)):
        m=re.search(r'\bFRENTE\s+A\.',target)
        issues.append(_issue(unit['id'],'orphan_abbreviation','retranslate',start=m.start(),end=m.end(),
                             abbreviation='a.',source_abbreviation='vs.'))
    return issues


def normalize_local_typography(unit):
    """Case and punctuation only, with every character retaining its run style."""
    out=deepcopy(unit)
    if unit.get('target_runs') is not None and ''.join(r['text'] for r in unit['target_runs'])!=unit['target']:
        return out  # validation owns malformed saved data; never conceal it
    source_styles={}
    for r in _source_runs(unit):
        key=tuple(sorted(r['style'].items()))
        source_styles[key]=source_styles.get(key,'')+unit['source'][r['start']:r['end']]
    from .dates import looks_like_date
    preserve_case=not (unit.get('native_ordinal') or unit.get('translation_method')=='deterministic_date' or looks_like_date(unit['source']))
    all_caps=preserve_case and _is_upper_text(unit['source']) and sum(c.isalpha() for c in unit['source'])>1
    def normalize(runs):
        chars=[]
        for run_index,r in enumerate(runs):
            original=source_styles.get(tuple(sorted(r['style'].items())),'')
            caps=all_caps or (preserve_case and _is_upper_text(original) and sum(c.isalpha() for c in original)>1)
            text=r['text'].upper() if caps else r['text']
            chars.extend((c,deepcopy(r['style']),run_index) for c in text)
        text=''.join(c for c,_,_ in chars)
        remove={i for m in re.finditer(r'(?<=[^\d\s])\s+(?=[,.;:!?])',text) for i in range(m.start(),m.end())}
        result=[{**deepcopy(r),'text':''} for r in runs]
        for i,(c,style,run_index) in enumerate(chars):
            if i not in remove:result[run_index]['text']+=c
        return result
    out['target_runs']=normalize(out.get('target_runs',[{'text':out['target'],'style':out.get('style',{})}]))
    out['target']=''.join(r['text'] for r in out['target_runs'])
    if out['target']==unit['target']:return deepcopy(unit)
    for p in out.get('paragraphs',[]):p['target_runs']=normalize(p['target_runs'])
    return out


def opening_punctuation_issues(text,unit_id):
    # Mask rather than delete tokens so offsets remain source-bound.
    safe=re.sub(r'\{[nv]\d+\}',lambda m:' '*len(m.group()),text)
    from .core import PROTECTED
    safe=PROTECTED.sub(lambda m:' '*len(m.group()),safe)
    issues=[];start=0;opened={'!':0,'?':0}
    for index,char in enumerate(safe):
        if char in '¡¿':opened['!' if char=='¡' else '?']+=1
        elif char in '!?':
            if opened[char]:opened[char]-=1
            elif re.search(r'[^\W\d_]',safe[start:index]):
                issues.append(_issue(unit_id,'missing_opening_punctuation',start=start,end=index+1,
                                     opening='¡' if char=='!' else '¿'))
            if index+1==len(safe) or safe[index+1] not in '!?':start=index+1;opened={'!':0,'?':0}
        elif char in '.;\n':start=index+1;opened={'!':0,'?':0}
    return issues


def lowercase_initial(text):
    """The first letter opens in lower case (style markers, opening punctuation
    and spaces skipped). A leading protected value or digit is not evidence."""
    text = re.sub(r'\{v\d+\}', '', text)
    match = re.match(r'[\s¿¡“‘"«(\[—–\-]*(.)', text)
    return bool(match and match.group(1).isalpha() and match.group(1).islower())


# Spanish verbs whose complement always takes a preposition (DPD): with a
# relative clause the preposition goes before the relative («de lo que la
# astrología carece»), never omitted (auditor batch-21, p17). Detection only.
_REGIME_STRICT = (
    ('de', r'carec(?:er|es?|en|emos|éis|ía|ías|íamos|ían|í|iste|ió|ieron|erá|erán|ería|erían|iendo|ido)|carezc(?:o|as?|amos|an)'),
    ('de', r'adolec(?:er|es?|en|emos|ía|ían|ió|ieron|erá|erán|ería|erían|iendo|ido)|adolezc(?:o|as?|amos|an)'),
    ('de', r'prescind(?:ir|o|es?|imos|en|ía|ías|íamos|ían|í|iste|ió|ieron|irá|irán|iría|irían|as?|amos|an|iendo|ido)'),
    ('en', r'consist(?:ir|o|es?|imos|en|ía|ían|ió|ieron|irá|irán|iría|irían|as?|an|iendo|ido)'),
    ('de', r'percat(?:arse|o|as?|amos|an|é|ó|aron|ará|arán|aría|arían|ándose|ado)'),
    ('de', r'jact(?:arse|o|as?|amos|an|é|ó|aron|ará|arán|aría|arían|ándose|ado)'),
)
# Verbs with an intransitive use of their own: flagged only when the relative
# is an object, i.e. an explicit subject stands between «lo que» and the verb.
_REGIME_SUBJECT = (
    ('de', r'depend(?:o|es?|emos|en|ía|ías|íamos|ían|ió|ieron|erá|erán|ería|erían|as?|amos|an)'),
    ('en', r'insist(?:o|es?|imos|en|ía|ían|ió|ieron|irá|irán|iría|irían|as?|an)'),
    ('a', r'renunci(?:o|as?|amos|an|aba|aban|é|ó|aron|ará|arán|aría|arían|e|es|emos|en)'),
    ('a', r'se\s+refier(?:es?|en|as?|an)|me\s+refiero|nos\s+referimos|te\s+refieres|se\s+refiri(?:ó|eron)'),
)
_PREPOSITION = {'de': r'de|del', 'en': r'en', 'a': r'a|al'}
_RELATIVE = re.compile(r'\b(?:que|cual|cuales|quien|quienes)\b', re.I)
_SUBJECT = (r'(?:(?:el|la|los|las|un|una|su|sus|mi|mis|tu|tus|nuestro|nuestra|nuestros|nuestras|este|esta|'
            r'estos|estas|ese|esa|esos|esas)\s+[^\W\d_]+(?:\s+[^\W\d_]+)?|[A-ZÁÉÍÓÚÑ][^\W\d_]+|yo|tú|él|ella|'
            r'ellos|ellas|nosotros|nosotras|usted|ustedes)')


def preposition_regime_issues(target, unit_id):
    """«lo que la astrología carece» (falta «de»): queísmo of a governed verb."""
    text = re.sub(r'\{v\d+\}', '', target)

    def governed(end, preposition):
        # «carece de», «consiste sobre todo en»: the preposition follows the verb.
        return bool(re.match(r'(?:\s+[^\W\d_]+){0,3}?\s+(?:'+_PREPOSITION[preposition]+r')\b',
                             text[end:end+40], re.I))

    def preceded(position, preposition):
        # The preposition opens the relative: «de lo que», «del que», «de todo lo que».
        words = re.findall(r'[^\W\d_]+', text[max(0, position-30):position])[-3:]
        return any(re.fullmatch(_PREPOSITION[preposition], w, re.I) for w in words)

    issues = []
    for preposition, verb in _REGIME_STRICT:
        for match in re.finditer(r'(?<!\w)(?:'+verb+r')(?!\w)', text, re.I):
            if governed(match.end(), preposition):
                continue
            start = max(0, match.start()-60)
            relatives = list(_RELATIVE.finditer(text, start, match.start()))
            if not relatives:
                continue  # no relative clause: another construction, not judged
            relative = relatives[-1]
            if (len(re.findall(r'[^\W\d_]+', text[relative.end():match.start()])) > 5
                    or preceded(relative.start(), preposition)):
                continue
            issues.append(_issue(unit_id, 'preposition_regime', start=match.start(), end=match.end(),
                                 verb=match.group(), expected_preposition=preposition))
    for preposition, verb in _REGIME_SUBJECT:
        pattern = (r'\b(?:lo|la|los|las|el|aquello)\s+que\s+'+_SUBJECT+
                   r'\s+(?:no\s+|también\s+|siempre\s+)?(?P<verb>'+verb+r')(?!\w)')
        for match in re.finditer(pattern, text):
            if governed(match.end(), preposition) or preceded(match.start(), preposition):
                continue
            issues.append(_issue(unit_id, 'preposition_regime', start=match.start(), end=match.end(),
                                 verb=match.group('verb'), expected_preposition=preposition))
    return issues


def story_break_issue(translation, marker):
    """A joined sentence's break marker: once, a word on each side, not inside a word."""
    if translation.count(marker) != 1:
        return True
    before, after = (re.sub(r'\{[nv]\d+\}', ' ', part) for part in translation.split(marker))
    if not re.search(r'[^\W_]', before) or not re.search(r'[^\W_]', after):
        return True
    return bool(re.search(r'[^\W_]$', before) and re.match(r'[^\W_]', after))


def format_warnings(units):
    """Non-blocking notes on protected numeric values kept literally (G3).

    The guide preserves source numbering and separators («no convertir la
    numeración … separadores»), so «25,000» and «siglo 4» are not changed;
    they are reported for the editor (auditor batch-21, p12/p14).
    """
    warnings = []
    for unit in units:
        target = unit.get('target', '')
        for match in re.finditer(r'(?<![\d.,\-–])\d{1,3}(?:,\d{3})+(?![\d,\-–]|\.\d)', target):
            warnings.append(_issue(unit['id'], 'english_thousands_separator', 'none', severity='minor',
                                   start=match.start(), end=match.end(), value=match.group(),
                                   note='G3 keeps protected separators literally; not normalized'))
        for match in re.finditer(r'\bsiglos?\s+(\d+)(?![\d.,]?\d)', target, re.I):
            warnings.append(_issue(unit['id'], 'arabic_century_numeral', 'none', severity='minor',
                                   start=match.start(), end=match.end(), value=match.group(1),
                                   note='G3 keeps protected numbering literally; Roman numerals not applied'))
    return warnings


def text_issues(source, target, unit_id, *, glossary=(), style_guide='', accent_vocabulary=()):
    source = unicodedata.normalize('NFC', STYLE.sub('', source))
    target = unicodedata.normalize('NFC', STYLE.sub('', target))
    issues = opening_punctuation_issues(target,unit_id)
    immediate = Counter(n for n, _, _ in _repetitions(source))
    original = Counter(immediate)
    original.update(n for n,_,_ in _separated_repetitions(source,source=True))
    for n, start, end in _repetitions(target):
        if original[n]:
            original[n] -= 1
            if immediate[n]:immediate[n] -= 1
        elif +immediate:
            # A repeated source term may have a target form of another word
            # count (e.g. a 2-word term with a 3-word approved glossary form).
            k = next(iter(+immediate));immediate[k] -= 1;original[k] -= 1
        else:
            issues.append(_issue(unit_id, 'repeated_ngram', start=start, end=end, n=n))
    for n, start, end in _separated_repetitions(target):
        if original[n]:original[n]-=1
        elif original[1]:
            # One source content word can expand to a multiword target term.
            # E.g. a repeated legal term is not newly introduced repetition.
            original[1]-=1
        elif not any(i.get('start',end)<end and i.get('end',start)>start for i in issues):
            issues.append(_issue(unit_id, 'repeated_ngram', start=start, end=end, n=n, nonadjacent=True))
    vocabulary = list(ACCENT_WORDS)+list(accent_vocabulary)
    vocabulary += [w.group().upper() for row in glossary for w in WORD.finditer(row['target'])]
    vocabulary += [w.group().upper() for w in WORD.finditer(style_guide)]
    accented = {_fold(w): w for w in vocabulary if len(w) >= 5 and _fold(w) != w
                and _fold(w) not in AMBIGUOUS and 'Ñ' not in w}
    for word in WORD.finditer(target):
        token = word.group()
        if token.casefold() == 'zodiaco':
            issues.append(_issue(unit_id, 'zodiac_spelling', start=word.start(), end=word.end()))
        elif token.isupper() and token in accented:
            issues.append(_issue(unit_id, 'uppercase_accent', start=word.start(), end=word.end(),
                                 expected=accented[token].upper()))
    # An English suffix kept after a protected digit or its placeholder
    # («CASA {n1}ND», «la casa 2nd»): digits are masked, so no detector sees it.
    for match in re.finditer(r'(?<![\w.])(?:\d+|\{n\d+\})\s?(?:st|nd|rd|th)(?![^\W\d_])', target, re.I):
        issues.append(_issue(unit_id, 'english_ordinal_suffix', start=match.start(), end=match.end()))
    for match in re.finditer(r'\b(?:la|una|esta|esa)\s+asterismo\b', target, re.I):
        issues.append(_issue(unit_id, 'asterism_agreement', start=match.start(), end=match.end()))
    if re.search(r'\bfind\b', source, re.I):
        signs = 'Aries|Tauro|Géminis|Cáncer|Leo|Virgo|Libra|Escorpio|Sagitario|Capricornio|Acuario|Piscis'
        for match in re.finditer(r'\bencontrar(?:la|lo|las|los)\s+(?:'+signs+r')\b', target):
            issues.append(_issue(unit_id, 'redundant_clitic', start=match.start(), end=match.end()))
    issues.extend(preposition_regime_issues(target, unit_id))
    return issues


def marked_style_issues(source, target, unit_id):
    """Nonempty source segments must retain content between their style markers."""
    markers = STYLE.findall(source)
    if not markers or STYLE.findall(target) != markers:
        return []  # the existing marker/order validator owns this error
    issues = []
    for left, right in zip(markers, markers[1:]):
        a = source.split(left, 1)[1].split(right, 1)[0].strip()
        b = target.split(left, 1)[1].split(right, 1)[0].strip()
        # When an unchanged term is still present, it must stay inside its run.
        elsewhere = bool(a and re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)', STYLE.sub('', target)))
        if a and (not b or (elsewhere and not re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)', b))):
            issues.append(_issue(unit_id, 'inline_style'))
    return issues


def _signature(style):
    return bool(style.get('italic')), bool(style.get('bold') or style.get('weight', 0) >= 600 or 'heavy' in style.get('font', '').lower() or re.sub(r'[^a-z]','',style.get('font','').lower()).endswith('lilitaone'))


def _source_runs(unit):
    """Fit artifacts may retain stale offsets after joins/splits; remap spans."""
    if not unit.get('spans'):
        return unit.get('style_runs', [])
    runs = []
    cursor = 0
    for span in unit['spans']:
        text = span['source'].strip()
        if not text:
            continue
        match = re.search(re.escape(text), unit['source'][cursor:])
        if match is None or unit['source'][cursor:cursor+match.start()].strip():
            return unit.get('style_runs', [])
        start, end = cursor+match.start(), cursor+match.end()
        if runs:
            runs[-1]['end'] = start
        if runs and runs[-1]['style'] == span['style']:
            runs[-1]['end'] = end
        else:
            runs.append(dict(start=start, end=end, style=span['style']))
        cursor = end
    return runs if not unit['source'][cursor:].strip() else unit.get('style_runs', [])


def _style_ranges(unit):
    runs = _source_runs(unit)
    return [dict(text=unit['source'][r['start']:r['end']].strip(), style=r['style'])
            for r in runs if any(_signature(r['style'])) and
            any(_signature(other['style']) != _signature(r['style']) for other in runs)]


def _matches(text, needle):
    return list(re.finditer(r'(?<!\w)'+re.escape(needle)+r'(?!\w)', text)) if needle else []


def _styles_at(unit, start, end):
    offset = 0
    for run in unit.get('target_runs', [{'text': unit['target'], 'style': unit.get('style', {})}]):
        stop = offset + len(run['text'])
        if start < stop and end > offset:
            yield run['style']
        offset = stop


def _inline_issues(unit, emphasis, glossary=()):
    if unit.get('native_ordinal'):
        return []  # I3's separate source/candidate proof owns ordinal morphology.
    issues = []
    for item in emphasis:
        matches = _matches(unit['target'], item['text'])
        if len(matches) != 1:
            continue
        match = matches[0]
        if any(_signature(s) != _signature(item['style']) for s in _styles_at(unit, *match.span())):
            issues.append(_issue(unit['id'], 'inline_style', 'local', start=match.start(), end=match.end(),
                                 source_text=item['text'], style=item['style']))
    # For translated emphasis, only the ordered style signature is deterministic.
    def sequence(runs):
        result = []
        for r in runs:
            if not WORD.search(r.get('text', 'x')):
                continue
            sig = _signature(r['style'])
            if not result or result[-1] != sig:
                result.append(sig)
        return result
    source_chars = [unit.get('style', {}) for _ in unit['source']]
    for r in _source_runs(unit):
        source_chars[r['start']:r['end']] = [r['style']]*(r['end']-r['start'])
    for item in emphasis:
        matches = _matches(unit['source'], item['text'])
        if len(matches) == 1:
            m = matches[0]
            source_chars[m.start():m.end()] = [item['style']]*(m.end()-m.start())
    expected = sequence([dict(text=c, style=s) for c,s in zip(unit['source'],source_chars)]) if _source_runs(unit) else []
    projected = [(c, r['style']) for r in unit.get('target_runs', []) for c in r['text']]
    for issue in issues:
        for index in range(issue['start'], min(issue['end'], len(projected))):
            projected[index] = (projected[index][0], issue['style'])
    actual = sequence([dict(text=c, style=s) for c,s in projected])
    if expected and actual:
        same = _emphasis_correspondence(unit['source'], source_chars, ''.join(c for c, _ in projected),
                                        projected, glossary)
        # Same order is accepted unless word anchors contradict it; another order
        # (Spanish fronting, postposed adjectives) needs positive correspondence.
        if same is False or (expected != actual and same is not True):
            issues.append(_issue(unit['id'], 'inline_style'))
    return issues


def _cognate(a, b):
    if a == b:
        return len(a) >= 2  # kept tokens such as MC
    common = len(next((p for p in (a[:k] for k in range(min(len(a), len(b)), 0, -1)) if b.startswith(p)), ''))
    return min(len(a), len(b)) >= 4 and common >= 4 and common >= .6*min(len(a), len(b))


def _emphasis_correspondence(source, source_styles, target, target_chars, glossary=()):
    """Do emphasized source phrases map to emphasized target phrases, in any order?

    Evidence is local and lexical only: identical words, orthographic cognates
    (experience/experiencia) and approved glossary pairs (Sun/Sol). Returns
    False on a contradiction (an anchored word changed style), True when every
    emphasized phrase is pinned by an anchor or is the single unanchored phrase
    of its style on both sides with a comparable length, None otherwise.
    """
    def words(text, sig_at):
        rows = []
        for w in WORD.finditer(text):
            rows.append(dict(word=_fold(w.group()), start=w.start(), end=w.end(), sig=sig_at(w.start())))
        run = -1
        for i, row in enumerate(rows):
            run += not i or rows[i-1]['sig'] != row['sig']
            row['run'] = run
        return rows
    src = words(source, lambda i: _signature(source_styles[i]))
    tgt = words(target, lambda i: _signature(target_chars[i][1]))
    if not src or not tgt:
        return None
    # A cognate anchors only when it is mutually unique: «escape» twice, or
    # INTUITION/intuitively → intuitivamente, is not evidence of one phrase.
    content = lambda rows: [k for k, r in enumerate(rows) if r['word'].casefold() not in CONTENT_STOP]
    links = {(i, j) for i in content(src) for j in content(tgt) if _cognate(src[i]['word'], tgt[j]['word'])}
    forward, backward = Counter(i for i, _ in links), Counter(j for _, j in links)
    pairs = {i: {j} for i, j in links if forward[i] == 1 and backward[j] == 1}
    # Offset-preserving fold: one folded character per original character.
    aligned = lambda text: ''.join((_fold(c) or c)[:1] if c.strip() else ' ' for c in text)
    folded_source, folded_target = aligned(source), aligned(target)
    for row in glossary:
        s, t = _fold(row.get('source', '')), _fold(row.get('target', ''))
        ms, mt = _matches(folded_source, s), _matches(folded_target, t)
        if len(ms) == 1 and len(mt) == 1:
            for i, a in enumerate(src):
                if ms[0].start() <= a['start'] < ms[0].end():
                    pairs.setdefault(i, set()).update(j for j, b in enumerate(tgt)
                                                      if mt[0].start() <= b['start'] < mt[0].end())
    # The plain style is the base even when emphasis has more words (p23).
    counts = Counter(a['sig'] for a in src)
    base = (False, False) if (False, False) in counts else counts.most_common(1)[0][0]
    confirmed_source, confirmed_target, shrunk = set(), set(), set()
    for i, js in pairs.items():
        styles = {tgt[j]['sig'] for j in js}
        if not styles:
            continue
        if src[i]['sig'] not in styles:
            # Emphasis spread onto a source-plain word, or onto another style.
            if styles != {base}:
                return False
            shrunk.add(src[i]['run'])
        elif styles == {src[i]['sig']}:
            confirmed_source.add(src[i]['run'])
            confirmed_target.update(tgt[j]['run'] for j in js)
    # An emphasized phrase is lost when none of its anchors kept the style. One
    # word left plain beside a phrase that is otherwise pinned is a boundary
    # choice («the sign on the MC» → «el signo del MC»), not lost emphasis.
    if shrunk - confirmed_source:
        return False
    def phrases(rows):
        found = {}
        for row in rows:
            found.setdefault(row['run'], dict(sig=row['sig'], size=0))['size'] += 1
        return found
    sp, tp = phrases(src), phrases(tgt)
    if not confirmed_source:
        return None
    for sig in {p['sig'] for p in sp.values()} | {p['sig'] for p in tp.values()}:
        if sig == base:
            continue
        a = [r for r, p in sp.items() if p['sig'] == sig]
        b = [r for r, p in tp.items() if p['sig'] == sig]
        if len(a) != len(b):
            return None
        loose_a = [sp[r]['size'] for r in a if r not in confirmed_source]
        loose_b = [tp[r]['size'] for r in b if r not in confirmed_target]
        if len(loose_a) != len(loose_b) or len(loose_a) > 1:
            return None
        if loose_a and not .5 <= loose_b[0]/loose_a[0] <= 2:
            return None
    return True


def _glyph_issues(unit):
    source, target = unit['source'], unit['target']
    issues = []
    for glyph in GLYPH.finditer(target):
        if glyph.start() and target[glyph.start()-1].isalpha() and re.search(r'\w\s+'+re.escape(glyph.group()), source):
            issues.append(_issue(unit['id'], 'glyph_spacing', 'local', start=glyph.start(), end=glyph.end()))
    return issues


def merged_style_break_issues(unit):
    """A size change across a line break inside one composed paragraph.

    Extraction separates such lines (heading/body, 8 pt cross-reference under
    11 pt text); a unit that still holds one was merged by mistake. Explicit
    heading/body paragraphs of one unit are a deliberate partition.
    """
    if unit.get('paragraphs') and not unit.get('story_frames'):
        return []
    from .fidelity import _break_index
    spans = [s for s in unit.get('spans', []) if s.get('source', s.get('text', '')).strip()]
    if not spans or any('style' not in s for s in spans):
        return []
    k = _break_index(spans)
    if k is None:
        return []
    a, b = spans[k-1], spans[k]
    return [_issue(unit['id'], 'merged_style_break', 'recompose',
                   before={'size': a['size'], 'bbox': list(a['bbox'])},
                   after={'size': b['size'], 'bbox': list(b['bbox'])})]


def unit_issues(unit, *, emphasis=None, glossary=(), style_guide=''):
    emphasis = _style_ranges(unit) if emphasis is None else emphasis
    return (text_issues(unit['source'], unit['target'], unit['id'], glossary=glossary, style_guide=style_guide)
            + _inline_issues(unit, emphasis, glossary) + _glyph_issues(unit) + _case_issues(unit) + _punctuation_issues(unit)
            + _display_text_issues(unit))


def repair_opening_punctuation(unit):
    """Only an entire uppercase exclamation/question with a clear beginning.

    Other ambiguous clauses remain a retranslation issue. Record the original
    and target before/after; no lexical substitution and no protected value edit.
    """
    issues=opening_punctuation_issues(unit['target'],unit['id'])
    target=unit['target'];source=unit['source']
    if len(issues)!=1 or not _is_upper_text(target) or not _is_upper_text(source):return unit
    if len(re.findall(r'[!?]',target))!=1 or re.search(r'[.;:\n]',target):return unit
    if not re.search(r'[!?][”’"\s]*$',source) or not re.search(r'[!?][”’"\s]*$',target):return unit
    if unit.get('paragraphs'):return unit  # paragraph reconstruction owns these boundaries
    start=re.match(r'[\s“‘"«]*',target).end()
    if not target[start:start+1].isalpha():return unit
    out=deepcopy(unit);offset=0
    for run in out['target_runs']:
        if offset+len(run['text'])>start:
            local=start-offset;run['text']=run['text'][:local]+issues[0]['opening']+run['text'][local:];break
        offset+=len(run['text'])
    out['target']=''.join(r['text'] for r in out['target_runs'])
    out.setdefault('grammatical_corrections',[]).append({
        'type':'grammatical_correction','rule':'spanish_opening_punctuation',
        'source':source,'before':target,'after':out['target'],'method':'deterministic',
        'unit_id':unit['id']})
    return out


def repair_composition(unit, *, emphasis=None):
    """Copy only styles and whitespace. Lexical text is never substituted."""
    if unit.get('target_runs') is not None and ''.join(r['text'] for r in unit['target_runs'])!=unit['target']:
        return deepcopy(unit)
    unit = repair_opening_punctuation(normalize_local_typography(unit))
    result = deepcopy(unit)
    emphasis = _style_ranges(unit) if emphasis is None else emphasis
    changes = _inline_issues(unit, emphasis)
    spacing = _glyph_issues(unit)
    if not any(i['action'] == 'local' for i in changes) and not spacing:
        return result
    runs = result.get('target_runs', [{'text': result['target'], 'style': result.get('style', {})}])
    chars = [(c, deepcopy(r['style'])) for r in runs for c in r['text']]
    for change in changes:
        if change['action'] == 'local':
            for i in range(change['start'], change['end']):
                chars[i] = (chars[i][0], deepcopy(change['style']))
    for issue in reversed(spacing):
        i = issue['start']
        chars.insert(i, (' ', deepcopy(chars[i-1][1])))
    rebuilt = []
    for char, style in chars:
        if rebuilt and rebuilt[-1]['style'] == style:
            rebuilt[-1]['text'] += char
        else:
            rebuilt.append(dict(text=char, style=style))
    result['target_runs'] = rebuilt
    result['target'] = ''.join(r['text'] for r in rebuilt)
    if result.get('paragraphs'):
        from .retranslation import _slice_runs
        if ''.join(r['text'] for p in unit['paragraphs'] for r in p['target_runs']) != unit['target']:
            from .core import Blocked
            raise Blocked('Cannot map local I6 repair to paragraph boundaries')
        old = new = 0
        for paragraph in result['paragraphs']:
            old += sum(len(r['text']) for r in paragraph['target_runs'])
            end = old + sum(i['start'] < old for i in spacing)
            paragraph['target_runs'] = _slice_runs(rebuilt, new, end)
            new = end
    return result


def source_emphasis(page, unit, *, shears=None):
    """Include faux italics encoded in text matrices, invisible to get_text flags.

    Kept outside canonical extraction so saved ids/spans/hash approvals stay valid.
    Source glyphs and symbol fonts are never rewritten or sent to a model.
    """
    emphasis = _style_ranges(unit)
    for item in matrix_italics(page) if shears is None else shears:
        if len(_matches(unit['source'], item['text'])) != 1:
            continue
        spans = [s for s in unit.get('spans', []) if fitz.Rect(s['bbox']).contains(fitz.Point(item['origin']))]
        if len(spans) != 1:
            continue
        style = {**spans[0]['style'], 'italic': True, 'force_fallback': True}
        emphasis.append({**item, 'style': style})
    return emphasis


def matrix_italics(page):
    """Inspect text device matrices (including Form XObjects) locally."""
    from .core import SYMBOL_FONT
    m = fitz.mupdf
    found = []
    class Device(m.FzDevice2):
        def __init__(self):
            super().__init__()
            self.use_virtual_fill_text()
        def fill_text(self, ctx, text, ctm, *args):
            raw = text.head
            while raw:
                span = m.FzTextSpan(raw)
                mat = m.fz_concat(span.trm(), m.FzMatrix(ctm))
                # Orthogonal rotations are not italics. Detect a skewed x/y basis.
                norm = ((mat.a**2+mat.b**2)*(mat.c**2+mat.d**2))**.5
                skew = abs(mat.a*mat.c+mat.b*mat.d)/norm if norm else 0
                name = fitz.JM_font_name(span.font())
                if .08 < skew < .6 and raw.len and not SYMBOL_FONT.search(name):
                    text_value = ''.join(chr(span.items(i).ucs) for i in range(raw.len) if span.items(i).ucs >= 0)
                    point = m.fz_transform_point(m.fz_make_point(span.items(0).x, span.items(0).y), m.FzMatrix(ctm))
                    if text_value.strip():
                        found.append(dict(text=text_value.strip(), origin=[point.x, point.y],
                                          matrix=[mat.a, mat.b, mat.c, mat.d], evidence='text_matrix_skew'))
                raw = raw.next
    device = Device()
    try:
        m.fz_run_page_contents(page.this, device, m.FzMatrix(), m.FzCookie())
    finally:
        m.fz_close_device(device)
    return found


def continuation_pairs(units):
    """Consecutive aligned caption fragments; never arbitrary page neighbours."""
    for left, right in zip(units, units[1:]):
        if not all(k in u for u in (left, right) for k in ('bbox', 'style')):
            continue
        a, b = fitz.Rect(left['bbox']), fitz.Rect(right['bbox'])
        size = left['style'].get('size', 0)
        aligned = min(abs(a.x0-b.x0), abs(a.x1-b.x1), abs((a.x0+a.x1-b.x0-b.x1)/2)) < 4
        if (size and left['style'] == right['style'] and 0 <= b.y0-a.y1 <= size*.6
                and aligned and not re.search(r'[.!?;]$', left['source'].rstrip())):
            yield left, right


def boundary_issues(left, right):
    source = left['source']+' '+right['source']
    target = left['target']+' '+right['target']
    boundary = len(left['target'])
    issues = []
    for issue in text_issues(source, target, left['id']):
        if issue['code'] == 'repeated_ngram' and issue['start'] < boundary < issue['end']:
            for unit in (left, right):
                issues.append({**issue, 'unit_id': unit['id'], 'related_units': [left['id'], right['id']],
                               'scope': 'caption_continuation'})
    return issues


def neighbor_issues(request, translation, answers):
    issues = []
    current = dict(id=request['id'], source=request.get('source', request.get('text', '')), target=translation)
    for context in request.get('quality_neighbors', []):
        neighbor = {**context, 'id': context['request_id'], 'target': answers[context['request_id']]['translation']} if context.get('request_id') in answers else context
        issues.extend(boundary_issues(neighbor, current) if context['side'] == 'before' else boundary_issues(current, neighbor))
    return issues


def glyph_boundaries(page, units):
    """Locate original word + whitespace + protected font glyph boundaries."""
    from .core import SYMBOL_FONT
    found = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            for left, right in zip(line['spans'], line['spans'][1:]):
                text = ''.join(c['c'] for c in left['chars'])
                glyphs = [c for c in right['chars'] if c['c'].strip()]
                spaces = [c for c in left['chars'] if c['c'].isspace()]
                if not text.rstrip() or not text[-1].isspace() or not glyphs or not spaces:
                    continue
                if not SYMBOL_FONT.search(right['font']) and not GLYPH.fullmatch(glyphs[0]['c']):
                    continue
                owners = [u for u in units if any(s['source'] == text and s['font'] == left['font']
                          and max(abs(a-b) for a,b in zip(s['bbox'], left['bbox'])) < .02 for s in u.get('spans', []))]
                for owner in owners:
                    # Require at least one ORIGINAL source-space advance, not
                    # two spaces or an arbitrary full glyph width.
                    gap = min(fitz.Rect(c['bbox']).width for c in spaces)
                    found.append(dict(unit_id=owner['id'], source_text=text.strip(),
                                      glyph_bbox=list(glyphs[0]['bbox']), minimum_gap=gap,
                                      label_bbox=list(left['bbox'])))
    return found


def constrain_glyph_spacing(page, units):
    """Reserve source-proven whitespace before a separately protected glyph."""
    result = deepcopy(units)
    for boundary in glyph_boundaries(page, result):
        for unit in result:
            # A multiparagraph/table owner must first be split by its existing
            # book recipe. Never shrink an entire table to the first row's gap.
            if unit['id'] == boundary['unit_id'] and len(unit['spans']) == 1:
                unit['glyph_spacing_boundary'] = boundary
    return result


NAME_WORD = re.compile(r"[^\W\d_]+(?:[’'-][^\W\d_]+)*")
SENTENCE_BREAK = re.compile(r'(?<=[.!?])["”’»)]*\s+')
NAME_STOP = frozenset('''the a an its it this that these those in on at of and or but for with from by as
to his her their our your my we you they he she i el la los las lo un una unos unas su sus en de del al y o
e u para pero como cuando donde este esta estos estas ese esa todo toda todos todas cada otro otra sobre
entre desde hasta tambien también aunque porque segun según luego si no se'''.split())


def _fold_name(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if unicodedata.category(c) != 'Mn')


def _name_runs(sentence, *, initial=False):
    """Runs of consecutive capitalised words (proper names), folded.

    The sentence-initial word is excluded unless ``initial``: its capital is
    grammatical. Leading function words («The», «La») never start a run.
    """
    words = list(NAME_WORD.finditer(re.sub(r'\{[nv]\d+\}', ' ', sentence)))
    runs, current, last = [], [], None
    for index, match in enumerate(words):
        word = match.group()
        capital = word[:1].isupper() and not word.isupper() and len(word) >= 3
        if capital and (index or initial) and not (not current and _fold_name(word) in NAME_STOP):
            gap = sentence[last:match.start()] if last is not None else ''
            if current and gap.strip():
                runs.append(current); current = []
            current.append(word)
        elif current:
            runs.append(current); current = []
        last = match.end()
    if current:
        runs.append(current)
    return [' '.join(_fold_name(w) for w in run) for run in runs]


def _prose(text):
    words = NAME_WORD.findall(text)
    return len(words) >= 4 and sum(w[:1].isupper() for w in words) / len(words) < .4


def aligned_names(source, target):
    """EN→ES name pairs, aligned by order only where the name counts agree.

    Sentences are paired when both sides have the same number; otherwise the
    whole unit is the single alignment window. Unequal counts align nothing.
    """
    source, target = STYLE.sub('', source), STYLE.sub('', target)
    if not _prose(source):
        return []
    left = [s for s in SENTENCE_BREAK.split(source.strip()) if s.strip()]
    right = [s for s in SENTENCE_BREAK.split(target.strip()) if s.strip()]
    pairs = list(zip(left, right)) if len(left) == len(right) else [(source, target)]
    result = []
    for a, b in pairs:
        names, targets = _name_runs(a), _name_runs(b)
        if names and len(names) == len(targets):
            result.extend((x, y) for x, y in zip(names, targets)
                          if len(x) >= 4 and len(y) >= 4 and y not in NAME_STOP)
    return result


def _has_name(folded_target, name):
    if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', folded_target):
        return True
    words = name.split()
    # Inflection tolerance for one-word names (Colmena/colmenas), never synonyms.
    return len(words) == 1 and len(name) >= 6 and any(
        len(w) >= 6 and w[:5] == name[:5] for w in NAME_WORD.findall(folded_target))


def term_consistency_issues(units, context=(), glossary=()):
    """Same source name on one page or spread = same Spanish name (auditor p47).

    ``context`` units (facing page, body clauses already translated) only
    contribute evidence. A unit is flagged when its source names a term whose
    page translation is unanimous, its target lacks that translation and it
    uses instead the translation of another name absent from its source: the
    caption «The Beehive…» → «El Pesebre…» while the body pairs Beehive →
    Colmena and Manger → Pesebre. Approved glossary terms are left to the
    glossary validator.
    """
    approved = {_fold_name(w) for row in glossary for w in NAME_WORD.findall(row.get('source', ''))}
    evidence = {}
    for unit in list(units)+list(context):
        for name, translated in aligned_names(unit.get('source', ''), unit.get('target', '')):
            if any(word in approved for word in name.split()):
                continue
            evidence.setdefault(name, {}).setdefault(translated, set()).add(unit.get('id'))
    mapping = {name: next(iter(found.items())) for name, found in evidence.items() if len(found) == 1}
    issues = []
    # Two aligned translations of one name: the minority side is inconsistent.
    for name, found in evidence.items():
        if len(found) < 2:
            continue
        top = max(len(ids) for ids in found.values())
        for unit in units:
            mine = [value for value, ids in found.items() if unit.get('id') in ids]
            if mine and all(len(found[value]) < top or list(map(len, found.values())).count(top) > 1 for value in mine):
                issues.append(_issue(unit.get('id'), 'term_inconsistency', source_term=name, expected=None,
                                     found=[{'source_term': name, 'target': v, 'units': sorted(i for i in ids if i)}
                                            for v, ids in sorted(found.items())],
                                     evidence_units=sorted(i for ids in found.values() for i in ids if i)))
    for unit in units:
        source, target = STYLE.sub('', unit.get('source', '')), _fold_name(STYLE.sub('', unit.get('target', '')))
        source_words = {_fold_name(w) for w in NAME_WORD.findall(source)}
        names = {n for s in SENTENCE_BREAK.split(source) for n in _name_runs(s, initial=True)}
        for name in sorted(names):
            if name not in mapping:
                continue
            expected, owners = mapping[name]
            if not owners-{unit.get('id')} or _has_name(target, expected):
                continue
            swapped = sorted({(other, value) for other, (value, _) in mapping.items()
                              if other != name and value != expected and not set(other.split()) & source_words
                              and _has_name(target, value)})
            if swapped:
                issues.append(_issue(unit.get('id'), 'term_inconsistency', source_term=name, expected=expected,
                                     found=[{'source_term': o, 'target': v} for o, v in swapped],
                                     evidence_units=sorted(i for i in owners if i is not None)))
    return issues


EN_INLINE_ORDINAL = re.compile(r'(?<![\w.])(\d+)\s?(st|nd|rd|th)(?![^\W\d_])', re.I)


def _normalize_font_name(name):
    return re.sub(r'^[A-Z]{6}\+', '', name).replace(' ', '').replace('-', '').lower()


def verified_text_fonts(candidate_page, units):
    """Embedded fonts written for these units (by file hash), as normalized names.

    Replacement instances may carry names that look like symbol fonts
    («AstroSlab»); only hash-verified composition fonts are treated as text.
    """
    import hashlib
    expected = {r['sha256'] for u in units for r in u.get('font_runs', [])}
    verified = set()
    if not expected:
        return verified
    for row in candidate_page.get_fonts(full=True):
        data = candidate_page.parent.extract_font(row[0])[3]
        if data and hashlib.sha256(data).hexdigest() in expected:
            verified.update((_normalize_font_name(row[3]), _normalize_font_name(fitz.Font(fontbuffer=data).name)))
    return verified


def english_ordinal_issues(page, units=()):
    """English ordinal suffixes left in a candidate page.

    Digits are protected values: they never reach the model and the language
    detector never sees them, so «12» + raised «th» (diagram labels) or an
    inline «7th» would pass as non-English. Spanish morphology (.ª/.º/.er) and
    bare digits are not flagged. Curved (non axis-aligned) runs belong to the
    deferred Phase 4 inventory and are left to that region's own status.
    """
    from .core import SYMBOL_FONT
    verified = verified_text_fonts(page, units)
    found = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            dx, dy = line['dir']
            if min(abs(dx), abs(dy)) > 1e-3:
                continue
            spans = [s for s in line['spans'] if s['text'].strip() and (
                not SYMBOL_FONT.search(s['font']) or _normalize_font_name(s['font']) in verified)]
            for a, b in zip(spans, spans[1:]):
                digits = re.search(r'(?<![\w.])(\d+)$', a['text'])
                # A protected digit span followed by a run that opens with the
                # suffix: raised «th» or inline «2» + «nd es la casa…» (p142).
                ending = re.match(r'(st|nd|rd|th)(?![^\W\d_])', b['text'], re.I)
                if not digits or not ending:
                    continue
                if max(abs(b['bbox'][0]-a['bbox'][2]), 0) > a['size']*.15:
                    continue
                found.append(_issue('protected_ordinal', 'english_ordinal_suffix', 'recompose',
                                    scope='candidate_pdf', ordinal=digits.group(1)+ending.group(1),
                                    candidate_bbox=list(fitz.Rect(a['bbox']) | fitz.Rect(b['bbox']))))
            for span in spans:
                for match in EN_INLINE_ORDINAL.finditer(span['text']):
                    found.append(_issue('protected_ordinal', 'english_ordinal_suffix', 'recompose',
                                        scope='candidate_pdf', ordinal=match.group(1)+match.group(2),
                                        candidate_bbox=list(span['bbox'])))
    return found


def ordinal_suffix_issues(unit):
    """A source ordinal «(1st)» whose target kept only the bare number «(1)» (audit batch-17, p139)."""
    issues = []
    target = unit.get('target', '')
    for match in re.finditer(r'\((\d+)(?:st|nd|rd|th)\)', unit.get('source', ''), re.I):
        found = target.find('('+match.group(1)+')')
        if found >= 0:
            issues.append(_issue(unit.get('id'), 'ordinal_suffix_dropped', 'retranslate', start=found,
                                 end=found+len(match.group(1))+2, ordinal=match.group(0)))
    return issues


def page_issues(units, *, source_page=None, candidate_page=None, glossary=(), style_guide='', spread_units=()):
    issues = []
    if candidate_page is not None:
        issues.extend(english_ordinal_issues(candidate_page, units))
    # Facing-page units are evidence only; issues are reported for this page.
    issues.extend(term_consistency_issues(units, spread_units, glossary))
    shears = matrix_italics(source_page) if source_page is not None else []
    # A lower-case occurrence on this page supplies accent evidence for a
    # matching all-caps occurrence (for example, JUGUETON / juguetón).
    page_accent_vocabulary=[]
    for item in units:
        page_accent_vocabulary.extend(w.group() for w in WORD.finditer(item.get('target',''))
                                      if w.group()!=w.group().upper() and any(
                                          unicodedata.category(c)=='Mn' for c in
                                          unicodedata.normalize('NFD',w.group())))
    logical=[]
    for unit in units:
        if unit.get('story_frames'):
            for index,p in enumerate(unit['paragraphs']):
                logical.append({**unit,'id':unit['id']+f'_paragraph{index}',
                    'source':p['source'],'target':''.join(r['text'] for r in p['target_runs']),
                    'spans':p['spans'],'target_runs':p['target_runs']})
        else:logical.append(unit)
    for unit in logical:
        if re.search(r'[«»\"]',unit['target']):
            issues.append(_issue(unit['id'],'quote_style','local'))
        emphasis = source_emphasis(source_page, unit, shears=shears) if source_page is not None else None
        issues.extend(text_issues(unit['source'],unit['target'],unit['id'],glossary=glossary,
                                  style_guide=style_guide,accent_vocabulary=page_accent_vocabulary))
        issues.extend(ordinal_suffix_issues(unit))
        emphasis=_style_ranges(unit) if emphasis is None else emphasis
        issues.extend(_inline_issues(unit,emphasis,glossary)+_glyph_issues(unit)+_case_issues(unit)+
                      _punctuation_issues(unit)+_display_text_issues(unit)+merged_style_break_issues(unit))
    if candidate_page is not None:
        # Saved targets are not evidence of the actual PDF spelling. Inspect
        # every lexical word, including running headers; assign only spatially
        # owned words and exclude protected/symbol-font content.
        from .core import SYMBOL_FONT
        import hashlib
        expected = {r['sha256'] for u in units for r in u.get('font_runs', [])}
        verified = set()
        normalize = lambda name: re.sub(r'^[A-Z]{6}\+', '', name).replace(' ', '').replace('-', '').lower()
        for row in candidate_page.get_fonts(full=True):
            data = candidate_page.parent.extract_font(row[0])[3]
            if data and hashlib.sha256(data).hexdigest() in expected:
                verified.update((normalize(row[3]), normalize(fitz.Font(fontbuffer=data).name)))
        for block in candidate_page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    if SYMBOL_FONT.search(span['font']) and normalize(span['font']) not in verified:
                        continue
                    owners = [u for u in units if fitz.Rect(u.get('edit_bbox', u['bbox'])).intersects(fitz.Rect(span['bbox']))]
                    if len(owners) != 1:
                        continue
                    owner = owners[0]
                    found = text_issues(owner['source'], span['text'], owner['id'], glossary=glossary, style_guide=style_guide)
                    if _case_issues({**owner,'target':span['text'],'target_runs':[]}) and WORD.search(span['text']):
                        issues.append(_issue(owner['id'],'source_case','local',scope='candidate_pdf',candidate_bbox=list(span['bbox'])))
                    for issue in found:
                        if issue['code'] in ('zodiac_spelling', 'uppercase_accent'):
                            issues.append({**issue, 'scope': 'candidate_pdf', 'candidate_bbox': list(span['bbox'])})
                line_text=''.join(span['text'] for span in line['spans'])
                line_words={w.group().casefold() for w in WORD.finditer(line_text)}
                if re.match(r'^\s{2,}',line_text) and line_words:
                    line_box=fitz.Rect(line['bbox'])
                    owners=[u for u in units if fitz.Rect(u.get('edit_bbox',u['bbox'])).intersects(line_box)
                            and not re.search(r'(?:^|_)quote(?:_|$)',u.get('id',''))
                            and not u.get('display_quote')
                            and (u.get('display_title') or u.get('display_lines') or
                                 ((u.get('size',0)>=16 or 'display' in u.get('scale_group','').lower()) and
                                  u.get('source','').isupper() and
                                  not re.match(r'\s*["“‘«]',u.get('source','')) and
                                  not re.match(r'\s*["“‘«]',u.get('target',''))))]
                    owners=[u for u in owners if line_words and
                            len(line_words & {w.group().casefold() for w in WORD.finditer(u.get('target',''))})/
                            len(line_words)>=.75]
                    if len(owners)==1:
                        owner=owners[0];leading=len(line_text)-len(line_text.lstrip())
                        issues.append(_issue(owner['id'],'display_leading_space','recompose',scope='candidate_pdf',
                                             start=0,end=leading,candidate_bbox=list(line_box)))
                        if line_text.startswith('  '):
                            issues.append(_issue(owner['id'],'display_double_space','recompose',scope='candidate_pdf',
                                                 start=0,end=leading,candidate_bbox=list(line_box)))
        # Check the actual ink against the composed frame, including frames
        # inside a coloured panel. Layout boxes are the compositor's measured
        # safe boundary; span metrics outside that boundary are a visible QA
        # defect even when the fit routine reported success.
        for unit in units:
            frames=unit.get('edit_boxes') or [unit.get('edit_bbox',unit['bbox'])]
            for frame_value in frames:
                frame=fitz.Rect(frame_value)
                if source_page is None:
                    continue
                from .panel_frames import has_coloured_bottom_boundary
                if not has_coloured_bottom_boundary(source_page,frame):
                    continue
                for block in candidate_page.get_text('dict')['blocks']:
                    for line in block.get('lines',[]):
                        for span in line['spans']:
                            ink=fitz.Rect(span['bbox'])
                            span_words={w.group().casefold() for w in WORD.finditer(span['text'])}
                            target_words={w.group().casefold() for w in WORD.finditer(unit['target'])}
                            related=bool(span_words and target_words and
                                         len(span_words&target_words)/len(span_words)>=.75)
                            if (related and ink.x1>frame.x0 and ink.x0<frame.x1 and
                                    ink.y0<frame.y1+frame.height and ink.y1>frame.y1+.05 and
                                    ink.y0>=frame.y0-.5):
                                issues.append(_issue(unit['id'],'box_bottom_overflow','recompose',
                                    frame_bbox=list(frame),candidate_bbox=list(ink)))
    for left, right in continuation_pairs(units):
        issues.extend(boundary_issues(left, right))
    if source_page is not None and candidate_page is not None:
        for boundary in glyph_boundaries(source_page, units):
            owner = next(u for u in units if u['id'] == boundary['unit_id'])
            matches = candidate_text_matches(candidate_page, owner, owner['target'].strip())
            if not matches and len(owner.get('spans',[]))>1:
                matches=candidate_boundary_matches(candidate_page,owner,boundary)
            if len(matches) != 1:
                issues.append(_issue(owner['id'], 'glyph_spacing', 'local',
                                     scope='external_protected_glyph', reason='label_ink_unresolved'))
                continue
            # Nonspace character boxes of this composed cell only. Symbol-font
            # text may decode as ASCII/bullets and have trailing layout spaces.
            ink = fitz.Rect(matches[0]['ink_bbox'])
            gap = boundary['glyph_bbox'][0]-ink.x1
            if gap+.05 < boundary['minimum_gap']:
                issues.append(_issue(boundary['unit_id'], 'glyph_spacing', 'local',
                                     scope='external_protected_glyph', actual_gap=gap,
                                     candidate_ink_bbox=list(ink), **{
                                         k:v for k,v in boundary.items() if k != 'unit_id'}))
    if source_page is not None:
        return qualify_issues(issues, source_page.number+1)
    return issues


def qualify_issues(issues, page_number):
    """Expose native_runner ids while retaining composed row/cell provenance."""
    result = deepcopy(issues)
    def qualify(identifier):
        match = re.search(r'(?:^|_)u\d+', identifier)
        if not match:
            return identifier
        native = identifier[match.start():].lstrip('_')
        native = re.split(r'_(?:cell|field|row)\d+', native, maxsplit=1)[0]
        return f'p{page_number:04d}_'+native
    for issue in result:
        identifier = issue['unit_id']
        normalized = qualify(identifier)
        if normalized != identifier:
            issue['composed_unit_id'] = identifier
            issue['unit_id'] = normalized
        if issue.get('related_units'):
            issue['related_units'] = [qualify(identifier) for identifier in issue['related_units']]
    return result


def candidate_text_matches(page, unit, text):
    """Exact, spatially owned occurrences; never a layout bbox or a glyph font."""
    from .core import SYMBOL_FONT
    import hashlib
    normalize = lambda name: re.sub(r'^[A-Z]{6}\+', '', name).replace(' ', '').replace('-', '').lower()
    expected = {r['sha256'] for r in unit.get('font_runs', [])}
    verified = set()
    for row in page.get_fonts(full=True):
        data = page.parent.extract_font(row[0])[3]
        if data and hashlib.sha256(data).hexdigest() in expected:
            verified.update((normalize(row[3]), normalize(fitz.Font(fontbuffer=data).name)))
    box = fitz.Rect(unit.get('edit_bbox', unit['bbox']))
    found = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                if SYMBOL_FONT.search(span['font']) and normalize(span['font']) not in verified:
                    continue
                value = ''.join(c['c'] for c in span['chars'])
                for match in _matches(value, text):
                    chars = [c for c in span['chars'][match.start():match.end()] if c['c'].strip()]
                    if not chars:
                        continue
                    ink = fitz.Rect(chars[0]['bbox'])
                    for c in chars[1:]:
                        ink |= fitz.Rect(c['bbox'])
                    # Do not clip at the cell edge: an overflowing label must
                    # still be measured and reported, including its final ink.
                    if ink.intersects(box):
                        found.append(dict(text=text, span_bbox=list(span['bbox']),
                                          start=match.start(), end=match.end(),
                                          ink_bbox=list(ink), italic=bool(span['flags'] & 2)))
    return found



def candidate_boundary_matches(page,unit,boundary):
    """Measure the single physical label row before a protected glyph.

    A composed table's full target is not one PDF span. Source row geometry
    identifies its prefix label without guessing its translated word count.
    Multiple candidate rows remain ambiguous; symbol-font ink is never used.
    """
    from .core import SYMBOL_FONT
    import hashlib
    normalize=lambda name:re.sub(r'^[A-Z]{6}\+','',name).replace(' ','').replace('-','').lower()
    expected={r['sha256'] for r in unit.get('font_runs',[])};verified=set()
    for row in page.get_fonts(full=True):
        data=page.parent.extract_font(row[0])[3]
        if data and hashlib.sha256(data).hexdigest() in expected:
            verified.update((normalize(row[3]),normalize(fitz.Font(fontbuffer=data).name)))
    source_box=fitz.Rect(boundary['label_bbox']);glyph=fitz.Rect(boundary['glyph_bbox'])
    owner=fitz.Rect(unit.get('edit_bbox',unit['bbox']));found=[]
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines',[]):
            row=fitz.Rect(line['bbox'])
            overlap=max(0,min(row.y1,source_box.y1)-max(row.y0,source_box.y0))
            if overlap<.5*min(row.height,source_box.height):continue
            chars=[]
            for span in line['spans']:
                if SYMBOL_FONT.search(span['font']) and normalize(span['font']) not in verified:continue
                if expected and normalize(span['font']) not in verified:continue
                for char in span['chars']:
                    box=fitz.Rect(char['bbox'])
                    if char['c'].strip() and box.intersects(owner) and source_box.x0-1<=box.x0<glyph.x0:
                        chars.append(char)
            if not chars:continue
            ink=fitz.Rect(chars[0]['bbox'])
            for char in chars[1:]:ink|=fitz.Rect(char['bbox'])
            found.append({'text':''.join(c['c'] for c in chars),'ink_bbox':list(ink),
                          'span_bbox':list(row),'evidence':'source_label_row_before_protected_glyph'})
    return found


def quoted_foreign_terms(source_page, candidate_page, units):
    """Narrow metalinguistic exception, proven in source, runs and actual PDF.

    Italic emphasis alone does not exempt untranslated prose. A word/term/name
    introduction or explicit quotation must mark the source mention. Each
    exception is tied to a single candidate span and character range.
    """
    proofs = []
    shears = matrix_italics(source_page)
    for unit in units:
        for item in source_emphasis(source_page, unit, shears=shears):
            term = item['text']
            if not item['style'].get('italic') or not 1 <= len(term.split()) <= 3:
                continue
            if not re.fullmatch(r"[^\W\d_]+(?:[ '-][^\W\d_]+)*", term):
                continue
            source = _matches(unit['source'], term)
            target = _matches(unit['target'], term)
            if len(source) != 1 or len(target) != 1:
                continue
            left, right = source[0].span()
            prefix, suffix = unit['source'][:left], unit['source'][right:]
            mention = re.search(r'\b(?:word|term|name|called)\s+[“‘"\']?$', prefix, re.I)
            quoted = prefix.endswith(('“', '‘', '"', "'")) and suffix.startswith(('”', '’', '"', "'"))
            if not (mention or quoted):
                continue
            styles = list(_styles_at(unit, *target[0].span()))
            if not styles or not all(s.get('italic') for s in styles):
                continue
            actual = candidate_text_matches(candidate_page, unit, term)
            if len(actual) != 1 or not actual[0]['italic']:
                continue
            proof = dict(code='quoted_foreign_term', unit_id=unit['id'],
                         source_italic=True, target_italic=True,
                         evidence=item.get('evidence', 'source_style_run'), **actual[0])
            if proof not in proofs:
                proofs.append(proof)
    return proofs
