"""Offline structural survey of a source PDF before any translation.

No inference and no writes besides ``work/structure/survey.json``. Every
detector reuses the compositor's own pattern code on *identity* targets (target
= source), so the survey sees the same columns, TOC rows, field rows, callouts
and display quotes that composition will see. Where the compositor has no
detector, a simple local heuristic is used and marked ``heuristic: true``.

Archetypes are exact groupings by a feature signature: the ordered tuple of
``TAG_ORDER`` tags present on the page (``scanned`` alone for route C pages).
Archetype ids follow first appearance in page order. Representatives are the
first page of the group plus, for larger groups, the page with most editable
units (lowest page number on ties): one or two pilot candidates per archetype.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import time

import pymupdf as fitz

from .core import Blocked, SYMBOL_FONT, atomic_json, read_extras, sha256

SURVEY_VERSION = 2
ACCENT_PROBE = 'áéíóúñÁÉÍÓÚÑ¡¿'
# Structural tags define the archetype signature; risk tags are per-page
# modifiers reported inside each archetype (they would otherwise fragment
# structurally identical pages into many singleton groups).
TAG_ORDER = ('scanned', 'blank', 'toc', 'index', 'glossary', 'table', 'field_rows', 'callouts', 'curved_text', 'rotated_text', 'columns',
             'run_in_list', 'display_quote', 'display_title')
RISK_TAGS = ('colored_panel', 'low_contrast', 'raster_text', 'font_fallback')
CLASS_HINTS = (('slab', ('serifa', 'rockwell', 'museoslab', 'robotoslab', 'clarendon')),
               ('sans', ('avenir', 'futura', 'frutiger', 'helvetica', 'arial', 'gothic', 'sans', 'dinnext',
                         'gill', 'myriad', 'univers', 'nimbussans')))
DISPLAY_MIN_SIZE = 20
LOW_CONTRAST = 4.5
DETECTORS = {
    'route_type': {'source': 'core.classify_page (current triage classifier); manifest values kept as manifest_type/manifest_route', 'heuristic': False},
    'fonts': {'source': 'fidelity.span_style + fonts.fallback_class + core.SYMBOL_FONT', 'heuristic': False},
    'font_name_hint': {'source': 'family-name keywords (CLASS_HINTS); flags classification conflicts only', 'heuristic': True},
    'font_display_class': {'source': f'family used only at >= {DISPLAY_MIN_SIZE} pt in the surveyed pages', 'heuristic': True},
    'font_coverage': {'source': 'Font.has_glyph(fallback=False) + fonts.glyph_ink_coverage', 'heuristic': False},
    'columns': {'source': 'column_flow.prepare_stories', 'heuristic': False},
    'rotated': {'source': 'native_rotated.extract_straight_labels/deferred_inventory + line direction', 'heuristic': False},
    'field_rows': {'source': 'structured_layout.prepare_saved_field_rows (row_composition)', 'heuristic': False},
    'callouts': {'source': 'chart_callouts.finalize_callouts (incl. mark_figure_legends: italic <=10 pt text reached by a leader line)', 'heuristic': False},
    'callout_leader_lines': {'source': 'thin open stroke (1-2 line/curve segments) ending within 12 pt of the callout box', 'heuristic': True},
    'display_quotes': {'source': 'editorial_rules.quote_retranslation_units + survey_detectors.single_unit_quotes (one display unit framed by quote marks; heuristic)', 'heuristic': False},
    'back_matter': {'source': 'book_index.page_role: alphabetical index (letter headings + locator lines) or run-in glossary in alphabetical order', 'heuristic': False},
    'tables': {'source': 'survey_detectors.detect_tables: >=3 key/value rows with the same labels and protected values, or rows of captioned symbol glyphs', 'heuristic': True},
    'display_titles': {'source': 'display_titles.title_retranslation_units', 'heuristic': False},
    'toc': {'source': 'contents_layout.prepare_contents/prepare_toc', 'heuristic': False},
    'colored_blocks': {'source': 'vector fill under the unit, else raster_obstacles background colour; WCAG ratio', 'heuristic': True},
    'raster': {'source': 'page.get_image_info + raster_obstacles.build_region_obstacles', 'heuristic': False},
    'raster_text': {'source': 'survey_detectors.probe_raster_text (local PaddleOCR) on pages without native text; None when OCR is unavailable or disabled', 'heuristic': False},
}


def _normalize(name):
    return re.sub(r'[^a-z0-9]', '', re.sub(r'^[A-Z]{6}\+', '', name).lower())


def _rgb(value):
    return [(value >> 16) & 255, (value >> 8) & 255, value & 255]


def _luminance(rgb):
    def channel(c):
        c = c/255
        return c/12.92 if c <= .03928 else ((c+.055)/1.055)**2.4
    r, g, b = (channel(c) for c in rgb)
    return .2126*r+.7152*g+.0722*b


def contrast_ratio(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return round((la+.05)/(lb+.05), 2)


def _coloured(rgb):
    return min(rgb) < 235 or max(rgb)-min(rgb) > 12


def _union(rects):
    rects = [fitz.Rect(r) for r in rects]
    box = rects[0]
    for r in rects[1:]:
        box |= r
    return [round(v, 2) for v in box]


class _Coverage:
    """Per-xref embedded font coverage of the Spanish probe characters."""

    def __init__(self, doc):
        self.doc = doc
        self.cache = {}
        self.fonts = {}
        self.folder = tempfile.TemporaryDirectory(prefix='astro-survey-fonts-')

    def close(self):
        self.folder.cleanup()

    def record(self, xref, basefont):
        if xref in self.cache:
            return self.cache[xref]
        from .fonts import glyph_ink_coverage
        result = {'xref': xref, 'embedded': False, 'missing': None, 'empty_outline': []}
        try:
            _, ext, _, data = self.doc.extract_font(xref)
        except Exception as exc:  # unusual font objects remain explicit
            result['error'] = f'{type(exc).__name__}: {exc}'
            data, ext = b'', ''
        font = None
        if data:
            result['embedded'] = True
            try:
                font = fitz.Font(fontbuffer=data)
            except Exception as exc:
                result['error'] = f'{type(exc).__name__}: {exc}'
        else:
            try:
                font = fitz.Font(fontname=re.sub(r'^[A-Z]{6}\+', '', basefont))
                result['source'] = 'builtin'
            except Exception:
                font = None
        if font is not None:
            result['missing'] = [c for c in ACCENT_PROBE if not font.has_glyph(ord(c), fallback=False)]
            present = ''.join(c for c in ACCENT_PROBE if c not in result['missing'])
            if data and present:
                import logging
                path = Path(self.folder.name)/f'{xref}.{ext or "bin"}'
                path.write_bytes(data)
                quiet = logging.getLogger('fontTools')
                level = quiet.level
                quiet.setLevel(logging.ERROR)  # subset metadata warnings are not findings
                try:
                    result['empty_outline'] = [c for c in present if not glyph_ink_coverage({'path': str(path)}, c)]
                finally:
                    quiet.setLevel(level)
        else:
            result['missing'] = list(ACCENT_PROBE)
        self.cache[xref] = result
        self.fonts[xref] = font
        return result

    def lacks(self, xref, chars):
        font = self.fonts.get(xref)
        if font is None:
            return sorted(chars)
        return sorted(c for c in chars if not font.has_glyph(ord(c), fallback=False))


def _page_fonts(page, coverage, families):
    from .fidelity import span_style
    from .fonts import fallback_class
    records = {}
    for xref, _, _, basefont, *_ in page.get_fonts(full=True):
        records.setdefault(_normalize(basefont), []).append(coverage.record(xref, basefont))
    used = {}
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                if not span['text'].strip():
                    continue
                style = span_style(span)
                key = (span['font'], style['weight'], style['italic'])
                row = used.setdefault(key, {'name': re.sub(r'^[A-Z]{6}\+', '', span['font']), 'sizes': set(),
                                            'weight': style['weight'], 'italic': style['italic'], 'chars': 0,
                                            'used': set()})
                row['used'].update(c for c in span['text'] if not c.isspace())
                row['sizes'].add(round(span['size'], 2))
                row['chars'] += len(span['text'].strip())
                if SYMBOL_FONT.search(span['font']):
                    row['class'] = 'symbol'
                else:
                    row['class'] = fallback_class(style, families)
                row['fallback_class'] = None if row['class'] == 'symbol' else fallback_class(style, families)
    fonts = []
    for row in used.values():
        found = records.get(_normalize(row['name']), [])
        missing = sorted({c for r in found for c in (r['missing'] or [])}, key=ACCENT_PROBE.index)
        empty = sorted({c for r in found for c in r['empty_outline']}, key=ACCENT_PROBE.index)
        used_chars = row.pop('used')
        # Source glyphs the extracted program cannot map: the cmap/encoding is
        # not usable for has_glyph, so reuse would fail the same way.
        unmapped = sorted({c for r in found for c in coverage.lacks(r['xref'], used_chars)}) if found else []
        hint = next((cls for cls, keys in CLASS_HINTS if any(k in _normalize(row['name']) for k in keys)), None)
        fonts.append({**row, 'sizes': sorted(row['sizes']), 'name_hint': hint,
                      'class_conflict': bool(hint and row['class'] != 'symbol' and hint != row['class']),
                      'coverage': {'matched_xrefs': [r['xref'] for r in found],
                                   'embedded': any(r['embedded'] for r in found) if found else None,
                                   'missing': missing if found else list(ACCENT_PROBE),
                                   'empty_outline': empty,
                                   'unmapped_source_chars': unmapped[:20],
                                   'cmap_usable': bool(found) and len(unmapped) <= max(1, len(used_chars)//10),
                                   'unmatched': not found}})
    return sorted(fonts, key=lambda f: (f['name'], f['weight'], f['italic']))


def _identity(units):
    units = deepcopy(units)
    for u in units:
        u['target'] = u['source']
        u['target_runs'] = [{'text': u['source'][r['start']:r['end']], 'style': r['style']} for r in u['style_runs']]
    return units


def _prepared(page, units, profile):
    """The compositor's pattern pipeline with identity targets (see reinsert_fidelity)."""
    from .profiles import BookProfile
    from .editorial_rules import protected_editorial_units, partition_display_units, prepare_display_lines
    from .column_flow import prepare_stories
    from .composition_patterns import book_composition
    errors = []
    detector = profile if profile.data.get('composition') == 'patterns' else BookProfile(None, {'composition': 'patterns'})
    protected = {x['unit_id'] for x in protected_editorial_units(page, units)}
    editable = [u for u in units if u['id'] not in protected]
    try:
        composed = detector.compose(page, editable)
    except (Blocked, ValueError, KeyError, IndexError) as exc:
        # Book hooks may bind to saved targets; fall back to the shared pattern code.
        errors.append(f'compose: {type(exc).__name__}: {exc}')
        composed = book_composition(page, editable)
    try:
        composed = detector.finalize(page, composed)
    except (Blocked, ValueError, KeyError, IndexError) as exc:
        errors.append(f'finalize: {type(exc).__name__}: {exc}')
    try:
        composed = prepare_stories(page, prepare_display_lines(page, partition_display_units(page, composed)))
    except (Blocked, ValueError, KeyError, IndexError) as exc:
        errors.append(f'stories: {type(exc).__name__}: {exc}')
    return composed, errors


def _leader_lines(drawings, box, page_rect):
    """Thin open strokes (1-2 line/curve segments) with an endpoint near the callout."""
    zone = fitz.Rect(box)+(-12, -12, 12, 12)
    count = 0
    for d in drawings:
        items = d['items']
        if d.get('fill') is not None or d['type'] != 's' or not 1 <= len(items) <= 2:
            continue
        if any(i[0] not in ('l', 'c') for i in items) or not page_rect.contains(fitz.Rect(d['rect'])):
            continue
        start, end = items[0][1], items[-1][-1]
        if abs(start.x-end.x)+abs(start.y-end.y) <= 8:
            continue
        if zone.contains(start) or zone.contains(end):
            count += 1
    return count


def _background(page, unit, fills):
    from .raster_obstacles import build_region_obstacles
    box = fitz.Rect(unit['bbox'])
    area = max(box.get_area(), 1e-6)
    vector = [d for d in fills if (fitz.Rect(d['rect']) & box).get_area() >= .5*area]
    if vector:
        colour = [round(c*255) for c in vector[-1]['fill'][:3]]
        return colour, 'vector_fill', None
    try:
        obstacles = build_region_obstacles(page, box, dpi=50)
    except Exception:
        return None, 'unknown', None
    if obstacles.covered_pixels and obstacles.background_rgb:
        return list(obstacles.background_rgb), 'raster', obstacles
    return [255, 255, 255], 'page', obstacles


def survey_page(doc, number, row, profile, coverage, extras=(), ocr=True, triage_config=None):
    from .core import classify_page
    from .fidelity import extract_fidelity_units
    from .survey_detectors import detect_tables, single_unit_quotes, probe_raster_text
    page = doc[number-1]
    text = page.get_text()
    images = page.get_image_info(xrefs=True)
    page_area = page.rect.get_area()
    triaged = classify_page(page, triage_config)
    route, kind = triaged['route'], triaged['type']
    families = profile.data.get('fonts', {}).get('classification')
    result = {'page': number, 'route': route, 'type': kind, 'manifest': row is not None,
              'visible_native_letters': triaged['visible_native_letters'],
              'native_chars': len(text.strip()), 'fonts': _page_fonts(page, coverage, families)}
    if row is not None:
        result.update(manifest_type=row.get('type'), manifest_route=row.get('route'),
                      retriage_pending=(row.get('type'), row.get('route')) != (kind, route))
    known = {x.get('xref'): x.get('has_text') for x in (row or {}).get('images', [])}
    probe = None
    if images and not text.strip():
        # Text-less raster pages: local OCR decides; unknown stays None.
        probe = probe_raster_text(page) if ocr else {'contains_text': None, 'method': 'disabled', 'lines': []}
        result['raster_text_probe'] = probe
    raster_images = [{'bbox': [round(v, 2) for v in i['bbox']], 'xref': i.get('xref'),
                      'area_ratio': round((fitz.Rect(i['bbox']) & page.rect).get_area()/page_area, 3),
                      'role': 'page_background' if (fitz.Rect(i['bbox']) & page.rect).get_area() >= .98*page_area else 'foreground',
                      'contains_text': probe['contains_text'] if probe else known.get(i.get('xref'))}
                     for i in images]
    if route == 'C' or not text.strip():
        tag = 'scanned' if route == 'C' else 'blank'
        risks = ['raster_text'] if probe and probe['contains_text'] else []
        result.update(raster={'images': raster_images, 'text_over_artwork': [], 'foreground_text_overlap': []},
                      tags=[tag]+risks, signature=tag, risk_tags=risks, units=0)
        return result
    units = extract_fidelity_units(page, extras)
    prepared, errors = _prepared(page, _identity(units), profile)
    from .editorial_rules import quote_retranslation_units
    from .display_titles import title_retranslation_units
    from .native_rotated import deferred_inventory, extract_straight_labels
    drawings = page.get_drawings()
    stories = [{'frames': len(u['story_frames']), 'paragraphs': len(u.get('paragraphs', [])),
                'bbox': [round(v, 2) for v in u['bbox']], 'unit_ids': u.get('composed_ids', [])}
               for u in prepared if u.get('story_frames')]
    lines = [(tuple(round(v, 4) for v in line['dir']), span) for b in page.get_text('dict')['blocks']
             for line in b.get('lines', []) for span in line['spans']
             if span['text'].strip() and not SYMBOL_FONT.search(span['font'])]
    vertical = sum(d in ((0., -1.), (0., 1.)) for d, _ in lines)
    curved = [r for r in deferred_inventory(page, extras) if r['reason'] == 'curved_or_fragmented_native_text']
    straight = extract_straight_labels(page, extras)
    rows = defaultdict(list)
    for u in prepared:
        if u.get('row_composition'):
            rows[u.get('row_parent_id') or u['scale_group']].append(u)
    callouts = [{'bbox': [round(v, 2) for v in u['bbox']], 'members': len(u.get('composed_ids', [])),
                 'leader_lines': _leader_lines(drawings, u['bbox'], page.rect), 'leader_heuristic': True,
                 'figure_legend': bool(u.get('figure_legend'))}
                for u in prepared if u.get('chart_callout') or u.get('figure_legend')]
    shared_quotes = quote_retranslation_units(page, units)
    quotes = [{'bbox': [round(v, 2) for v in q['bbox']], 'unit_ids': q['composed_ids'],
               'quoted': bool(re.match(r'^[\s“"…]', q['source']))} for q in shared_quotes]
    quotes += single_unit_quotes(page, units, {i for q in shared_quotes for i in q['composed_ids']})
    tables = detect_tables(page)
    from .book_index import page_role
    role = page_role(page)
    titles = [{'bbox': [round(v, 2) for v in t['bbox']], 'unit_ids': t['composed_ids']}
              for t in title_retranslation_units(page, units)]
    toc_rows = sum(bool(u.get('toc_row')) for u in prepared)
    contents_rows = sum(bool(u.get('contents_column')) and not u.get('toc_row') for u in prepared)
    fills = [d for d in drawings if d.get('fill') is not None and d['type'] in ('f', 'fs')]
    coloured, over_artwork, overlap = [], [], []
    foreground = [fitz.Rect(i['bbox']) for i in raster_images if i['role'] == 'foreground']
    for u in units:
        colour, origin, obstacles = _background(page, u, fills)
        if obstacles is not None and obstacles.covered_pixels and (obstacles.ambiguous or obstacles.background_fraction < .98):
            over_artwork.append({'unit_id': u['id'], 'bbox': [round(v, 2) for v in u['bbox']],
                                 'artwork_fraction': round(1-obstacles.background_fraction, 3),
                                 'ambiguous': obstacles.ambiguous})
        if any(fitz.Rect(u['bbox']).intersects(r) for r in foreground):
            overlap.append(u['id'])
        if colour is None or not _coloured(colour):
            continue
        text_rgb = _rgb(u['color'])
        ratio = contrast_ratio(text_rgb, colour)
        large = u['size'] >= 18 or (u['size'] >= 14 and u['style']['weight'] >= 600)
        coloured.append({'unit_id': u['id'], 'bbox': [round(v, 2) for v in u['bbox']], 'chars': len(u['source']),
                         'background_rgb': colour, 'background_source': origin, 'text_rgb': text_rgb,
                         'contrast_ratio': ratio, 'low_contrast': ratio < (3 if large else LOW_CONTRAST),
                         'heuristic': True})
    result.update(units=len(units), prep_errors=errors,
                  columns={'count': max([s['frames'] for s in stories] or [0]), 'stories': stories},
                  rotated={'vertical': vertical, 'straight': len(straight),
                           'curved_fragments': sum(len(r['source_spans']) for r in curved),
                           'curved_regions': [[round(v, 2) for v in r['bbox']] for r in curved],
                           'other_directions': sum(d not in ((1., 0.), (0., -1.), (0., 1.)) for d, _ in lines)},
                  field_rows={'tables': len(rows), 'rows': sum(map(len, rows.values())),
                              'bboxes': [_union([u['bbox'] for u in rs]) for rs in rows.values()]},
                  callouts=callouts, display_quotes=quotes, display_titles=titles, tables=tables, back_matter=role,
                  toc={'rows': toc_rows, 'contents_rows': contents_rows},
                  colored_blocks=coloured,
                  raster={'images': raster_images, 'text_over_artwork': over_artwork,
                          'foreground_text_overlap': overlap},
                  composition_flags=dict(sorted(Counter(k for u in prepared for k in (
                      'run_in_heading', 'caption_growth', 'native_ordinal', 'protected_label', 'drop_cap',
                      'display_ink_bbox') if u.get(k)).items())))
    fallback = [f['name'] for f in result['fonts'] if f['class'] != 'symbol'
                and (f['coverage']['missing'] or f['coverage']['empty_outline'] or not f['coverage']['cmap_usable'])]
    result['font_fallback'] = fallback
    present = {
        'toc': toc_rows >= 4 or contents_rows >= 4,
        'table': bool(tables),
        'index': role == 'index',
        'glossary': role == 'glossary',
        'field_rows': bool(rows),
        'callouts': bool(callouts),
        'curved_text': bool(curved),
        'rotated_text': bool(vertical or straight),
        'columns': result['columns']['count'] >= 2,
        'display_quote': bool(quotes),
        'display_title': bool(titles),
        'run_in_list': result['composition_flags'].get('run_in_heading', 0) >= 3,
        'colored_panel': any(b['chars'] >= 20 for b in coloured),
        'low_contrast': any(b['low_contrast'] for b in coloured),
        'raster_text': bool(overlap or any(a['artwork_fraction'] >= .05 or a['ambiguous'] for a in over_artwork)),
        'font_fallback': bool(fallback),
    }
    structural = [t for t in TAG_ORDER if present.get(t)]
    result['risk_tags'] = [t for t in RISK_TAGS if present.get(t)]
    result['tags'] = structural+result['risk_tags']
    result['signature'] = '+'.join(structural) or 'plain'
    return result


def _book_fonts(pages):
    fonts = {}
    for page in pages:
        for f in page.get('fonts', []):
            row = fonts.setdefault(f['name'], {'name': f['name'], 'classes': set(), 'pages': set(), 'sizes': set(),
                                               'weights': set(), 'italic': set(), 'missing': set(),
                                               'empty_outline': set(), 'embedded': set(), 'chars': 0,
                                               'cmap_usable': True, 'fallback_class': f.get('fallback_class'),
                                               'name_hint': f.get('name_hint'), 'class_conflict': False})
            row['classes'].add(f['class']); row['pages'].add(page['page']); row['sizes'].update(f['sizes'])
            row['weights'].add(f['weight']); row['italic'].add(f['italic']); row['chars'] += f['chars']
            row['missing'].update(f['coverage']['missing']); row['empty_outline'].update(f['coverage']['empty_outline'])
            row['embedded'].add(f['coverage']['embedded']); row['cmap_usable'] &= bool(f['coverage']['cmap_usable'])
            row['class_conflict'] |= bool(f.get('class_conflict'))
    result = []
    for name, row in sorted(fonts.items()):
        cls = 'symbol' if 'symbol' in row['classes'] else sorted(row['classes'])[0]
        display = cls != 'symbol' and (min(row['sizes']) >= DISPLAY_MIN_SIZE or 'lilita' in _normalize(name))
        result.append({'name': name, 'class': 'display' if display else cls, 'fallback_class': row['fallback_class'],
                       'heuristic': display, 'pages': sorted(row['pages']), 'sizes': sorted(row['sizes']),
                       'weights': sorted(row['weights']), 'italic': sorted(row['italic']), 'chars': row['chars'],
                       'embedded': sorted(x for x in row['embedded'] if x is not None) or [None],
                       'missing': sorted(row['missing'], key=ACCENT_PROBE.index),
                       'empty_outline': sorted(row['empty_outline'], key=ACCENT_PROBE.index),
                       'cmap_usable': row['cmap_usable'], 'name_hint': row['name_hint'],
                       'class_conflict': row['class_conflict']})
    return result


def _font_risks(fonts, pages):
    risks = []
    for f in fonts:
        if f['class'] == 'symbol':
            continue  # symbol fonts are protected and never reused for new text
        reasons = []
        if f['missing']:reasons.append('missing_glyphs')
        if f['empty_outline']:reasons.append('empty_glyph_outline')
        if not f['cmap_usable']:reasons.append('cmap_unusable')
        if f['embedded'] == [False]:reasons.append('not_embedded')
        if f['class_conflict']:reasons.append('classification_suspect')
        if not reasons:
            continue
        affected = [p['page'] for p in pages for x in p.get('fonts', []) if x['name'] == f['name']
                    and (x['coverage']['missing'] or x['coverage']['empty_outline'] or not x['coverage']['cmap_usable'])]
        risks.append({'font': f['name'], 'class': f['class'], 'fallback_class': f['fallback_class'],
                      'name_hint': f['name_hint'],
                      'missing': f['missing'], 'empty_outline': f['empty_outline'], 'reasons': reasons,
                      'fallback_required': bool(set(reasons) & {'missing_glyphs', 'empty_glyph_outline', 'cmap_unusable'}),
                      'pages': sorted(set(affected)), 'chars': f['chars'],
                      'note': ('Group fallback (fonts.select_group_fonts) replaces this font for any group needing '
                               'these characters, using fallback_class'+('; the family name suggests '+str(f['name_hint'])
                               +': add it to [fonts].classification in profile.toml' if f['class_conflict'] else '')+'.')})
    return risks


def archetypes(pages):
    """Exact structural-signature groups; ids by first page; deterministic representatives.

    The second representative maximizes, among the remaining pages, (risk tags
    not covered by the first page, editable units, -page number).
    """
    groups = {}
    for page in sorted(pages, key=lambda p: p['page']):
        groups.setdefault(page['signature'], []).append(page)
    result = []
    for index, (signature, members) in enumerate(groups.items(), 1):
        numbers = [p['page'] for p in members]
        first = members[0]
        representatives = [first['page']]
        if len(members) > 1:
            seen = set(first.get('risk_tags', []))
            second = max(members[1:], key=lambda p: (len(set(p.get('risk_tags', []))-seen), p.get('units', 0), -p['page']))
            representatives.append(second['page'])
        risks = Counter(t for p in members for t in p.get('risk_tags', []))
        result.append({'id': f'A{index:02d}', 'signature': signature,
                       'tags': [t for t in TAG_ORDER if t in signature.split('+')], 'pages': numbers,
                       'page_count': len(numbers), 'representative_pages': representatives,
                       'risk_pages': {t: [p['page'] for p in members if t in p.get('risk_tags', [])]
                                      for t in RISK_TAGS if risks[t]}})
    return result


def _protected_terms(book):
    """The do-not-translate list production masks (book pipeline.toml, else repo config)."""
    import tomllib
    root = Path(__file__).resolve().parents[2]
    config = book/'work/pipeline.toml'
    if not config.is_file():
        config = root/'config/pipeline.toml'
    if not config.is_file():
        return ()
    value = tomllib.loads(config.read_text()).get('paths', {}).get('do_not_translate')
    if not value:
        return ()
    path = Path(value) if Path(value).is_absolute() else root/value
    return read_extras(path) if path.is_file() else ()


def survey_book(book, pages=None, *, write=True, ocr=True):
    from .production import read, selected_pages
    from .profiles import load_profile, BookProfile
    book = Path(book).resolve()
    started = time.monotonic()
    source = book/'source.pdf'
    manifest = read(book/'work/manifest.json', {})
    digest = sha256(source)
    rows = {r['page']: r for r in manifest.get('pages', [])} if manifest.get('source_sha256') == digest else {}
    try:
        profile = load_profile(book)
    except Blocked:
        profile = BookProfile(book)
    extras = _protected_terms(book)
    with fitz.open(source) as doc:
        coverage = _Coverage(doc)
        try:
            numbers = selected_pages(pages, len(doc))
            config = manifest.get('triage_config') if rows else None
            surveyed = [survey_page(doc, n, rows.get(n), profile, coverage, extras, ocr=ocr, triage_config=config)
                        for n in numbers]
            count = len(doc)
        finally:
            coverage.close()
    fonts = _book_fonts(surveyed)
    display = {f['name'] for f in fonts if f['class'] == 'display'}
    for page in surveyed:
        for f in page.get('fonts', []):
            if f['name'] in display:
                f.update({'class': 'display', 'heuristic': True})
    result = {'version': SURVEY_VERSION, 'book': book.name, 'source_sha256': digest, 'page_count': count,
              'surveyed_pages': numbers, 'manifest_bound': bool(rows), 'llm_calls': 0,
              'accent_probe': ACCENT_PROBE, 'detectors': DETECTORS, 'tag_order': list(TAG_ORDER),
              'archetype_method': ('Exact grouping by the ordered structural tag signature (TAG_ORDER); route C '
                                   'pages are "scanned", other pages without native text "blank", pages with no '
                                   'structural tag "plain". Risk tags (RISK_TAGS) do not split groups; they are listed '
                                   'per archetype in risk_pages. Ids follow first page. Representatives: first page, '
                                   'plus the remaining page adding most uncovered risk tags, then most editable '
                                   'units, then lowest page number.'),
              'risk_tags': list(RISK_TAGS),
              'pages': surveyed, 'fonts': fonts, 'font_risks': _font_risks(fonts, surveyed),
              'archetypes': archetypes(surveyed),
              'retriage_pending': [p['page'] for p in surveyed if p.get('retriage_pending')],
              'elapsed_s': round(time.monotonic()-started, 2)}
    if write:
        atomic_json(book/'work/structure/survey.json', result)
    return result
