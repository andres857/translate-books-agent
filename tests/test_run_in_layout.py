"""I4: offline regressions from saved real pages; all renders live in tmp_path."""
import copy
import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest
from pilot_fixtures import pre_resegment

from astro_translate import fidelity, fonts

BOOK = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology'
sys.path.insert(0, str(BOOK / 'work/pilot'))


def saved_composition(page, number):
    from local_layout import book_composition
    payload = json.loads(pre_resegment(number))
    # These saved I4 fixtures predate the explicit caption migration.
    canonical = {u['id']: u for u in fidelity.extract_fidelity_units(page, merge_captions=False)}
    units = []
    for supplied in payload['blocks']:
        own = canonical[supplied['id'].split('_', 1)[1]]
        assert supplied['source'] == own['source'] and supplied['spans'] == own['spans']
        units.append({**own, 'target': supplied['target'], 'target_runs': supplied.get(
            'target_runs', [{'text': supplied['target'], 'style': own['style']}])})
    units = book_composition(page, units)
    if number == 141:
        from layout141 import repair_clitic
        scope = json.loads((BOOK / 'work/pilot/p0141-clitic-scope.json').read_text())
        units = repair_clitic(units, scope)
    if number == 180:
        override = json.loads((BOOK / 'work/pilot/p0180-composition-translations.json').read_text())
        assert fidelity.composition_sources_sha256(units) == override['composition_sha256']
        glossary = BOOK / 'work/glossary'
        units = fidelity.apply_composition_overrides(
            BOOK, units, BOOK / 'work/pilot/p0180-composition-translations.json', number,
            glossary / 'astro_en_es.csv', glossary / 'style_guide.md',
            glossary / 'do_not_translate.txt')
    return units


@pytest.mark.parametrize('number,count', [(101, 12), (141, 23)])
def test_real_run_in_entries_are_single_paragraphs(number, count):
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        before = saved_composition(doc[number-1], number)
        original = copy.deepcopy(before)
        units = fidelity.finalize_editorial_composition(doc[number-1], before)
        entries = [u for u in units if u.get('run_in_heading')]
        assert len(entries) == count
        assert before == original
        assert len({u['scale_group'] for u in entries}) == 2
        for u in entries:
            assert u['kind'] == 'paragraph' and not u.get('flow_start_x')
            assert len(u.get('paragraphs', [None])) == 1
            assert u['target_runs'][0]['style']['bold']
            assert not u['target_runs'][-1]['style']['bold']
            members = [v for v in before if v['id'] in u.get('composed_ids', [u['id']])]
            assert ''.join(u['target'].split()) == ''.join(''.join(v['target'].split()) for v in members)
            assert u['spans'] == [s for v in members for s in v['spans']]
        aries = next(u for u in entries if u['source'].startswith('Aries '))
        assert len(aries['composed_ids']) == 2
        assert aries['column_bounds'][1] > aries['spans'][1]['bbox'][2]


@pytest.mark.parametrize('number,ident,parts', [(86, 'u0009', 2), (87, 'u0007', 3), (180, 'u0026', 3)])
def test_centered_caption_fragments_reflow_together(number, ident, parts):
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        units = fidelity.finalize_editorial_composition(doc[number-1], saved_composition(doc[number-1], number))
        unit = next(u for u in units if u['id'] == ident)
        assert len(unit.get('composed_ids', [])) == parts
        assert unit['alignment'] == 'center'
        assert unit['first_line_indent'] == 0
        assert not unit.get('paragraphs')


@pytest.mark.parametrize('number', [101, 141])
def test_real_columns_fit_at_least_point_eight_and_first_line_uses_body(number, tmp_path):
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        page = doc[number-1]
        units = fidelity.finalize_editorial_composition(page, saved_composition(page, number))
        entries = [u for u in units if u.get('run_in_heading')]
        assert entries
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        mask = fidelity._vector_mask(page)
        guards = fidelity.immutable_boxes(page, ())
        boxes = {u['id']: fidelity._candidate_boxes(page, u, units, guards) for u in entries}
        for u in entries:
            assert boxes[u['id']]
            for box in boxes[u['id']]:
                assert not any(box.intersects(g) for g in guards)
                assert box.y1 <= u['layout_bottom']
                assert box.x0 == pytest.approx(u['column_bounds'][0])
                from astro_translate.editorial_margins import editorial_bounds
                assert box.x1 == pytest.approx(min(u['column_bounds'][1],editorial_bounds(page)[1]))
                assert not any(box.intersects(fitz.Rect(v['bbox'])) for v in units if v['id'] != u['id'])
            trials = [(box, fidelity._test_fit(page, u, box, .8, mapping, tmp_path, mask)) for box in boxes[u['id']]]
            accepted = [(box, info) for box, (ok, info) in trials if ok]
            assert accepted, u['id']
            box, info = accepted[0]
            assert info['vector_overlap_pixels'] == 0 and info['raster_guard']['ok']
            if not u['source'].startswith('Aries '):
                continue
            # A first-line word from the old continuation now shares the heading's
            # baseline. This checks the rendered flow, not merely the HTML tag count.
            old = saved_composition(page, number)
            tail = next(v for v in old if v['id'] == u['composed_ids'][1])
            word = tail['target'].split()[0]
            overlay = fitz.open()
            out = overlay.new_page(width=page.rect.width, height=page.rect.height)
            content, css, _ = fidelity._html_for(u, mapping, tmp_path, .8)
            spare, _ = out.insert_htmlbox(box, content, css=css, archive=fitz.Archive(str(tmp_path)), scale_low=1)
            assert spare >= 0 and content.count('<p ') == 1
            words = out.get_text('words')
            first_y = words[0][1]
            assert any(w[4] == word and abs(w[1]-first_y) < 1 for w in words)
            overlay.close()


@pytest.mark.parametrize('number,ident', [(86, 'u0009'), (87, 'u0007'), (180, 'u0026')])
def test_centered_caption_fits_without_moving_its_center(number, ident, tmp_path):
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        page = doc[number-1]
        units = fidelity.finalize_editorial_composition(page, saved_composition(page, number))
        unit = next(u for u in units if u['id'] == ident)
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        mask = fidelity._vector_mask(page)
        boxes = fidelity._candidate_boxes(page, unit, units, fidelity.immutable_boxes(page, ()))
        assert boxes
        assert all(box.x0+box.x1 == pytest.approx(unit['bbox'][0]+unit['bbox'][2]) for box in boxes)
        assert any(fidelity._test_fit(page, unit, box, .8, mapping, tmp_path, mask)[0] for box in boxes)


def test_run_in_candidates_cannot_shift_around_a_protected_obstacle():
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        page = doc[100]
        units = fidelity.finalize_editorial_composition(page, saved_composition(page, 101))
        unit = next(u for u in units if u.get('run_in_heading'))
        assert fidelity._candidate_boxes(page, unit, units, [])
        protected = fitz.Rect(unit['bbox'])
        assert fidelity._candidate_boxes(page, unit, units, [protected]) == []


def test_finalizer_keeps_heading_body_boundaries_and_clitic_repair():
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        units = fidelity.finalize_editorial_composition(doc[140], saved_composition(doc[140], 141))
        cancer = next(u for u in units if u['id'] == 'u0021')
        assert 'protegerte.' in cancer['target'] and cancer['orthographic_repair']
        units = fidelity.finalize_editorial_composition(doc[179], saved_composition(doc[179], 180))
        assert any(u['id'] == 'u0017' for u in units)  # Standalone heading stays separate.
        assert any(u['id'] == 'u0018' for u in units)  # Its body is not run-in.
        heading = next(u for u in units if u['id'] == 'u0001')
        assert heading['composition_override_sha256']
        assert heading['target_runs'][0]['style']['weight'] != heading['target_runs'][-1]['style']['weight']
        assert len(next(u for u in units if u['id'] == 'u0006')['paragraphs']) == 2
        assert len(next(u for u in units if u['id'] == 'u0016')['paragraphs']) == 2
