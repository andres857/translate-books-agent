"""Phase 2F: saved real inputs only; no model or production writes."""
import json
from copy import deepcopy
from pathlib import Path
import pymupdf as fitz
import pytest
from astro_translate import fidelity
from astro_translate.production import Production

BOOK=Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
FIXTURES=Path(__file__).parent/'fixtures/phase2f'

def saved(n):
    return json.loads((FIXTURES/f'p{n:04d}-translation.json').read_text())

@pytest.mark.parametrize('n',[42,46,50,54])
def test_real_sign_rows_uniform_preserved_glyph_and_styles(n,tmp_path,monkeypatch):
    r=Production(BOOK)
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    fit=fidelity.reinsert_fidelity(BOOK,saved(n),r.glossary,r.style,r.protected,
        artifact_dir=tmp_path,corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete'],fit.get('failures')
    rows=[u for u in fit['blocks'] if u.get('row_parent_id')]
    assert len(rows)==7
    assert len({u['scale'] for u in rows})==1
    assert min(u['scale'] for u in rows)>=.75
    assert all(max(s['bbox'][1] for s in u['spans'])-min(s['bbox'][1] for s in u['spans'])<.5 for u in rows)
    qa=fidelity.fidelity_qa(BOOK,fit,r.glossary,r.protected,artifact_dir=tmp_path,update_manifest=False)
    assert qa['local_fidelity_ok'],qa.get('text_quality_issues')
    assert qa['non_text_pixel_diff']==0 and qa['immutable_spans_equal']
    with fitz.open(fit['output']) as candidate:
        for row in rows:
            spans=[s for b in candidate[n-1].get_text('dict',clip=fitz.Rect(row['edit_bbox']))['blocks']
                   for line in b.get('lines',[]) for s in line['spans'] if s['text'].strip()]
            assert spans
            assert max(s['origin'][1] for s in spans)-min(s['origin'][1] for s in spans)<.1


def test_field_detection_is_structural_not_large_font_only():
    from astro_translate.structured_layout import prepare_saved_field_rows
    with fitz.open(BOOK/'source.pdf') as doc:
        units=[{**u,'id':u['id'].removeprefix('p0042_')} for u in saved(42)['blocks']]
        unit=next(u for u in fidelity.apply_editorial_composition(doc[41],units) if u['id']=='u0006')
        unit=deepcopy(unit);unit['size']=11
        for s in unit['spans']:s['size']=11;s['style']['size']=11
        for r in unit['target_runs']:r['style']['size']=11
        assert len(prepare_saved_field_rows(doc[41],[unit]))==7

@pytest.mark.parametrize('n,count',[(39,4),(43,4),(47,3),(51,4),(55,4)])
def test_constellation_callouts_are_spatial_paragraphs(n,count):
    # Each expected caption has one spatial column; original PDF blocks can
    # interleave unrelated guide labels (real Gemini/Cancer regressions).
    from astro_translate.figure_captions import caption_units
    with fitz.open(BOOK/'source.pdf') as doc:
        units=fidelity.extract_fidelity_units(doc[n-1])
        caps=caption_units(doc[n-1],units)
        assert len(caps)==count
        for u in caps:
            assert u['style']['italic']
            assert u['alignment']=='center'
            assert fitz.Rect(u['bbox']).width<110
            assert max(s['bbox'][0] for s in u['spans'])-min(s['bbox'][0] for s in u['spans'])<35


def test_saved_cross_callout_translation_requires_retranslation(tmp_path,monkeypatch):
    r=Production(BOOK);monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    fit=fidelity.reinsert_fidelity(BOOK,saved(43),r.glossary,r.style,r.protected,
        artifact_dir=tmp_path,corrections_path=tmp_path/'absent.json')
    assert not fit['fit_complete']
    assert fit['failures'][0]['reason']=='caption_retranslation_required'


def test_caption_plan_offline_preserves_unaffected_bytes(tmp_path,monkeypatch):
    import importlib.util
    from astro_translate import llm
    from astro_translate.resegmentation import block_slices
    path=BOOK.parents[1]/'scripts/prepare_phase2f_retranslations.py'
    spec=importlib.util.spec_from_file_location('phase2f_prepare',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    runner=Production(BOOK);runner.folder=FIXTURES
    monkeypatch.setattr(llm,'run_task',lambda *a,**k:pytest.fail('offline called LLM'))
    plan=mod.build_plan(runner,'41-60')
    assert [x['page'] for x in plan['pages']]==[43,47]
    assert [len(x['jobs']) for x in plan['pages']]==[3,2]
    for record in plan['pages']:
        raw=(FIXTURES/f'p{record["page"]:04d}-translation.json').read_bytes()
        # Structural replacement fixture only, no translated text authored.
        replacements={j['request']['id']:{**j['unit'],'id':j['request']['id']} for j in record['jobs']}
        new=mod.replace_bytes(raw,record,replacements)
        oldparts,newparts=block_slices(raw),block_slices(new)
        for key,(a,b) in oldparts.items():
            if key in newparts:
                c,d=newparts[key];assert new[c:d]==raw[a:b]

@pytest.mark.parametrize('n',[49,53,57])
def test_trait_burst_colors_are_source_span_colors(n,tmp_path):
    from astro_translate.fonts import select_group_fonts
    from astro_translate.text_quality import repair_composition,source_emphasis
    from astro_translate.fidelity_audit import audit_rendered_styles
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[n-1]
        units=[{**u,'id':u['id'].removeprefix(f'p{n:04d}_')} for u in saved(n)['blocks']]
        units=fidelity.finalize_editorial_composition(page,fidelity.apply_editorial_composition(page,units))
        units=[repair_composition(u,emphasis=source_emphasis(page,u)) for u in units]
        # All mixed body/header boxes (including the colored bursts) are
        # rendered from saved runs. Text color cannot be inferred from a panel.
        selected=[u for u in units if len({s['color'] for s in u['spans']})>1]
        assert selected
        fonts,_=select_group_fonts(doc,page,selected,tmp_path)
        for u in selected:
            assert {r['style']['color'] for r in u['target_runs']}=={s['color'] for s in u['spans']}
            content,css,font_runs=fidelity._html_for(u,fonts,tmp_path,1)
            rendered=fitz.open();target=rendered.new_page(width=1000,height=1000)
            box=fitz.Rect(20,20,980,980)
            spare,_=target.insert_htmlbox(box,content,css=css,archive=fitz.Archive(str(tmp_path)),scale_low=1)
            assert spare>=0
            audit=audit_rendered_styles(target,{'blocks':[{**u,'edit_bbox':list(box),'font_runs':font_runs}]})
            assert audit['ok'],audit
            rendered.close()


def test_callout_actual_font_is_italic_and_centered(tmp_path):
    from astro_translate.fonts import select_group_fonts
    from astro_translate.figure_captions import caption_units
    from astro_translate.caption_growth import insert_anchored
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[50];units=fidelity.extract_fidelity_units(page)
        targets={u['id'].removeprefix('p0051_'):u for u in saved(51)['blocks']}
        caps=[{**u,'target':targets[u['id']]['target'],'target_runs':targets[u['id']]['target_runs']} for u in caption_units(page,units)]
        fonts,_=select_group_fonts(doc,page,caps,tmp_path)
        for u in caps:
            assert all(r['style']['italic'] for r in u['target_runs'])
            content,css,_=fidelity._html_for(u,fonts,tmp_path,1)
            assert 'text-align:center' in content
            out=fitz.open();p=out.new_page(width=600,height=700)
            p.insert_htmlbox(fitz.Rect(10,10,590,690),content,css=css,archive=fitz.Archive(str(tmp_path)),scale_low=1)
            spans=[s for b in p.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
            assert spans and all(s['flags']&2 for s in spans)
            out.close()


def test_neighboring_callouts_with_distinct_guides_never_merge():
    from astro_translate.figure_captions import caption_units
    with fitz.open() as doc:
        p=doc.new_page(width=300,height=300)
        p.insert_text((30,100),'First label',fontsize=9,fontname='tiit')
        p.insert_text((30,110),'Next label',fontsize=9,fontname='tiit')
        for y in (97,107):p.draw_line((70,y),(150,y+30),width=.3)
        units=fidelity.extract_fidelity_units(p,merge_captions=False)
        caps=caption_units(p,units)
        assert len(caps)==2


def test_field_wrapped_value_keeps_full_saved_run_without_word_guessing():
    from astro_translate.structured_layout import prepare_saved_field_rows
    with fitz.open(BOOK/'source.pdf') as doc:
        units=[{**u,'id':u['id'].removeprefix('p0042_')} for u in saved(42)['blocks']]
        unit=deepcopy(next(u for u in fidelity.apply_editorial_composition(doc[41],units) if u['id']=='u0006'))
        last=unit['spans'].pop();a,b=deepcopy(last),deepcopy(last)
        a['source'],b['source']=last['source'].split(', ',1)
        a['source']+=',';b['bbox']=[b['bbox'][0],b['bbox'][1]+24,b['bbox'][2],b['bbox'][3]+24]
        unit['spans'] += [a,b]
        rows=prepare_saved_field_rows(doc[41],[unit])
        assert len(rows)==7 and len(rows[-1]['spans'])==2
        assert rows[-1]['target']==unit['target_runs'][-1]['text']
