"""Common mechanics for source-bound native ordinal policies.

Policies classify and translate editorial ordinal spans. This module preserves
native numeric glyphs, composes approved runs and verifies their source-bound QA.
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter

import pymupdf as fitz

from .core import Blocked, SYMBOL_FONT
from .secrets_ordinal_policy import (EN_ORDINAL, ES_SUFFIX, lines as _lines,
    SECRETS_POLICY, suffix)


def inventory(page, translated_units=()):
    result = []
    diagram_lines = SECRETS_POLICY.diagram_lines(page)
    for line in _lines(page):
        text = ''.join(s['text'] for s in line['spans']).strip()
        if line in diagram_lines:
            # The pair may be glued to a following label («11thFriendships»).
            text = ''.join(s['text'] for s in SECRETS_POLICY.diagram_pair(line)).strip()
        for match in EN_ORDINAL.finditer(text):
            treatment=SECRETS_POLICY.inventory_treatment(line,text,match,diagram_lines,translated_units)
            result.append({'page': page.number + 1, 'ordinal': match.group(), 'line': text,
                           'bbox': list(line['bbox']), 'native': True, 'treatment': treatment})
    return result


def raised_suffix(digit, ending):
    """Source suffix composed as a superscript (house wheel «1» + raised «st»)."""
    return ending['origin'][1] < digit['origin'][1]-.1*digit['size']


def indicator_style(digit, ending):
    """Style of «.ª» for a house-wheel pair: one superscript mechanism only.

    «ª» is itself a superior glyph. Drawn at the raised origin of the English
    suffix it was raised twice (p138/p139: ink 2.2 pt above the figures, «11. ª»).
    For a raised source suffix the marks take the numeral's baseline and size,
    which puts «ª» in the band of the original «st»; a suffix already on the
    baseline keeps its own style. ``superior_glyph`` keeps the indicator a run
    of its own: with the numeral's size its style would otherwise equal the
    numeral's, and the font of «ª» would replace the native digit's.
    """
    style = dict(_span(ending)['style'])
    if raised_suffix(digit, ending):
        style.update(size=digit['size'], superior_glyph=True)
    return style


def _span(raw):
    from .fidelity import span_style
    return {'source': raw['text'], 'bbox': list(raw['bbox']), 'font': raw['font'],
            'size': raw['size'], 'color': raw['color'], 'flags': raw['flags'],
            'rotate': 0, 'style': span_style(raw)}


def _headings(page,policy=SECRETS_POLICY):
    diagram_lines = policy.diagram_lines(page)
    for line in _lines(page):
        kind=policy.kind(line,diagram_lines)
        if not kind:
            continue
        # Metadata must compare identically before and after fit.json serialization.
        raw = json.loads(json.dumps([s for s in line['spans'] if s['text'].strip()]))
        if kind == 'house_diagram':
            # Only the digit/suffix pair is local; a label later on the same
            # PDF line (p138 «11th» + «Friendships») remains its own unit.
            raw = json.loads(json.dumps(list(policy.diagram_pair(line))))
        # Only independently styled numeric/suffix spans have this local rule.
        if len(raw) != (4 if kind == 'sign' else 2 if kind == 'house_diagram' else 3):
            continue
        digit, ending = raw[1:3] if kind == 'sign' else raw[:2]
        if (tuple(line['dir']) != (1., 0.) or not digit['text'].isdigit()
                or not re.fullmatch(r'st|nd|rd|th', ending['text'].strip() if kind == 'house_diagram' else ending['text'], re.I)
                or any(SYMBOL_FONT.search(s['font']) for s in raw)):
            raise Blocked('Ordinal heading source is ambiguous')
        yield kind, raw, digit, ending


def adapt_headings(page, units, policy=SECRETS_POLICY):
    """Keep extraction ids and saved hashes stable; replace only final headings."""
    from .fidelity import _make_unit
    result = copy.deepcopy(units)
    headings = list(_headings(page,policy))
    if diagram_layout_conflicts(headings, units):
        # A wheel is converted whole or not at all: the composition guard
        # compares metric boxes, and one label touching another unit's box
        # (p170 «11th» under the 48-pt title) cannot be placed. The labels stay
        # protected and the English-ordinal QA keeps the page in review.
        headings = [h for h in headings if h[0] != 'house_diagram']
    for kind, raw, digit, ending in headings:
        spans = [_span(s) for s in raw]
        lexical = [] if kind == 'house_diagram' else [spans[0], spans[-1]] if kind == 'sign' else [spans[-1]]
        owners = [u for u in result if any(s in u['spans'] for s in lexical)]
        if (not owners and kind != 'house_diagram') or any(s not in lexical for u in owners for s in u['spans']):
            raise Blocked('Ordinal heading shares an unrelated composition unit')
        if any(sum(s in u['spans'] for u in owners) != 1 for s in lexical):
            raise Blocked('Ordinal heading lexical coverage changed')
        unit = _make_unit(spans, owners[0]['source_block'] if owners else None, False, [])
        identifier = owners[0]['id'] if owners else f'native_house_{digit["text"]}_{len(result)}'
        style = lambda s: _span(s)['style']
        runs=policy.run_text(kind,raw,digit,ending,style)
        if kind == 'house_diagram':
            runs[1] = {**runs[1], 'style': indicator_style(digit, ending)}
        unit.update(id=identifier, composed_ids=[u['id'] for u in owners],
                    target=''.join(r['text'] for r in runs), target_runs=runs,
                    scale_group=f'p{page.number+1:04d}_native_ordinal_{identifier}',
                    kind='label', alignment='left', first_line_indent=0,
                    native_ordinal={'kind': kind, 'digit': copy.deepcopy(digit),
                                    'suffix': copy.deepcopy(ending), 'raw': copy.deepcopy(raw),
                                    'rule': policy.rule(kind)})
        position = min((result.index(u) for u in owners), default=len(result))
        result = [u for u in result if u not in owners]
        result.insert(position, unit)
    return result


PAREN_ORDINAL = re.compile(r'\((\d+)(st|nd|rd|th)\)', re.I)
# Nouns whose Spanish gender gives an unambiguous agreement to a bare ordinal.
AGREEMENT = {'house': 'f'}


def parenthesized_ordinals(units):
    """«(1st)» composed as «(1.ª)» when the unit also says «1st house» (audit batch-17, p139).

    The model receives the protected digit plus its English suffix and may answer
    with a bare «(1)». Only the Spanish ordinal mark is added, after the digit, in
    the digit's own run; nothing else in the translation changes. Without an
    agreement noun for the same number in the source, nothing is added and the
    QA (``text_quality.ordinal_suffix_issues``) keeps the unit visible.
    """
    result = []
    for unit in units:
        source = unit.get('source', '')
        matches = list(PAREN_ORDINAL.finditer(source))
        if not matches or unit.get('native_ordinal'):
            result.append(unit)
            continue
        fixed = copy.deepcopy(unit)
        for match in matches:
            number = match.group(1)
            noun = next((n for n in AGREEMENT if re.search(r'(?<!\w)'+number+r'\s*(?:st|nd|rd|th)\s+'+n+r'\b', source, re.I)), None)
            bare = '('+number+')'
            if noun is None or fixed['target'].count(bare) != 1 or len(re.findall(r'\('+number+r'(?:st|nd|rd|th)\)', source, re.I)) != 1:
                continue
            ordinal = '('+number+suffix(number, AGREEMENT[noun])+')'
            # The unit's runs and, for joined paragraphs, the same run of its paragraph.
            groups = [fixed['target_runs']] if fixed.get('target_runs') is not None else []
            groups += [p['target_runs'] for p in fixed.get('paragraphs', []) if p.get('target_runs')]
            owners = [[r for r in runs if bare in r['text']] for runs in groups]
            if any(len(found) > 1 for found in owners) or (fixed.get('paragraphs') and sum(map(len, owners[1:])) != 1):
                continue
            for found in owners:
                for run in found:
                    run['text'] = run['text'].replace(bare, ordinal, 1)
            fixed['target'] = fixed['target'].replace(bare, ordinal, 1)
            fixed.setdefault('editorial_ordinals', []).append(
                {'ordinal': match.group(1)+match.group(2), 'target': ordinal, 'agreement': noun})
        result.append(fixed)
    return result


def diagram_layout_conflicts(headings, units):
    """House-diagram labels whose source box intersects another unit's box."""
    found = []
    for kind, raw, digit, ending in headings:
        if kind != 'house_diagram':
            continue
        box = fitz.Rect(digit['bbox']) | fitz.Rect(ending['bbox'])
        found.extend({'ordinal': digit['text']+ending['text'].strip(), 'unit_id': u['id'], 'bbox': list(box)}
                     for u in units if fitz.Rect(u['bbox']).intersects(box))
    return found


def _choice(unit, run, font_map):
    return font_map[(unit['scale_group'], tuple(sorted(run['style'].items())))]


def prepare_headings(page, units, font_map):
    """Anchor signs on the original digit; houses keep the original left edge."""
    for unit in units:
        data = unit.get('native_ordinal')
        if not data:
            continue
        runs = unit['target_runs']; digit = data['digit']
        chosen = _choice(unit, runs[0 if data['kind'] == 'house_diagram' else 1], font_map)
        if chosen['source'] != 'original':
            raise Blocked('Ordinal numeric span must reuse its original font')
        widths = [_choice(unit, r, font_map)['font'].text_length(r['text'], fontsize=r['style']['size']) for r in runs]
        if data['kind'] == 'sign':
            x = digit['origin'][0]
            positions = [(x-widths[0], data['raw'][0]['origin'][1]), tuple(digit['origin']),
                         tuple(data['suffix']['origin']),
                         (data['suffix']['origin'][0]+widths[2], data['raw'][-1]['origin'][1])]
        elif data['kind'] == 'house_diagram':
            ending = data['suffix']
            positions = [tuple(digit['origin']),
                         (ending['origin'][0], digit['origin'][1]) if raised_suffix(digit, ending)
                         else tuple(ending['origin'])]
        else:
            x, y = digit['origin']
            positions = [(x, data['raw'][-1]['origin'][1]), (x+widths[0], y)]
        data['positions'] = [list(p) for p in positions]
        overlay, _ = heading_overlay(page, unit, 1, font_map)
        boxes = [fitz.Rect(s['bbox']) for b in overlay[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
        overlay.close()
        box = boxes[0]
        for rect in boxes[1:]:box |= rect
        if not page.rect.contains(box):raise Blocked('Ordinal heading exceeds page')
        data['edit_bbox'] = list(box + (-.05, -.05, .05, .05))


def heading_overlay(page, unit, factor, font_map):
    """Render source-styled runs on exact baselines; never shrink the digit."""
    if factor != 1:raise Blocked('Ordinal headings cannot scale protected numeric spans')
    doc = fitz.open(); target = doc.new_page(width=page.rect.width, height=page.rect.height)
    for index, (run, point) in enumerate(zip(unit['target_runs'], unit['native_ordinal']['positions'])):
        if index == (0 if unit['native_ordinal']['kind'] == 'house_diagram' else 1):
            # Copy the original numeric PDF span, including its glyph operator.
            numeric = _numeric_span_pdf(page, unit['native_ordinal']['digit'])
            dx = point[0]-unit['native_ordinal']['digit']['origin'][0]
            dy = point[1]-unit['native_ordinal']['digit']['origin'][1]
            target.show_pdf_page(page.rect+(dx,dy,dx,dy), numeric, 0)
            numeric.close()
            continue
        choice = _choice(unit, run, font_map); style = run['style']; color = style['color']
        name = f'Ordinal{index}'
        target.insert_font(fontname=name, fontfile=str(choice['path']))
        rgb = tuple(((color >> shift) & 255)/255 for shift in (16, 8, 0))
        target.insert_text(point, run['text'], fontname=name, fontsize=style['size'], color=rgb)
    return doc, True


def point_issues(candidate, blocks):
    """QA of a raised house suffix: «.» and «ª» belong on the numeral's baseline.

    ``ordinal_point_off_baseline``: point floating with a raised «.ª» («1·ª»,
    audit batch-17 p138). ``ordinal_indicator_raised_twice``: the superior glyph
    «ª» drawn at the raised origin of the English suffix (2.2 pt above the
    figures, p138/p139).
    """
    issues = []
    for block in blocks:
        data = block.get('native_ordinal')
        if not data or not block.get('edit_bbox'):
            continue
        digit, ending = data['digit'], data['suffix']
        if not raised_suffix(digit, ending):
            continue  # suffix on the baseline (signs): nothing raised
        clip = fitz.Rect(block['edit_bbox'])+(-.5, -.5, .5, .5)
        chars = [(c['c'], c['origin']) for b in candidate.get_text('rawdict', clip=clip)['blocks'] for l in b.get('lines', [])
                 for s in l['spans'] for c in s['chars']]
        baseline = digit['origin'][1]
        points = [o for c, o in chars if c == '.']
        if any(abs(y-baseline) > .01 for _x, y in points):
            issues.append({'code': 'ordinal_point_off_baseline', 'unit_id': block['id'], 'action': 'recompose',
                           'baseline': baseline, 'points_y': [round(y, 3) for _x, y in points]})
        letters = [o for c, o in chars if c in 'ªº']
        if any(y < baseline-.01 for _x, y in letters):
            issues.append({'code': 'ordinal_indicator_raised_twice', 'unit_id': block['id'], 'action': 'recompose',
                           'baseline': baseline, 'indicator_y': [round(y, 3) for _x, y in letters]})
    return issues


def qa_adjustments(source, candidate, blocks, immutable):
    """Validate exact local source/target/style before exempting replaced suffixes.

    Numeric values are still counted globally; sign digits retain origin, and a
    relocated house digit must keep its actual font, size and color.
    """
    from .fidelity import _normalized
    from .fidelity_audit import audit_rendered_styles
    counts = Counter(); proof = []
    expected = list(_headings(source))
    for block in blocks:
        data = block.get('native_ordinal')
        if not data:continue
        match = next((h for h in expected if h[1] == data['raw']), None)
        if match is None:raise Blocked('Ordinal QA source provenance changed')
        kind, raw, digit, ending = match
        target = SECRETS_POLICY.target(kind,digit)
        if block['target'] != target or not audit_rendered_styles(candidate, {'blocks': [block]})['ok']:
            raise Blocked('Ordinal QA target/style mismatch')
        spans = [s for b in candidate.get_text('dict', clip=fitz.Rect(block['edit_bbox']))['blocks'] for l in b.get('lines', []) for s in l['spans']]
        observed = next(({**s, 'origin': c['origin']} for b in candidate.get_text('rawdict', clip=fitz.Rect(block['edit_bbox']))['blocks'] for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if c['c'] == digit['text'][0]), None)
        if not observed or any(observed[k] != digit[k] for k in ('font', 'color')) or abs(observed['size']-digit['size']) > .001:
            raise Blocked('Ordinal numeric style changed')
        if max(abs(a-b) for a,b in zip(observed['origin'], data['positions'][0 if kind == 'house_diagram' else 1])) > .001:
            raise Blocked('Ordinal numeric placement changed')
        if kind in ('sign', 'house_diagram') and max(abs(a-b) for a,b in zip(observed['origin'], digit['origin'])) > .001:
            raise Blocked('Ordinal sign numeric position changed')
        if _normalized(''.join(s['text'] for s in spans)) != _normalized(target):
            raise Blocked('Ordinal heading text incomplete')
        if kind == 'house_diagram':
            numeral_run = {'text': digit['text'], 'style': _span(digit)['style']}
            current = [numeral_run, {'text': '.ª', 'style': indicator_style(digit, ending)}]
            # Earlier candidates (suffix style, raised origin) still validate here;
            # point_issues reports them for recomposition.
            legacy = [numeral_run, {'text': '.ª', 'style': _span(ending)['style']}]
            if block['target_runs'] not in (current, legacy):
                raise Blocked('House ordinal source styles changed')
            numeric = [s for s in spans if s['text'] == digit['text']]
            joined = [s for s in spans if s['text'] == '.ª']
            point = [s for s in spans if s['text'] == '.']; letter = [s for s in spans if s['text'] == 'ª']
            if len(numeric) != 1 or not ((len(joined) == 1 and not point and not letter)
                                         or (not joined and len(point) == 1 and len(letter) == 1)):
                raise Blocked('House ordinal native spans changed')
            if any(abs(a-b) > .001 for a,b in zip(numeric[0]['bbox'], digit['bbox'])):
                raise Blocked('House ordinal numeric geometry changed')
            on_baseline = (ending['origin'][0], digit['origin'][1])
            anchor = on_baseline if block['target_runs'] == current else tuple(ending['origin'])
            if joined:
                moved = max(abs(a-b) for a, b in zip(joined[0]['origin'], anchor)) > .001
            else:  # batch-17 form: point on the baseline, «ª» at the raised suffix origin
                moved = (abs(letter[0]['origin'][1]-ending['origin'][1]) > .001
                         or max(abs(a-b) for a, b in zip(point[0]['origin'], on_baseline)) > .001
                         or letter[0]['origin'][0] <= point[0]['origin'][0])
            if moved:
                raise Blocked('House ordinal superscript position changed')
            clip = fitz.Rect(digit['bbox'])
            if source.get_pixmap(clip=clip,dpi=220).samples != candidate.get_pixmap(clip=clip,dpi=220).samples:
                raise Blocked('House ordinal numeric ink changed')
        # Diagram digits stay in the immutable comparison as well as numeric
        # counts and the pixel proof; only their English suffix is replaced.
        for span in ((ending,) if kind == 'house_diagram' else (ending, digit)):
            rect = fitz.Rect(span['bbox']) + (-.01, -.01, .01, .01)
            for key in list(immutable):
                char, font, bbox = key
                if font == span['font'] and char in span['text'] and rect.contains(fitz.Rect(bbox)):
                    del immutable[key]
        if SECRETS_POLICY.count_masculine_marker(kind,digit):counts['º'] += 1
        proof.append({'id': block['id'], 'numeric_value_style_verified': True,
                      'numeric_origin_preserved': kind in ('sign', 'house_diagram'), 'rule': data['rule']})
    return counts, proof


def select_heading_fonts(delegate, doc, page, units, folder, **kwargs):
    """Ordinal style boundaries are independent; a missing accent cannot replace a digit."""
    ordinary = [u for u in units if not u.get('native_ordinal')]
    mapping, audit = delegate(doc, page, ordinary, folder, **kwargs)
    for unit in units:
        if not unit.get('native_ordinal'):continue
        styles = {}
        for run in unit['target_runs']:
            key = tuple(sorted(run['style'].items()))
            styles.setdefault(key, []).append(run)
        for runs in styles.values():
            part = {**unit, 'target_runs': runs, 'target': ''.join(r['text'] for r in runs)}
            chosen, evidence = delegate(doc, page, [part], folder, **kwargs)
            mapping.update(chosen)
            audit.extend({**row, 'ordinal_style_boundary': True} for row in evidence)
    return mapping, audit


def _numeric_span_pdf(page, digit):
    """Isolate a native numeric span without retyping or rasterizing its glyphs."""
    doc = fitz.open()
    doc.insert_pdf(page.parent, from_page=page.number, to_page=page.number,
                   links=False, annots=False, widgets=False)
    target = doc[0]
    whole = fitz.Rect(target.rect)
    for _, bounds, *_ in target.get_bboxlog():whole |= fitz.Rect(bounds)
    target.add_redact_annot(whole+(-2,-2,2,2), fill=False)
    target.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                            text=fitz.PDF_REDACT_TEXT_NONE)
    prior = fitz.TOOLS.set_small_glyph_heights()
    try:
        fitz.TOOLS.set_small_glyph_heights(False)
        spans = [s for b in target.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
    finally:
        fitz.TOOLS.set_small_glyph_heights(prior)
    own = [s for s in spans if s['text'] == digit['text'] and s['font'] == digit['font']
           and max(abs(a-b) for a,b in zip(s['origin'],digit['origin'])) < .001]
    if len(own) != 1:
        doc.close();raise Blocked('Cannot isolate original numeric span')
    guard = fitz.Rect(own[0]['bbox'])
    for span in spans:
        if span is own[0]:continue
        rect = fitz.Rect(span['bbox'])
        if rect.intersects(guard):
            if rect.x0 < guard.x0:rect.x1 = guard.x0-.01
            elif rect.x1 > guard.x1:rect.x0 = guard.x1+.01
            else:
                doc.close();raise Blocked('Numeric span overlaps unrelated native text')
        if not rect.is_empty:target.add_redact_annot(rect, fill=False)
    target.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    if target.get_text().strip() != digit['text']:
        # Large neighbouring title metrics can overlap the numeric guard even
        # though their ink does not. Rectangle clipping then leaves a letter.
        # Delete only those remaining native operators, never retype the digit.
        from .native_redaction import remove_source_spans
        residual = [s for b in target.get_text('dict')['blocks']
                    for l in b.get('lines', []) for s in l['spans']
                    if not (s['text'] == digit['text'] and s['font'] == digit['font']
                            and max(abs(a-b) for a,b in zip(s['origin'],digit['origin'])) < .001)]
        if residual:
            remove_source_spans(target, [{'id': 'numeric_isolation',
                                         'spans': [_span(s) for s in residual]}])
        if target.get_text().strip() != digit['text']:
            doc.close();raise Blocked('Isolated numeric span contains unrelated text')
    clean = fitz.open(stream=doc.tobytes(garbage=4), filetype='pdf')
    doc.close()
    return clean
