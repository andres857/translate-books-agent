import pymupdf as f
from astro_translate.pilot_assembly import assemble


def test_assembly_preserves_transparency_group_and_comparison(tmp_path):
    source=tmp_path/'source.pdf';d=f.open();p=d.new_page(width=200,height=250);p.draw_rect(f.Rect(0,0,200,250),color=None,fill=(.1,.3,.6));p.insert_text((20,40),'Source');d.xref_set_key(p.xref,'Group','<</S/Transparency/CS/DeviceRGB/I true>>');d.save(source);d.close()
    out=tmp_path/'sample.pdf';comparison=tmp_path/'comparison.pdf'
    proofs=assemble(source,[{'source_page':1,'candidate':str(source),'index':0}],out,comparison)
    assert proofs[0]['group_preserved'] and proofs[0]['assembled_render_equal']
    with f.open(source) as a,f.open(comparison) as b:
        expected=a[0].get_pixmap(dpi=72)
        for rect in (f.Rect(0,28,200,278),f.Rect(200,28,400,278)):
            actual=b[0].get_pixmap(dpi=72,clip=rect)
            assert actual.samples==expected.samples


def test_assembly_reads_one_page_candidate_for_nonfirst_book_page(tmp_path):
    source=tmp_path/'source.pdf';d=f.open()
    for n in range(3):
        p=d.new_page(width=200,height=250);p.insert_text((20,40),f'Page {n+1}')
    d.save(source);d.close()
    candidate=tmp_path/'candidate.pdf'
    with f.open(source) as doc,f.open() as one:
        one.insert_pdf(doc,from_page=1,to_page=1);one.save(candidate,garbage=4,deflate=True)
    out=tmp_path/'sample.pdf';comparison=tmp_path/'comparison.pdf'
    proofs=assemble(source,[{'source_page':2,'candidate':str(candidate),'index':0}],out,comparison)
    assert proofs[0]['candidate_index']==0 and proofs[0]['assembled_render_equal']
    with f.open(out) as result:
        assert len(result)==1 and result[0].get_text().strip()=='Page 2'
