"""Span placement in the free gaps of field/value rows (no value rewriting).

Opted into by the Secrets pilot after saved translation provenance is checked.
The caller's group fitter tries 1.00..0.75; only at the floor may an anchor
move left. No shortened text, wrapping, or independent label scaling is used.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pymupdf as fitz

from .core import Blocked, atomic_json, book_lock

FIELDS = {'Root', 'Symbol', 'Angle', 'Orb'}
MIN_SCALE = .75
CLEARANCE_EM = .25
FAILURE = 'protected_label_no_space: full label cannot fit at scale 0.75; short form requires human-approved supplement'


def stop_failed_labels(book, folder, page, result):
    """Persist review evidence before stopping; never publish a partial page."""
    failed = [f for f in result['failures'] if f.get('reason') == FAILURE]
    if not failed:
        return
    book, folder = Path(book), Path(folder)
    capture = folder / f'p{result["page"]:04d}-protected-label-failure.png'
    page.get_pixmap(dpi=110).save(capture)
    with book_lock(book):
        path = book / 'work/review_queue.json'
        queue = json.loads(path.read_text()) if path.exists() else []
        queue = [q for q in queue if not (q['page'] == result['page'] and q['reason'] == FAILURE)]
        queue.append({'page': result['page'], 'reason': FAILURE, 'failed_ids': [f['id'] for f in failed],
                      'artifacts': [str(folder / f'p{result["page"]:04d}-fit.json'), str(capture)],
                      'suggested_action': 'Human review; no unapproved short forms'})
        atomic_json(path, queue)
    raise Blocked(FAILURE)


def protected_ink_boxes(page):
    from .fidelity import immutable_span

    raw = [s for b in page.get_text('rawdict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
    protected = []
    for span in raw:
        text = ''.join(c['c'] for c in span['chars'])
        if text.strip() and immutable_span({'source': text, 'font': span['font']}):
            chars = [fitz.Rect(c['bbox']) for c in span['chars'] if c['c'].strip()]
            rect = fitz.Rect(chars[0])
            for char in chars[1:]:
                rect |= char
            protected.append(rect)
    return protected


def partition_rows(page, units):
    from .layout import _visible_path, foreground_images
    raw = [s for b in page.get_text('rawdict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
    protected = protected_ink_boxes(page)
    # The semisquare symbol is a stroked path, not a native text glyph.
    # Treat visible artwork as a gap boundary too; the exact ink guard still
    # checks the rendered overlay. Ignore out-of-CropBox printing marks.
    for drawing in page.get_drawings():
        if _visible_path(drawing, page.rect):
            half = drawing.get('width', 0)/2 if drawing['type'] in ('s', 'fs') else 0
            protected.append(fitz.Rect(drawing['rect'])+(-half, -half, half, half))
    protected.extend(foreground_images(page))
    result = []
    for unit in units:
        if not unit['spans'] or not all(s['source'].strip() in FIELDS for s in unit['spans']):
            result.append(unit)
            continue
        # These saved field names are one word each. Partition existing runs;
        # never manufacture a translation or infer a short form.
        words = [(word, run['style']) for run in unit['target_runs'] for word in run['text'].split()]
        if len(words) != len(unit['spans']):
            raise Blocked('Ambiguous protected field/translation span alignment')
        for index, (span, (target, style)) in enumerate(zip(unit['spans'], words)):
            matches = [s for s in raw if s['font'] == span['font']
                       and ''.join(c['c'] for c in s['chars']) == span['source']
                       and max(abs(a-b) for a,b in zip(s['bbox'], span['bbox'])) < .01]
            if len(matches) != 1:
                raise Blocked('Cannot identify protected-row label source span')
            source = matches[0]
            chars = [c for c in source['chars'] if c['c'].strip()]
            ink = fitz.Rect(chars[0]['bbox'])
            for char in chars[1:]:
                ink |= fitz.Rect(char['bbox'])
            row = [r for r in protected if r.y0 < ink.y1 and r.y1 > ink.y0]
            right = min((r.x0 for r in row if r.x0 >= ink.x1-.01), default=None)
            left = max((r.x1 for r in row if r.x1 <= ink.x0+.01), default=page.rect.x0)
            if right is None:
                raise Blocked('Field has no following protected value')
            child = copy.deepcopy(unit)
            child.update(id=unit['id'] if len(words) == 1 else unit['id']+f'_field{index}',
                         source=span['source'].strip(), target=target, spans=[copy.deepcopy(span)],
                         target_runs=[{'text': target, 'style': copy.deepcopy(style)}],
                         style=copy.deepcopy(style), bbox=list(span['bbox']), kind='label',
                         context='table_label', alignment='left', first_line_indent=0,
                         scale_group=unit['scale_group'] if unit.get('row_parent_id') else f'p{page.number+1:04d}_protected_fields',
                         protected_label={'left': left, 'right': right, 'anchor': ink.x0,
                                          'baseline': source['origin'][1],
                                          'clearance_em': CLEARANCE_EM})
            child.pop('paragraphs', None)
            result.append(child)
    return result


def candidate_boxes(unit):
    gap = unit['protected_label']
    rect = fitz.Rect(unit['bbox'])
    # Reserve the smallest permitted padding here; the overlay enforces the
    # exact factor-dependent padding on both sides against actual text width.
    pad = unit['size']*MIN_SCALE*CLEARANCE_EM
    rect.x0, rect.x1 = gap['left']+pad, gap['right']-pad
    if unit['source'] == 'Root':
        # First field defines the row/column's left edge. A missing preceding
        # value is not permission to claim whitespace across the other column.
        rect.x0 = max(rect.x0, gap['anchor'])
    return [] if rect.is_empty else [rect]


def label_overlay(page, unit, box, factor, font_map):
    """Render one complete label at its original baseline, inside its gap."""
    doc = fitz.open()
    out = doc.new_page(width=page.rect.width, height=page.rect.height)
    if not MIN_SCALE <= factor <= 1:
        return doc, False
    gap = unit['protected_label']
    style = unit['target_runs'][0]['style']
    choice = font_map[(unit['scale_group'], tuple(sorted(style.items())))]
    font = choice['font']
    size = style['size']*factor
    pad = size*CLEARANCE_EM
    left, right = max(box.x0, gap['left']+pad), min(box.x1, gap['right']-pad)
    width = font.text_length(unit['target'], fontsize=size)
    anchor = max(left, gap['anchor'])
    if anchor+width > right:
        if factor > MIN_SCALE or width > right-left:
            return doc, False
        anchor = right-width
    if anchor < left or anchor+width > right+.0001:
        return doc, False
    color = tuple(((style['color'] >> shift) & 255)/255 for shift in (16, 8, 0))
    out.insert_font(fontname='field', fontfile=choice['path'])
    out.insert_text((anchor, gap['baseline']), unit['target'], fontname='field', fontsize=size, color=color)
    # Check the actual renderer too (side bearings / extraction / clipping).
    spans = [s for b in out.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
    ok = len(spans) == 1 and spans[0]['text'] == unit['target']
    if ok:
        actual = fitz.Rect(spans[0]['bbox'])
        ok = actual.x0 >= left-.001 and actual.x1 <= right+.001
    return doc, ok
