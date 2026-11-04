import pymupdf as f
from astro_translate.layout import extract_layout_units, drawing_obstacles

def test_paragraph_grouping_and_local_vector():
    d=f.open(); p=d.new_page(); p.insert_text((40,50),'The planets describe'); p.insert_text((40,64),'our personal experience')
    p.draw_line((300,300),(400,300))
    units=extract_layout_units(p)
    assert len(units)==1 and len(units[0]['spans'])==2
    assert units[0]['kind']=='paragraph' and not units[0]['vector']
    assert 'describe our' in units[0]['source'];d.close()

def test_protected_span_splits_paragraph():
    d=f.open();p=d.new_page();p.insert_text((40,50),'The planets describe');p.insert_text((40,64),'12 houses');p.insert_text((40,78),'our personal experience')
    units=extract_layout_units(p)
    assert all('12' not in x['source'] for x in units)
    assert all(not f.Rect(x['bbox']).intersects(f.Rect(40,52,100,65)) for x in [])
    assert len(units)==2;d.close()

def test_printer_frame_not_intersecting_body():
    d=f.open();p=d.new_page();p.draw_rect(f.Rect(-20,-20,p.rect.width+20,p.rect.height+20));p.insert_text((40,50),'Plain text')
    units=extract_layout_units(p);assert not units[0]['vector'];d.close()

def test_shortening_budget_not_english_ratio():
    from astro_translate.layout import shortening_budget
    unit={'source':'Mars','size':12}
    budget=shortening_budget(unit,'Marte',f.Rect(0,0,100,30))
    assert budget>=4

def test_caption_shortform_not_applied_to_paragraph(tmp_path):
    import csv,json
    from astro_translate.core import approve_glossary,reinsert,sha256
    book=tmp_path/'book';book.mkdir();d=f.open();p=d.new_page();p.insert_text((40,50),'Birth chart');p.insert_text((40,64),'personal meaning');d.save(book/'source.pdf');d.close()
    g=tmp_path/'g.csv';g.write_text('source,target,tgt_lng\nBirth chart,carta natal,es\n');s=tmp_path/'s.md';s.write_text('style')
    approve_glossary(book,g,s,'synthetic')
    forms=tmp_path/'forms.csv';forms.write_text('source,target,short_target\nBirth chart personal meaning,carta natal,c.natal\n');approval=tmp_path/'approval.json';approval.write_text(json.dumps({'approved':True,'scope':'shortforms','sha256':sha256(forms)}))
    with f.open(book/'source.pdf') as d:unit=extract_layout_units(d[0])[0]
    result=reinsert(book,{'page':1,'blocks':[{**unit,'target':'demasiado '*1000}]},g,s,shortforms={'path':forms,'approval':approval})
    assert result['status']=='review' and not any(x.get('shortform_used') for x in result['blocks'])

def test_approved_label_abbreviation_preserves_original_protected_tokens(tmp_path):
    import json
    from astro_translate.core import approve_glossary,reinsert,sha256,visual_qa
    book=tmp_path/'book';book.mkdir();d=f.open();p=d.new_page();p.insert_text((40,50),'Ascendant');p.draw_rect(f.Rect(39,39,100,52));p.insert_text((40,90),'12 R');d.save(book/'source.pdf');d.close()
    g=tmp_path/'g.csv';g.write_text('source,target,tgt_lng\nAscendant,Ascendente,es\n');s=tmp_path/'s.md';s.write_text('style');approve_glossary(book,g,s,'synthetic')
    forms=tmp_path/'forms.csv';forms.write_text('source,target,short_target\nAscendant,Ascendente,Asc.\n');approval=tmp_path/'approval.json';approval.write_text(json.dumps({'approved':True,'scope':'shortforms','sha256':sha256(forms)}))
    with f.open(book/'source.pdf') as d:unit=extract_layout_units(d[0])[0]
    result=reinsert(book,{'page':1,'blocks':[{**unit,'target':'Asc.'}]},g,s,shortforms={'path':forms,'approval':approval})
    assert result['blocks'][0]['shortform_used']
    qa=visual_qa(book,result['output'],result,g,selected_pages=[1])
    assert qa['pages'][0]['glyph_set_equal'] and not qa['pages'][0]['glossary_missing']
    with f.open(result['output']) as d:assert '12 R' in d[0].get_text()
    import pytest
    from astro_translate.core import Blocked
    approval.write_text(json.dumps({'approved':True,'scope':'shortforms','sha256':sha256(forms),'base_glossary_sha256':'stale'}))
    with pytest.raises(Blocked,match='base glossary'):reinsert(book,{'page':1,'blocks':[{**unit,'target':'Asc.'}]},g,s,shortforms={'path':forms,'approval':approval})

def test_external_crop_mark_segments_are_not_body_obstacles():
    d=f.open();p=d.new_page();shape=p.new_shape();shape.draw_line((-20,0),(-10,0));shape.draw_line((p.rect.width+10,p.rect.height),(p.rect.width+20,p.rect.height));shape.finish();shape.commit();p.insert_text((40,50),'Plain text')
    assert not drawing_obstacles(p)
    assert not extract_layout_units(p)[0]['vector'];d.close()

def test_cached_formatting_changes_only_whitespace():
    from astro_translate.layout import format_cached_translation
    unit={'spans':[{'font':'Heading','bbox':[0,0,30,10]},{'font':'Body','bbox':[0,12,50,22]}]}
    assert format_cached_translation(unit,'Título La carta está aquí .')=='Título\nLa carta está aquí.'

def test_ambiguous_capitals_not_used_for_heading_boundary():
    from astro_translate.layout import format_cached_translation
    unit={'spans':[{'font':'Heading','bbox':[0,0,30,10]},{'font':'Body','bbox':[0,12,50,22]}]}
    text='El Sol La carta y la Luna'
    assert format_cached_translation(unit,text)==text

def test_redaction_preserves_adjacent_protected_prose_not_only_number(tmp_path):
    from astro_translate.core import approve_glossary,reinsert,visual_qa
    book=tmp_path/'book';book.mkdir();d=f.open();p=d.new_page();p.insert_text((80,50),'Head',fontsize=9);p.insert_text((40,60),'10 letters remain',fontsize=9);d.save(book/'source.pdf');d.close()
    g=tmp_path/'g.csv';g.write_text('source,target,tgt_lng\nHead,Cabecera,es\n');s=tmp_path/'s.md';s.write_text('style');approve_glossary(book,g,s,'synthetic')
    with f.open(book/'source.pdf') as d:unit=next(u for u in extract_layout_units(d[0]) if u['source']=='Head')
    result=reinsert(book,{'page':1,'blocks':[{**unit,'target':'Cabecera'}]},g,s)
    with f.open(result['output']) as d:assert '10 letters remain' in d[0].get_text()
    qa=visual_qa(book,result['output'],result,g,selected_pages=[1]);assert qa['pages'][0]['protected_spans_equal'];assert qa['pages'][0]['non_text_pixel_diff']==0
    from astro_translate.core import sha256
    corrupt=book/'work/corrupt.pdf';d=f.open();p=d.new_page();p.insert_text((80,50),'Head',fontsize=9);p.insert_text((40,60),'10 letters',fontsize=9);d.save(corrupt);d.close()
    bad=visual_qa(book,corrupt,{'page':1,'source_sha256':sha256(book/'source.pdf'),'output_sha256':sha256(corrupt),'blocks':[]},selected_pages=[1])
    assert bad['pages'][0]['glyph_set_equal'] and not bad['pages'][0]['protected_spans_equal']

def test_single_line_without_label_context_does_not_authorize_shortform(tmp_path):
    import json,pytest
    from astro_translate.core import approve_glossary,reinsert,sha256,Blocked
    book=tmp_path/'book';book.mkdir();d=f.open();p=d.new_page();p.insert_text((40,50),'Ascendant');d.save(book/'source.pdf');d.close()
    g=tmp_path/'g.csv';g.write_text('source,target,tgt_lng\nAscendant,Ascendente,es\n');s=tmp_path/'s.md';s.write_text('style');approve_glossary(book,g,s,'synthetic')
    forms=tmp_path/'forms.csv';forms.write_text('source,target,short_target\nAscendant,Ascendente,Asc.\n');approval=tmp_path/'approval.json';approval.write_text(json.dumps({'approved':True,'scope':'shortforms','sha256':sha256(forms)}))
    with f.open(book/'source.pdf') as d:unit=extract_layout_units(d[0])[0]
    assert unit['context']=='running_text'
    with pytest.raises(Blocked,match='explicitly approved'):reinsert(book,{'page':1,'blocks':[{**unit,'target':'Asc.'}]},g,s,shortforms={'path':forms,'approval':approval})
