"""Horizontal growth limits from the rules and panels next to a block (audit batch-17, p176).

A display title underlined by a rule ends where the rule ends; a flat panel beside a
block keeps its original clearance. Both are found on the rendered original, so they
work for vector paths and for rules painted into a full-page raster alike.

* Rule: a thin (≤4 pt) run of uniform non-background colour within 0.75 em above or
  below the block's ink that spans ≥80 % of the ink width.
* Panel: beside the block's ink band, ≥ max(4 pt, 0.25 em) consecutive columns that are
  ≥90 % uniform non-background colour over the band.

New text may not pass the rule end, nor come closer to a panel than
``min(original clearance, max(3 pt, 0.25 em))``. Limits never shrink the source footprint.
"""
from __future__ import annotations

import numpy as np
import pymupdf as fitz

ZOOM = 4
BACKGROUND_DELTA = 40
COLOR_STEP = 40


def _render(page):
    cached = getattr(page, '_rule_limits_render', None)
    if cached is None:
        pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), alpha=False)
        cached = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].astype(np.int16)
        # A high-resolution decode left in MuPDF's store changes later renders of
        # the same raster by one colour level (pixel proofs compare exact samples).
        fitz.TOOLS.store_shrink(100)
        try:
            page._rule_limits_render = cached
        except AttributeError:
            pass
    return cached


def _px(value, limit):
    return int(min(max(round(value*ZOOM), 0), limit))


def source_ink(page, unit):
    """Union of the real glyph ink of the unit's source spans (metric box if unknown)."""
    from .line_clearance import source_unit_records
    own, _others = source_unit_records(page, unit)
    if not own:
        return fitz.Rect(unit['bbox'])
    rect = fitz.Rect(own[0]['ink'])
    for record in own[1:]:
        rect |= record['ink']
    return rect


def _background(image, ink, ring=3):
    """Most frequent colour of a thin ring around the ink (heavy type darkens the ink box)."""
    h, w = image.shape[:2]
    outer = ink+(-ring, -ring, ring, ring)
    x0, y0, x1, y1 = _px(outer.x0, w), _px(outer.y0, h), _px(outer.x1, w), _px(outer.y1, h)
    ix0, iy0, ix1, iy1 = _px(ink.x0, w), _px(ink.y0, h), _px(ink.x1, w), _px(ink.y1, h)
    parts = [image[y0:iy0, x0:x1], image[iy1:y1, x0:x1], image[iy0:iy1, x0:ix0], image[iy0:iy1, ix1:x1]]
    pixels = np.concatenate([part.reshape(-1, 3) for part in parts])
    if not len(pixels):
        return np.array([255, 255, 255])
    colors, counts = np.unique(pixels, axis=0, return_counts=True)
    return colors[counts.argmax()]


def _row_runs(row, mask):
    """[start, end) runs of uniform non-background colour in one pixel row."""
    jump = np.abs(np.diff(row, axis=0)).max(axis=1) > COLOR_STEP
    breaks = ~mask[:-1] | ~mask[1:] | jump
    starts = np.flatnonzero(mask & np.concatenate(([True], breaks)))
    ends = np.flatnonzero(mask & np.concatenate((breaks, [True])))+1
    return list(zip(starts.tolist(), ends.tolist()))


def rules(page, ink, size):
    """Thin rules over/under ``ink``: list of (x0, x1, y0, y1) in points."""
    image = _render(page)
    h, w = image.shape[:2]
    bg = _background(image, ink)
    reach = .75*size
    found = []
    for top, bottom in ((ink.y1, ink.y1+reach), (ink.y0-reach, ink.y0)):
        y0, y1 = _px(top, h), _px(bottom, h)
        if y1 <= y0:
            continue
        band = image[y0:y1]
        mask = np.abs(band-bg).max(axis=2) > BACKGROUND_DELTA
        hits = []
        for index in range(band.shape[0]):
            if mask[index].sum() < .8*ink.width*ZOOM:
                hits.append(None)
                continue
            best = None
            for a, b in _row_runs(band[index], mask[index]):
                covered = min(b/ZOOM, ink.x1)-max(a/ZOOM, ink.x0)
                if covered >= .8*ink.width and (best is None or b-a > best[1]-best[0]):
                    best = (a, b)
            hits.append(best)
        index = 0
        while index < len(hits):
            if hits[index] is None:
                index += 1
                continue
            end = index
            while end+1 < len(hits) and hits[end+1] is not None:
                end += 1
            if (end-index+1)/ZOOM <= 4:
                xs = sorted(hits[i] for i in range(index, end+1))
                a, b = xs[len(xs)//2]
                found.append((a/ZOOM, b/ZOOM, (y0+index)/ZOOM, (y0+end+1)/ZOOM))
            index = end+1
    return found


def panels(page, ink, size):
    """Flat panels beside the ink band: (left_panel_x1 or None, right_panel_x0 or None)."""
    image = _render(page)
    h, w = image.shape[:2]
    y0, y1 = _px(ink.y0, h), _px(ink.y1, h)
    if y1 <= y0:
        return None, None
    bg = _background(image, ink)
    band = image[y0:y1]
    mask = np.abs(band-bg).max(axis=2) > BACKGROUND_DELTA
    spread = band.std(axis=0).max(axis=1)
    solid = (mask.mean(axis=0) >= .9) & (spread < 12)
    width = max(4, .25*size)*ZOOM

    def first(columns):
        count = 0
        for x in columns:
            count = count+1 if solid[x] else 0
            if count >= width:
                return x
        return None
    right = first(range(_px(ink.x1, w), w))
    left = first(range(_px(ink.x0, w)-1, -1, -1))
    right = None if right is None else (right-width+1)/ZOOM
    left = None if left is None else (left+width)/ZOOM
    return left, right


def horizontal_limits(page, unit):
    """(left, right) limits in points for new text of ``unit``, plus evidence."""
    ink = source_ink(page, unit)
    size = unit.get('size', 10)
    left, right = 0., page.rect.width
    evidence = []
    for x0, x1, y0, y1 in rules(page, ink, size):
        if x1 >= ink.x1-1:
            right = min(right, max(x1, ink.x1))
        if x0 <= ink.x0+1:
            left = max(left, min(x0, ink.x0))
        evidence.append({'kind': 'rule', 'bbox': [x0, y0, x1, y1]})
    panel_left, panel_right = panels(page, ink, size)
    clearance = max(3., .25*size)
    if panel_right is not None:
        right = min(right, max(ink.x1, panel_right-min(panel_right-ink.x1, clearance)))
        evidence.append({'kind': 'panel', 'x0': panel_right})
    if panel_left is not None:
        left = max(left, min(ink.x0, panel_left+min(ink.x0-panel_left, clearance)))
        evidence.append({'kind': 'panel', 'x1': panel_left})
    return left, right, ink, evidence


EXEMPT = ('native_ordinal', 'protected_label', 'story_frames', 'contents_column', 'chart_callout',
          'caption_growth', 'column_bounds')


def applies(unit):
    return bool(unit.get('spans')) and unit.get('rotate', 0) == 0 and not any(unit.get(k) for k in EXEMPT)


def clamp(page, unit, boxes):
    """Candidate boxes clipped to the rule/panel limits, never below the source footprint."""
    if not boxes or not applies(unit):
        return boxes
    left, right, _ink, _evidence = horizontal_limits(page, unit)
    base = fitz.Rect(unit['bbox'])
    right, left = max(right, base.x1), min(left, base.x0)
    result = []
    for box in boxes:
        box = fitz.Rect(box)
        box.x1 = min(box.x1, right)
        box.x0 = max(box.x0, left)
        if not box.is_empty and box not in result:
            result.append(box)
    return result


def overflow_issues(source_page, candidate_page, blocks):
    """QA: composed glyph ink past a rule end or into a panel's clearance."""
    from .line_clearance import glyph_records, _inside
    issues = []
    records = glyph_records(candidate_page)
    for unit in blocks:
        if not applies(unit) or not unit.get('edit_bbox'):
            continue
        left, right, ink, evidence = horizontal_limits(source_page, unit)
        if not evidence:
            continue
        boxes = [fitz.Rect(b) for b in unit.get('edit_boxes', [unit['edit_bbox']])]
        mine = [r for r in records if _inside(r, boxes)]
        if not mine:
            continue
        x0 = min(r['ink'].x0 for r in mine)
        x1 = max(r['ink'].x1 for r in mine)
        if x1 > max(right, ink.x1)+.5 or x0 < min(left, ink.x0)-.5:
            issues.append({'code': 'rule_or_panel_overflow', 'unit_id': unit['id'], 'action': 'recompose',
                           'ink_x': [round(x0, 2), round(x1, 2)], 'limits': [round(left, 2), round(right, 2)],
                           'evidence': evidence})
    return issues
