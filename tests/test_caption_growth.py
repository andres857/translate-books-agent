"""I8: saved targets, real fonts and collision guards; no inference or pilot writes."""
import copy
import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import fidelity, fonts

BOOK = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology'
PILOT = BOOK / 'work/pilot'
sys.path.insert(0, str(PILOT))


def saved_units(page):
    from local_layout import book_composition
    number = page.number + 1
    saved = json.loads((PILOT / f'p{number:04d}-translation.json').read_text())
    canonical = {u['id']: u for u in fidelity.extract_fidelity_units(page)}
    units = []
    for supplied in saved['blocks']:
        own = canonical[supplied['id'].removeprefix(f'p{number:04d}_')]
        assert own['source'] == supplied['source'] and own['spans'] == supplied['spans']
        units.append({**own, 'target': supplied['target'], 'target_runs': supplied['target_runs']})
    units = book_composition(page, units)
    if number == 141:
        from layout141 import repair_clitic
        units = repair_clitic(units, json.loads((PILOT/'p0141-clitic-scope.json').read_text()))
    if number == 180:
        g = BOOK / 'work/glossary'
        units = fidelity.apply_composition_overrides(
            BOOK, units, PILOT/'p0180-composition-translations.json', number,
            g/'astro_en_es.csv', g/'style_guide.md', g/'do_not_translate.txt')
    return fidelity.finalize_editorial_composition(page, units)


@pytest.mark.parametrize('number,needle', [(69, 'Kaus Australis'), (35, 'Gamma Arietis'),
                                          (180, 'Example aspect pattern')])
def test_real_caption_fits_at_least_ninety_percent(number, needle, tmp_path):
    from local_layout import composition_context
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        page = doc[number-1]
        units = saved_units(page)
        unit = next(u for u in units if needle in u['source'])
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        mask = fidelity._vector_mask(page)
        guards = fidelity.immutable_boxes(page, ())
        boxes = fidelity._candidate_boxes(page, unit, units, guards)
        accepted = []
        for box in boxes:
            ok, info = fidelity._test_fit(page, unit, box, .9, mapping, tmp_path, mask)
            if ok:
                accepted.append(info)
                break
        assert accepted, (number, unit['id'], len(boxes))
        assert accepted[0]['raster_guard']['ok']
        assert accepted[0]['vector_overlap_pixels'] == 0


def test_all_four_real_sagittarius_captions_share_ninety_percent(tmp_path):
    from local_layout import composition_context
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        page = doc[68]
        units = saved_units(page)
        captions = [u for u in units if u.get('merged_from')]
        assert len(captions) == 4
        assert len({u['scale_group'] for u in captions}) == 1
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        mask = fidelity._vector_mask(page)
        guards = fidelity.immutable_boxes(page, ())
        occupied = []
        for unit in captions:
            accepted = None
            for box in fidelity._candidate_boxes(page, unit, units, guards):
                if any(box.intersects(r) for r in occupied):
                    continue
                ok, info = fidelity._test_fit(page, unit, box, .9, mapping, tmp_path, mask)
                if ok:
                    accepted = box
                    break
            assert accepted is not None, unit['id']
            occupied.append(accepted)


def synthetic_unit():
    style = dict(font='Helvetica', size=9, width=100)
    return dict(id='caption', kind='caption', context='running_text', size=9,
                bbox=[80,80,120,100], alignment='center', style=style,
                spans=[], scale_group='siblings', target='saved target')


@pytest.mark.parametrize('anchor', ['top', 'bottom', 'center'])
@pytest.mark.parametrize('alignment', ['left', 'right', 'center'])
def test_growth_preserves_anchor_and_stops_at_every_obstacle(anchor, alignment):
    import numpy as np
    from PIL import Image
    import io
    from astro_translate.caption_growth import candidate_boxes
    from local_layout import exact_vector_mask
    with fitz.open() as doc:
        page = doc.new_page(width=220, height=220)
        page.draw_line((10,150),(210,150))
        page.insert_text((10,120), 'native ink')
        image = Image.new('RGB',(20,20),'red')
        buf = io.BytesIO(); image.save(buf,format='PNG')
        page.insert_image(fitz.Rect(110,120,140,140), stream=buf.getvalue())
        unit = synthetic_unit()
        unit.update(growth_anchor=anchor, alignment=alignment)
        guard = fitz.Rect(150,50,170,140)
        sibling = {**unit,'id':'sibling','bbox':[50,25,130,50]}
        boxes = candidate_boxes(page,unit,[unit,sibling],[guard],exact_vector_mask(page))
        assert len(boxes) > 1
        for box in boxes:
            assert page.rect.contains(box)
            assert not box.intersects(guard)
            assert not box.intersects(fitz.Rect(sibling['bbox']))
            assert not box.intersects(fitz.Rect(110,120,140,140))
            assert box.y1 < 150
            assert not box.intersects(fitz.Rect(page.get_text('blocks')[0][:4]))
            assert (box.x0 if alignment=='left' else box.x1 if alignment=='right' else (box.x0+box.x1)/2) == (80 if alignment=='left' else 120 if alignment=='right' else 100)
            assert (box.y0 if anchor=='top' else box.y1 if anchor=='bottom' else (box.y0+box.y1)/2) == (80 if anchor=='top' else 100 if anchor=='bottom' else 90)


@pytest.mark.parametrize('anchor', ['top', 'bottom', 'center'])
def test_rendered_baselines_follow_the_vertical_anchor(anchor):
    from astro_translate.caption_growth import insert_anchored
    baseline = []
    for extra in (0,40):
        with fitz.open() as doc:
            page = doc.new_page(width=220,height=220)
            box = fitz.Rect(40,70,180,110)
            if anchor=='top': box.y1 += extra
            elif anchor=='bottom': box.y0 -= extra
            else: box += (0,-extra/2,0,extra/2)
            spare, _ = insert_anchored(page,box,'<div>Saved caption</div>',
                                      'body,div {font-size:9pt;margin:0;padding:0}',None,anchor)
            assert spare >= 0
            baseline.append(page.get_text('dict')['blocks'][0]['lines'][0]['spans'][0]['origin'][1])
    assert baseline[0] == pytest.approx(baseline[1],abs=.01)


def test_body_columns_and_default_legacy_profile_are_unchanged():
    from local_layout import composition_context
    from astro_translate.caption_growth import prepare_captions
    original_finalize = fidelity.finalize_editorial_composition
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        for n in (101,141):
            units = saved_units(doc[n-1])
            assert not any(u.get('caption_growth') for u in units)
            assert prepare_captions(doc[n-1], units) == units
    assert fidelity.finalize_editorial_composition is original_finalize
    with fitz.open(BOOK.parent/'astrology-wisdom-stars/source.pdf') as doc:
        for n in (7,15,121):
            assert not any(u.get('caption_growth') for u in fidelity.extract_fidelity_units(doc[n-1]))


@pytest.mark.parametrize('number', [35,69,180])
def test_full_real_transaction_preserves_art_and_uses_uniform_scales(number,tmp_path,monkeypatch):
    from local_layout import composition_context
    from test_i7_layout import assert_pixels_unchanged_outside_text
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**kw:tmp_path)
    # The LLM boundary must never be entered, even if a fit fails.
    from astro_translate import llm
    monkeypatch.setattr(llm,'run_task',lambda *a,**kw:pytest.fail('I8 forbids inference'))
    g=BOOK/'work/glossary'
    kwargs={'composition_overrides':PILOT/'p0180-composition-translations.json'} if number==180 else {}
    with composition_context():
        result=fidelity.reinsert_fidelity(BOOK,json.loads((PILOT/f'p{number:04d}-translation.json').read_text()),
            g/'astro_en_es.csv',g/'style_guide.md',g/'do_not_translate.txt',artifact_dir=tmp_path,phase='pilot',**kwargs)
    assert result['fit_complete'],result['failures']
    captions=[u for u in result['blocks'] if u.get('caption_growth')]
    # 2G: p35's old 0.91 used space beyond its original editorial margin.
    assert min(u['scale'] for u in captions)>=(.88 if number==35 else .9)
    for group in {u['scale_group'] for u in captions}:
        assert len({u['scale'] for u in captions if u['scale_group']==group})==1
    with fitz.open(BOOK/'source.pdf') as original,fitz.open(result['output']) as translated:
        page,candidate=original[number-1],translated[number-1]
        assert not (fidelity._immutable_characters(page,())-fidelity._immutable_characters(candidate,(),False))
        drawings=lambda p:[{k:v for k,v in d.items() if k!='seqno'} for d in p.get_drawings()]
        assert drawings(page)==drawings(candidate)
        from collections import Counter
        import hashlib
        hashes=lambda p:Counter(hashlib.sha256(p.parent.extract_image(x[0])['image']).hexdigest() for x in p.get_images())
        assert hashes(page)==hashes(candidate)
        assert_pixels_unchanged_outside_text(page,candidate,result['blocks'])
        for u in captions:
            assert fidelity._normalized(u['target']) in fidelity._normalized(candidate.get_text())


def test_sibling_policy_does_not_change_targets_or_body_scale_groups():
    from astro_translate.caption_growth import prepare_captions
    with fitz.open() as doc:
        page=doc.new_page(width=300,height=300)
        one=synthetic_unit()
        two={**one,'id':'second','bbox':[180,80,220,100]}
        body={**one,'id':'body','kind':'paragraph','bbox':[20,180,280,240],
              'column_bounds':[20,280],'alignment':'left','run_in_heading':True}
        before=copy.deepcopy([one,two,body])
        result=prepare_captions(page,before)
        assert before==[one,two,body]
        assert result[-1]==body
        assert result[0]['scale_group']==result[1]['scale_group']!=body['scale_group']
        assert all(a['target']==b['target'] and a['spans']==b['spans'] for a,b in zip(before,result))


@pytest.mark.parametrize('anchor,y', [('top',81),('center',90),('bottom',99)])
def test_side_leader_retains_its_attachment_without_page_number_rules(anchor,y):
    from astro_translate.caption_growth import infer_anchor
    with fitz.open() as doc:
        page=doc.new_page(width=220,height=220)
        page.draw_line((121,y),(170,y+30))
        assert infer_anchor(page,synthetic_unit())==anchor


def test_closed_diagram_curves_are_not_leader_endpoints():
    from astro_translate.caption_growth import infer_anchor
    with fitz.open() as doc:
        page=doc.new_page(width=220,height=220)
        page.draw_circle((133,90),12)
        assert infer_anchor(page,synthetic_unit())=='top'


def test_small_figure_paragraph_does_not_require_a_condensed_font():
    from astro_translate.caption_growth import prepare_captions
    with fitz.open() as doc:
        page=doc.new_page(width=300,height=300)
        page.draw_line((125,90),(175,130))
        unit={**synthetic_unit(),'kind':'paragraph','context':'figure_label','alignment':'left'}
        assert prepare_captions(page,[unit])[0].get('caption_growth')


@pytest.mark.parametrize('identifier,max_lines', [('u0001',1), ('u0002',2),
                                                  ('u0003',1), ('u0004',2), ('u0005',1)])
def test_real_p173_prefers_original_line_count_before_vertical_growth(identifier, max_lines, tmp_path):
    from local_layout import composition_context
    from astro_translate.caption_growth import insert_anchored
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        page = doc[172]
        units = saved_units(page)
        unit = next(u for u in units if u['id'] == identifier)
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        boxes = fidelity._candidate_boxes(page, unit, units, fidelity.immutable_boxes(page, ()))
        assert all(b.width >= fitz.Rect(unit['bbox']).width-.001 for b in boxes)
        selected = next((b for b in boxes if fidelity._test_fit(
            page, unit, b, 1, mapping, tmp_path, fidelity._vector_mask(page))[0]), None)
        assert selected is not None
        content, css, _ = fidelity._html_for(unit, mapping, tmp_path, 1)
        with fitz.open() as probe:
            p = probe.new_page(width=page.rect.width, height=page.rect.height)
            spare, _ = insert_anchored(p, selected, content, css, fitz.Archive(str(tmp_path)), unit['growth_anchor'])
            assert spare >= 0
            lines = [l for b in p.get_text('dict')['blocks'] for l in b.get('lines', [])]
            assert len(lines) <= max_lines


def test_growth_never_narrows_original_unit_or_explicit_column():
    from astro_translate.caption_growth import candidate_boxes
    from local_layout import exact_vector_mask
    with fitz.open() as doc:
        page=doc.new_page(width=220,height=220)
        unit={**synthetic_unit(),'column_bounds':[70,140],'growth_anchor':'top'}
        boxes=candidate_boxes(page,unit,[unit],[],exact_vector_mask(page))
        assert boxes and boxes[0].x0==70 and boxes[0].x1==140
        assert all(b.x0<=80 and b.x1>=120 and b.width>=70 for b in boxes)
        # A real guard blocks the container; it must not silently narrow it.
        assert candidate_boxes(page,unit,[unit],[fitz.Rect(110,80,115,100)],exact_vector_mask(page))==[]


def test_real_p173_transaction_preserves_art_and_caption_lines(tmp_path, monkeypatch):
    from local_layout import composition_context
    from layout173 import composition173
    from astro_translate.native_ordinals import qa_adjustments
    from astro_translate import llm
    from astro_translate import composition_corrections
    from test_i7_layout import assert_pixels_unchanged_outside_text
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**kw:tmp_path)
    monkeypatch.setattr(llm,'run_task',lambda *a,**kw:pytest.fail('No inference permitted'))
    # This is a pilot-layout regression. Production quote corrections may be
    # appended concurrently, so isolate the replay from that mutable input.
    monkeypatch.setattr(composition_corrections,'apply_corrections',lambda units,*a,**kw:(units,[]))
    g=BOOK/'work/glossary'
    with composition_context():
        result=fidelity.reinsert_fidelity(BOOK,json.loads((PILOT/'p0173-translation.json').read_text()),
            g/'astro_en_es.csv',g/'style_guide.md',g/'do_not_translate.txt',artifact_dir=tmp_path,phase='pilot')
    assert result['fit_complete'],result['failures']
    with fitz.open(BOOK/'source.pdf') as source,fitz.open(result['output']) as candidate:
        page,cp=source[172],candidate[172]
        for identifier,count in [('u0001',1),('u0002',2),('u0003',1),('u0004',2),('u0005',1)]:
            u=next(u for u in result['blocks'] if u['id']==identifier)
            # Descendant shares the heading group and is bounded by its leader.
            assert u['scale'] >= (.99 if count == 1 else 1)
            assert fitz.Rect(u['edit_bbox']).width>=fitz.Rect(u['bbox']).width-.001
            lines=[l for b in cp.get_text('dict',clip=fitz.Rect(u['edit_bbox'])+(-.1,-.1,.1,.1))['blocks'] for l in b.get('lines',[])]
            assert len([l for l in lines if any(s['text'].strip() for s in l['spans'])])==count
        immutable=fidelity._immutable_characters(page,())
        _,proof=qa_adjustments(page,cp,result['blocks'],immutable)
        assert len(proof)==8
        # Existing quote repair moves its terminal full stop; it is audited by
        # the production QA path, not an ordinal exemption.
        isolated=tmp_path/'book';isolated.mkdir()
        (isolated/'source.pdf').symlink_to(BOOK/'source.pdf')
        qa=fidelity.fidelity_qa(isolated,result,g/'astro_en_es.csv',g/'do_not_translate.txt',artifact_dir=tmp_path,phase='pilot')
        assert qa['immutable_spans_equal'] and qa['numbers_equal'] and qa['glyph_set_equal']
        assert qa['typography_audit']['ok']
        assert qa['non_text_pixel_diff']==0
        assert qa['images_equal'] and qa['vectors_equal']
        drawings=lambda p:[{k:v for k,v in d.items() if k!='seqno'} for d in p.get_drawings()]
        assert drawings(page)==drawings(cp)
        assert_pixels_unchanged_outside_text(page,cp,result['blocks'])
