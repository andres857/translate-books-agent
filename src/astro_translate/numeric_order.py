"""Conservative EN→ES count-noun order checks; never rewrite translations.

Only explicit plural count nouns with an unambiguous bilingual alignment are
covered. Singular labels (House 7), ordinals, dates, unknown nouns and repeated
values/nouns are left to editorial QA. Both masked wire text and locally
restored caches can be checked. Diagnostics never contain protected values.
"""
import re
from collections import Counter


# Alignment vocabulary, not translation substitutions or glossary overrides.
COUNT_NOUNS = {
    'signs': 'signos', 'planets': 'planetas', 'houses': 'casas',
    'elements': 'elementos', 'modes': 'modalidades',
    'polarities': 'polaridades', 'aspects': 'aspectos',
    'angles': 'ángulos', 'axes': 'ejes', 'degrees': 'grados',
    'years': 'años', 'months': 'meses', 'days': 'días',
    'hours': 'horas', 'minutes': 'minutos', 'seconds': 'segundos',
    'sections': 'secciones', 'parts': 'partes',
}
STYLE = re.compile(r'\{v\d+\}')
TOKEN = re.compile(r'\{n\d+\}|\d+(?:[.,:/−+\-]\d+)*|[^\W\d_]+|[^\w\s]', re.UNICODE)


def numeric_quantifier_issues(source, target):
    """Flag only an adjacent noun/count inversion of a unique plural noun.

    Example shape: EN ``{nN} signs`` → ES ``signos {nN}``. Reordering
    whole clauses is allowed; ambiguous alignments are deliberately abstained
    from. Style boundaries are transparent, not word boundaries.
    """
    src = TOKEN.findall(STYLE.sub('', source).casefold())
    dst = TOKEN.findall(STYLE.sub('', target).casefold())
    src_counts, dst_counts = Counter(src), Counter(dst)
    issues = []
    for index, token in enumerate(src[:-1]):
        if not (re.fullmatch(r'\{n\d+\}', token) or token.isdecimal()):
            continue
        noun = src[index + 1]
        equivalent = COUNT_NOUNS.get(noun)
        if not equivalent or any(count != 1 for count in (
            src_counts[token], dst_counts[token], src_counts[noun], dst_counts[equivalent]
        )):
            continue
        position = dst.index(token)
        if position and dst[position - 1] == equivalent:
            issues.append({'reason': 'displaced_numeric_quantifier',
                           'source_noun': noun, 'target_noun': equivalent})
    return issues
