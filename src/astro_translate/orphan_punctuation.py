"""Source punctuation that touches editorial text belongs to that text.

``fidelity.immutable_span`` keeps digit-like tokens intact, and a span made only
of ``,`` ``.`` ``:`` ``/`` ``-`` matches that class. When such a span touches a
span of a composed unit (the comma of «MAP,» set in another font on p9, the
comma of «astrology,» on p21) keeping it in place leaves an orphan beside the
new Spanish text («MAP,A», a doubled comma). Composition removes it together
with the unit that owns the preceding text; the Spanish punctuation comes from
the translation. Saved translations keep their source provenance unchanged.

A terminal mark the translation does not end with is never removed silently: it
stays, and QA (``orphan_punctuation_issues``) reports it beside the new text.
"""
from __future__ import annotations

import re

import pymupdf as fitz

# Punctuation-only spans that ``immutable_span`` protects (no digits).
ABSORBABLE = re.compile(r'[.,:;/\-]{1,3}')
# Punctuation whose original glyph must not survive next to rewritten text.
# Quote marks are excluded: decorative display marks are preserved on purpose.
ORPHAN = re.compile(r'[.,:;!?…/\-]{1,3}')


def _spans(page):
    return [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', [])
            if tuple(l['dir']) == (1., 0.) for s in l['spans'] if s['text'].strip()]


def _same(raw, span, tolerance=.01):
    """Same source span: text, font, horizontal extent and vertical centre."""
    a, b = raw['bbox'], span['bbox']
    return (raw['text'] == span.get('source', span.get('text')) and raw['font'] == span['font']
            and abs(a[0]-b[0]) < tolerance and abs(a[2]-b[2]) < tolerance
            and abs((a[1]+a[3])-(b[1]+b[3])) < raw['size'])


def _band(a, b):
    a, b = fitz.Rect(a), fitz.Rect(b)
    return min(a.y1, b.y1)-max(a.y0, b.y0) > .5*min(a.height, b.height)


def _follows(mark, text, gap=1.5):
    """``mark`` starts where ``text`` ends, on the same line band."""
    return _band(mark['bbox'], text['bbox']) and -gap <= mark['bbox'][0]-text['bbox'][2] <= gap


def _near(a, b, gap):
    ra, rb = fitz.Rect(a['bbox']), fitz.Rect(b['bbox'])
    if ra.intersects(rb):
        return True
    return _band(ra, rb) and (0 <= ra.x0-rb.x1 <= gap or 0 <= rb.x0-ra.x1 <= gap)


def absorb_adjacent_punctuation(page, units, extras=()):
    """Give each unowned punctuation span touching a unit's text to that unit.

    The span goes to ``absorbed_punctuation`` (redacted with the unit, excluded
    from the immutable check); spans, source and target stay as saved.
    """
    from .core import SYMBOL_FONT
    from .fidelity import immutable_span, span_style
    raw = _spans(page)
    result = [dict(u) for u in units]
    owner = {}
    for unit in result:
        for span in (unit.get('spans', [])+unit.get('relocated_punctuation', [])
                     + unit.get('absorbed_punctuation', [])):
            for index, item in enumerate(raw):
                if index not in owner and _same(item, span):
                    owner[index] = unit
    for index, item in enumerate(raw):
        value = item['text'].strip()
        if index in owner or not ABSORBABLE.fullmatch(value) or SYMBOL_FONT.search(item['font']):
            continue
        if not immutable_span({'source': item['text'], 'font': item['font']}, extras):
            continue
        # Punctuation belongs to the text it follows; a leading mark to the next.
        left = [j for j, other in enumerate(raw) if j != index and _follows(item, other)]
        right = [j for j, other in enumerate(raw) if j != index and _follows(other, item)]
        neighbours = left or right
        if len(neighbours) != 1 or neighbours[0] not in owner:
            continue
        unit = owner[neighbours[0]]
        spans = unit.get('spans', [])
        terminal = bool(left) and spans and _same(raw[neighbours[0]], spans[-1])
        if terminal and not unit.get('target', '').rstrip().endswith(value):
            continue
        span = {'source': item['text'], 'bbox': list(item['bbox']), 'font': item['font'],
                'size': item['size'], 'color': item['color'], 'flags': item['flags'], 'rotate': 0,
                'style': span_style(item)}
        unit['absorbed_punctuation'] = unit.get('absorbed_punctuation', [])+[span]
        owner[index] = unit
    return result


def redaction_unit(unit):
    """The unit as redaction sees it: its spans plus the absorbed punctuation."""
    extra = unit.get('absorbed_punctuation') or []
    if not extra:
        return unit
    rect = fitz.Rect(unit['bbox'])
    for span in extra:
        rect |= fitz.Rect(span['bbox'])
    result = {**unit, 'spans': list(unit.get('spans', []))+list(extra), 'bbox': list(rect)}
    if unit.get('precise_edit_rects'):
        result['precise_edit_rects'] = list(unit['precise_edit_rects'])+[list(s['bbox']) for s in extra]
    return result


def orphan_punctuation_issues(source_page, candidate_page):
    """Original punctuation kept in place while the text it touched was rewritten.

    A punctuation-only source span still present unchanged in the candidate is an
    orphan when a lettered source span it touches is gone, or when new candidate
    text intersects it or lies within 3 pt on its line band.
    """
    from .core import SYMBOL_FONT
    source, candidate = _spans(source_page), _spans(candidate_page)
    index = {}
    for span in candidate:
        index.setdefault((span['text'], span['font']), []).append(span)
    kept = lambda s: any(_same(s, c, .05) for c in index.get((s['text'], s['font']), []))
    original = {}
    for span in source:
        original.setdefault((span['text'], span['font']), []).append(span)
    new = [c for c in candidate if re.search(r'\w', c['text'])
           and not any(_same(c, s, .05) for s in original.get((c['text'], c['font']), []))]
    issues = []
    for span in source:
        value = span['text'].strip()
        if not ORPHAN.fullmatch(value) or SYMBOL_FONT.search(span['font']) or not kept(span):
            continue
        touching = [o for o in source if o is not span and re.search(r'[^\W\d_]', o['text'])
                    and (_follows(span, o) or _follows(o, span))]
        vanished = [o['text'] for o in touching if not kept(o)]
        beside = [c['text'] for c in new if _near(span, c, 3)]
        if vanished or beside:
            issues.append({'code': 'orphan_source_punctuation', 'text': value, 'font': span['font'],
                           'bbox': [round(x, 2) for x in span['bbox']], 'rewritten_neighbours': vanished,
                           'new_text_beside': beside, 'action': 'recompose'})
    return issues
