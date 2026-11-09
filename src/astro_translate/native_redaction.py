"""Identity-based fallback when full font metrics make rectangle deletion unsafe.

MuPDF's sanitize text filter removes only selected native character operators.
Unrelated glyphs are neither retyped nor rasterized. Ambiguous origins fail closed.
"""
from collections import Counter
import pymupdf as fitz
from .core import Blocked


def _characters(page):
    previous = fitz.TOOLS.set_small_glyph_heights()
    try:
        fitz.TOOLS.set_small_glyph_heights(True)
        raw = page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
    finally:
        fitz.TOOLS.set_small_glyph_heights(previous)
    return [(s, c) for b in raw['blocks'] for l in b.get('lines', [])
            for s in l['spans'] for c in s['chars'] if c['c'].strip()]


def _traced(page):
    result = {}
    for span in page.get_texttrace():
        for char in span['chars']:
            key = (char[0], round(char[2][0], 3), round(char[2][1], 3))
            result[key] = (span['font'], round(span['size'], 3), tuple(round(v, 3) for v in char[3]),
                           tuple(round(v, 6) for v in span['dir']))
    return result


def remove_source_spans(page, units):
    """Mutate only a disposable working page; verify every retained character."""
    wanted = [s for u in units for s in u['spans']]
    chars = _characters(page)
    selected, retained, matched = [], [], set()
    for span, char in chars:
        text = ''.join(c['c'] for c in span['chars'])
        matches = [i for i, s in enumerate(wanted) if text == s['source']
                   and span['font'] == s['font']
                   and max(abs(a-b) for a, b in zip(span['bbox'], s['bbox'])) < .01]
        if len(matches) > 1:
            raise Blocked('Ambiguous source spans for native text filter')
        if matches:
            matched.update(matches)
            selected.append((span, char))
        else:
            retained.append((span, char))
    if matched != set(range(len(wanted))) or not selected:
        raise Blocked('Cannot identify every source span for native text filter')
    origins = [fitz.Point(c['origin']) for _, c in selected]
    # The callback exposes matrices, not font identity. Only unique origins
    # can therefore authorize removal; coincident text must be reviewed.
    for origin in origins:
        if sum(abs(origin.x-c['origin'][0]) < .002 and abs(origin.y-c['origin'][1]) < .002
               for _, c in chars) != 1:
            raise Blocked('Ambiguous glyph origin for native text filter')
    # Per-glyph geometry from each glyph's own text matrix, independent of
    # how rawdict regroups lines once neighbours disappear.
    traced = _traced(page)
    removed = Counter()
    transform = page.transformation_matrix
    m = fitz.mupdf

    class TextFilter(m.PdfSanitizeFilterOptions2):
        def __init__(self):
            super().__init__()
            self.use_virtual_text_filter()

        def text_filter(self, ctx, ucs, length, trm, ctm, bbox, mode, ca, CA):
            matrix = fitz.Matrix(m.FzMatrix(trm)) * fitz.Matrix(m.FzMatrix(ctm))
            point = fitz.Point(matrix.e, matrix.f) * transform
            matches = [i for i, p in enumerate(origins)
                       if abs(point.x-p.x) < .001 and abs(point.y-p.y) < .001]
            if not matches:
                return 0
            if len(matches) != 1 or length != 1 or mode not in (0, 1, 2):
                raise Blocked('Unsupported or ambiguous native text operator')
            removed[matches[0]] += 1
            return 1

    options = TextFilter()
    filters = fitz._make_PdfFilterOptions(instance_forms=1, sanitize=1, sopts=options)
    pdf_page = page._pdf_page()
    m.pdf_filter_page_contents(pdf_page.doc(), pdf_page, filters)
    if removed != Counter(range(len(origins))):
        raise Blocked('Native text filter did not remove each selected glyph exactly once')
    after = _characters(page)
    if len(after) != len(retained):
        raise Blocked('Native text filter changed unrelated character count')
    for (a, ca), (b, cb) in zip(retained, after):
        if (ca['c'], a['font'], a['size'], a['color']) != (cb['c'], b['font'], b['size'], b['color']):
            raise Blocked('Native text filter changed unrelated glyph style or value')
        if max(abs(x-y) for x, y in zip(ca['origin']+ca['bbox'], cb['origin']+cb['bbox'])) > .001:
            # Rawdict char boxes follow the (re)grouped line direction; only an
            # identical origin plus identical per-glyph traced box is accepted.
            if max(abs(x-y) for x, y in zip(ca['origin'], cb['origin'])) > .001:
                raise Blocked('Native text filter changed unrelated glyph geometry')
            key = (ord(cb['c']), round(cb['origin'][0], 3), round(cb['origin'][1], 3))
            if key not in traced or _traced(page).get(key) != traced[key]:
                raise Blocked('Native text filter changed unrelated glyph geometry')
    return {'method': 'native_text_filter', 'unit_ids': [u['id'] for u in units],
            'removed_characters': len(origins), 'retained_characters_verified': len(after)}


def redact_source(source, target, units, profile, default):
    """Keep the existing rectangular path; use identity only when it is blocked."""
    rectangles, fallback = [], []
    for unit in units:
        try:
            rectangles.extend(profile.redactions(source, unit, default))
        except Blocked:
            if profile.data.get('composition') != 'patterns':
                raise
            fallback.append(unit)
    proofs = [remove_source_spans(target, fallback)] if fallback else []
    for rect in rectangles:
        target.add_redact_annot(rect, fill=False)
    if rectangles:
        target.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    return proofs
