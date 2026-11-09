"""Real batch-one regressions. No model, no authored translations."""
import json
from copy import deepcopy
from pathlib import Path
import pymupdf as fitz
import pytest
from astro_translate import fidelity

BOOK=Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
FIXTURES=Path(__file__).parent/'fixtures/phase2c'


def saved(n):
    data=json.loads((FIXTURES/f'p{n:04d}-translation.json').read_text())
    return [{**u,'id':u['id'].removeprefix(f'p{n:04d}_')} for u in data['blocks']]


@pytest.mark.parametrize('n,columns',[(12,2),(13,1),(14,2),(15,3),(16,2),(17,3),(18,2),(19,3),(20,2)])
def test_real_column_story(n,columns):
    from astro_translate.column_flow import prepare_stories
    with fitz.open(BOOK/'source.pdf') as doc:
        units=fidelity.apply_editorial_composition(doc[n-1],saved(n))
        result=prepare_stories(doc[n-1],units)
        stories=[u for u in result if u.get('story_frames')]
        assert len(stories)==1
        story=stories[0]
        assert len(story['story_frames'])==columns
        assert all(p['role'] in ('body','heading') for p in story['paragraphs'])
        assert ''.join(r['text'] for p in story['paragraphs'] for r in p['target_runs'])==story['target']
        assert len(story['spans'])==sum(len(u['spans']) for u in units if u['id'] in story['composed_ids'])
        if n in (14,15):
            assert sum(p['role']=='heading' for p in story['paragraphs'])>=2
        if n==13:assert story['paragraphs'][0]['indent']==0
        if n==20:assert any(p['indent']>5 for p in story['paragraphs'])


def test_real_body_columns_do_not_require_a_subheading():
    from astro_translate.column_flow import prepare_stories
    with fitz.open(BOOK/'source.pdf') as doc:
        units=fidelity.apply_editorial_composition(doc[11],saved(12))
        body=[u for u in units if all(s['size']<=12 for s in u['spans'])]
        stories=[u for u in prepare_stories(doc[11],body) if u.get('story_frames')]
        assert len(stories)==1 and len(stories[0]['story_frames'])==2


@pytest.mark.parametrize('n',[6,7])
def test_real_toc_columns(n):
    from astro_translate.contents_layout import prepare_contents
    with fitz.open(BOOK/'source.pdf') as doc:
        result=prepare_contents(doc[n-1],saved(n))
        rows=[u for u in result if u.get('toc_row')]
        assert len(rows)>=10
        for u in rows:
            assert u['column_bounds'][1] <= u['toc_number_bbox'][0]-4
            assert u['minimum_column_gap']==4
        # Two columns; rows of another size (column header with its folio)
        # scale apart from the entries of their column (title/body separate).
        assert len({u['scale_group'].split('_')[1] for u in rows})==2
        for u in rows:
            entries=[v for v in rows if v['scale_group'].split('_')[1]==u['scale_group'].split('_')[1]]
            body=max({v['size'] for v in entries},key=[v['size'] for v in entries].count)
            assert (len(u['scale_group'].split('_'))>2)==(u['size']!=body)


def test_display_reverse_source_order_partitions_runs():
    from astro_translate.editorial_rules import partition_display_units
    with fitz.open(BOOK/'source.pdf') as doc:
        units=partition_display_units(doc[2],saved(3))
        assert len(units)==3
        assert sorted(len(u['target_runs']) for u in units)==[1,1,1]
        assert all(max(s['size'] for s in u['spans'])==min(s['size'] for s in u['spans']) for u in units)


def test_published_title_protected_from_real_attribution():
    from astro_translate.editorial_rules import protected_editorial_units
    with fitz.open(BOOK/'source.pdf') as doc:
        exemptions=protected_editorial_units(doc[8],fidelity.extract_fidelity_units(doc[8]))
    assert [e['code'] for e in exemptions]==['published_work_title']
    assert exemptions[0]['unit_id']=='u0003'


def test_real_credit_exceptions_do_not_whitelist_editorial_copyright():
    from astro_translate.editorial_rules import credit_exceptions
    with fitz.open(BOOK/'source.pdf') as doc:
        exceptions=credit_exceptions(doc[3],doc[3],[])
        assert any(x['code']=='proper_name' for x in exceptions)
        assert any(x['code']=='contact' for x in exceptions)
        assert all(x['source_identical'] for x in exceptions)
        assert not any(x['text']=='Copyright' for x in exceptions)


def test_quote_retranslation_groups_real_continuation():
    from astro_translate.editorial_rules import quote_retranslation_units
    with fitz.open(BOOK/'source.pdf') as doc:
        units=quote_retranslation_units(doc[8],saved(9))
    assert len(units)==1
    assert len(units[0]['composed_ids'])==3
    assert len(units[0]['spans'])==sum(len(u['spans']) for u in saved(9)[:3])
    assert len({tuple(s['bbox']) for s in units[0]['spans']})==len(units[0]['spans'])


def test_reordered_style_spans_are_decoded_by_identity():
    from astro_translate.style_spans import encode,decode
    source=[{'text':'treasure ','style':{'weight':700}},{'text':'map','style':{'weight':300}}]
    marked,pairs=encode(source)
    assert marked=='{v1}treasure {v2}{v3}map{v4}'
    target='{v3}mapa {v4}{v1}del tesoro{v2}'
    runs=decode(target,pairs)
    assert [r['style']['weight'] for r in runs]==[300,700]
    with pytest.raises(ValueError):decode('{v1}a{v3}b{v2}{v4}',pairs)


def test_deterministic_quotes_preserve_runs():
    from astro_translate.editorial_rules import normalize_quotes
    unit={'target':'«A “B”» y "C"','target_runs':[{'text':'«A “B”» y "C"','style':{}}]}
    assert normalize_quotes(unit)['target']=='“A ‘B’” y “C”'


def test_real_toc_inline_folios_retained():
    from astro_translate.contents_layout import prepare_contents
    with fitz.open(BOOK/'source.pdf') as doc:
        for n in (6,7):
            units=prepare_contents(doc[n-1],saved(n))
            preserved=[u for u in units if u.get('preserved_inline_folios')]
            assert preserved
            for u in preserved:
                for folio in u['preserved_inline_folios']:
                    assert not u['target'].endswith(folio['text'])
                    assert not any(fitz.Rect(r).intersects(fitz.Rect(folio['bbox'])) for r in u['precise_edit_rects'])


def test_real_pale_native_underlay_is_not_dark_art():
    from astro_translate.raster_obstacles import build_region_obstacles
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[16];rect=fitz.Rect(36,422,518,601)
        strict=build_region_obstacles(page,rect)
        underlay=build_region_obstacles(page,rect,native_underlay=True)
        assert strict.mask.sum()>0
        assert underlay.mask.sum()<strict.mask.sum()*.02
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[14]
        art=build_region_obstacles(page,fitz.Rect(20,60,530,280),native_underlay=True)
        assert art.mask.sum()>0 or art.ambiguous


def test_raster_pilot_reuse_does_not_require_candidate():
    from astro_translate.production import Production
    runner=Production(BOOK)
    assert runner.reuse_pilot(1,{'pages':{'1':{'candidate_sha256':'original-retained'}}},'offline') is False


def test_paired_styles_pass_wire_validation_without_book_values():
    from astro_translate import llm
    from astro_translate.style_spans import encode
    text,pairs=encode([{'text':'treasure ','style':{}},{'text':'map','style':{}}])
    wire,maps=llm._prepare('translate_page',{'blocks':[{'id':'q','source':text,'_style_pairs':pairs}]},[],[])
    answer={'blocks':[{'id':'q','translation':'{v3}mapa {v4}{v1}del tesoro{v2}','glossary_terms_used':[]}]}
    assert llm._validate('translate_page',answer,wire,{'type':'object'})==answer
    answer['blocks'][0]['translation']='{v3}mapa {v1}del tesoro{v2}{v4}'
    with pytest.raises(llm.ValidationFailure):llm._validate('translate_page',answer,wire,{'type':'object'})


def test_pair_prompt_explicitly_overrides_only_legacy_style_order():
    from astro_translate import llm
    from astro_translate.style_spans import encode
    text,pairs=encode([{'text':'treasure ','style':{}},{'text':'map','style':{}}])
    wire,_=llm._prepare('translate_page',{'blocks':[{'id':'q','source':text,'_style_pairs':pairs}]},[],[])
    wire['_template']='Legacy instruction: keep style markers in source order.'
    prompt=llm._prompt('translate_page',wire)
    assert 'supersedes the template requirement to keep style markers in source order' in prompt
    assert prompt.index('supersedes')>prompt.index('Legacy instruction')
    assert 'supersedes' not in llm._prompt('translate_page',{'blocks':[]})


def test_pair_instruction_invalidates_only_pair_cache(tmp_path,monkeypatch):
    from astro_translate import style_spans
    from astro_translate.translation_context import response_key,block_keys
    for name in ('g','s','d','p'):(tmp_path/name).write_text(name)
    (tmp_path/'j').write_text('{}')
    cfg={'tasks':{'translate_page':{'model':'test','effort':'low','prompt':str(tmp_path/'p'),'schema':str(tmp_path/'j')}}}
    args=(cfg,tmp_path/'g',tmp_path/'s',tmp_path/'d')
    plain={'blocks':[{'id':'q','source':'Example'}]}
    text,pairs=style_spans.encode([{'text':'Example','style':{}}])
    paired={'blocks':[{'id':'q','source':text,'_style_pairs':pairs}]}
    keys=lambda payload:(response_key(*args,payload),block_keys(*args,payload))
    before_plain,before_paired=keys(plain),keys(paired)
    monkeypatch.setattr(style_spans,'PAIR_INSTRUCTION','Changed pair semantics')
    assert keys(plain)==before_plain
    assert all(a!=b for a,b in zip(keys(paired),before_paired))


def test_sparse_native_chapter_is_not_a_scan():
    from astro_translate.production import Production
    runner=Production(BOOK)
    assert not any(r['reason']=='route_C_or_no_native_text' for r in runner.inventory({'page':11,'route':'C'}))


def test_real_subset_fonts_with_empty_outlines_are_not_reused(tmp_path):
    from astro_translate.fonts import original_font, glyph_ink_coverage
    with fitz.open(BOOK/'source.pdf') as doc:
        for name,letter in [('RobotoSlab-Regular','L'),('LilitaOne','Í')]:
            choice=original_font(doc,doc[2],name,tmp_path)
            assert choice['font'].has_glyph(ord(letter),fallback=False)
            assert not glyph_ink_coverage(choice,letter)


def test_real_display_face_is_heavy_sans_despite_pdf_serif_flag():
    with fitz.open(BOOK/'source.pdf') as doc:
        units=fidelity.apply_editorial_composition(doc[2],saved(3))
    styles=[r['style'] for u in units for r in u['target_runs'] if r['style']['font']=='LilitaOne']
    assert styles and all(not s['serif'] and s['weight']>=900 for s in styles)


@pytest.fixture(scope='module')
def real_compositions():
    import tempfile
    import shutil
    from astro_translate.production import Production
    r=Production(BOOK)
    with tempfile.TemporaryDirectory(prefix='phase2c-test-',dir=BOOK/'work') as temp:
        folder=Path(temp);results={}
        corrections=folder/'composition_corrections.json'
        shutil.copyfile(FIXTURES/'composition_corrections.json',corrections)
        for n in (3,6,7,9,14,15,17,19):
            payload=json.loads((FIXTURES/f'p{n:04d}-translation.json').read_text())
            fit=fidelity.reinsert_fidelity(BOOK,payload,r.glossary,r.style,r.protected,
                config=r.config,artifact_dir=folder,phase='production',corrections_path=corrections)
            assert fit['fit_complete'],fit.get('failures')
            qa=fidelity.fidelity_qa(BOOK,fit,r.glossary,r.protected,artifact_dir=folder,update_manifest=False)
            results[n]=(fit,qa)
        yield results


def test_real_composition_fidelity_and_fixed_folios(real_compositions):
    for n,(fit,qa) in real_compositions.items():
        assert qa['non_text_pixel_diff']==0 and qa['numbers_equal'] and qa['images_equal']
        assert qa['typography_audit']['ok'],(n,qa['typography_audit'])
        if n in (15,17):
            # Phase 2E recognizes these historical pull quotes as fragmented.
            # Preserve all mechanical assertions above; do not manufacture a
            # new approval for its previously reviewed saved fragments.
            assert not qa['local_fidelity_ok']
            # p17 also moved the bold from «forms» to «más antiguas» (quote u0001):
            # a real displaced emphasis, reported since the 2026-11-09 check.
            expected={'fragmented_display_quote'}|({'inline_style'} if n==17 else set())
            assert {issue['code'] for issue in qa['text_quality_issues']}==expected
            assert [i['unit_id'] for i in qa['text_quality_issues'] if i['code']=='inline_style']==(['p0017_u0001_quote'] if n==17 else [])
        else:
            assert qa['local_fidelity_ok'],(n,qa['text_quality_issues'])
        if n==9:assert fit['correction_proofs']
        if n in (6,7):
            assert qa['toc_folio_proof']['rows'] and qa['toc_folio_proof']['ok']
        if n in (14,15,17):
            story=next(u for u in fit['blocks'] if u.get('story_frames'))
            assert .75<=story['scale']<=1
            assert len(story['story_placements'])==len(story['story_frames'])
            assert not story['story_placements'][-1]['more']


def test_real_safe_pairs_including_shading_and_protected_folios(real_compositions):
    from astro_translate import safe_vlm
    for n,(fit,qa) in real_compositions.items():
        fonts=json.loads(Path(fit['font_audit']).read_text())
        pair=safe_vlm.build_safe_pair(Path(fit['artifact_dir'])/'safe-book',BOOK/'source.pdf',fit['output'],n,n,
                                    trusted_text_font_files=[f['chosen']['path'] for f in fonts])
        assert safe_vlm.verify_safe_pair(pair)['ready_for_native_judge']
        assert all(pair.report[k]['protected_chars_without_paint_bounds']==0 for k in ('before','after'))
        assert all(not pair.report[k]['remaining_non_text_paint_operations'] for k in ('before','after'))


def test_real_story_headings_never_split_between_frames(real_compositions):
    for n,(fit,_) in real_compositions.items():
        with fitz.open(fit['output']) as doc:
            for u in fit['blocks']:
                if not u.get('story_frames'):continue
                frame_texts=[fidelity._normalized(doc[n-1].get_text(clip=fitz.Rect(f)+(-1,-1,1,1))) for f in u['story_frames']]
                for p in u['paragraphs']:
                    if p['role']!='heading':continue
                    text=fidelity._normalized(''.join(r['text'] for r in p['target_runs']))
                    assert any(text in t for t in frame_texts),(n,p['unit_id'])
