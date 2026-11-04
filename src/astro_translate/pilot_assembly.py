"""Pilot assembly preserving page transparency groups and vector comparisons."""
from pathlib import Path
import os,tempfile
import pymupdf as fitz
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DictionaryObject,NameObject,NumberObject,ArrayObject,FloatObject,DecodedStreamObject
from .core import Blocked,sha256


def _write(writer,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,suffix='.pdf');os.close(fd)
    try:
        writer.write(temp);os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def _form(writer,page):
    if int(page.get('/Rotate',0))%360:raise Blocked('Pilot comparison requires normalized page rotation')
    box=page.cropbox;x0,y0,x1,y1=map(float,box)
    form=DecodedStreamObject();form.set_data(page.get_contents().get_data())
    form.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Form'),NameObject('/FormType'):NumberObject(1),NameObject('/BBox'):ArrayObject([FloatObject(x) for x in (x0,y0,x1,y1)]),NameObject('/Matrix'):ArrayObject([FloatObject(x) for x in (1,0,0,1,-x0,-y0)]),NameObject('/Resources'):page['/Resources'].clone(writer)})
    if '/Group' in page:form[NameObject('/Group')]=page['/Group'].clone(writer)
    return writer._add_object(form)


def assemble(source_path,rows,out,comparison):
    """Rows contain source_page(1-based), candidate path, index(0-based), label."""
    source=PdfReader(source_path);readers={str(source_path):source};sample=PdfWriter();paired=PdfWriter();sample.pdf_header=paired.pdf_header='%PDF-1.7'
    proofs=[]
    for row in rows:
        path=str(row['candidate'])
        if path not in readers:readers[path]=PdfReader(path)
        reader=readers[path];index=row['index'];original=source.pages[row['source_page']-1];candidate=reader.pages[index]
        if tuple(map(float,original.cropbox.width_height if hasattr(original.cropbox,'width_height') else (original.cropbox.width,original.cropbox.height)))!=tuple(map(float,(candidate.cropbox.width,candidate.cropbox.height))):
            if abs(float(original.cropbox.width)-float(candidate.cropbox.width))>.001 or abs(float(original.cropbox.height)-float(candidate.cropbox.height))>.001:raise Blocked('Comparison page sizes differ')
        sample.append(reader,pages=[index],import_outline=False)
        width,height=float(candidate.cropbox.width),float(candidate.cropbox.height);page=paired.add_blank_page(width*2,height+28)
        resources=DictionaryObject({
            NameObject('/XObject'):DictionaryObject({NameObject('/Original'):_form(paired,original),NameObject('/Translated'):_form(paired,candidate)}),
            NameObject('/Font'):DictionaryObject({NameObject('/Header'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})
        })
        page[NameObject('/Resources')]=resources
        label=row.get('label','Piloto - traduccion para revision').replace('(','[').replace(')',']').replace('\\','/')
        commands=f'q /Original Do Q\nq 1 0 0 1 {width:.9f} 0 cm /Translated Do Q\nBT /Header 10 Tf 10 {height+10:.9f} Td (Original - pagina PDF {row["source_page"]}) Tj ET\nBT /Header 10 Tf {width+10:.9f} {height+10:.9f} Td ({label}) Tj ET\n'
        stream=DecodedStreamObject();stream.set_data(commands.encode('ascii'));page[NameObject('/Contents')]=paired._add_object(stream)
        proofs.append({'page':row['source_page'],'candidate':path,'candidate_sha256':sha256(path),'candidate_index':index,'group_preserved':('/Group' in candidate)==('/Group' in sample.pages[-1])})
    _write(sample,out);_write(paired,comparison)
    # The standalone pilot must render identically to each checked candidate.
    with fitz.open(out) as final:
        for index,(row,proof) in enumerate(zip(rows,proofs)):
            with fitz.open(row['candidate']) as candidate:
                a=candidate[row['index']].get_pixmap(dpi=110,alpha=False);b=final[index].get_pixmap(dpi=110,alpha=False)
                equal=(a.width,a.height,a.samples)==(b.width,b.height,b.samples)
                proof['assembled_render_equal']=equal
                if not equal:raise Blocked('Assembled pilot changed candidate pixels on page '+str(row['source_page']))
    return proofs
