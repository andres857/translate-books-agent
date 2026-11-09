"""D1–D3: real saved pilot cases, offline; compose only temporary proofs."""
import copy
import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen

from astro_translate import fidelity, fonts, llm
from astro_translate.retranslation import validate_units
from astro_translate.text_quality import page_issues
from test_caption_growth import saved_units as caption_saved_units

BOOK = Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
PILOT = BOOK/'work/pilot'
sys.path.insert(0, str(PILOT))
from local_layout import composition_context


def saved_units(page):
    if page.number != 4:
        return caption_saved_units(page)
    from local_layout import book_composition
    saved=json.loads((PILOT/'p0005-translation.json').read_text())
    canonical={u['id']:u for u in fidelity.extract_fidelity_units(page)}
    units=[]
    for supplied in saved['blocks']:
        own=canonical[supplied['id'].removeprefix('p0005_')]
        assert own['spans']==supplied['spans'] and own['source']==supplied['source']
        units.append({**own,'target':supplied['target'],'target_runs':supplied.get('target_runs',
                      [dict(text=supplied['target'],style=own['style'])])})
    return fidelity.finalize_editorial_composition(page,book_composition(page,units))


def test_d1a_real_title_iacute_is_precomposed_and_above_capital(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        units = saved_units(doc[4])
        unit = next(u for u in units if u['id']=='u0000')
        mapping, _ = fonts.select_group_fonts(doc, doc[4], [unit], tmp_path)
        run = next(r for r in unit['target_runs'] if 'Í' in r['text'])
        choice = mapping[(unit['scale_group'], tuple(sorted(run['style'].items())))]
        with TTFont(choice['path']) as font:
            assert font.getBestCmap()[0xCD]=='Iacute'
            glyphs=font.getGlyphSet()
            def bounds(char):
                pen=BoundsPen(glyphs);glyphs[font.getBestCmap()[ord(char)]].draw(pen)
                return pen.bounds
            plain, accented = bounds('I'), bounds('Í')
            assert accented[3] > plain[3]+100
            assert accented[:2]==plain[:2]
        assert fonts.original_text_roundtrip(choice, 'Í í fi fl ZODÍACO zodíaco')


def test_d1b_real_capricorn_cannot_migrate_into_left_number_column():
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        units = saved_units(doc[4]); u=next(u for u in units if u['source']=='Capricorn')
        boxes=fidelity._candidate_boxes(doc[4],u,units,fidelity.immutable_boxes(doc[4], ()))
        assert boxes
        assert all(b.x0==pytest.approx(u['bbox'][0],abs=.01) for b in boxes)
        assert max(b.x1 for b in boxes)>u['bbox'][2]


def test_d1c_real_dates_reserve_gap_to_protected_folios_and_share_scale(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        page=doc[4];units=saved_units(page)
        dates=[u for u in units if 'November' in u['source'] or 'December' in u['source']]
        guards=fidelity.immutable_boxes(page, ())
        for u in dates:
            right=min(g.x0 for g in guards if g.x0>=u['bbox'][2] and g.y0<u['bbox'][3] and g.y1>u['bbox'][1])
            boxes=fidelity._candidate_boxes(page,u,units,guards)
            assert boxes and all(b.x1<=right-4 for b in boxes)
        siblings=[u for u in units if u['scale_group']==dates[0]['scale_group']]
        assert len(siblings)==7
        mapping,_=fonts.select_group_fonts(doc,page,siblings,tmp_path)
        mask=fidelity._vector_mask(page)
        for factor in [p/100 for p in range(100,74,-1)]:
            if all(any(fidelity._test_fit(page,u,b,factor,mapping,tmp_path,mask)[0]
                       for b in fidelity._candidate_boxes(page,u,units,guards)) for u in siblings):
                break
        else:pytest.fail('No uniform date scale >= 0.75')
        assert factor<1


@pytest.mark.parametrize('number',[28,34,60])
def test_d2_real_running_header_rejects_unaccented_saved_and_wire_output(number):
    saved=json.loads((PILOT/f'p{number:04d}-translation.json').read_text())
    header=next(u for u in saved['blocks'] if u['source']=='The Zodiac Signs')
    # Current saved wording is already accented. Reproduce the reported lexical
    # failure by deleting only that accent, leaving real ids/styles/geometry.
    bad=copy.deepcopy(header);bad['target']=bad['target'].replace('í','i')
    for r in bad['target_runs']:r['text']=r['text'].replace('í','i')
    assert 'zodiac_spelling' in validate_units({'blocks':[bad]})[bad['id']][0]
    with pytest.raises(llm.ValidationFailure,match='zodiac_spelling'):
        llm._validate('translate_page',{'blocks':[{'id':bad['id'],'translation':bad['target']}]},
                      {'blocks':[{'id':bad['id'],'source':bad['source']}]},{'type':'object'})
    assert not validate_units({'blocks':[header]})


def test_d2_real_header_candidate_is_checked_even_when_saved_target_is_correct():
    header=json.loads((PILOT/'p0028-translation.json').read_text())['blocks'][0]
    # Use the actual AstroSlab replacement: its name matches the legacy symbol
    # regex, so PDF spelling checks must verify its hash before inspecting it.
    fit=json.loads((PILOT/'p0028-fit.json').read_text())
    header={**header, 'font_runs':fit['blocks'][0]['font_runs']}
    choice=json.loads((PILOT/'p0028-fonts.json').read_text())[0]['chosen']
    path=Path(choice['path'])
    with fitz.open(BOOK/'source.pdf') as source, fitz.open() as candidate:
        page=candidate.new_page(width=source[27].rect.width,height=source[27].rect.height)
        page.insert_htmlbox(fitz.Rect(header['bbox'])+(0,0,80,3),'Los signos del zodiaco',
                            css=f'@font-face{{font-family:proof;src:url("{path.name}")}} body{{font-family:proof;font-size:12pt;margin:0}}',
                            archive=fitz.Archive(str(path.parent)),scale_low=1)
        issues=page_issues([header],source_page=source[27],candidate_page=page)
        assert any(i['code']=='zodiac_spelling' and i['unit_id']==header['id'] for i in issues)


def test_d3_real_p128_standalone_mixed_weight_heading_keeps_paragraph_break():
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        unit=next(u for u in saved_units(doc[127]) if u['source'].startswith('POWER AND'))
        paragraphs=unit.get('paragraphs',[])
        assert len(paragraphs)==2
        assert ''.join(r['text'] for r in paragraphs[0]['target_runs']).strip()=='PODER Y TRANSFORMACIÓN'
        assert paragraphs[1]['target_runs'][0]['text'].lstrip().startswith('Rebajado')
        assert not unit.get('run_in_heading')


@pytest.mark.parametrize('number',[100,101,141])
def test_d3_real_run_in_headings_have_body_on_original_line(number):
    from astro_translate.run_in_layout import _run_in, _source_baselines
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        units=saved_units(doc[number-1])
        entries=[u for u in units if (_run_in(u) if number!=100 else u['id'] in ('u0001','u0002','u0003','u0004'))]
        assert entries
        assert all(not u.get('paragraphs') for u in entries)
        for u in entries:
            a,b=_source_baselines(doc[number-1],u['spans'][:2])
            assert a==pytest.approx(b,abs=.1)


def test_d3_real_p128_body_renders_below_heading(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc, composition_context(), fitz.open() as proof:
        unit=next(u for u in saved_units(doc[127]) if u['source'].startswith('POWER AND'))
        mapping,_=fonts.select_group_fonts(doc,doc[127],[unit],tmp_path)
        html,css,_=fidelity._html_for(unit,mapping,tmp_path,.9)
        page=proof.new_page(width=550,height=700)
        spare,_=page.insert_htmlbox(fitz.Rect(unit['bbox'])+(0,0,24,20),html,css=css,
                                    archive=fitz.Archive(str(tmp_path)),scale_low=1)
        assert spare>=0
        words=page.get_text('words')
        title=next(w for w in words if w[4]=='TRANSFORMACIÓN')
        body=next(w for w in words if w[4]=='Rebajado')
        assert body[1]>title[3]


def test_d3_real_p180_raised_inline_title_is_not_a_standalone_heading():
    from astro_translate.run_in_layout import separate_standalone_heading
    saved=json.loads((PILOT/'p0180-translation.json').read_text())
    unit=next(u for u in saved['blocks'] if u['id']=='p0180_u0006')
    with fitz.open(BOOK/'source.pdf') as doc:
        assert not separate_standalone_heading(doc[179],copy.deepcopy(unit)).get('source_paragraph_boundary')
