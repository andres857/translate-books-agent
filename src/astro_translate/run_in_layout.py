"""I4: reflow validated saved runs, without changing extraction or translations.

Installed only by this book's composition context, AFTER scoped repairs and
composition overrides have checked their original ids / source hashes.
"""
from __future__ import annotations

import copy
from collections import defaultdict

import pymupdf as fitz
from astro_translate import fidelity
from astro_translate.layout import foreground_images


def _source_baselines(page, spans):
    """Read original origins without adding fields to hash-bound extraction."""
    raw = [s for b in page.get_text('dict')['blocks']
           for line in b.get('lines', []) for s in line['spans']]
    result = []
    for span in spans:
        matches = [s for s in raw if s['text'] == span['source']
                   and s['font'] == span['font']
                   and abs(s['bbox'][0]-span['bbox'][0]) < .01
                   and abs(s['bbox'][2]-span['bbox'][2]) < .01
                   and fitz.Rect(s['bbox']).intersects(fitz.Rect(span['bbox']))]
        result.append(matches[0]['origin'][1] if len(matches) == 1 else None)
    return result


def _run_in(unit, page=None):
    spans = unit['spans']
    if len(spans) < 2 or unit['size'] > 11:
        return False
    a, b = spans[:2]
    baselines = _source_baselines(page, [a, b]) if page is not None else None
    same_line = (all(y is not None for y in baselines) and abs(baselines[0]-baselines[1]) < .1
                 if baselines is not None else abs(a['bbox'][1]-b['bbox'][1]) < .1)
    return (a['style']['bold'] and not b['style']['bold']
            and a['size'] == b['size']
            and same_line
            and abs(a['bbox'][2] - b['bbox'][0]) < 1)


def separate_standalone_heading(page, unit):
    """Retain source heading/body boundaries, including mixed-weight titles.

    Only split at an existing, unambiguous style-run boundary. Neither source
    ids/spans nor target wording change. Same-baseline introductions stay inline.
    """
    if unit.get('paragraphs') or len(unit['spans']) < 2:
        return unit
    spans = unit['spans']
    ys = _source_baselines(page, spans)
    if any(y is None for y in ys):
        return unit
    # A heading can wrap. Find the first line with a different font/size;
    # changes of weight within a title do not turn its conjunction into body.
    first = spans[0]
    for index in range(1, len(spans)):
        head, body = spans[:index], spans[index:]
        if ys[index]-max(ys[:index]) < min(first['size'], body[0]['size'])*.5:
            continue
        text = ' '.join(s['source'].strip() for s in head)
        size_heading = min(s['size'] for s in head) > max(s['size'] for s in body) + .5
        weight_heading = (first['source'].lstrip()[:1].isalpha()
                          and all(s['style']['weight'] > body[0]['style']['weight'] for s in head))
        emphasized_heading = (any(s['style']['bold'] for s in head)
                              and not body[0]['style']['bold']
                              and (text.isupper() or all(s['style']['bold'] for s in head)))
        if not (size_heading or weight_heading or emphasized_heading):
            continue
        if (body[0]['font'], body[0]['size']) == (first['font'], first['size']):
            continue
        head_styles = []
        for span in head:
            if not head_styles or head_styles[-1] != span['style']:
                head_styles.append(span['style'])
        runs = unit['target_runs']
        cut = len(head_styles)
        if [r['style'] for r in runs[:cut]] != head_styles or cut >= len(runs):
            continue
        if runs[cut]['style'] != body[0]['style']:
            continue
        unit['paragraphs'] = [dict(target_runs=runs[:cut], indent=unit.get('first_line_indent', 0)),
                              dict(target_runs=runs[cut:], indent=max(0, body[0]['bbox'][0]-unit['bbox'][0]))]
        unit['source_paragraph_boundary'] = dict(heading_baseline=ys[index-1], body_baseline=ys[index])
        unit.pop('run_in_heading', None)
        break
    return unit


def _join(a, b):
    """Only add separating whitespace; retain every character, run and span."""
    a.setdefault('composed_ids', [a['id']]).extend(b.get('composed_ids', [b['id']]))
    a['source'] += ' ' + b['source']
    a['target'] += ' ' + b['target']
    a['target_runs'][-1]['text'] += ' '
    a['target_runs'].extend(b['target_runs'])
    a['spans'].extend(b['spans'])
    rebuilt = fidelity._make_unit(a['spans'], a['source_block'], False, [])
    for key in ('bbox', 'style_runs', 'line_height'):
        a[key] = rebuilt[key]
    a['kind'] = 'paragraph'
    a.pop('paragraphs', None)


def _separators(page):
    # Thin horizontal strokes include dotted separators. Rect.intersects alone
    # would miss these zero-height paths. Account for the actual stroke width.
    result = []
    for drawing in page.get_drawings():
        if drawing['type'] not in ('s', 'fs') or not drawing.get('stroke_opacity', 1):
            continue
        for item in drawing['items']:
            if item[0] != 'l':
                continue
            a, b = item[1:]
            if abs(a.y-b.y) < .1 and abs(a.x-b.x) > 40:
                half = max(.25, drawing.get('width', 0)/2)
                result.append(fitz.Rect(min(a.x,b.x), a.y-half, max(a.x,b.x), a.y+half))
    return result


def finalize_paragraphs(page, units, *, enabled=None, center_small=None):
    """Narrow pilot scope; legacy book policies and extraction stay untouched."""
    units = [separate_standalone_heading(page, copy.deepcopy(u)) for u in units]
    from .profiles import profile_for_page
    policy=profile_for_page(page).page_policy(page)
    if enabled is None:enabled=policy.get("run_in", True)
    if center_small is None:center_small=policy.get("center_small",False)
    if not enabled:
        return units
    units = copy.deepcopy(units)
    separators = _separators(page)
    result = []
    for unit in units:
        prev = result[-1] if result else None
        run_in = _run_in(unit, page) and not unit.get('paragraphs')
        if prev:
            a, b = fitz.Rect(prev['bbox']), fitz.Rect(unit['bbox'])
            contiguous = 0 <= b.y0-a.y1 <= min(4, unit['size']*.5)
            same_body = prev['spans'][-1]['style'] == unit['spans'][0]['style']
            same_column = abs(a.x0-b.x0) < .5
            # Centered captions are separate PDF blocks despite being one
            # paragraph. Size/weight, centers and leading distinguish headings.
            centered = (unit['size'] <= 9 and same_body
                        and unit['style']['weight'] == 300
                        and abs((a.x0+a.x1-b.x0-b.x1)/2) < 3
                        and not prev['source'].rstrip().endswith(('.', '!', '?')))
            bridge = fitz.Rect(min(a.x0,b.x0), a.y1, max(a.x1,b.x1), b.y0)
            divided = any(bridge.intersects(r) for r in separators)
            # Original line breaks kept by fidelity.split_line_items are hard.
            item = bool(unit.get('line_item') or prev.get('line_item'))
            if contiguous and same_body and not run_in and not divided and not item and (
                    prev.get('run_in_heading') and same_column or centered):
                _join(prev, unit)
                if centered:
                    prev.update(alignment='center', first_line_indent=0, centered_caption=True)
                continue
        if run_in:
            unit.update(run_in_heading=True, kind='paragraph', first_line_indent=0, alignment='left')
            unit.pop('paragraphs', None)
        result.append(unit)

    columns = defaultdict(list)
    for unit in result:
        if unit.get('run_in_heading'):
            # Cluster source left edges (not page halves or global font sizes).
            key = next((k for k in columns if abs(k-unit['bbox'][0]) < .5), unit['bbox'][0])
            columns[key].append(unit)
        if unit.get('centered_caption') or (center_small and unit['size'] <= 8.5):
            unit.update(alignment='center', first_line_indent=0)
            # Freeze caption centers: no sideways migration into siblings.
            unit['column_bounds'] = [unit['bbox'][0], unit['bbox'][2]]
    for x, members in columns.items():
        right = max(u['bbox'][2] for u in members)
        nearby = [r for r in separators if abs(r.x0-x) < 2]
        right = max([right] + [r.x1 for r in nearby])
        for u in members:
            box = fitz.Rect(u['bbox'])
            # The last entry can use one extra original line of local whitespace;
            # never consume the large decorative region below the whole column.
            bottom = min(page.rect.height-2, box.y1 + u['size']*u['line_height'])
            limits = [r.y0-.5 for r in nearby if r.y0 >= box.y1]
            limits += [v['bbox'][1]-1 for v in result if v is not u
                       and v['bbox'][1] >= box.y1
                       and v['bbox'][0] < right and v['bbox'][2] > x]
            if limits:
                bottom = min(bottom, min(limits))
            # Shared gap above: the entry may rise into half the white space
            # between the rule (or ink) above it and its own first line.
            above = [r.y1 for r in nearby if r.y1 <= box.y0]
            above += [v['bbox'][3] for v in result if v is not u
                      and v['bbox'][3] <= box.y0
                      and v['bbox'][0] < right and v['bbox'][2] > x]
            top = box.y0 - max(0, box.y0-max(above))/2 if above else box.y0
            u.update(column_bounds=[x, right], layout_bottom=bottom, layout_top=top,
                     scale_group=f'p{page.number+1:04d}_run_in_{x:.2f}',
                     scale_policy='Uniform column; minimum 0.75; review below 0.80')
    return result


def paragraph_candidates(page, unit, units, guards):
    """Fixed full column width; grow down only inside this entry's safe slot.

The caller still runs _test_fit's real vector-ink and raster-overlay guards.
No shifted-candidate fallback is allowed to escape the column or separator.
"""
    original = fitz.Rect(unit['bbox'])
    base = fitz.Rect(unit['column_bounds'][0], original.y0,
                     unit['column_bounds'][1], original.y1)
    others = [fidelity._ink_bbox(page, u) for u in units if u['id'] != unit['id']]
    others += list(guards) + foreground_images(page) + _separators(page)
    bottom = unit['layout_bottom']
    heights = sorted({base.y1, bottom} | {min(bottom, base.y1+n/2) for n in range(1, int(max(0,bottom-base.y1)*2)+1)})
    # Original top first; then rise 1 pt at a time into the shared gap above,
    # always with the full slot below (minimum move for the scale reached).
    top = min(base.y0, unit.get('layout_top', base.y0))
    shapes = [(base.y0, y1) for y1 in heights]
    shapes += [(y0, bottom) for y0 in [base.y0-n for n in range(1, int(base.y0-top)+1)]+[top] if y0 < base.y0]
    result = []
    for y0, y1 in shapes:
        if y1 > bottom or y1 <= y0:
            continue
        box = fitz.Rect(base.x0, y0, base.x1, y1)
        if box in result or not page.rect.contains(box) or any(box.intersects(r) for r in others):
            continue
        if box != original and not fidelity._background_clear_cached(page, box):
            continue
        result.append(box)
    return result
