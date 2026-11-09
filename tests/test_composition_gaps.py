"""Composition gaps found by the pseudo-translation of The Secrets of Astrology.

Every case is a real unit of ``books/the-secrets-of-astrology/source.pdf``
(page, unit and bbox in each test). No LLM: pseudo answers or identity answers.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT/'books/the-secrets-of-astrology'
secrets = pytest.mark.skipif(not (SECRETS/'source.pdf').is_file(), reason='Secrets source PDF not available')


def _unit(page_number, unit_id):
    from astro_translate.core import read_extras
    from astro_translate.fidelity import extract_fidelity_units
    extras = read_extras(ROOT/'glossary/do_not_translate.txt') if (ROOT/'glossary/do_not_translate.txt').is_file() else []
    with fitz.open(SECRETS/'source.pdf') as doc:
        units = extract_fidelity_units(doc[page_number-1], extras)
    unit = next(u for u in units if u['id'] == unit_id)
    return {**unit, 'id': f'p{page_number:04d}_{unit_id}', 'page': page_number}


# G-PUNCT (gap 6): p37 u0011 bbox≈(36.6,176.6,277.1,238.6) SohoPro-HeavyCompressed caps;
# p41 u0004 bbox≈(367.9,241.8,498.5,281.8) SohoPro-LightCondensed 12 pt;
# p56 u0010 bbox≈(116.9,574.5,460.3,620.5) SohoPro-Light. The sentence memory sends the
# clause core without its final «!»/«?», so no translator can write «¡»/«¿»; the
# composition must restore the opening mark where it restores the closing one.
@secrets
@pytest.mark.parametrize('page,unit_id', [(37, 'u0011'), (41, 'u0004'), (56, 'u0010')])
def test_clause_memory_restores_spanish_opening_marks_on_real_units(page, unit_id):
    from astro_translate.consistency import build_plan, resolve_plan
    from astro_translate.text_quality import opening_punctuation_issues
    unit = _unit(page, unit_id)
    plan = build_plan([unit])
    # The wire keeps the punctuation-free core (memory contract unchanged).
    assert all(not r['source'].rstrip().endswith(('!', '?')) for r in plan['requests'])
    resolved = resolve_plan(plan, {r['id']: r['source'] for r in plan['requests']})
    target = resolved['units'][0]['target']
    assert not opening_punctuation_issues(target, unit['id']), target
    assert target.count('¡') == target.count('!')
    assert ''.join(r['text'] for r in resolved['units'][0]['target_runs']) == target


def _plain(identifier, source):
    style = {'font': 'Body', 'bold': False, 'italic': False, 'size': 10, 'color': 0}
    return {'id': identifier, 'page': 1, 'source': source, 'style': style,
            'style_runs': [{'start': 0, 'end': len(source), 'style': style}]}


def test_opening_mark_goes_to_the_sentence_start_and_is_not_duplicated():
    from astro_translate.consistency import build_plan, resolve_plan
    source = 'It works. Calm—most of the time! But not always? Fine.'
    plan = build_plan([_plain('u', source)])
    out = resolve_plan(plan, {r['id']: r['source'] for r in plan['requests']})['units'][0]['target']
    assert out == 'It works. ¡Calm—most of the time! ¿But not always? Fine.'
    # A translation that already opens the mark (e.g. retranslated clause) is untouched.
    plan = build_plan([_plain('u', 'Win now!')])
    request = plan['requests'][0]
    out = resolve_plan(plan, {request['id']: '¡Gana ya'})['units'][0]['target']
    assert out == '¡Gana ya!'


# G-PROFILE (gap 7): p34/p60 ficha bound by units_sha256. Production saved the units in
# routing order (model units, then local ones: p34 u0004, p60 u0003 last) while the
# pseudo run (and any re-extraction) yields id order. Same ids/sources/spans: the binding
# is about content, so the digest must not depend on order; real content edits still block.
@secrets
@pytest.mark.parametrize('page_number', [34, 60])
def test_profile_unit_binding_is_order_independent_but_content_bound(page_number):
    from copy import deepcopy
    from astro_translate.core import Blocked
    from astro_translate.fidelity import extract_fidelity_units
    from astro_translate.profiles import load_profile, units_digest
    from astro_translate.production import Production
    from astro_translate.core import read_extras
    profile = load_profile(SECRETS)
    extras = read_extras(Production(SECRETS).protected)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[page_number-1]
        units = [{**u, 'target': u['source'], 'target_runs': [{'text': u['source'], 'style': u['style']}]}
                 for u in extract_fidelity_units(page, extras)]
        rotated = units[1:]+units[:1]
        assert units_digest(rotated) == units_digest(units)
        assert units_digest(units) == profile.page_policy(page)['units_sha256']
        profile.compose(page, units)
        profile.compose(page, rotated)
        changed = deepcopy(units)
        changed[-1]['spans'][0]['source'] = changed[-1]['spans'][0]['source']+'x'
        with pytest.raises(Blocked, match='Profile unit source hash changed'):
            profile.compose(page, changed)


@secrets
def test_pseudo_payload_keeps_production_routing_order_p34():
    """Saved overrides hash the composed units in production order (adapters)."""
    import json
    from astro_translate.core import read_extras, sha256
    from astro_translate.production import Production
    from astro_translate import pseudo
    runner = Production(SECRETS)
    saved = json.loads((SECRETS/'work/production/p0034-translation.json').read_text())['blocks']
    with fitz.open(SECRETS/'source.pdf') as doc:
        payload, _, _ = pseudo.pseudo_page_payload(doc[33], 34, sha256(SECRETS/'source.pdf'),
                                                   extras=read_extras(runner.protected),
                                                   glossary=pseudo._terms(runner.glossary), expansion=1.25)
    assert [b['id'] for b in payload['blocks']] == [b['id'] for b in saved]


# G-PROFILE p46 (gap 7): ficha u0006, block 4, rows at x≈52, y≈382…525, SohoPro-LightCondensed
# labels / SohoPro-BoldCompressed values, 23 pt. «Symbol» (glyph value) and «Polarity» are one
# same-style run across two rows; any translation with another word count was blocked as
# «Ambiguous saved field row boundary». The row detector now gives each label cell its own
# style marker on the wire, so the saved runs partition exactly, with no word guessing.
@secrets
def test_field_table_cells_get_own_markers_and_partition_exactly_p46():
    from astro_translate import fidelity, pseudo
    from astro_translate.consistency import build_plan, resolve_plan
    from astro_translate.structured_layout import prepare_saved_field_rows
    from astro_translate.production import Production
    from astro_translate.core import read_extras
    runner = Production(SECRETS)
    extras, terms = read_extras(runner.protected), pseudo._terms(runner.glossary)
    unit = _unit(46, 'u0006')
    plan = build_plan([unit])
    (request,) = plan['requests']
    assert request['source'].startswith('{v1}Symbol {v2}Polarity {v3}Negative')
    answer, failure = pseudo.validated(request['source'], extras=extras, glossary=terms)
    assert failure is None
    # The pseudo answer that used to block: an extra word inside the label cells.
    first, second = answer.split('{v2}', 1)
    answer = first.rstrip()+' jamás {v2}'+second
    resolved = resolve_plan(plan, {request['id']: answer})['units'][0]
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[45]
        composed = fidelity.apply_editorial_composition(page, [{**resolved, 'id': 'u0006'}])
        rows = prepare_saved_field_rows(page, composed)
    assert [r['source'].strip() for r in rows][:3] == ['Symbol', 'Polarity Negative', 'Mode Cardinal']
    assert rows[0]['target'].endswith('jamás')
    assert fidelity._normalized(''.join(r['target'] for r in rows)) == fidelity._normalized(resolved['target'])


def _pseudo_fit(page_number, tmp_path, monkeypatch=None):
    """Real page, pseudo payload (no LLM), real reinsertion in a private snapshot book."""
    from astro_translate import fidelity, pseudo
    from astro_translate.core import atomic_json, read_extras, sha256
    from astro_translate.production import Production
    runner = Production(SECRETS)
    snap = pseudo._snapshot(SECRETS, tmp_path)
    with fitz.open(SECRETS/'source.pdf') as doc:
        payload, corrections, _ = pseudo.pseudo_page_payload(
            doc[page_number-1], page_number, sha256(SECRETS/'source.pdf'), extras=read_extras(runner.protected),
            glossary=pseudo._terms(runner.glossary), expansion=1.25)
    deps = {'source_sha256': sha256(SECRETS/'source.pdf'), 'glossary_sha256': sha256(runner.glossary),
            'style_sha256': sha256(runner.style), 'protected_sha256': sha256(runner.protected)}
    atomic_json(snap/'work/production/composition_corrections.json', {**deps, 'corrections': corrections})
    folder = snap/'work/compose'
    folder.mkdir(parents=True, exist_ok=True)
    return fidelity.reinsert_fidelity(snap, payload, runner.glossary, runner.style, runner.protected,
                                      artifact_dir=folder, phase='production', approval_book=SECRETS)


# G-FIT p22 (gap 5): story «YOUR INDIVIDUAL MAP» u0004–u0006, frames (36.1,476.8,178.2,613.4) and
# (196.4,476.8,326.9,589.3), SohoPro-Light 11 pt. The «play» icon raster occupies ≈(36.7,476.5,64.8,488.9):
# the first line clears it by a fixed 33.9 pt indent, but at a reduced scale the second line rose
# into the icon (raster guard: native_ink_over_raster_artwork), so no scale ≥0.75 was accepted.
@secrets
def test_story_icon_clearance_is_a_band_not_a_first_line_indent_p22(tmp_path, monkeypatch):
    fit = _pseudo_fit(22, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    story = next(b for b in fit['blocks'] if b.get('story_frames'))
    assert story['scale'] >= .75
    with fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 21)]
        icon = fitz.Rect(36.6, 476.5, 64.9, 489.0)
        words = [w for w in page.get_text('words') if fitz.Rect(w[:4]).intersects(icon)]
    assert not words, words


# G-FIT p23 (gap 5): «YOUR ZODIAC SIGNS» panel, SohoPro-Light 11 pt. One paragraph was extracted as
# u0007 (377,196,502,235; first line indented to 408.5 by the icon) and u0008 (377,238,517,305; three
# lines indented to 397.1 by the puzzle tab) split mid-sentence («…A person may» / «have the Sun…»).
# Neither fragment had room to grow, so the 11 pt group failed at 0.75. The fragments are one
# paragraph flowing through the original line shape (icon band, tab band, full width).
@secrets
def test_wrapped_paragraph_fragments_flow_through_original_line_shape_p23(tmp_path, monkeypatch):
    fit = _pseudo_fit(23, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    joined = next(b for b in fit['blocks'] if b.get('composed_ids') == ['u0007', 'u0008'])
    assert joined['scale'] >= .75 and len(joined['story_frames']) >= 3
    left = [f[0] for f in joined['story_frames']]
    assert max(left)-min(left) > 20  # icon / tab bands keep their original left edges
    with fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 22)]
        tab = fitz.Rect(377.5, 239.0, 396.5, 278.5)  # where the original lines leave room for the tab
        assert not [w for w in page.get_text('words') if fitz.Rect(w[:4]).intersects(tab)]
        # Continuous leading: no blank half-line where the shape changes.
        area = fitz.Rect(joined['bbox'])
        tops = sorted({round(l['bbox'][1], 1) for b in page.get_text('dict', clip=area)['blocks']
                       for l in b.get('lines', []) if fitz.Rect(l['bbox']) in area+(-1, -1, 1, 1)
                       and ''.join(x['text'] for x in l['spans']).strip()})
        pitch = min(b-a for a, b in zip(tops, tops[1:]))
        assert max(b-a for a, b in zip(tops, tops[1:])) < pitch*1.3, tops


# G-FIT p23 (gap 5): u0014 «Your Ascendant is the zodiac sign…» (198,376,499,429) SohoPro-Light 11 pt.
# Its own box contains the wheel icon (first-line indent), so every grown box failed the flat-raster
# test even though the added strip below lies on the flat white panel. Only the added area must be
# free background; ink over artwork is still rejected by the raster guard in _test_fit.
@secrets
def test_growth_checks_only_the_added_strip_for_flat_background_p23():
    from astro_translate import fidelity
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    extras = read_extras(Production(SECRETS).protected)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[22]
        units = fidelity.extract_fidelity_units(page, extras)
        unit = next(u for u in units if u['id'] == 'u0014')
        unit = {**unit, 'target': unit['source'], 'target_runs': [{'text': unit['source'], 'style': unit['style']}]}
        boxes = fidelity._candidate_boxes(page, unit, units, [])
        original = fitz.Rect(unit['bbox'])
    assert any(b.height >= original.height+12 and b.y0 <= original.y0+.01 for b in boxes), boxes
    # Never down into the blue puzzle panel / tab below the white panel.
    assert all(b.y1 < 468 for b in boxes)


# G-FIT p53 (gap 5): u0009 «Leos are positive and forward-looking…» (347.2,504.2,503.8,530.3),
# SohoPro-LightCondensed 12 pt, right below the orange chevrons whose tip reaches y≈506. Spanish
# accents on the first line touch the tip, and the free space is below; candidates could either
# move the box or grow it, never settle 1–2 pt and grow down together.
@secrets
def test_paragraph_candidates_settle_and_grow_down_p53():
    from astro_translate import fidelity
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    extras = read_extras(Production(SECRETS).protected)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[52]
        units = fidelity.extract_fidelity_units(page, extras)
        unit = next(u for u in units if u['id'] == 'u0009')
        unit = {**unit, 'target': unit['source'], 'target_runs': [{'text': unit['source'], 'style': unit['style']}]}
        boxes = fidelity._candidate_boxes(page, unit, units, [])
        base = fitz.Rect(unit['bbox'])
    assert any(base.y0+.5 < b.y0 <= base.y0+2.01 and b.height >= base.height+8 for b in boxes)
    # The yellow «ME, MYSELF, AND I» banner text below (y≥553) stays out of reach.
    assert all(b.y1 <= 553 for b in boxes)


# G-FIT p57 (gap 5): u0000 «EFFICIENT & PRACTICAL» (369.9,186.1,469.7,220.1) FrutigerLTStd-Bold 16 pt
# inside the grey starburst; its body u0001 starts at y≈220. Every horizontal growth also added
# 0.5–1 pt downwards and was rejected against u0001, although the strip to the right is flat grey.
@secrets
def test_pure_horizontal_growth_for_units_touching_the_next_block_p57():
    from astro_translate import fidelity
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    extras = read_extras(Production(SECRETS).protected)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[56]
        units = fidelity.extract_fidelity_units(page, extras)
        unit = next(u for u in units if u['id'] == 'u0000')
        unit = {**unit, 'target': unit['source'], 'target_runs': [{'text': unit['source'], 'style': unit['style']}]}
        base = fidelity._ink_bbox(page, unit)
        boxes = fidelity._candidate_boxes(page, unit, units, [])
    wider = [b for b in boxes if b.x1 >= base.x1+8 and abs(b.y0-base.y0) < .01 and abs(b.y1-base.y1) < .01
             and abs(b.x0-base.x0) < .01]
    assert wider, boxes
    assert all(b.x1 <= base.x1+16.01 for b in wider)  # stops at the starburst edge


# G-OVERLAP p7 (gap 12): TOC column «THE HOUSES 136» (SohoPro-Bold 14, folio x0=243.6) above entries
# «2nd House Possessions, money, and finance 142» (SohoPro 12, name ends 245.1, folio x0=253.6).
# (1) The column right bound was min(all folios)-4 = 239.6, narrower than every original entry, so
# the entries fell to 0.79; (2) the 14 pt header shared the entries' scale group; (3) the spaces
# between «HOUSES» and its inline folio stayed native, so a 127.6–270.9 line overlapped the title.
@secrets
def test_toc_rows_use_their_own_folio_edge_and_leave_no_native_gap_p7(tmp_path):
    from astro_translate import pseudo
    fit = _pseudo_fit(7, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    rows = [b for b in fit['blocks'] if b.get('toc_row')]
    header = next(b for b in rows if b['source'].startswith('THE HOUSES'))
    entry = next(b for b in rows if 'Possessions' in b['source'])
    assert header['scale_group'] != entry['scale_group']
    assert entry['column_bounds'][1] >= 245.1
    assert min(fit['group_scales'][b['scale_group']] for b in rows) >= .8
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as dst:
        overlaps, overflow = pseudo._new_overlaps_and_overflow(src[6], dst[fit.get('output_page', 6)])
    assert not overlaps and not overflow, overlaps


# Regression found while fixing gap 5 (p35): u0009 «The constellation of Aries…» (292,90.9,520.6,239.9)
# SerifaStd-Light 14 pt and the caption u0006_join_u0007 (293.9,260.1,414.5,289.1) share a 20 pt gap.
# Once the added strip alone had to be flat, the body grew down to 259.9 and the caption could no
# longer grow at all. Vertical growth towards a neighbouring text block claims at most half the gap.
@secrets
def test_vertical_growth_shares_the_gap_with_the_neighbouring_block_p35():
    from astro_translate import fidelity
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    extras = read_extras(Production(SECRETS).protected)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[34]
        units = fidelity.extract_fidelity_units(page, extras)
        unit = next(u for u in units if u['id'] == 'u0009')
        unit = {**unit, 'target': unit['source'], 'target_runs': [{'text': unit['source'], 'style': unit['style']}]}
        boxes = fidelity._candidate_boxes(page, unit, units, [])
    middle = (239.9+260.1)/2
    assert all(b.y1 <= middle+.01 for b in boxes), [b for b in boxes if b.y1 > middle]
    assert any(b.y1 > 239.9+4 for b in boxes)


def test_opening_mark_takes_the_style_of_the_clause_it_opens():
    from astro_translate.consistency import build_plan, resolve_plan
    regular = {'font': 'Body', 'bold': False, 'italic': False, 'size': 10, 'color': 0}
    bold = {**regular, 'font': 'Bold', 'bold': True}
    source = 'Fine. Win now!'
    unit = {'id': 'u', 'page': 1, 'source': source, 'style': regular,
            'style_runs': [{'start': 0, 'end': 6, 'style': regular}, {'start': 6, 'end': len(source), 'style': bold}]}
    plan = build_plan([unit])
    out = resolve_plan(plan, {r['id']: r['source'] for r in plan['requests']})['units'][0]
    assert out['target'] == 'Fine. ¡Win now!'
    assert next(r for r in out['target_runs'] if '¡' in r['text'])['style'] == bold


# p30 u0007 «High in negative/ low in positive» (44,514,159,541) may still use the whole 4 pt gap to
# its body u0008 (44,545,171,612): only captions get the half-gap reservation.
@secrets
def test_heading_keeps_growth_into_its_body_gap_p30():
    from astro_translate import fidelity
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    extras = read_extras(Production(SECRETS).protected)
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[29]
        units = fidelity.extract_fidelity_units(page, extras)
        unit = next(u for u in units if u['id'] == 'u0007')
        unit = {**unit, 'target': unit['source'], 'target_runs': [{'text': unit['source'], 'style': unit['style']}]}
        boxes = fidelity._candidate_boxes(page, unit, units, [])
    assert any(b.y1 >= 544.5 for b in boxes)  # grows its 4 pt to the body
