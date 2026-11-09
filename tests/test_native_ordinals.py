"""I3: real native heading spans, offline composition, no book artifacts written."""
import copy
import json
import re
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import fidelity
from astro_translate.fonts import select_group_fonts
from astro_translate.fidelity_audit import audit_rendered_styles

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / 'books/the-secrets-of-astrology'
sys.path.insert(0, str(BOOK / 'work/pilot'))


def saved_units(page, number):
    from local_layout import book_composition
    from table_repair import table_composition
    payload = json.loads((BOOK / f'work/pilot/p{number:04d}-translation.json').read_text())
    canonical = {u['id']: u for u in fidelity.extract_fidelity_units(page)}
    units = [{**canonical[u['id'].split('_', 1)[1]], 'target': u['target'],
              'target_runs': u.get('target_runs', [{'text': u['target'], 'style': canonical[u['id'].split('_', 1)[1]]['style']}])} for u in payload['blocks']]
    units = (table_composition if number in (34, 60) else book_composition)(page, units)
    if number in (34, 60):
        payload = json.loads((BOOK / f'work/pilot/p{number:04d}-composition-translations.json').read_text())
        assert fidelity.composition_sources_sha256(units) == payload['composition_sha256']
        targets = {u['id']: u for u in payload['blocks']}
        units = [{**u, 'target': targets[u['id']]['target'],
                  'target_runs': targets[u['id']]['target_runs']} for u in units]
    return units


@pytest.mark.parametrize('number,expected', [(34, 'EL 1.er SIGNO DEL ZODÍACO'),
                                           (60, 'EL 7.º SIGNO DEL ZODÍACO'),
                                           (154, 'CASA 7')])
def test_real_heading_extract_compose_and_styles(number, expected, tmp_path):
    from astro_translate.native_ordinals import adapt_headings, prepare_headings, heading_overlay, select_heading_fonts
    with fitz.open(BOOK / 'source.pdf') as doc:
        page = doc[number - 1]
        units = saved_units(page, number)
        before = copy.deepcopy(units)
        revised = adapt_headings(page, units)
        assert units == before  # Saved source/override contracts remain unchanged.
        heading = next(u for u in revised if u.get('native_ordinal'))
        assert heading['target'] == expected
        assert ''.join(r['text'] for r in heading['target_runs']) == expected
        original_digit = heading['native_ordinal']['digit']
        font_map, _ = select_heading_fonts(select_group_fonts, doc, page, [heading], tmp_path, classify_slab=True)
        prepare_headings(page, [heading], font_map)
        overlay, finished = heading_overlay(page, heading, 1, font_map)
        try:
            text = ' '.join(overlay[0].get_text().split())
            assert text == expected
            assert not re.search(r'(?i)(?:\d\s*)?(?:ST|ND|RD|TH)\b', text)
            spans = [s for b in overlay[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
            digit = next({**s, 'origin': c['origin']} for b in overlay[0].get_text('rawdict')['blocks'] for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if c['c'] == original_digit['text'])
            assert digit['font'] == original_digit['font']
            assert digit['size'] == pytest.approx(original_digit['size'], abs=.001)
            assert digit['color'] == original_digit['color']
            if number != 154:
                assert digit['origin'] == pytest.approx(original_digit['origin'], abs=.001)
            _, _, fonts = fidelity._html_for(heading, font_map, tmp_path, 1)
            report = {**heading, 'edit_bbox': heading['native_ordinal']['edit_bbox'], 'font_runs': fonts}
            assert audit_rendered_styles(overlay[0], {'blocks': [report]})['ok']
            assert finished
        finally:
            overlay.close()


def test_diagram_is_native_and_feminine_at_any_page_index():
    from astro_translate.native_ordinals import inventory, adapt_headings
    with fitz.open(BOOK / 'source.pdf') as doc:
        found = inventory(doc[172])
        deferred = [r for r in found if r['treatment'] == 'local_house_diagram']
        assert [r['ordinal'] for r in deferred] == ['3rd', '4th', '5th', '6th', '7th', '8th', '9th', '10th']
        assert all(r['native'] for r in deferred)
        units = fidelity.extract_fidelity_units(doc[172])
        assert len([u for u in adapt_headings(doc[172], units) if u.get('native_ordinal')]) == 8
        with fitz.open() as moved:
            moved.insert_pdf(doc, from_page=172, to_page=172)
            assert len([r for r in inventory(moved[0]) if r['treatment'] == 'local_house_diagram']) == 8


@pytest.mark.parametrize('number', [34, 60, 154])
def test_real_candidate_redaction_qa_and_numeric_guard(number, tmp_path):
    from collections import Counter
    from astro_translate.core import Blocked
    from astro_translate.native_ordinals import adapt_headings, prepare_headings, heading_overlay, select_heading_fonts, qa_adjustments
    from local_layout import precise_redaction_rects, composition_context
    with fitz.open(BOOK / 'source.pdf') as source:
        page = source[number-1]
        with composition_context():
            units = fidelity.finalize_editorial_composition(page, saved_units(page, number))
        heading = next(u for u in units if u.get('native_ordinal'))
        mapping, _ = select_heading_fonts(select_group_fonts, source, page, [heading], tmp_path, classify_slab=True)
        prepare_headings(page, [heading], mapping)
        guards = [r for r in fidelity.immutable_boxes(page, ()) if not any(r == fitz.Rect(s['bbox']) for s in heading['spans'])]
        boxes = fidelity._candidate_boxes(page, heading, units, guards)
        assert len(boxes) == 1
        with composition_context():
            vector_mask = fidelity._vector_mask(page)
        ok, _ = fidelity._test_fit(page, heading, boxes[0], 1, mapping, tmp_path, vector_mask)
        assert ok
        overlay, _ = heading_overlay(page, heading, 1, mapping)
        candidate = fitz.open()
        try:
            candidate.insert_pdf(source, from_page=number-1, to_page=number-1)
            cp = candidate[0]
            for rect in precise_redaction_rects(page, heading):cp.add_redact_annot(rect, fill=False)
            cp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            cp.show_pdf_page(cp.rect, overlay, 0)
            _, _, fonts = fidelity._html_for(heading, mapping, tmp_path, 1)
            block = {**heading, 'edit_bbox': list(boxes[0]), 'font_runs': fonts, 'scale': 1}
            text = cp.get_text(clip=boxes[0])
            # Redaction may leave empty whitespace-only source lines.
            assert '  ' not in text.strip()
            assert ' '.join(text.split()) == heading['target']
            assert not re.search(r'(?i)\b(?:ST|ND|RD|TH)\b', text)
            immutable = fidelity._immutable_characters(page, ())
            block = json.loads(json.dumps(block))  # Later qa-local reloads fit.json.
            added, proof = qa_adjustments(page, cp, [block], immutable)
            assert proof[0]['numeric_value_style_verified']
            if number != 154:
                clip = fitz.Rect(heading['native_ordinal']['digit']['bbox'])
                assert page.get_pixmap(clip=clip, dpi=220).samples == cp.get_pixmap(clip=clip, dpi=220).samples
            assert not (immutable-fidelity._immutable_characters(cp, (), False))
            assert Counter(fidelity.NUMBER_PATTERN.findall(page.get_text())) == Counter(fidelity.NUMBER_PATTERN.findall(cp.get_text()))
            assert Counter(fidelity.SYMBOL_PATTERN.findall(page.get_text())) + added == Counter(fidelity.SYMBOL_PATTERN.findall(cp.get_text()))
            assert not any('.er' in t for t in fidelity.verified_editorial_text(candidate, cp, {'blocks': [block]}))
            # Wrong numeric color is a QA failure, never an exemption.
            bad = copy.deepcopy(block)
            bad['target_runs'][1]['style']['color'] ^= 255
            with pytest.raises(Blocked):qa_adjustments(page, cp, [bad], fidelity._immutable_characters(page, ()))
        finally:
            candidate.close(); overlay.close()


@pytest.mark.parametrize('number,gender,expected', [(1,'m','.er'), (3,'m','.er'), (7,'m','.º'), (1,'f','.ª')])
def test_editorial_suffix_rule(number, gender, expected):
    from astro_translate.native_ordinals import suffix
    assert suffix(number, gender) == expected.strip()


def test_complete_pilot_inventory_from_saved_candidates():
    from collections import Counter
    from astro_translate.native_ordinals import inventory
    pages = (1,5,19,28,34,35,60,69,86,87,100,101,128,141,154,169,173,175,179,180)
    rows = []
    with fitz.open(BOOK / 'source.pdf') as doc:
        for number in pages:
            path = BOOK / f'work/pilot/p{number:04d}-fit.json'
            saved = json.loads(path.read_text()).get('blocks', []) if path.exists() else []
            rows.extend(inventory(doc[number-1], saved))
    assert Counter(r['page'] for r in rows) == {34:1, 60:1, 141:5, 154:8, 173:9}
    assert Counter(r['treatment'] for r in rows) == {
        'local_sign_heading':2, 'local_house_heading':1,
        'existing_casa_number':13, 'local_house_diagram':8}


def test_unknown_ordinal_remains_protected_pending_context():
    from astro_translate.native_ordinals import inventory, adapt_headings
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((40,40), 'The 2nd')
    units = fidelity.extract_fidelity_units(page)
    assert inventory(page)[0]['treatment'] == 'pending_context'
    assert adapt_headings(page, units) == units
    doc.close()


def test_real_house_diagram_suffixes_preserve_digits_and_pass_qa_masks(tmp_path):
    from astro_translate.native_ordinals import adapt_headings, prepare_headings, heading_overlay, select_heading_fonts, qa_adjustments
    from local_layout import precise_redaction_rects
    from run_visual_qa import ordinal_policy, verified_terms
    from astro_translate.safe_vlm import _protected_rects
    from astro_translate.core import Blocked
    from collections import Counter
    import hashlib
    with fitz.open(BOOK/'source.pdf') as doc, fitz.open() as candidate:
        page = doc[172]
        units = [u for u in adapt_headings(page, fidelity.extract_fidelity_units(page)) if u.get('native_ordinal')]
        assert len(units) == 8
        mapping, _ = select_heading_fonts(select_group_fonts, doc, page, units, tmp_path, classify_slab=True)
        prepare_headings(page, units, mapping)
        candidate.insert_pdf(doc, from_page=172, to_page=172)
        cp = candidate[0]
        for u in units:
            for rect in precise_redaction_rects(page,u):cp.add_redact_annot(rect,fill=False)
        cp.apply_redactions(images=0,graphics=0)
        reports=[]
        for u in units:
            data=u['native_ordinal'];digit=data['digit'];ending=data['suffix']
            assert u['target'] == digit['text']+'.ª'
            # «ª» is a superior glyph: «.ª» on the numeral's baseline (raised once, 2026-11-09).
            assert data['positions'] == [digit['origin'],[ending['origin'][0],digit['origin'][1]]]
            overlay,_=heading_overlay(page,u,1,mapping)
            cp.show_pdf_page(cp.rect,overlay,0);overlay.close()
            _,_,used=fidelity._html_for(u,mapping,tmp_path,1)
            reports.append({**u,'edit_bbox':data['edit_bbox'],'font_runs':used,'scale':1})
        immutable=fidelity._immutable_characters(page,())
        added,proof=qa_adjustments(page,cp,json.loads(json.dumps(reports)),immutable)
        assert len(proof)==8 and all(p['numeric_origin_preserved'] for p in proof)
        assert not (immutable-fidelity._immutable_characters(cp,(),False))
        assert Counter(fidelity.NUMBER_PATTERN.findall(page.get_text()))==Counter(fidelity.NUMBER_PATTERN.findall(cp.get_text()))
        assert Counter(fidelity.SYMBOL_PATTERN.findall(page.get_text()))+added==Counter(fidelity.SYMBOL_PATTERN.findall(cp.get_text()))
        for u in units:
            data=u['native_ordinal'];clip=fitz.Rect(data['digit']['bbox'])
            assert page.get_pixmap(clip=clip,dpi=220).samples == cp.get_pixmap(clip=clip,dpi=220).samples
        trusted={hashlib.sha256(Path(v['path']).read_bytes()).hexdigest() for v in mapping.values()}
        terms=verified_terms(page,cp,['st','nd','rd','th'])
        with ordinal_policy():
            _,records,_,_=_protected_rects(cp,terms,(),trusted)
        assert all(r['protected'] for r in records if r['char'].isdigit())
        assert len([r for r in records if r['char']=='ª' and not r['protected']])==8
        # A second digit corruption in 10 must not be hidden by an exemption.
        bad=copy.deepcopy(reports)
        ten=next(u for u in bad if u['target']=='10.ª')
        ten['native_ordinal']['positions'][0][0]+=1
        with pytest.raises(Blocked):qa_adjustments(page,cp,bad,fidelity._immutable_characters(page,()))
