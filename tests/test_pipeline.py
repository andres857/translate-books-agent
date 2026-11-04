import csv
import json
from pathlib import Path
import pytest
import pymupdf as fitz
from PIL import Image, ImageDraw
from astro_translate.core import (Blocked, atomic_json, sha256, triage, approve_glossary, require_approval, protected, extract_units, reinsert, visual_qa, assemble)

@pytest.fixture
def book(tmp_path):
    b=tmp_path/'book'; b.mkdir(); (b/'work').mkdir()
    d=fitz.open()
    p=d.new_page(width=400,height=500); p.insert_text((40,50),'The planets and houses',fontsize=12); p.insert_text((40,90),'15 degrees 23 R',fontsize=12)
    font=Path('fonts/NotoSansSymbols-Regular.ttf')
    if font.exists():
        p.insert_font(fontname='AstroSymbols',fontfile=str(font)); p.insert_text((40,130),'♈♌☽',fontname='AstroSymbols',fontsize=18)
    p=d.new_page(width=400,height=500)
    for y in (40,80,120): p.draw_line((30,y),(300,y))
    p.insert_text((40,65),'Planet'); p.insert_text((160,65),'Degree'); p.insert_text((40,105),'Sun'); p.insert_text((160,105),'15.23')
    p=d.new_page(width=400,height=500)
    im=Image.new('RGB',(400,500),'white'); ImageDraw.Draw(im).text((40,50),'Ascendant',fill='black'); ImageDraw.Draw(im).text((200,50),'15',fill='black'); import io
    buf=io.BytesIO(); im.save(buf,format='PNG'); p.insert_image(p.rect,stream=buf.getvalue())
    d.save(b/'source.pdf'); d.close()
    return b

@pytest.fixture
def glossary(tmp_path):
    g=tmp_path/'glossary.csv'; g.write_text('source,target,tgt_lng\nPlanet,Planeta,es\n'); s=tmp_path/'style.md'; s.write_text('Spanish'); return g,s

def test_triage_idempotent(book):
    a=triage(book); c=triage(book); assert a==c
    assert len(a['pages'])==3 and a['pages'][2]['type']=='scanned'
    assert sha256(book/'source.pdf')==a['source_sha256']

def test_gate_hashes(book,glossary):
    g,s=glossary
    with pytest.raises(Blocked): require_approval(book,g,s)
    approve_glossary(book,g,s,'human')
    require_approval(book,g,s)
    g.write_text(g.read_text()+'Sun,Sol,es\n')
    with pytest.raises(Blocked): require_approval(book,g,s)

def test_protected():
    for text,font in [('15°♌23′','Helvetica'),('a','AstroSymbols'),('R','Helvetica')]: assert protected(text,font)
    assert not protected('Ascendant','Helvetica')

def test_extract_safe(book):
    with fitz.open(book/'source.pdf') as d:
        units=extract_units(d[0]); assert units
        assert all(not any(c.isdigit() for c in u['source']) for u in units)

def test_reinsert_preserves_and_qa(book,glossary):
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as d: unit=next(u for u in extract_units(d[1]) if u['source']=='Planet')
    result=reinsert(book,{'page':2,'blocks':[{**unit,'target':'Planeta'}]},g,s)
    out=Path(result['output']); assert out.exists(); assert result['blocks'][0]['scale']>=.8
    with fitz.open(book/'source.pdf') as a,fitz.open(out) as b:
        assert a[1].get_drawings()==b[1].get_drawings()
        assert '15.23' in b[1].get_text()
        assert a.extract_image(a[2].get_images()[0][0])['image']==b.extract_image(b[2].get_images()[0][0])['image']
    qa=visual_qa(book,out,result)
    assert qa['pages'][1]['glyph_set_equal']
    assert qa['pages'][1]['non_text_pixel_diff']<=.01
    assert qa['pages'][1]['verdict']=='revisar' # VLM/human evidence absent

def test_protected_redaction_refused(book,glossary):
    g,s=glossary; approve_glossary(book,g,s,'human')
    with pytest.raises(Blocked): reinsert(book,{'page':1,'blocks':[{'bbox':[30,60,250,100],'source':'15 degrees 23 R','target':'x'}]},g,s)
    assert not list((book/'work').glob('translated*.pdf'))

def test_assemble_blocks_incomplete(book):
    with pytest.raises(Blocked): assemble(book,[str(book/'source.pdf')],[],{'pages':[]})

def test_glyphs_unchanged_after_edit(book,glossary):
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as d:
        assert '♈' in d[0].get_text()
        unit=extract_units(d[0])[0]
    result=reinsert(book,{'page':1,'blocks':[{**unit,'target':'Los planetas y casas'}]},g,s)
    with fitz.open(result['output']) as d: assert '♈♌☽' in d[0].get_text()

def test_failure_rolls_back(book,glossary):
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as d: unit=extract_units(d[0])[0]
    result=reinsert(book,{'page':1,'blocks':[{**unit,'target':'mucho '*1000,'vector':True}]},g,s)
    assert result['status']=='review' and 'output' not in result

def test_only_one_shortening_attempt(book,glossary):
    from unittest.mock import Mock
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as d: unit=extract_units(d[0])[0]
    short=Mock(return_value=[{'id':'0','translation':'Planetas'}])
    r=reinsert(book,{'page':1,'blocks':[{**unit,'target':'mucho '*1000,'vector':True}]},g,s,shortener=short)
    assert short.call_count==1; assert r['blocks'][0]['scale']==1

def test_vlm_masks_protected_native(book,glossary):
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as d: unit=extract_units(d[0])[0]
    result=reinsert(book,{'page':1,'blocks':[{**unit,'target':'Planetas'}]},g,s)
    def judge(a,b,page):
        # Number at original baseline near x=40,y=90 must be black masked.
        if a.getpixel((65,130))==(0,0,0): judge.masked=True
        return {'ok':True,'residual_english':[],'notes':'mock'}
    judge.masked=False
    qa=visual_qa(book,result['output'],result,vlm=judge)
    assert judge.masked
    assert not qa['pages'][2]['vlm_complete']

def test_changed_source_blocks_resume(book):
    triage(book)
    with open(book/'source.pdf','ab') as stream: stream.write(b'\n')
    with pytest.raises(Blocked): triage(book)

def test_schemas_and_persistent_qa(book,glossary):
    import jsonschema
    g,s=glossary; approve_glossary(book,g,s,'human')
    manifest=triage(book); jsonschema.validate(manifest,json.loads(Path('schemas/manifest.json').read_text()))
    with fitz.open(book/'source.pdf') as d: unit=extract_units(d[0])[0]
    report=reinsert(book,{'page':1,'blocks':[{**unit,'target':'Planetas'}]},g,s)
    qa=visual_qa(book,report['output'],report)
    for item in qa['pages']: jsonschema.validate(item,json.loads(Path('schemas/qa.json').read_text()))
    queue=json.loads((book/'work/review_queue.json').read_text())
    jsonschema.validate(queue,json.loads(Path('schemas/review_queue.json').read_text()))
    assert (book/'work/renders/p0001-before.png').exists()
    assert (book/'work/qa/p0001.json').exists()
    state=json.loads((book/'work/manifest.json').read_text()); assert state['pages'][0]['attempts']==1; assert state['pages'][0]['status']=='review'

def test_reinsert_idempotent(book,glossary):
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as d: unit=extract_units(d[0])[0]
    payload={'page':1,'blocks':[{**unit,'target':'Planetas'}]}
    a=reinsert(book,payload,g,s); b=reinsert(book,payload,g,s)
    assert a==b
    assert json.loads((book/'work/manifest.json').read_text())['pages'][0]['attempts']==1

def test_raster_reviewed_region_preserves_pixels(book,glossary):
    from astro_translate.raster import rebuild
    import io
    from PIL import ImageChops
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as doc:
        xref=doc[2].get_images()[0][0]; before=Image.open(io.BytesIO(doc.extract_image(xref)['image'])).convert('RGB')
    payload={'source_sha256':sha256(book/'source.pdf'),'page':3,'xref':xref,'reviewed_by':'synthetic test','regions':[{'bbox':[30,40,120,80],'source':'Ascendant','target':'Ascendente','size':8,'safe_text_only':True,'background':'white'}]}
    result=rebuild(book,payload,g,s)
    with fitz.open(result['output']) as doc:
        after=Image.open(io.BytesIO(doc.extract_image(doc[2].get_images()[0][0])['image'])).convert('RGB')
        assert 'Ascendente' in doc[2].get_text()
    assert ImageChops.difference(before.crop((150,0,400,500)),after.crop((150,0,400,500))).getbbox() is None

def test_raster_ocr_boxes_alone_not_authorized(book,glossary):
    from astro_translate.raster import rebuild
    g,s=glossary; approve_glossary(book,g,s,'human')
    with pytest.raises(Blocked): rebuild(book,{'page':3,'regions':[]},g,s)

def test_planet_not_residual_substring_of_planeta(book,glossary):
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as d: unit=next(u for u in extract_units(d[1]) if u['source']=='Planet')
    report=reinsert(book,{'page':2,'blocks':[{**unit,'target':'Planeta'}]},g,s)
    qa=visual_qa(book,report['output'],report,g)
    assert 'Planet' not in qa['pages'][1]['residual_english']

def test_reinsert_gate_before_output(book,glossary):
    g,s=glossary
    with pytest.raises(Blocked): reinsert(book,{'page':1,'blocks':[]},g,s)
    assert not list((book/'work').glob('translated*.pdf'))

def test_native_reinsert_on_mixed_page(book,glossary):
    # Add native text beside the raster before source identity is approved.
    with fitz.open(book/'source.pdf') as doc:
        doc[2].insert_text((40,200),'Planet'); data=doc.tobytes()
    (book/'source.pdf').write_bytes(data)
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as doc: unit=extract_units(doc[2])[0]
    result=reinsert(book,{'page':3,'blocks':[{**unit,'target':'Planeta'}]},g,s)
    assert 'output' in result

def test_protected_prose_cannot_pass_qa_with_positive_vlm(book):
    with fitz.open(book/'source.pdf') as doc:
        doc[0].insert_text((40,250),'The planets move through 12 houses'); data=doc.tobytes()
    (book/'source.pdf').write_bytes(data)
    digest=sha256(book/'source.pdf')
    qa=visual_qa(book,book/'source.pdf',{'source_sha256':digest,'output_sha256':digest,'blocks':[]},vlm=lambda a,b,page:{'ok':True,'residual_english':[],'notes':'mock'})
    assert qa['pages'][0]['verdict']=='revisar'
    assert any('houses' in text for text in qa['pages'][0]['residual_english'])

def test_rotated_pages_fail_closed(book,glossary):
    with fitz.open(book/'source.pdf') as doc:
        doc[0].set_rotation(90); data=doc.tobytes()
    (book/'source.pdf').write_bytes(data)
    g,s=glossary; approve_glossary(book,g,s,'human')
    with fitz.open(book/'source.pdf') as doc: unit=extract_units(doc[0])[0]
    with pytest.raises(Blocked,match='Rotated'): reinsert(book,{'page':1,'blocks':[{**unit,'target':'Planetas'}]},g,s)
    digest=sha256(book/'source.pdf')
    with pytest.raises(Blocked,match='Rotated'): visual_qa(book,book/'source.pdf',{'source_sha256':digest,'output_sha256':digest,'blocks':[]})
