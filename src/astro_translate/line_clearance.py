"""Ink clearance between consecutive text lines of display type (audit batch-17, p37/p46).

Spanish capitals carry accents above the cap height («INTRÉPIDO», «CÁNCER»). A display
block composed with the source leading of an unaccented English title can put the
tilde on the line above. Glyph ink comes from the real MuPDF font objects
(``safe_vlm._glyph_ink_bounds``), never from metric boxes.

The required clearance is ``max(1 pt, 0.04 em)`` of the larger glyph, but never more
than the clearance the original already had at the same place (descenders over
ascenders in tight source leading stay legal).
"""
from __future__ import annotations

import pymupdf as fitz

DISPLAY_MIN_SIZE = 17
TOLERANCE = .05


def required_gap(size):
    return max(1., .04*size)


def glyph_records(page):
    """Horizontal glyphs with ink: dicts with char, size, baseline, ink rect and origin."""
    cached = getattr(page, '_line_clearance_records', None)
    if cached is not None:
        return cached
    from .safe_vlm import _glyph_ink_bounds
    ink, _unsupported, failed = _glyph_ink_bounds(page)
    records = []
    if not failed:
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines', []):
                if tuple(round(v, 3) for v in line['dir']) != (1., 0.):
                    continue
                for span in line['spans']:
                    for char in span['chars']:
                        if not char['c'].strip():
                            continue
                        rects = ink.get((ord(char['c']), round(char['origin'][0], 2), round(char['origin'][1], 2)))
                        if not rects:
                            continue
                        rect = fitz.Rect(rects[0])
                        for other in rects[1:]:
                            rect |= other
                        records.append({'char': char['c'], 'size': span['size'], 'baseline': char['origin'][1],
                                        'origin': tuple(char['origin']), 'ink': rect})
    try:
        page._line_clearance_records = records
    except AttributeError:
        pass
    return records


def smallest_gap(lower, uppers):
    """Smallest vertical ink gap from ``lower`` glyphs up to glyphs of an earlier line.

    Returns ``(gap, lower_record, upper_record)`` or ``None``. Lines are told apart by
    baseline (more than 0.3 em lower) and only nearby lines (≤1.6 em) are compared.
    """
    best = None
    for low in lower:
        for up in uppers:
            if low is up or low['baseline'] <= up['baseline']+.3*low['size']:
                continue
            if low['baseline']-up['baseline'] > 1.6*max(low['size'], up['size']):
                continue
            a, b = low['ink'], up['ink']
            if min(a.x1, b.x1)-max(a.x0, b.x0) <= 0:
                continue
            gap = a.y0-b.y1
            if best is None or gap < best[0]:
                best = (gap, low, up)
    return best


def _inside(record, rects):
    x, y = record['origin']
    return any(r.x0-.5 <= x <= r.x1+.5 and r.y0-.5 <= y <= r.y1+.5 for r in rects)


def source_unit_records(page, unit):
    """Source glyphs of a unit (origins inside its span boxes) and the rest of the page."""
    rects = [fitz.Rect(s['bbox']) for s in unit.get('spans', [])]
    records = glyph_records(page)
    own = [r for r in records if _inside(r, rects)]
    return own, [r for r in records if not _inside(r, rects)]


def source_clearance(page, unit):
    """Clearance the original already had above the unit's glyphs (inf if none)."""
    cache = getattr(page, '_line_clearance_source', None)
    if cache is None:
        cache = {}
        try:
            page._line_clearance_source = cache
        except AttributeError:
            pass
    key = (unit.get('id'), tuple(tuple(s['bbox']) for s in unit.get('spans', [])))
    if key not in cache:
        own, _others = source_unit_records(page, unit)
        found = smallest_gap(own, glyph_records(page))
        cache[key] = float('inf') if found is None else found[0]
    return cache[key]


def overlay_collision(source_page, unit, overlay_page, size):
    """Clearance problem of a freshly rendered unit, or None.

    Upper glyphs are the overlay's own lines and the source glyphs of every other
    text on the page (a proxy for neighbours composed separately). Returns a dict with
    ``gap``, ``limit`` and ``internal`` (both glyphs belong to the overlay).
    """
    if size < DISPLAY_MIN_SIZE:
        return None
    mine = glyph_records(overlay_page)
    if not mine:
        return None
    _own, others = source_unit_records(source_page, unit)
    found = smallest_gap(mine, mine+others)
    if found is None:
        return None
    gap, low, up = found
    limit = min(required_gap(max(low['size'], up['size'])), source_clearance(source_page, unit))
    if gap >= limit-TOLERANCE:
        return None
    internal = smallest_gap(mine, mine)
    return {'gap': gap, 'limit': limit, 'internal': bool(internal and internal[0] < limit-TOLERANCE),
            'internal_gap': internal[0] if internal else None, 'lower': low['char'], 'upper': up['char'],
            'bbox': list(low['ink'] | up['ink'])}


def collision_issues(source_page, candidate_page, blocks):
    """QA: display glyphs of a composed unit closer to the line above than allowed."""
    issues = []
    records = glyph_records(candidate_page)
    for unit in blocks:
        if not unit.get('spans') or not unit.get('edit_bbox') or unit.get('native_ordinal') or unit.get('protected_label'):
            continue
        size = unit.get('size', 0)*unit.get('scale', 1)
        if size < DISPLAY_MIN_SIZE:
            continue
        boxes = [fitz.Rect(b) for b in unit.get('edit_boxes', [unit['edit_bbox']])]
        mine = [r for r in records if _inside(r, boxes) and r['size'] >= .9*size]
        found = smallest_gap(mine, records)
        if found is None:
            continue
        gap, low, up = found
        limit = min(required_gap(max(low['size'], up['size'])), source_clearance(source_page, unit))
        if gap < limit-TOLERANCE:
            issues.append({'code': 'line_ink_collision', 'unit_id': unit['id'], 'action': 'recompose',
                           'gap': round(gap, 3), 'required': round(limit, 3), 'lower': low['char'],
                           'upper': up['char'], 'bbox': [round(v, 2) for v in (low['ink'] | up['ink'])]})
    return issues
