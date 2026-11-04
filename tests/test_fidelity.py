import pymupdf as f
from astro_translate.fidelity import extract_fidelity_units

def test_numbers_in_prose_included_but_symbols_not():
    d=f.open();p=d.new_page();p.insert_text((40,50),'There are 12 houses');p.insert_text((40,90),'MC')
    units=extract_fidelity_units(p)
    assert len(units)==1 and '12' in units[0]['source'];d.close()

def test_inline_weight_one_container_heading_body_separate():
    d=f.open();p=d.new_page();p.insert_text((40,50),'The ',fontname='helv',fontsize=12);p.insert_text((62,50),'chart',fontname='hebo',fontsize=12)
    p.insert_text((40,100),'Heading',fontname='hebo',fontsize=10);p.insert_text((40,114),'The body explains meaning.',fontname='helv',fontsize=10)
    units=extract_fidelity_units(p)
    assert any(len(u['style_runs'])==2 and 'chart' in u['source'] for u in units)
    assert not any('Heading' in u['source'] and 'body' in u['source'] for u in units);d.close()

def test_group_font_selection_uses_all_translated_characters(tmp_path):
    from astro_translate.fonts import covers,required_characters,fallback_font
    font=f.Font('helv');assert covers(font,'Aries')
    style={'font':'SohoPro-LightCondensed','size':9,'color':0,'weight':300,'bold':False,'italic':False,'serif':True,'width':75}
    choice=fallback_font(style,tmp_path)
    assert covers(choice['font'],'Géminis, ÍZODÍACO, ñ¿¡')
    assert choice['axes']['wdth']==75 and choice['axes']['wght']==300

def test_prose_numbers_restore_and_styles_render_transactionally(tmp_path):
    from astro_translate.fidelity import reinsert_fidelity
    from astro_translate.core import approve_glossary
    book=tmp_path/'book';book.mkdir();d=f.open();p=d.new_page();p.insert_text((40,50),'There are 12 houses',fontsize=11);p.insert_text((40,90),'MC',fontsize=11);d.save(book/'source.pdf');d.close()
    g=tmp_path/'g.csv';g.write_text('source,target,tgt_lng\nhouses,casas,es\n');s=tmp_path/'s.md';s.write_text('Spanish');approve_glossary(book,g,s,'synthetic')
    with f.open(book/'source.pdf') as d:unit=extract_fidelity_units(d[0])[0]
    report=reinsert_fidelity(book,{'page':1,'blocks':[{**unit,'target':'Hay 12 casas'}]},g,s)
    assert report['fit_complete']
    with f.open(report['output']) as d:
        assert '12' in d[0].get_text() and 'MC' in d[0].get_text()
    import pytest
    from astro_translate.core import Blocked
    with pytest.raises(Blocked,match='numeric'):reinsert_fidelity(book,{'page':1,'blocks':[{**unit,'target':'Hay 13 casas'}]},g,s)

def test_fallback_ligatures_extract_original_latin_characters(tmp_path):
    from astro_translate.fonts import fallback_font
    from pathlib import Path
    style={'font':'SohoPro-LightCondensed','size':12,'color':0,'weight':300,'bold':False,'italic':False,'serif':True,'width':75}
    choice=fallback_font(style,tmp_path)
    d=f.open();p=d.new_page();text='significativo afirmación reflejo ÍZODÍACO'
    css=f"@font-face {{font-family:test;src:url({Path(choice['path']).name});}} body {{font-family:test;font-size:12pt;}}"
    p.insert_htmlbox(f.Rect(30,30,500,100),text,css=css,archive=f.Archive(str(tmp_path)))
    assert p.get_text().strip()==text
    assert 'AstroSerif' in p.get_text('dict')['blocks'][0]['lines'][0]['spans'][0]['font']
    d.close()

def test_display_paragraph_candidates_keep_opening_quote_anchor():
    from astro_translate.fidelity import _candidate_boxes
    d=f.open();p=d.new_page();p.insert_text((50,100),'The birth chart is a holistic image',fontsize=19)
    unit=extract_fidelity_units(p)[0];unit['kind']='paragraph'
    boxes=_candidate_boxes(p,unit,[unit],[])
    assert min(b.y0 for b in boxes)>=unit['bbox'][1]-1.01
    d.close()

def test_editorial_composition_preserves_columns_indent_and_payloads():
    import copy,json
    from pathlib import Path
    from astro_translate.fidelity import apply_editorial_composition
    book=Path('books/astrology-wisdom-stars')
    with f.open(book/'source.pdf') as d:
        units=extract_fidelity_units(d[14])
        payload=json.loads((book/'work/fidelity/p0015-translation.json').read_text())
        for u,b in zip(units,payload['blocks']):u.update(target=b['target'],target_runs=b['target_runs'])
        before=copy.deepcopy(units);result=apply_editorial_composition(d[14],units)
        assert units==before
        left=next(u for u in result if u.get('composed_ids')==['u0002','u0003','u0004'])
        assert [p['indent'] for p in left['paragraphs']]==[0,8.5,8.5]
        assert abs(left['column_bounds'][1]-189.08467)<.001
        assert len(left['target_runs'])==sum(len(p['target_runs']) for p in left['paragraphs'])
        right=next(u for u in result if u.get('composed_ids')==['u0007','u0008'])
        assert [p['indent'] for p in right['paragraphs']]==[0,8.5]
        assert right['column_bounds'][0]-left['column_bounds'][1]>10.97


def test_editorial_names_bold_without_changing_other_styles():
    import json
    from pathlib import Path
    from astro_translate.fidelity import apply_editorial_composition
    book=Path('books/astrology-wisdom-stars')
    with f.open(book/'source.pdf') as d:
        units=extract_fidelity_units(d[6]);payload=json.loads((book/'work/fidelity/p0007-translation.json').read_text())
        for u,b in zip(units,payload['blocks']):u.update(target=b['target'],target_runs=b['target_runs'])
        result=apply_editorial_composition(d[6],units)
    modified=[u for u in result if u.get('editorial_override')]
    assert len(modified)==23
    assert all(r['style']['weight']==700 for u in modified for r in u['target_runs'])
    assert all(u['style']['color']==v['style']['color'] for u,v in zip(result,units))
    assert next(u for u in result if u['source'].startswith('March'))['style']['weight']==300

def test_artifact_folder_rejects_escape_and_source_tree(tmp_path):
    import pytest
    from astro_translate.fidelity import _artifact_folder
    from astro_translate.core import Blocked
    book=tmp_path/'book'
    assert _artifact_folder(book)==book/'work/fidelity'
    assert _artifact_folder(book,book/'work/pilot')==book/'work/pilot'
    for destination in (book,book/'work',book/'work/../../escape'):
        with pytest.raises(Blocked):_artifact_folder(book,destination)

def test_body_column_candidates_preserve_gutter_and_vertical_origin():
    from pathlib import Path
    from astro_translate.fidelity import _candidate_boxes
    with f.open(Path('books/astrology-wisdom-stars/source.pdf')) as d:
        units=extract_fidelity_units(d[14]);u=units[2]
        u['column_bounds']=[35.71659851074219,189.0846710205078]
        boxes=_candidate_boxes(d[14],u,units,[])
        assert all(abs(b.x0-u['column_bounds'][0])<.001 and abs(b.x1-u['column_bounds'][1])<.001 for b in boxes)
        assert all(abs(b.y0-u['bbox'][1])<.001 for b in boxes)


def test_printer_identifier_is_protected_without_hiding_date_prose():
    from astro_translate.fidelity import immutable_span
    assert immutable_span({"source":"001–310781–Sep/2018","font":"SohoPro"})
    assert not immutable_span({"source":"March 21–April 20","font":"SohoPro"})

def test_qa_identifies_rotated_editorial_text_not_covered_by_extractor():
    from astro_translate.fidelity import omitted_rotated_text
    with f.open('books/astrology-wisdom-stars/source.pdf') as d:
        omitted=omitted_rotated_text(d[62])
        assert {u['text'] for u in omitted}=={'Neptune','Uranus','Pluto'}
        assert omitted_rotated_text(d[14])==[]

def test_local_font_correction_preserves_payload_and_sans_bold(tmp_path):
    import copy
    from astro_translate.fidelity import apply_editorial_composition
    from astro_translate.fonts import fallback_font,covers
    style={'font':'DINNextLTPro-Bold','size':10,'color':0,'weight':700,'bold':True,'italic':False,'serif':True,'width':100}
    units=[{'style':style,'target_runs':[{'text':'Español','style':style}],'kind':'label','bbox':[0,0,10,10]}];before=copy.deepcopy(units)
    class Page:
        number=4
        def get_text(self,*args):return {'blocks':[]}
    corrected=apply_editorial_composition(Page(),units)
    assert units==before and not corrected[0]['style']['serif']
    font=fallback_font(corrected[0]['style'],tmp_path)
    assert 'AstroSans' in font['name'] and font['axes']['wght']==700
    assert covers(font['font'],'Español ¿Í?')
    heavy={**style,'font':'SohoPro-Heavy','weight':400,'bold':False}
    fixed=apply_editorial_composition(Page(),[{'style':heavy,'target_runs':[{'text':'Texto','style':heavy}],'kind':'label','bbox':[0,0,10,10]}])
    assert fixed[0]['style']['weight']==900 and fixed[0]['style']['serif']

def test_english_in_verified_generated_font_is_not_hidden_as_symbol(tmp_path):
    import hashlib
    from pathlib import Path
    from astro_translate.fonts import fallback_font
    from astro_translate.fidelity import verified_editorial_text
    from astro_translate.language import residual_english
    style={'font':'SohoPro-Light','size':12,'color':0,'weight':300,'bold':False,'italic':False,'serif':True,'width':100}
    choice=fallback_font(style,tmp_path)
    d=f.open();p=d.new_page()
    english='The planets move through the houses and reveal the meaning of your birth chart.'
    css=f"@font-face {{font-family:test;src:url({Path(choice['path']).name});}} body {{font-family:test;font-size:12pt;}}"
    p.insert_htmlbox(f.Rect(30,30,500,160),english,css=css,archive=f.Archive(str(tmp_path)))
    proof={'blocks':[{'font_runs':[{'sha256':choice['sha256']}]}]}
    editorial=verified_editorial_text(d,p,proof)
    assert residual_english(editorial,[])
    assert verified_editorial_text(d,p,{'blocks':[{'font_runs':[{'sha256':'wrong'}]}]})==[]
    d.close()

def test_drop_cap_uses_translated_initial_and_credit_fragments_keep_order():
    import json,copy
    from pathlib import Path
    from astro_translate.fidelity import apply_editorial_composition
    root=Path('books/astrology-wisdom-stars')
    with f.open(root/'source.pdf') as d:
        for number in [6,10]:
            payload=json.loads((root/f'work/pilot/p{number:04d}-translation.json').read_text())
            units=[]
            for own,block in zip(extract_fidelity_units(d[number-1]),payload['blocks']):units.append({**own,'target':block['target'],'target_runs':block['target_runs']})
            original=copy.deepcopy(units);composed=apply_editorial_composition(d[number-1],units)
            assert units==original
            if number==10:
                cap=next(u for u in composed if u.get('drop_cap'))
                assert cap['target_runs'][0]['text']=='D'
                assert cap['target_runs'][0]['style']['size']>30
                assert ''.join(r['text'] for r in cap['paragraphs'][0]['target_runs']).startswith('Durante')
                assert not any(u['source']=='F' for u in composed)
            else:
                joined=next(u for u in composed if u.get('composed_ids')==['u0025','u0026','u0027','u0028'])
                assert ''.join(r['text'] for r in joined['target_runs'])==joined['target']

def test_index_entries_keep_reference_sequences_and_source_columns():
    import json,re
    from pathlib import Path
    from astro_translate.fidelity import apply_editorial_composition
    book=Path('books/astrology-wisdom-stars')
    payload=json.loads((book/'work/pilot/p0250-translation.json').read_text())
    with f.open(book/'source.pdf') as d:
        units=[{**u,'target':b['target'],'target_runs':b['target_runs']} for u,b in zip(extract_fidelity_units(d[249]),payload['blocks'])]
        composed=apply_editorial_composition(d[249],units)
    assert len(composed)==101
    assert re.findall(r'\d+',' '.join(u['target'] for u in units))==re.findall(r'\d+',' '.join(u['target'] for u in composed))
    assert all(''.join(r['text'] for r in u['target_runs'])==u['target'] for u in composed)


def test_transparency_group_survives_transactional_reinsertion(tmp_path):
    from astro_translate.fidelity import reinsert_fidelity
    from astro_translate.core import approve_glossary
    book=tmp_path/'book';book.mkdir();d=f.open();p=d.new_page();p.insert_text((40,50),'The houses',fontsize=11)
    d.xref_set_key(p.xref,'Group','<</S/Transparency/CS/DeviceCMYK/I true>>');d.save(book/'source.pdf');d.close()
    g=tmp_path/'g.csv';g.write_text('source,target,tgt_lng\nhouses,casas,es\n');s=tmp_path/'s.md';s.write_text('Spanish');approve_glossary(book,g,s,'synthetic')
    with f.open(book/'source.pdf') as d:unit=extract_fidelity_units(d[0])[0]
    report=reinsert_fidelity(book,{'page':1,'blocks':[{**unit,'target':'Las casas'}]},g,s)
    assert report['fit_complete']
    with f.open(report['output']) as d:assert '/DeviceCMYK' in d.xref_get_key(d[0].xref,'Group')[1]

def test_composition_override_binds_source_hash_styles_and_protected_values(tmp_path):
    import json,copy
    from astro_translate.fidelity import apply_composition_overrides,composition_sources_sha256
    from astro_translate.core import sha256,Blocked
    import pytest
    book=tmp_path/'book';(book/'work').mkdir(parents=True)
    d=f.open();p=d.new_page();p.insert_text((40,40),'There are 12 houses');d.save(book/'source.pdf');d.close()
    with f.open(book/'source.pdf') as d:u=extract_fidelity_units(d[0])[0]
    u.update(target='Hay 12 casas',target_runs=[{'text':'Hay 12 casas','style':u['style']}]);units=[u]
    g=tmp_path/'g.csv';g.write_text('source,target\nhouses,casas\n');s=tmp_path/'style.md';s.write_text('Spanish')
    override={'page':1,'source_sha256':sha256(book/'source.pdf'),'glossary_sha256':sha256(g),'style_sha256':sha256(s),'do_not_translate_sha256':None,'composition_sha256':composition_sources_sha256(units),'blocks':copy.deepcopy(units),'provenance':{'test':'unit-only mocked call record'}}
    path=book/'work/override.json';path.write_text(json.dumps(override))
    assert apply_composition_overrides(book,units,path,1,g,s,None)[0]['target']=='Hay 12 casas'
    override['blocks'][0]['target']='Hay 13 casas';override['blocks'][0]['target_runs'][0]['text']='Hay 13 casas';path.write_text(json.dumps(override))
    with pytest.raises(Blocked,match='protected'):apply_composition_overrides(book,units,path,1,g,s,None)
    override['source_sha256']='changed';path.write_text(json.dumps(override))
    with pytest.raises(Blocked,match='hashes'):apply_composition_overrides(book,units,path,1,g,s,None)

def test_mc_continuation_stays_in_paragraph_with_original_terminal_period():
    import json
    from pathlib import Path
    from astro_translate.fidelity import apply_editorial_composition
    book=Path('books/astrology-wisdom-stars');payload=json.loads((book/'work/pilot/p0146-translation.json').read_text())
    with f.open(book/'source.pdf') as d:
        units=[{**u,'target':v['target'],'target_runs':v['target_runs']} for u,v in zip(extract_fidelity_units(d[145]),payload['blocks'])]
        result=apply_editorial_composition(d[145],units)
    assert len(result)==14 and not any(u['id']=='u0022' for u in result)
    paragraph=next(u for u in result if u['id']=='u0021')
    assert paragraph['source'].endswith('her MC.')
    assert paragraph['target'].endswith('su MC.')
    assert ''.join(r['text'] for r in paragraph['target_runs'])==paragraph['target']
    assert paragraph['relocated_punctuation'][0]['source']=='.'
