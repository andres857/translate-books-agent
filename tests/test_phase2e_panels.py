"""Real saved panel regressions, entirely offline."""
import json
from pathlib import Path
import pymupdf as fitz
import pytest
from astro_translate import fidelity
from astro_translate.column_flow import prepare_stories, fit_story
from astro_translate.fonts import select_group_fonts

BOOK=Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
FIXTURES=Path(__file__).parent/'fixtures/phase2e'


def saved(n):
    data=json.loads((FIXTURES/f'p{n:04d}-translation.json').read_text())
    return [{**u,'id':u['id'].removeprefix(f'p{n:04d}_')} for u in data['blocks']]


def composed(page,n):
    return prepare_stories(page,fidelity.finalize_editorial_composition(page,
        fidelity.apply_editorial_composition(page,saved(n))))


def test_real_puzzle_story_clears_icon_at_group_scale(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[21];units=composed(page,22)
        unit=next(u for u in units if u.get('story_frames'))
        fonts,_=select_group_fonts(doc,page,units,tmp_path)
        selected=fit_story(page,unit,fonts,tmp_path,fidelity._vector_mask(page))
        assert selected is not None
        assert selected['scale']>=.75
        assert selected['raster_guard']['ok']
        # A frame can use the panel whitespace below original final text, but
        # never crosses the source reference or adjacent panel boundary.
        assert unit['story_frames'][0][3]>unit['story_members'][0]['bbox'][3]
        assert unit['story_frames'][1][3]<591.34


def test_reference_caption_can_use_free_second_story_column(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[21];units=composed(page,22)
        unit=next(u for u in units if u['id']=='u0007')
        boxes=fidelity._candidate_boxes(page,unit,units,fidelity.immutable_boxes(page,()))
        assert boxes
        fonts,_=select_group_fonts(doc,page,units,tmp_path)
        assert any(fidelity._test_fit(page,unit,b,.75,fonts,tmp_path,fidelity._vector_mask(page))[0] for b in boxes)


def test_multi_span_editorial_title_is_not_split_into_table_cells():
    with fitz.open(BOOK/'source.pdf') as doc:
        original=next(u for u in saved(23) if u['id']=='u0013')
        result=fidelity.apply_editorial_composition(doc[22],saved(23))
        matches=[u for u in result if u['id'].startswith('u0013')]
        assert len(matches)==1
        assert matches[0]['target_runs']==original['target_runs']
        assert matches[0]['spans']==original['spans']


@pytest.mark.parametrize("number", [22, 23])
def test_real_panel_recomposition_has_no_failed_spans(number, tmp_path, monkeypatch):
    from astro_translate.production import Production
    runner=Production(BOOK)
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *args,**kwargs:tmp_path)
    payload=json.loads((FIXTURES/f'p{number:04d}-translation.json').read_text())
    fit=fidelity.reinsert_fidelity(BOOK,payload,runner.glossary,runner.style,
        runner.protected,artifact_dir=tmp_path,phase='production',corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete'],fit.get('failures')
    if number==22:
        assert fit['group_scales']['story_u0004']==1
        assert fit['group_scales']['p0022_body_8.0']==1
    else:
        title=next(u for u in fit['blocks'] if u['id']=='u0013')
        with fitz.open(fit['output']) as target:
            lines=[line for b in target[number-1].get_text('dict',clip=fitz.Rect(title['edit_bbox']))['blocks']
                   for line in b.get('lines',[])]
            assert len(lines)==1
            assert fidelity._normalized(''.join(s['text'] for s in lines[0]['spans']))==fidelity._normalized(title['target'])
        assert title['raster_guard']['raster_overlap_pixels']==0
    qa=fidelity.fidelity_qa(BOOK,fit,runner.glossary,runner.protected,
        artifact_dir=tmp_path,update_manifest=False)
    for key in ('numbers_equal','immutable_spans_equal','images_equal','vectors_equal',
                'complete_translated_text','uniform_scale_groups','original_colors_verified'):
        assert qa[key]
    assert qa['non_text_pixel_diff']==0


def test_actual_table_context_still_partitions_positioned_cells():
    from astro_translate.composition_patterns import split_cells
    unit=next(u for u in saved(23) if u['id']=='u0013')
    unit['context']='table_label'
    assert len(split_cells(unit))==2


def test_panel_growth_stops_at_colour_boundary_and_neighbor():
    import io
    from PIL import Image
    from astro_translate.panel_frames import extend_bottom
    image=Image.new('RGB',(200,200),(180,220,210));stream=io.BytesIO();image.save(stream,format='PNG')
    with fitz.open() as doc:
        page=doc.new_page(width=300,height=300)
        page.insert_image(fitz.Rect(10,10,210,210),stream=stream.getvalue())
        frame=fitz.Rect(30,50,180,160)
        grown=extend_bottom(page,frame,[],em=10)
        assert 198<grown.y1<201
        blocked=extend_bottom(page,frame,[fitz.Rect(30,180,180,190)],em=10)
        assert blocked.y1==178
        white=extend_bottom(page,fitz.Rect(220,50,280,160),[],em=10)
        assert white.y1==160


def test_continuous_title_fits_without_covering_puzzle_tab(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[22];units=composed(page,23)
        unit=next(u for u in units if u['id']=='u0013')
        fonts,_=select_group_fonts(doc,page,units,tmp_path)
        boxes=fidelity._candidate_boxes(page,unit,units,fidelity.immutable_boxes(page,()))
        accepted=[info for box in boxes for ok,info in
            [fidelity._test_fit(page,unit,box,.75,fonts,tmp_path,fidelity._vector_mask(page))] if ok]
        assert accepted
        assert accepted[0]['raster_guard']['raster_overlap_pixels']==0


def test_reverse_pdf_span_order_does_not_indent_a_single_line_title(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[29];units=composed(page,30)
        unit=next(u for u in units if u['id']=='u0011')
        assert unit['spans'][0]['bbox'][0]>unit['spans'][1]['bbox'][0]
        assert unit.get('first_line_indent',0)==0
        fonts,_=select_group_fonts(doc,page,units,tmp_path)
        boxes=fidelity._candidate_boxes(page,unit,units,fidelity.immutable_boxes(page,()))
        mask=fidelity._vector_mask(page)
        assert any(fidelity._test_fit(page,unit,box,.75,fonts,tmp_path,mask)[0] for box in boxes)


def test_display_title_stack_grows_to_left_with_right_alignment(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[24];units=composed(page,25)
        title=next(u for u in units if u['id']=='u0002')
        assert title['alignment']=='right'
        fonts,_=select_group_fonts(doc,page,units,tmp_path)
        boxes=fidelity._candidate_boxes(page,title,units,fidelity.immutable_boxes(page,()))
        mask=fidelity._vector_mask(page)
        # 2G: the old fragmented title cannot borrow the page's blank left margin.
        from astro_translate.editorial_margins import editorial_bounds
        assert all(box.x0>=editorial_bounds(page)[0] for box in boxes)
        assert not any(fidelity._test_fit(page,title,box,.75,fonts,tmp_path,mask)[0] for box in boxes)
        # Its complete title remains prepared for flat-emphasis retranslation.
        from astro_translate.composition_corrections import quote_job
        assert any(j['unit'].get('display_title') for j in quote_job(page,units,'source'))


def test_distant_column_headers_remain_independent_cells():
    from astro_translate.composition_patterns import split_cells
    with fitz.open(BOOK/'source.pdf') as doc:
        data=json.loads((BOOK/'work/pilot/p0179-translation.json').read_text())
        units=[{**u,'id':u['id'].removeprefix('p0179_')} for u in data['blocks']]
        header=next(u for u in units if u['id']=='u0001')
        assert len(split_cells(header))==2
