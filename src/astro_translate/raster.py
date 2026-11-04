"""Reviewed raster-region editing; automatic cleanup is deliberately conservative."""
import io
import json
import hashlib
from pathlib import Path
import pymupdf as fitz
from PIL import Image, ImageDraw
from .core import Blocked, require_approval, sha256, protected, read_extras, _save_pdf, atomic_json, book_lock, triage, fingerprint

def rebuild(book,payload,glossary,style,do_not_translate=None,font_path=None):
    """Raster labels require an explicit local audit of text-only white rectangles.

    OCR boxes alone do not prove that symbols or chart lines are absent. The
    audit binds to source hash; curved, textured and partially overlapping text
    is rejected and must use a dedicated reviewed mask/inpainter in a later stage.
    """
    require_approval(book,glossary,style,do_not_translate)
    book=Path(book); extras=read_extras(do_not_translate)
    if not (book/'work/manifest.json').exists(): triage(book)
    elif json.loads((book/'work/manifest.json').read_text())['source_sha256']!=sha256(book/'source.pdf'): raise Blocked('Immutable source changed')
    cache_key=hashlib.sha256(json.dumps({'payload':payload,'dependencies':fingerprint(book,glossary,style,do_not_translate),'font':sha256(font_path) if font_path else None},sort_keys=True).encode()).hexdigest()
    cached=book/'work'/f'raster-p{payload["page"]:04d}.json'
    if cached.exists():
        previous=json.loads(cached.read_text()); output=Path(previous.get('output',''))
        if previous.get('cache_key')==cache_key and output.is_file() and sha256(output)==previous.get('output_sha256'): return previous
    if payload.get('source_sha256')!=sha256(book/'source.pdf') or not payload.get('reviewed_by'): raise Blocked('Hash-bound local raster protection review required')
    with fitz.open(book/'source.pdf') as doc:
        page=doc[payload['page']-1]
        if page.rotation: raise Blocked('Rotated PDF pages require coordinate normalization before raster editing')
        xref=payload['xref']; images=page.get_image_rects(xref)
        if len(images)!=1: raise Blocked('Shared/multiply placed raster needs separate review')
        # Replacing shared xref can change other pages.
        if sum(any(x[0]==xref for x in p.get_images()) for p in doc)!=1: raise Blocked('Image xref is shared across pages')
        image=Image.open(io.BytesIO(doc.extract_image(xref)['image'])).convert('RGB'); draw=ImageDraw.Draw(image)
        box=images[0]; sx=box.width/image.width; sy=box.height/image.height
        labels=[]
        for r in payload['regions']:
            if r.get('safe_text_only') is not True or r.get('background')!='white': raise Blocked('Only locally reviewed text-only flat white regions supported')
            if protected(r['source'],'',extras) or protected(r['target'],'',extras): raise Blocked('Raster label includes protected content')
            if r.get('rotate',0) not in (0,90,180,270): raise Blocked('Radial or curved labels require manual review')
            x0,y0,x1,y1=map(int,r['bbox'])
            if not (1<=x0<x1<image.width-1 and 1<=y0<y1<image.height-1): raise Blocked('Raster box out of bounds')
            ring=[image.getpixel((x,y)) for x in range(x0-1,x1+2) for y in (y0-1,y1+1)] + [image.getpixel((x,y)) for y in range(y0,y1+1) for x in (x0-1,x1+1)]
            if any(min(p)<245 for p in ring): raise Blocked('Non-flat boundary: line or textured background requires review')
            draw.rectangle((x0,y0,x1,y1),fill='white')
            labels.append((fitz.Rect(box.x0+x0*sx,box.y0+y0*sy,box.x0+x1*sx,box.y0+y1*sy),r))
        buf=io.BytesIO(); image.save(buf,format='PNG'); page.replace_image(xref,stream=buf.getvalue())
        import html
        reports=[]
        for rect,r in labels:
            css=f'body {{font-size:{r.get("size",10)}pt;margin:0}}'; archive=None
            if font_path:
                fp=Path(font_path).resolve(); archive=fitz.Archive(str(fp.parent)); css+=f'@font-face {{font-family:Book;src:url({fp.name})}} body {{font-family:Book}}'
            spare,scale=page.insert_htmlbox(rect,html.escape(r['target']),scale_low=1,rotate=r.get('rotate',0),css=css,archive=archive)
            if spare<0: raise Blocked('Raster label does not fit at original scale; no output committed')
            reports.append({'bbox':list(rect),'fit':True,'scale':scale,'source':r['source'],'target':r['target']})
        output=book/'work'/f'raster-p{payload["page"]:04d}.pdf'
        with book_lock(book):
            _save_pdf(doc,output)
            result={'cache_key':cache_key,'page':payload['page'],'source_sha256':sha256(book/'source.pdf'),'output':str(output),'output_sha256':sha256(output),'blocks':reports,'status':'pending_qa','raster_protection_reviewed_by':payload['reviewed_by']}
            atomic_json(book/'work'/f'raster-p{payload["page"]:04d}.json',result)
            manifest=json.loads((book/'work/manifest.json').read_text()); state=manifest['pages'][payload['page']-1]; state['attempts']+=1; state['status']='translated'; state['dependencies']=fingerprint(book,glossary,style,do_not_translate); atomic_json(book/'work/manifest.json',manifest)
        return result
