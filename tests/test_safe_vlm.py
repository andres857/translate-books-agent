import io
import json
from pathlib import Path
import pymupdf as fitz
import pytest
from PIL import Image,ImageDraw
from astro_translate.core import sha256,Blocked
from astro_translate.safe_vlm import build_safe_pair,verify_safe_pair,judge_safe_pair,MASK_RGB


def make_pdf(path,text='Editorial 15°23′ text MC',rotation=0):
    doc=fitz.open();page=doc.new_page(width=260,height=180)
    raster=Image.new('RGB',(260,180),(255,0,255));ImageDraw.Draw(raster).text((5,5),'RASTER SECRET 987',fill='black')
    stream=io.BytesIO();raster.save(stream,format='PNG');page.insert_image(page.rect,stream=stream.getvalue())
    page.draw_rect(fitz.Rect(12,12,70,55),fill=(1,0,0))
    font=Path('fonts/NotoSans-Regular.ttf').resolve();page.insert_font(fontname='Body',fontfile=str(font));page.insert_text((20,75),'Editorial visible layout',fontname='Body',fontsize=12);page.insert_text((20,115),text,fontname='Body',fontsize=12)
    if rotation:page.set_rotation(rotation)
    doc.save(path);doc.close()


def test_full_page_raster_removed_but_native_text_is_visible(tmp_path):
    book=tmp_path/'book';book.mkdir();source=book/'source.pdf';target=book/'candidate.pdf';make_pdf(source);make_pdf(target,'Editorial 15°23′ texto MC');before=sha256(source)
    pair=build_safe_pair(book,source,target,1,1)
    assert sha256(source)==before
    assert pair.report['ready_for_native_judge']
    assert not pair.report['raster_assessed'] and not pair.report['vectors_assessed']
    for side,path in zip(('before','after'),pair.images):
        report=pair.report[side];assert report['removed_raster_instances']==1 and report['removed_vector_paths']>=1
        assert report['native_text_preserved'] and report['editorial_chars_visible']>0
        image=Image.open(path).convert('RGB');pixels=list(image.get_flattened_data())
        assert (255,0,255) not in pixels and (255,0,0) not in pixels and (0,0,0) in pixels
        assert MASK_RGB in pixels
        for box in report['mask_pixel_rects']:
            x0,y0,x1,y1=box
            assert image.getpixel(((x0+x1)//2,(y0+y1)//2))==MASK_RGB


def test_rotated_page_masks_protected_values_at_render_coordinates(tmp_path):
    book=tmp_path/'book';book.mkdir();make_pdf(book/'source.pdf',rotation=90);make_pdf(book/'candidate.pdf',rotation=90)
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1)
    assert pair.report['before']['rotation']==90
    for side,path in zip(('before','after'),pair.images):
        image=Image.open(path)
        assert all(0<=b[0]<b[2]<=image.width and 0<=b[1]<b[3]<=image.height for b in pair.report[side]['mask_pixel_rects'])


def test_image_only_page_is_not_claimed_ready(tmp_path):
    book=tmp_path/'book';book.mkdir()
    for name in ('source.pdf','candidate.pdf'):
        doc=fitz.open();page=doc.new_page();im=Image.new('RGB',(100,100),'black');b=io.BytesIO();im.save(b,format='PNG');page.insert_image(page.rect,stream=b.getvalue());doc.save(book/name);doc.close()
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1)
    assert not pair.report['ready_for_native_judge']
    assert pair.report['before']['editorial_chars_visible']==0


def test_tampered_sanitized_image_is_refused_before_model(tmp_path,monkeypatch):
    book=tmp_path/'book';book.mkdir();make_pdf(book/'source.pdf');make_pdf(book/'candidate.pdf')
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1)
    pair.images[0].write_bytes(b'unsafe replacement')
    with pytest.raises(Blocked,match='hash'):verify_safe_pair(pair)
    called=[];monkeypatch.setattr('astro_translate.safe_vlm.run_task',lambda *a,**k:called.append(a))
    with pytest.raises(Blocked):judge_safe_pair(book,pair)
    assert not called


def test_protection_removes_operators_and_counts_collateral(tmp_path):
    book=tmp_path/'book';book.mkdir();make_pdf(book/'source.pdf');make_pdf(book/'candidate.pdf','A shifted 999° editorial MC')
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1)
    for side in ('before','after'):
        report=pair.report[side]
        assert report['mask_strategy'] in ('verified_glyph_ink','verified_glyph_ink_with_paint_fallback')
        assert report['remaining_protected_visible_chars']==0
        assert report['editorial_chars_visible']<=report['editorial_chars_total']
        assert report['editorial_chars_removed_by_protective_redaction']>=0
    assert pair.report['before']['mask_pixel_rects']!=pair.report['after']['mask_pixel_rects']


def test_explicit_symbol_font_masks_whole_span_even_if_latin(tmp_path):
    book=tmp_path/'book';book.mkdir();make_pdf(book/'source.pdf');make_pdf(book/'candidate.pdf')
    with fitz.open(book/'source.pdf') as doc:name=doc[0].get_text('dict')['blocks'][-1]['lines'][0]['spans'][0]['font']
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1,protected_fonts=[name])
    assert not pair.report['ready_for_native_judge']
    assert pair.report['before']['editorial_chars_visible']==0
    assert pair.report['before']['protected_visible_chars']>30


def test_judge_routes_only_verified_images_and_keeps_partial_scope(tmp_path,monkeypatch):
    from astro_translate.llm import LLMResult
    book=tmp_path/'book';book.mkdir();make_pdf(book/'source.pdf');make_pdf(book/'candidate.pdf')
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1)
    calls=[];gates=[]
    monkeypatch.setattr('astro_translate.safe_vlm.require_approval',lambda *a:None)
    monkeypatch.setattr('astro_translate.adapters.require_backend_approval',lambda *a:gates.append(a))
    def fake(task,payload):
        calls.append((task,payload))
        return LLMResult({'verdict':'ok','residual_english':[],'overlaps':[],'notes':''},'fake','low',11,3,'codex_sdk',False,.1,cached_input_tokens=4)
    monkeypatch.setattr('astro_translate.safe_vlm.run_task',fake)
    result=judge_safe_pair(book,pair,phase='pilot',logging_phase='pilot_vlm_probe')
    assert len(calls)==1 and calls[0][0]=='qa_judge'
    payload=calls[0][1]
    assert payload['_images']==[str(p) for p in pair.images]
    assert payload['_images_verified'] and payload['_phase']=='pilot_vlm_probe'
    assert gates[0][-1]=='pilot'
    assert 'source.pdf' not in json.dumps(payload) and '15°23' not in json.dumps(payload)
    assert not result['whole_page_qa_complete'] and not result['raster_assessed']
    assert result['cached_input_tokens']==4


def test_white_text_visible_and_unicode_numbers_are_protected(tmp_path):
    from astro_translate.safe_vlm import _glyph_sensitive
    assert _glyph_sensitive('Ⅻ') and _glyph_sensitive('½') and _glyph_sensitive('₃')
    book=tmp_path/'book';book.mkdir()
    for name in ('source.pdf','candidate.pdf'):
        doc=fitz.open();page=doc.new_page(width=200,height=100)
        page.draw_rect(page.rect,fill=(0,0,0))
        page.insert_text((10,40),'Editorial white text',color=(1,1,1))
        doc.save(book/name);doc.close()
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1)
    assert pair.report['ready_for_native_judge'] and not pair.report['colors_assessed']
    assert pair.report['before']['editorial_char_coverage']==1
    assert (0,0,0) in Image.open(pair.images[0]).get_flattened_data()


def test_astro_text_font_requires_embedded_byte_provenance(tmp_path):
    from fontTools.ttLib import TTFont
    font=TTFont('fonts/NotoSans-Regular.ttf')
    for entry in font['name'].names:
        if entry.nameID in (1,4,6,16):
            entry.string='AstroSerif-Test'.encode(entry.getEncoding())
    font_path=tmp_path/'AstroSerif-Test.ttf';font.save(font_path);font.close()
    book=tmp_path/'book';book.mkdir()
    for name in ('source.pdf','candidate.pdf'):
        doc=fitz.open();page=doc.new_page(width=230,height=100)
        page.insert_font(fontname='body',fontfile=str(font_path))
        page.insert_text((10,40),'Editorial visible text',fontname='body')
        doc.save(book/name);doc.close()
    denied=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1,trusted_text_font_files=['fonts/NotoSans-Regular.ttf'])
    assert not denied.report['ready_for_native_judge']
    allowed=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1,trusted_text_font_files=[font_path])
    assert allowed.report['ready_for_native_judge']
    assert allowed.report['before']['trusted_embedded_text_fonts']


@pytest.mark.parametrize('rotation,shear,render_mode',[(0,0,0),(90,.7,0),(270,-.6,0),(0,0,1)])
def test_actual_ink_masks_cover_italic_overhang_shear_rotation_and_stroke(tmp_path,rotation,shear,render_mode):
    book=tmp_path/'book';book.mkdir()
    for name in ('source.pdf','candidate.pdf'):
        doc=fitz.open();page=doc.new_page(width=300,height=200);point=fitz.Point(95,115)
        page.insert_text(point,'fj',fontname='tiit',fontsize=80,morph=(point,fitz.Matrix(1,0,shear,1,0,0)),render_mode=render_mode,border_width=2)
        page.set_rotation(rotation);doc.save(book/name);doc.close()
    with fitz.open(book/'source.pdf') as doc:
        protected_font=doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]['font']
        original=doc[0].get_pixmap(dpi=110,alpha=False)
        assert any(pixel!=(255,255,255) for pixel in Image.frombytes('RGB',(original.width,original.height),original.samples).get_flattened_data())
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1,protected_fonts=[protected_font])
    assert not pair.report['ready_for_native_judge']
    for side,path in zip(('before','after'),pair.images):
        # There is no surviving antialiasing fringe, even outside nominal bboxes.
        assert set(Image.open(path).get_flattened_data())<={(255,255,255),MASK_RGB}
        assert pair.report[side]['editorial_chars_removed_by_protective_redaction']==0
        if render_mode==1:assert pair.report[side]['protected_chars_paint_fallback']==2
        else:assert pair.report[side]['protected_chars_ink_bound']==2


def test_unsupported_ink_tracer_falls_back_to_verified_paint_bounds(tmp_path,monkeypatch):
    book=tmp_path/'book';book.mkdir();make_pdf(book/'source.pdf');make_pdf(book/'candidate.pdf')
    def unavailable(page):raise RuntimeError('MuPDF binding unavailable')
    monkeypatch.setattr('astro_translate.safe_vlm._glyph_ink_bounds',unavailable)
    pair=build_safe_pair(book,book/'source.pdf',book/'candidate.pdf',1,1)
    assert pair.report['before']['protected_chars_paint_fallback']>0
    assert pair.report['before']['protected_chars_ink_bound']==0
    assert pair.report['before']['editorial_chars_removed_by_protective_redaction']==0


def test_roundoff_requires_identical_native_ink_pixels(tmp_path,monkeypatch):
    from astro_translate.safe_vlm import _verify_native_geometry
    documents=[]
    for x in (20.00045,20.00050,20.01):
        doc=fitz.open();page=doc.new_page(width=200,height=100);page.insert_text((x,40),'Editorial text');documents.append(doc)
    proof=_verify_native_geometry(documents[0][0],documents[1][0],110)
    assert proof['mode']=='pixel_identical_float_roundoff'
    assert 0<proof['max_coordinate_delta_pt']<=.0001
    with pytest.raises(Blocked,match='geometry'):_verify_native_geometry(documents[0][0],documents[2][0],110)
    monkeypatch.setattr('astro_translate.safe_vlm._native_ink_fingerprint',lambda page,dpi:str(page.parent is documents[0]))
    with pytest.raises(Blocked,match='pixels'):_verify_native_geometry(documents[0][0],documents[1][0],110)
    for doc in documents:doc.close()


def test_partial_candidate_uses_explicit_page_mapping_and_separate_artifact_keys(tmp_path):
    book=tmp_path/'book';book.mkdir()
    first=book/'first.pdf';second=book/'second.pdf';make_pdf(first,'Different source first page');make_pdf(second,'Second page with 42° and MC')
    source=book/'source.pdf';candidate=book/'partial.pdf';full_candidate=book/'full.pdf'
    doc=fitz.open()
    for path in (first,second):
        with fitz.open(path) as part:doc.insert_pdf(part)
    doc.save(source);doc.close();make_pdf(candidate,'Candidate second page 42° MC')
    doc=fitz.open()
    for path in (first,candidate):
        with fitz.open(path) as part:doc.insert_pdf(part)
    doc.save(full_candidate);doc.close()
    source_hash=sha256(source)
    partial=build_safe_pair(book,source,candidate,2,1)
    full=build_safe_pair(book,source,full_candidate,2,2)
    assert partial.report['page']==2 and partial.report['before']['page_index']==2
    assert partial.report['after']['page_index']==1 and full.report['after']['page_index']==2
    assert partial.manifest!=full.manifest
    assert partial.report['image_sha256']==full.report['image_sha256']
    assert sha256(source)==source_hash
    verify_safe_pair(partial);verify_safe_pair(full)
    with pytest.raises(Blocked,match='bounds'):build_safe_pair(book,source,candidate,2,2)


def test_new_candidate_content_invalidates_artifact_key_without_overwriting_old_pair(tmp_path):
    book=tmp_path/'book';book.mkdir();source=book/'source.pdf';candidate=book/'candidate.pdf'
    make_pdf(source);make_pdf(candidate,'Candidate initial 15°23′ MC')
    old=build_safe_pair(book,source,candidate,1,1);old_hashes=old.report['image_sha256'][:]
    candidate.unlink();make_pdf(candidate,'Candidate revised text 15°23′ MC')
    new=build_safe_pair(book,source,candidate,1,1)
    assert old.manifest!=new.manifest
    assert old.report['after']['source_pdf_sha256']!=new.report['after']['source_pdf_sha256']
    assert [sha256(p) for p in old.images]==old_hashes
    assert new.report['image_sha256']!=old_hashes
