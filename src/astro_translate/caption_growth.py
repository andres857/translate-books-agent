"""Opt-in, page-independent free-space layout for diagram captions.

Applied after source/translation validation, never during extraction. Existing
scale groups remain the sibling contract; caption members are split from body
members so an unrelated column cannot lower their scale. The book profile opts
in with prepare_captions; legacy profiles retain their exact layout policy.
"""
from __future__ import annotations

import copy
import math
from collections import Counter

import numpy as np
import pymupdf as fitz


def _eligible(page, unit):
    if any(unit.get(k) for k in ('run_in_heading', 'native_ordinal', 'protected_label',
                                'drop_cap', 'flow_start_x', 'chart_callout')):
        return False
    if unit.get('context') == 'table_label' or unit.get('rotate') not in (None, 0):
        return False
    if unit.get('spatial_caption'):return True
    box = fitz.Rect(unit['bbox'])
    size = unit['size']
    if size > 10 or box.y0 < page.rect.height*.07 or box.y1 > page.rect.height*.94:
        return False  # Running headers/folios are not figure captions.
    if unit.get('column_bounds') and not (unit.get('centered_caption') or unit.get('alignment') == 'center'):
        return False
    if unit.get('kind') in ('label', 'caption'):
        return True
    if unit.get('centered_caption') or (unit.get('column_bounds') and unit.get('alignment') == 'center'):
        return True
    # Narrow, small explanations beside diagrams, not body columns.
    return ((unit['style'].get('width', 100) < 100 or unit.get('context') == 'figure_label')
            and box.width < page.rect.width*.3
            and box.height <= size*8 and bool(page.get_drawings() or page.get_images()))


def infer_anchor(page, unit, units=()):
    """Use the nearest visible guide endpoint, not a page-half heuristic."""
    explicit = unit.get('growth_anchor')
    if explicit in ('top', 'bottom', 'center'):
        return explicit
    box = fitz.Rect(unit['bbox'])
    near = []
    for drawing in page.get_drawings():
        if drawing['type'] not in ('s', 'fs') or not drawing.get('stroke_opacity', 1):
            continue
        # Only open path ends are guide anchors. Bézier joins and closed
        # circles are artwork, even when a curve segment ends beside the text.
        endpoints = [point for item in drawing['items'] if item[0] in ('l','c')
                     for point in (item[1],item[-1])]
        key = lambda point: (round(point.x,2),round(point.y,2))
        counts = Counter(key(point) for point in endpoints)
        for point in endpoints:
            if counts[key(point)] != 1:
                continue
            dx = max(box.x0-point.x, 0, point.x-box.x1)
            dy = max(box.y0-point.y, 0, point.y-box.y1)
            distance = math.hypot(dx, dy)
            if distance > unit['size']*2:
                continue
            if box.y0 <= point.y <= box.y1:
                fraction = (point.y-box.y0)/box.height
                anchor = 'top' if fraction < 1/3 else 'bottom' if fraction > 2/3 else 'center'
            elif box.x0 <= point.x <= box.x1:
                anchor = 'top' if point.y < box.y0 else 'bottom'
            else:
                continue
            near.append((distance, anchor))
    if near:
        return min(near)[1]
    # A caption heading with an immediately following explanation is attached
    # to that explanation: gain height above, retaining its last baseline.
    if unit.get('kind') == 'label' and any(
            v['id'] != unit['id'] and 0 <= v['bbox'][1]-box.y1 <= unit['size']*.3
            and min(v['bbox'][2], box.x1) > max(v['bbox'][0], box.x0)
            and v['size'] <= unit['size'] for v in units):
        return 'bottom'
    return 'top'


def prepare_captions(page, units):
    """Annotate only composition copies; targets, spans and protected ink stay intact."""
    result = copy.deepcopy(units)
    eligible = {u['id'] for u in result if _eligible(page, u)}
    mixed = {u['scale_group'] for u in result if u['id'] not in eligible}
    for unit in result:
        if unit['id'] not in eligible:
            continue
        # Extraction cannot infer the alignment of a single-line heading.
        # Recover it only from an immediately adjacent, aligned explanation.
        if unit.get('kind') == 'label' and unit.get('alignment') == 'left':
            b = fitz.Rect(unit['bbox'])
            following = [v for v in result if v['id'] in eligible and v['id'] != unit['id']
                         and 0 <= v['bbox'][1]-b.y1 <= unit['size']*.5
                         and min(v['bbox'][2],b.x1) > max(v['bbox'][0],b.x0)]
            if len(following) == 1:
                other = fitz.Rect(following[0]['bbox'])
                if (following[0].get('alignment') == 'center' or abs(b.x0-other.x0) > 3) and abs((b.x0+b.x1-other.x0-other.x1)/2) <= 3:
                    unit['alignment'] = 'center'
                elif abs(b.x0-other.x0) > 3 and abs(b.x1-other.x1) <= 3:
                    unit['alignment'] = 'right'
        unit['caption_growth'] = True
        unit['growth_anchor'] = infer_anchor(page, unit, result)
        unit['scale_policy'] = 'Sibling group: grow into free space at 1.00 before shrinking; minimum 0.75'
        if unit['scale_group'] in mixed:
            unit['scale_group'] += '_captions'
    return result


def candidate_boxes(page, unit, units, guards, vector_mask):
    """Aligned growth, with whole-box native/vector/raster clearance.

    Original height (plus at most 1pt leading) is tried across widths before
    adding lines. The original container is a floor, never a width cap.
    Centered captions keep their horizontal center;
    side-guided captions keep their vertical center. No shifted-box fallback.
    Final fitting still applies the actual-ink vector and raster guards.
    """
    from .raster_obstacles import build_region_obstacles
    from .layout import foreground_images
    base = fitz.Rect(unit['bbox'])
    if unit.get('column_bounds'):
        base.x0 = min(base.x0, unit['column_bounds'][0])
        base.x1 = max(base.x1, unit['column_bounds'][1])
    # A story's union bbox also contains gutters and unused space below a
    # shorter column. Only its real flow frames are occupied.
    obstacles = [fitz.Rect(box) for u in units if u['id'] != unit['id']
                 for box in u.get('story_frames', [u['bbox']])]
    obstacles += [fitz.Rect(g) for g in guards] + foreground_images(page)
    # Include native text excluded by extraction (rotated, protected or omitted).
    own = {(s['font'], tuple(round(v, 2) for v in s['bbox'])) for s in unit['spans']}
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                key = (span['font'], tuple(round(v, 2) for v in span['bbox']))
                if span['text'].strip() and key not in own:
                    obstacles.append(fitz.Rect(span['bbox']))
    bounds = page.rect + (2, 2, -2, -2)
    alignment = unit.get('alignment', 'left')
    anchor = unit.get('growth_anchor', 'top')
    widths = (0, 1, 2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80)
    heights = (0, .5, 1, 2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80, 96, 128, 160, 224, 320)
    # Guide endpoints can touch the original container's empty corners.
    # Allow that existing geometry only; actual new ink must still clear it.
    growth_mask = vector_mask.copy()
    original_pixels = (base * fitz.Matrix(110/72,110/72)).irect
    growth_mask[max(0,original_pixels.y0):original_pixels.y1, max(0,original_pixels.x0):original_pixels.x1] = False
    result = []
    for width in widths:
        for height in heights:
            left = width if alignment == 'right' else width/2 if alignment == 'center' else 0
            up = height if anchor == 'bottom' else height/2 if anchor == 'center' else 0
            box = base + (-left, -up, width-left, height-up)
            if not bounds.contains(box) or any(box.intersects(r) for r in obstacles):
                continue
            pixels = (box * fitz.Matrix(110/72, 110/72)).irect
            if np.any(growth_mask[max(0,pixels.y0):pixels.y1, max(0,pixels.x0):pixels.x1]):
                continue
            raster = build_region_obstacles(page, box)
            if raster.ambiguous or np.any(raster.mask):
                continue
            result.append(box)
    # Do not accept an extra line at the narrow source-ink width while a
    # single/two-line setting still fits in free horizontal space. Keep the
    # original box first, then small leading allowances, then taller fallbacks.
    # The latter retain I8's height-first policy for constrained diagram gaps.
    # I7 title+body callouts keep their established paragraph wrapping policy.
    if not unit.get('chart_callout'):
        result.sort(key=lambda b: (b.height > base.height+1.001,
                                   b.width-base.width,
                                   b.height-base.height))
    return result


def insert_anchored(page, box, content, css, archive, anchor, headroom=0):
    """Fit and anchor glyph metrics, excluding Story's extra half-leading.

    A two-line caption with 9pt glyphs and 10pt baselines occupies 19pt,
    although Story requires a 20pt line box. Measure in a taller scratch page,
    then require the complete glyph bounds to fit the guarded destination.
    Neither padding nor an enlarged scratch box authorizes ink outside it.
    """
    box = fitz.Rect(box)
    with fitz.open() as probe:
        scratch = probe.new_page(width=page.rect.width, height=page.rect.height*2)
        # Headroom (fonts.insert_htmlbox_headroom): the clip moves out, the
        # content box, line breaks and baselines stay; accents are not cut.
        from .fonts import insert_htmlbox_headroom
        layout = fitz.Rect(box.x0, box.y0, box.x1, page.rect.height*2)
        spare, scale = insert_htmlbox_headroom(scratch, layout, content, css, archive, headroom)
        if spare < 0:
            return spare, scale
        spans = [s for b in scratch.get_text('dict')['blocks']
                 for line in b.get('lines', []) for s in line['spans'] if s['text'].strip()]
        if not spans:
            return -1, 1
        ink = fitz.Rect(spans[0]['bbox'])
        for span in spans[1:]:
            ink |= fitz.Rect(span['bbox'])
        if (ink.height > box.height+.001 or ink.x0 < box.x0-.001 or ink.x1 > box.x1+.001):
            return -1, 1
        dy = (box.y1-ink.y1 if anchor == 'bottom' else
              (box.y0+box.y1-ink.y0-ink.y1)/2 if anchor == 'center' else box.y0-ink.y0)
        # Equal-sized source/destination rectangles: translation, never scaling.
        destination = scratch.rect + (0, dy, 0, dy)
        page.show_pdf_page(destination, probe, 0)
        return max(0, box.height-ink.height), 1


def vector_mask(page,dpi=110):
    """Render original vector operators with actual opacity, dashes and clipping."""
    from . import fidelity
    m=fitz.mupdf
    pix=fitz.Pixmap(fitz.csRGB,(page.rect*fitz.Matrix(dpi/72,dpi/72)).irect,True);pix.clear_with()
    draw=m.fz_new_draw_device(m.FzMatrix(dpi/72,0,0,dpi/72,0,0),pix.this)
    class Device(m.FzDevice2):
        def __init__(self):
            super().__init__()
            for op in ('clip_path','clip_stroke_path','clip_text','clip_stroke_text','clip_image_mask','pop_clip','begin_group','end_group','fill_path','stroke_path'):
                getattr(self,'use_virtual_'+op)()
        def clip_path(self,ctx,*a):m.ll_fz_clip_path(draw.m_internal,*a)
        def clip_stroke_path(self,ctx,*a):m.ll_fz_clip_stroke_path(draw.m_internal,*a)
        def clip_text(self,ctx,*a):m.ll_fz_clip_text(draw.m_internal,*a)
        def clip_stroke_text(self,ctx,*a):m.ll_fz_clip_stroke_text(draw.m_internal,*a)
        def clip_image_mask(self,ctx,*a):m.ll_fz_clip_image_mask(draw.m_internal,*a)
        def pop_clip(self,ctx):m.ll_fz_pop_clip(draw.m_internal)
        def begin_group(self,ctx,*a):m.ll_fz_begin_group(draw.m_internal,*a)
        def end_group(self,ctx):m.ll_fz_end_group(draw.m_internal)
        def fill_path(self,ctx,*a):m.ll_fz_fill_path(draw.m_internal,*a)
        def stroke_path(self,ctx,*a):m.ll_fz_stroke_path(draw.m_internal,*a)
    dev=Device()
    try:m.fz_run_page_contents(page.this,dev,m.FzMatrix(),m.FzCookie())
    finally:m.fz_close_device(dev);m.fz_close_device(draw)
    rgba=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)
    mask=(rgba[:,:,3]>35)&(rgba[:,:,:3].min(axis=2)<220)
    # Omitted rotated glyphs remain conservative protected obstacles.
    for item in fidelity.omitted_rotated_text(page):
        r=(fitz.Rect(item['bbox'])*fitz.Matrix(dpi/72,dpi/72)).irect
        mask[max(0,r.y0):min(pix.height,r.y1),max(0,r.x0):min(pix.width,r.x1)]=True
    return mask

