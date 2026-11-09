"""Composition defects of the Sonnet pilot, batch 16 audit (work/audit/batch-16.json).

Every case composes a real page of ``books/the-secrets-of-astrology/source.pdf`` with
its saved Sonnet translation (``work/production/p<NNNN>-translation.json``) in a
private snapshot book. No LLM call: the translation is read, never produced.
"""
from __future__ import annotations

import json
import shutil

import pymupdf as fitz
import pytest

from test_composition_gaps import SECRETS, secrets


def _translation(page_number):
    return SECRETS/f'work/production/p{page_number:04d}-translation.json'


def saved(page_number):
    return pytest.mark.skipif(not _translation(page_number).is_file(), reason='Saved Sonnet translation not available')


def _saved_fit(page_number, tmp_path):
    """Real page, saved Sonnet payload, real reinsertion in a private snapshot book."""
    from astro_translate import fidelity, pseudo
    from astro_translate.production import Production
    runner = Production(SECRETS)
    snap = pseudo._snapshot(SECRETS, tmp_path)
    (snap/'work/production').mkdir(parents=True, exist_ok=True)
    corrections = SECRETS/'work/production/composition_corrections.json'
    if corrections.is_file():
        shutil.copy2(corrections, snap/'work/production/composition_corrections.json')
    payload = json.loads(_translation(page_number).read_text())
    folder = snap/'work/compose'
    folder.mkdir(parents=True, exist_ok=True)
    return fidelity.reinsert_fidelity(snap, payload, runner.glossary, runner.style, runner.protected,
                                      config=runner.config, artifact_dir=folder, phase='production',
                                      approval_book=SECRETS)


def _lines(page, clip):
    """Composed text lines inside ``clip``: (text, bbox), in reading order."""
    rows = []
    for block in page.get_text('dict', clip=clip)['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).strip()
            if text:
                rows.append((text, fitz.Rect(line['bbox'])))
    rows.sort(key=lambda r: (round(r[1].y0), r[1].x0))
    merged = []
    for text, box in rows:  # PDF lines split by font changes share one baseline
        if merged and abs(merged[-1][1].y0-box.y0) < 2 and box.x0 >= merged[-1][1].x1-1:
            merged[-1] = (merged[-1][0]+' '+text, merged[-1][1] | box)
        else:
            merged.append((text, box))
    return merged


def _glyphs(page, clip, min_size=0, max_size=1000):
    out = []
    for block in page.get_text('rawdict', clip=clip)['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                if min_size <= span['size'] <= max_size:
                    out += [(c['c'], fitz.Rect(c['bbox'])) for c in span['chars'] if c['c'].strip()]
    return out


# 1. OVERFLOW p11/p25 (chapter openings). p11 u0001 «THE STORY / OF ASTROLOGY», SohoPro-Light/Bold
# 50 pt, lines (241.5,433.8)–(526.9,501.9) and (134.9,493.8)–(516.4,561.9); p25 u0001_title «THE / ZODIAC
# SIGNS» (420.8,433.5,526.8,502.3)/(159.3,496.1,516.4,561.5). The span bboxes «STORY », «THE » include a
# trailing space up to x=526.9, but the visible glyphs of every line end at x=516.4 (right-aligned to the
# rule). p11 was classified left (per-span ends), p25 anchored its right alignment at the space: the
# translations ended at x≈526.6, past the rule.
@secrets
@saved(11)
@saved(25)
@pytest.mark.parametrize('page_number', [11, 25])
def test_right_aligned_opening_title_keeps_visible_right_edge(page_number, tmp_path):
    fit = _saved_fit(page_number, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    with fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', page_number-1)]
        glyphs = _glyphs(page, fitz.Rect(100, 430, 552, 580), 30, 100)
    assert glyphs
    rows = {}
    for _, box in glyphs:
        rows.setdefault(round(box.y1/10), []).append(box.x1)
    ends = [max(xs) for xs in rows.values()]
    assert len(ends) == 2, rows
    assert max(ends) <= 516.4+1, ends
    assert all(abs(end-516.4) <= 1.5 for end in ends), ends  # still right-aligned


@secrets
def test_title_lines_with_trailing_spaces_are_recognised_as_right_aligned_p11():
    from astro_translate import fidelity
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[10]
        units = fidelity.extract_fidelity_units(page, read_extras(Production(SECRETS).protected))
        title = next(u for u in units if u['source'] == 'THE STORY OF ASTROLOGY')
        assert title['alignment'] == 'right'
        assert fidelity._ink_bbox(page, title).x1 == pytest.approx(516.4, abs=.2)


# 2. MERGE p4 (credits page, centred column x≈141.4). Credit lines «Consultant  Kim Farley» /
# «Art Editors  Alison Shackleton,» (block 5, SohoPro-Regular role + SohoPro-Bold name, 8 pt,
# 79.9,112.3,205.1,134.4) were composed as one left-aligned paragraph: «Consultora Kim Farley Editoras» /
# «de arte Alison Shackleton,». The copyright block (blocks 16–18, SohoPro-Light 8 pt, 49.9,298.5,233.5,362)
# lost its forced breaks: «…, 2018 Publicada en…» and «…NY 10018 Copyright ©…» (blank line at y=330).
@secrets
@saved(4)
def test_credit_lines_and_copyright_breaks_are_kept_p4(tmp_path):
    fit = _saved_fit(4, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        source_credits = _lines(src[3], fitz.Rect(30, 88, 255, 247))
        page = doc[fit.get('output_page', 3)]
        credits = _lines(page, fitz.Rect(30, 88, 255, 247))
        legal = _lines(page, fitz.Rect(30, 286, 255, 600))
    texts = [t for t, _ in credits]
    consultant = next(t for t in texts if 'Kim Farley' in t)
    assert 'Editora' not in consultant, texts
    art = next(t for t in texts if 'Alison Shackleton' in t)
    assert art.startswith('Editor'), texts  # role and name on the same line
    # One composed line per original credit line, each centred on the column axis.
    assert len(source_credits) == len(credits) == 13, texts
    for _, box in credits:
        assert abs((box.x0+box.x1)/2-141.4) < 3, credits
    legal_texts = [t for t, _ in legal]
    first = next(i for i, t in enumerate(legal_texts) if 'Primera edici' in t)
    assert legal_texts[first].endswith('2018'), legal_texts
    assert legal_texts[first+1].startswith('Publicad'), legal_texts
    copyright_line = next(i for i, t in enumerate(legal_texts) if t.startswith('Copyright ©'))
    gap = legal[copyright_line][1].y0-legal[copyright_line-1][1].y1
    assert gap >= 7, (legal_texts[copyright_line-1:copyright_line+1], gap)  # the blank line survives
    assert 'Todos los derechos reservados.' in legal_texts, legal_texts


# 3. MERGE p138/p139. Bullet lists: each «• Item» is its own source line (FrutigerLTPro-BoldCn 8.06 pt),
# e.g. p138 u0014 «• Self-sacrifice» (437.9,231.7,484.7,239.7) / «• Alone time» (437.9,241.7,478.3,249.7)
# composed as «• Autosacrificio • Tiempo a solas» reaching x=543.5; p139 u0010 «• Career» / «• Reputation»
# composed as «• Carrera •» / «Reputación». One bullet = one line.
BULLETS = {138: ['u0008', 'u0010', 'u0012', 'u0014'], 139: ['u0010', 'u0012', 'u0014', 'u0016', 'u0020', 'u0022']}


@secrets
@saved(138)
@saved(139)
@pytest.mark.parametrize('page_number', [138, 139])
def test_each_bullet_keeps_its_own_line(page_number, tmp_path):
    fit = _saved_fit(page_number, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', page_number-1)]
        for parent in BULLETS[page_number]:
            members = [b for b in fit['blocks'] if b['id'] == parent or b.get('split_from') == parent]
            assert len(members) == 2, (parent, [b['id'] for b in members])
            for block in members:
                assert block['target'].count('•') == 1 and block['target'].lstrip().startswith('•'), block['target']
                box = fitz.Rect(block['edit_bbox'])
                rows = _lines(page, box+(-1, -1, 1, 1))
                assert len(rows) == 1, (block['target'], rows)
            source_right = max(s['bbox'][2] for b in members for s in b['spans'])
            assert max(fitz.Rect(b['edit_bbox']).x1 for b in members) <= source_right+30


# 3b. KEY legend p139 (SohoPro-Condensed 7 pt, rows at y≈63.7/71.7/79.7 beside raster colour swatches):
# u0026 «House cusp / Circle of birth chart / Ascendant» and u0027 «IC (Imum Coeli) / Descendant /
# MC (Medium Coeli)». The translation was reflowed: «Círculo de la carta» / «natal Ascendente», «Descendente
# MC» / «(Medio Cielo)». Each row stays on its swatch row.
@secrets
@saved(139)
def test_key_legend_rows_stay_on_their_rows_p139(tmp_path):
    fit = _saved_fit(139, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    expected = {'u0026': ['Cúspide de casa', 'Círculo de la carta natal', 'Ascendente'],
                'u0027': ['IC (Fondo del Cielo)', 'Descendente', 'MC (Medio Cielo)']}
    with fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 138)]
        for parent, rows in expected.items():
            members = sorted((b for b in fit['blocks'] if b['id'] == parent or b.get('split_from') == parent),
                             key=lambda b: b['bbox'][1])
            assert [b['target'].strip() for b in members] == rows
            for block in members:
                source_top = min(s['bbox'][1] for s in block['spans'])
                (text, box), = _lines(page, fitz.Rect(block['edit_bbox'])+(-1, -1, 1, 1))
                assert text == block['target'].strip()
                assert abs(box.y0-source_top) < 2.5, (text, box, source_top)


# 4. OVERLAP p15. Pull quote u0001_quote (98.8,148.9,316.3,245.5), SohoPro-Light/BoldCompressed 27 pt,
# framed by vector quote marks (gray fills): the closing mark (197.9,233.5,220.0,254.4) hangs after the
# last line «in the sky.». The translated last line «de la experiencia humana.» ran over it. Decorative
# quote marks are obstacles: around the mark the candidate must look exactly like the original.
# The same rule for p138 u0015 (65.2,463.8,222.9,611.2), SohoPro Light/BoldCompressed 27 pt, a framed
# quote that is not a joined display_quote unit: closing mark (172.5,604.5,194.6,625.4) after «experience.».
@secrets
@saved(15)
@saved(138)
@pytest.mark.parametrize('page_number,mark,unit_id', [(15, (197.9, 233.5, 220.0, 254.4), 'u0001_quote'),
                                                     (138, (172.5, 604.5, 194.6, 625.4), 'u0015')])
def test_translated_quote_clears_the_decorative_closing_mark(page_number, mark, unit_id, tmp_path):
    import numpy as np
    fit = _saved_fit(page_number, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    quote = next(b for b in fit['blocks'] if b['id'] == unit_id)
    # The trailing mark owns the band of the source line it follows (x of the mark, up to that line).
    band = fitz.Rect(mark)+(-1, -1, 1, 1)
    band.y0 = min(s['bbox'][1] for s in quote['spans'] if s['bbox'][3] > mark[1] and s['bbox'][2] <= mark[0])
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        a = src[page_number-1].get_pixmap(dpi=220, clip=band)
        b = doc[fit.get('output_page', page_number-1)].get_pixmap(dpi=220, clip=band)
    diff = np.abs(np.frombuffer(a.samples, np.uint8).astype(int)-np.frombuffer(b.samples, np.uint8).astype(int))
    assert diff.max() <= 8, int(diff.max())
    assert quote['scale'] >= .75


@secrets
def test_quote_mark_rects_are_the_vector_marks_p15():
    from astro_translate.display_quotes import quote_mark_rects
    with fitz.open(SECRETS/'source.pdf') as doc:
        marks = quote_mark_rects(doc[14], [98.8, 148.9, 316.3, 245.5], 27)
    assert len(marks) == 4
    assert any(abs(m.x0-197.9) < .2 and abs(m.y0-233.5) < .2 for m in marks)


# 5. MISSING p93. Running head u0000 «The Moon» (447.4,18.1,487.8,34.6), SohoPro-LightCondensed 12 pt,
# right-aligned before the folio, printed over a pale star watermark with white specks (raster, background
# (205,231,245)). «La Luna» is shorter, but every box failed the raster guard (21 px over the dilated specks,
# while the original ink itself lies on the same star) and the fit asked for a short form.
@secrets
@saved(93)
def test_shorter_running_head_over_pale_watermark_composes_p93(tmp_path):
    fit = _saved_fit(93, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    head = next(b for b in fit['blocks'] if b['id'] == 'u0000')
    assert head['target'] == 'La Luna' and head['scale'] == 1.0
    assert fitz.Rect(head['edit_bbox']).x1 <= 487.9
    assert head['raster_guard']['ok']


@secrets
def test_pale_footprint_allows_new_ink_only_inside_the_original_text_footprint_p93():
    from astro_translate.raster_obstacles import build_region_obstacles
    region = fitz.Rect(431.4, 18.1, 487.8, 35.9)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[92]
        plain = build_region_obstacles(page, region, native_underlay=True)
        relaxed = build_region_obstacles(page, region, native_underlay=True,
                                         pale_footprint=[fitz.Rect(447.4, 18.1, 487.8, 34.6)])
    assert plain.mask.sum() > relaxed.mask.sum() > 0  # artwork outside the footprint stays protected
    scale = 110/72
    x0, y0 = relaxed.pixel_bbox[:2]
    left, bottom = int(447.4*scale)-x0, int(34.6*scale)-y0
    inside = relaxed.mask[:bottom-2, left+3:]  # 2 px dilation of artwork outside the footprint
    assert plain.mask[:bottom-2, left+3:].any() and not inside.any()
    assert relaxed.mask[:, :left].any()  # the star left of the head
    assert relaxed.mask[bottom:, :].any()  # the star below the head


# Local QA: the audited Sonnet candidates (work/audit/batch-16.json, candidate_sha256) carried these
# defects with detected_by_local_qa=false. composition_line_issues must flag them. Skipped once the
# production candidate is recomposed (its hash no longer matches the audited one).
AUDITED = {15: {('quote_mark_overlap', 'u0001_quote')}, 25: {('right_edge_overflow', 'u0001_title')},
           138: {('bullet_line_merge', 'u0014')}, 139: {('bullet_line_merge', 'u0010'), ('bullet_line_merge', 'u0016')}}


@secrets
@pytest.mark.parametrize('page_number', sorted(AUDITED))
def test_local_qa_flags_the_audited_composition_defects(page_number):
    from astro_translate import fidelity
    from astro_translate.core import sha256
    fit_path = SECRETS/f'work/production/p{page_number:04d}-fit.json'
    audit_path = SECRETS/'work/audit/batch-16.json'
    if not (fit_path.is_file() and audit_path.is_file()):
        pytest.skip('Audited candidate not available')
    fit = json.loads(fit_path.read_text())
    audited = {p['page']: p['candidate_sha256'] for p in json.loads(audit_path.read_text())['pages']}
    if not fit.get('output') or not (SECRETS/'work/production'/f'p{page_number:04d}-translated.pdf').is_file() \
            or sha256(fit['output']) != audited.get(page_number):
        pytest.skip('Production candidate recomposed after the audit')
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        issues = fidelity.composition_line_issues(src[page_number-1], doc[fit.get('output_page', 0)], fit['blocks'])
    assert AUDITED[page_number] <= {(i['code'], i['unit_id']) for i in issues}


@secrets
@saved(138)
def test_local_qa_is_clean_after_recomposition_p138(tmp_path):
    from astro_translate import fidelity
    fit = _saved_fit(138, tmp_path)
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        assert fidelity.composition_line_issues(src[137], doc[fit.get('output_page', 137)], fit['blocks']) == []


# Native ordinal guard p170 (orchestrator follow-up). House wheel label «11» + «th» (SohoPro-Bold 25.8/15 pt,
# 441.1,166.9 – 491.8,200.7) sits under the 48 pt title «THE ASCENDANT -DESCENDANT» u0005 (metric box
# 36.4,76.5,470.9,172.5). The ordinal edit box (y0=172.5) only touches the title's metric box; the title's
# real glyph ink ends well above it. The guard compared metric boxes, so «11.ª» had no candidate and the
# whole wheel had to stay in English. It must compare real ink.
@secrets
def test_native_ordinal_guard_compares_real_ink_p170():
    from astro_translate import fidelity, native_ordinals
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    extras = read_extras(Production(SECRETS).protected)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[169]
        units = fidelity.extract_fidelity_units(page, extras)
        units = [{**u, 'target': u['source'], 'target_runs': [{'text': u['source'][r['start']:r['end']], 'style': r['style']}
                                                             for r in u['style_runs']]} for u in units]
        headings = [h for h in native_ordinals._headings(page) if h[0] == 'house_diagram' and h[2]['text'] == '11']
        assert headings
        kind, raw, digit, ending = headings[0]
        box = fitz.Rect(digit['bbox']) | fitz.Rect(ending['bbox'])
        title = next(u for u in units if 'DESCENDANT' in u['source'] and u['size'] == 48)
        assert fitz.Rect(title['bbox']).intersects(box)  # metric boxes overlap by ~0.6 pt
        unit = {'id': 'native_house_11', 'native_ordinal': {'edit_bbox': list(box)}}
        boxes = fidelity._base_candidate_boxes(page, unit, units, [])
        ink = fidelity.unit_ink_rects(page, title)
    assert boxes == [box]
    assert ink and max(r.y1 for r in ink) < box.y0  # real glyph ink of the title ends above
