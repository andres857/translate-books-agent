"""Join geometrically continuous caption fragments before translation.

Legacy ids are assigned first. Joins get a new explicit id; unrelated containers
keep their ids, source spans and styles. No targets are concatenated here.
"""
from __future__ import annotations

import re
import pymupdf as fitz


def _lines(unit):
    lines = []
    for span in unit['spans']:
        rect = fitz.Rect(span['bbox'])
        if lines and abs(rect.y0-lines[-1].y0) < .5:
            lines[-1] |= rect
        else:
            lines.append(rect)
    return lines


def _alignment(lines):
    for name, values in (
        ('center', [(r.x0+r.x1)/2 for r in lines]),
        ('left', [r.x0 for r in lines]),
        ('right', [r.x1 for r in lines]),
    ):
        if max(values)-min(values) <= 2:
            return name
    return None


def _separators(page):
    from .layout import _visible_path
    rects = []
    for drawing in page.get_drawings():
        half = max((drawing.get('width') or 0)/2, .05)
        if not (_visible_path(drawing, page.rect) or (drawing['type'] == 's' and
                (fitz.Rect(drawing['rect'])+(-half,-half,half,half)).intersects(page.rect))):
            continue
        for item in drawing['items']:
            if item[0] == 're':
                r = fitz.Rect(item[1])
                # Rectangle outlines have four edges, not a solid interior.
                if drawing['type'] == 's':
                    rects.extend([fitz.Rect(r.x0,r.y0,r.x1,r.y0), fitz.Rect(r.x0,r.y1,r.x1,r.y1),
                                  fitz.Rect(r.x0,r.y0,r.x0,r.y1), fitz.Rect(r.x1,r.y0,r.x1,r.y1)])
                    continue
            elif item[0] == 'qu':
                r = item[1].rect
            else:
                points = [p for p in item[1:] if isinstance(p, fitz.Point)]
                if not points:
                    continue
                r = fitz.Rect(min(p.x for p in points), min(p.y for p in points),
                              max(p.x for p in points), max(p.y for p in points))
            rects.append(r+(-half,-half,half,half))
    return [r+(-.05,-.05,.05,.05) for r in rects]


def merge_caption_units(page, units):
    from .fidelity import _make_unit
    from .layout import drawing_obstacles, foreground_images
    drawings = drawing_obstacles(page)
    separators = _separators(page) + foreground_images(page)
    merged = []
    for unit in units:
        previous = merged[-1] if merged else None
        join = False
        if previous is not None:
            lines = _lines(previous) + _lines(unit)
            a, b = _lines(previous)[-1], _lines(unit)[0]
            size = unit['size']
            alignment = _alignment(lines)
            gap = fitz.Rect(min(a.x0,b.x0), a.y1, max(a.x1,b.x1), b.y0)
            styles = [s['style'] for u in (previous,unit) for s in u['spans']]
            join = (size <= 10 and all(s == styles[0] for s in styles)
                    and previous['context'] != 'table_label' and unit['context'] != 'table_label'
                    and max(r.width for r in lines) < page.rect.width*.48
                    and .85*size <= b.y0-a.y0 <= 1.6*size
                    and b.y0 >= a.y1-.1 and alignment is not None
                    and not re.search(r'[.!?;:][”’\"\']?$', previous['source'].rstrip())
                    and not any(gap.intersects(r) for r in separators))
        if join:
            ids = previous.get('merged_from', [previous['id']]) + [unit['id']]
            fresh = _make_unit(previous['spans']+unit['spans'], previous['source_block'], False, drawings)
            fresh.update(id=ids[0]+'_join_'+'_'.join(ids[1:]),
                         container_id=previous['container_id']+'_join_'+unit['id'],
                         merged_from=ids, scale_group=previous['scale_group'], alignment=alignment)
            merged[-1] = fresh
        else:
            merged.append(unit)
    return merged
