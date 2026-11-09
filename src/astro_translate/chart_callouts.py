"""Diagram callouts using validated saved runs and measured free space."""
from __future__ import annotations

import copy
import pymupdf as fitz


LEGEND_MAX_SIZE = 10
LEGEND_GUIDE_REACH = 12


def _guide_endpoints(page):
    """Free ends of thin open strokes (1-2 line/curve items) inside the page:
    leader lines, solid or dashed. Crop marks lie outside the page box."""
    ends = []
    for d in page.get_drawings():
        items = d['items']
        if d.get('fill') is not None or d['type'] != 's' or not 1 <= len(items) <= 2:
            continue
        if any(i[0] not in ('l', 'c') for i in items) or (d.get('width') or 0) > 1.5:
            continue
        if not page.rect.contains(fitz.Rect(d['rect'])):
            continue
        start, end = items[0][1], items[-1][-1]
        if abs(start.x-end.x)+abs(start.y-end.y) <= 8:
            continue
        ends.append((start, end))
    return ends


def mark_figure_legends(page, units):
    """Flag small all-italic text blocks reached by a leader line.

    A figure legend explains a point of an illustration; it is composed as
    its own caption (free-space growth), never chained into a prose story.
    Only adds ``figure_legend`` / ``legend_guides``; targets are untouched.
    """
    units = copy.deepcopy(units)
    guides = _guide_endpoints(page)
    if not guides:
        return units
    for u in units:
        spans = u.get('spans') or []
        if (not spans or u['size'] > LEGEND_MAX_SIZE or not all(s['style'].get('italic') for s in spans)
                or any(u.get(k) for k in ('chart_callout', 'run_in_heading', 'contents_column', 'native_ordinal',
                                          'protected_label', 'row_composition'))
                or len(u['source'].split()) < 2):
            continue
        zone = fitz.Rect(u['bbox'])+(-LEGEND_GUIDE_REACH, -LEGEND_GUIDE_REACH, LEGEND_GUIDE_REACH, LEGEND_GUIDE_REACH)
        reached = sum(zone.contains(a) or zone.contains(b) for a, b in guides)
        if reached:
            u['figure_legend'] = True
            u['legend_guides'] = reached
    return units


def finalize_callouts(page, units):
    units = mark_figure_legends(page, units)
    result = []
    i = 0
    while i < len(units):
        head = units[i]
        if head['size'] != 9 or head['style']['weight'] != 500:
            result.append(head)
            i += 1
            continue
        members = [head]
        j = i + 1
        while j < len(units):
            body = units[j]
            prev = members[-1]
            # Continuations stay local; never consume MC/IC or another callout.
            if (body['style']['weight'] != 300 or body['size'] > 9
                    or not -.5 <= body['bbox'][1] - prev['bbox'][3] <= 3
                    or min(body['bbox'][2], head['bbox'][2]) <= max(body['bbox'][0], head['bbox'][0])):
                break
            members.append(body)
            j += 1
        if len(members) < 2:
            result.append(head)
            i += 1
            continue
        box = fitz.Rect(head['bbox'])
        for u in members[1:]:
            box |= fitz.Rect(u['bbox'])
        # Heading extraction calls single lines "left" even when their right
        # edge is shared with the right-aligned explanation.
        alignment = members[1].get('alignment', 'left')
        head_runs = copy.deepcopy(head['target_runs'])
        body_runs = []
        for member in members[1:]:
            if body_runs:
                body_runs[-1]['text'] += ' '
            body_runs.extend(copy.deepcopy(member['target_runs']))
        head_runs[-1]['text'] += ' '
        combined = {**head, 'bbox': list(box), 'chart_callout': True,
                    'composed_ids': [u['id'] for u in members],
                    'source': ' '.join(u['source'] for u in members),
                    'target': ''.join(r['text'] for r in head_runs + body_runs),
                    'target_runs': head_runs + body_runs,
                    'paragraphs': [{'target_runs': head_runs, 'indent': 0},
                                   {'target_runs': body_runs, 'indent': 0}],
                    'spans': [s for u in members for s in u['spans']],
                    'kind': 'paragraph', 'alignment': alignment,
                    'first_line_indent': 0, 'line_height': 1.12,
                    'scale_group': f'p{page.number+1:04d}_chart_callouts',
                    'growth_anchor': 'bottom' if box.y0 > page.rect.height/2 else 'top'}
        result.append(combined)
        i = j
    return result


def callout_candidates(page, unit, units, guards, vector_mask):
    """I7 uses the shared I8 free-space guards and aligned candidate generator."""
    from astro_translate.caption_growth import candidate_boxes
    return candidate_boxes(page, unit, units, guards, vector_mask)
