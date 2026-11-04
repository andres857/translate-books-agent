"""Conservative paragraph geometry with traceable source spans.

Print frames whose actual paths are wholly outside the visible page are ignored.
Visible drawing bounding boxes remain obstacles, as required for chart labels.
Full-page raster backdrops are never redacted; growth additionally checks their
local pixel content and the final QA compares pixels outside approved edits.
"""
from __future__ import annotations
import io
from functools import lru_cache
import pymupdf as fitz
from PIL import Image
import numpy as np

def _visible_path(drawing,page_rect):
    if not drawing['rect'].intersects(page_rect): return False
    if drawing['type'] in ('f','fs'): return True
    # Printer frames enclose the CropBox without painting inside it.
    for item in drawing['items']:
        if item[0]=='re':
            r=item[1]
            if r.x0<page_rect.x0 and r.y0<page_rect.y0 and r.x1>page_rect.x1 and r.y1>page_rect.y1: continue
            bounds=r
        elif item[0] in ('l','c'):
            points=item[1:];bounds=fitz.Rect(min(p.x for p in points),min(p.y for p in points),max(p.x for p in points),max(p.y for p in points))
        else:return True
        width=max(float(drawing.get('width',0))/2,.01)
        bounds=fitz.Rect(bounds.x0-width,bounds.y0-width,bounds.x1+width,bounds.y1+width)
        if bounds.intersects(page_rect):return True
    return False

def drawing_obstacles(page):
    return [fitz.Rect(d['rect']) for d in page.get_drawings() if _visible_path(d,page.rect)]

def foreground_images(page):
    return [fitz.Rect(x['bbox']) for x in page.get_image_info() if (fitz.Rect(x['bbox']) & page.rect).get_area()<page.rect.get_area()*.98]

def _union(spans):
    rect=fitz.Rect(spans[0]['bbox'])
    for span in spans[1:]: rect|=fitz.Rect(span['bbox'])
    return rect

def extract_layout_units(page,extras=()):
    from .core import protected,protected_boxes
    guards=protected_boxes(page,extras); drawings=drawing_obstacles(page); images=foreground_images(page)
    units=[];table_context=False
    def emit(spans):
        if not spans:return
        rect=_union(spans);vector=any(rect.intersects(r) for r in drawings)
        # Intersecting chart rectangles require the smallest source boxes.
        if len(spans)>1 and (vector or any(rect.intersects(r) for r in guards+images)):
            run=[]
            for span in spans:
                sr=fitz.Rect(span['bbox'])
                if any(sr.intersects(r) for r in drawings+guards+images):
                    emit(run);run=[];emit([span])
                else:run.append(span)
            if len(run)==len(spans):
                # Keep maximal contiguous paragraphs whose union avoids artwork,
                # e.g. a decorative closing quote beside the last line.
                safe=[]
                for span in run:
                    merged=_union(safe+[span])
                    if safe and any(merged.intersects(r) for r in drawings+guards+images):
                        emit(safe);safe=[]
                    safe.append(span)
                if safe:emit(safe)
            else:emit(run)
            return
        source=' '.join(s['source'].strip() for s in spans).strip()
        if not source:return
        kind='paragraph' if len(spans)>1 and not vector else 'label'
        units.append({'id':f'u{len(units):04d}','source':source,'bbox':list(rect),'spans':spans,'kind':kind,'context':'figure_label' if vector else 'table_label' if table_context and kind=='label' else 'running_text','vector':vector,'font':spans[0]['font'],'size':max(s['size'] for s in spans),'rotate':spans[0]['rotate']})
    for block in page.get_text('dict')['blocks']:
        table_context=sum(any(c.isdigit() for c in s['text']) for l in block.get('lines',[]) for s in l['spans'])>=3
        run=[];last=None
        for line in block.get('lines',[]):
            for span in line['spans']:
                text=span['text']
                if not text.strip():continue
                item={'source':text,'bbox':list(span['bbox']),'font':span['font'],'size':span['size'],'rotate':0 if tuple(line['dir'])==(1.,0.) else None}
                if protected(text,span['font'],extras) or item['rotate'] is None:
                    emit(run);run=[];last=None;continue
                rect=fitz.Rect(item['bbox'])
                if any(rect.intersects(r) for r in guards):
                    emit(run);run=[];last=None;continue
                if last:
                    prev=fitz.Rect(last['bbox']);same_line=abs(prev.y0-rect.y0)<max(item['size'],last['size'])*.4
                    separated=(same_line and rect.x0-prev.x1>item['size']*.8) or (not same_line and (rect.y0-prev.y1>item['size']*.6 or rect.x1<_union(run).x0 or rect.x0>_union(run).x1))
                    style_change=max(item['size'],last['size'])/min(item['size'],last['size'])>1.22
                    if separated or style_change:emit(run);run=[]
                run.append(item);last=item
        emit(run)
    return units

def background_clear(page,rect,tolerance=18):
    """Allow growth only over near-uniform local raster background, never artwork."""
    for image in page.get_images(full=True):
        for box in page.get_image_rects(image[0]):
            overlap=rect & box
            if overlap.is_empty:continue
            if (box & page.rect).get_area()<page.rect.get_area()*.98:return False
            data=page.parent.extract_image(image[0])['image'];im=Image.open(io.BytesIO(data)).convert('RGB')
            crop=(int((overlap.x0-box.x0)/box.width*im.width),int((overlap.y0-box.y0)/box.height*im.height),int((overlap.x1-box.x0)/box.width*im.width)+1,int((overlap.y1-box.y0)/box.height*im.height)+1)
            pixels=np.asarray(im.crop(crop)).reshape(-1,3).astype(float)
            if len(pixels) and np.quantile(np.abs(pixels-np.median(pixels,axis=0)),.995)>tolerance:return False
    return True

def expansion_candidates(page,unit,all_units,guards):
    """Grow toward free space, preserving visible paths and neighboring text."""
    rect=fitz.Rect(unit['bbox']);own=[fitz.Rect(s['bbox']) for s in unit.get('spans',[unit])]
    obstacles=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                if not span['text'].strip():continue
                r=fitz.Rect(span['bbox'])
                if any(r==x for x in own):continue
                # Shared line bboxes may overlap by a small font-metric amount.
                obstacles.append(r)
    obstacles+=foreground_images(page)+guards+drawing_obstacles(page)
    # Do not make a pre-existing drawing intersection larger.
    touching=[r for r in obstacles if rect.intersects(r)]
    blockers=[r for r in obstacles if r not in touching]
    candidates=[]
    for dx,dy in [(0,.5),(8,.5),(20,.5),(40,.5),(60,.5),(100,.5),(0,1),(8,1),(20,1),(40,1),(60,1),(100,1),(8,0),(20,0),(40,0),(70,0),(100,0),(0,2),(10,2),(20,4),(40,6),(60,10),(0,12),(0,24),(20,24),(40,36),(80,48)]:
        r=fitz.Rect(rect.x0,rect.y0,min(page.rect.x1-2,rect.x1+dx),min(page.rect.y1-2,rect.y1+dy))
        if any(r.intersects(x) for x in blockers):continue
        if any((r & x).get_area()>(rect & x).get_area()+.01 for x in touching):continue
        if background_clear(page,r):candidates.append(r)
    return candidates

def shortening_budget(unit,target,rect):
    """Operational character budget from usable area; tiny labels never use EN ratio."""
    size=max(float(unit.get('size',11)),1)
    capacity=int(max(1,rect.width/(size*.50))*max(1,rect.height/(size*1.15)))
    return max(1,min(len(target)-1,max(int(len(target)*.75),capacity)))

def format_cached_translation(unit,target):
    """Whitespace-only repair of cached output, retaining original heading breaks.

    A source heading is evidenced by a separate first line in a different font.
    When the cached output lost that break, a capitalized body start supplies a
    recoverable boundary. We change whitespace only, never translate wording.
    """
    import re
    target=re.sub(r'\s+([,.;:!?])',r'\1',target)
    spans=unit.get('spans',[])
    if len(spans)>1:
        first,second=spans[:2]
        if first['font']!=second['font'] and second['bbox'][1]>=first['bbox'][3]-1:
            boundaries=list(re.finditer(r'(?<=[a-záéíóúüñ]) +(?=[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ])',target))
            if len(boundaries)==1:
                boundary=boundaries[0];target=target[:boundary.start()]+'\n'+target[boundary.end():]
    return target

def safe_redaction_rects(page,unit):
    """Select source glyphs without touching any other glyph's REAL font bbox.

    MuPDF redaction uses full font metrics even when extraction uses nominal
    small glyph heights. A narrow band still deletes the selected glyphs, but
    avoids collateral deletion of adjacent condensed-font lines.
    """
    from .core import Blocked
    previous=fitz.TOOLS.set_small_glyph_heights()
    try:
        fitz.TOOLS.set_small_glyph_heights(False)
        raw=page.get_text('rawdict')
    finally:fitz.TOOLS.set_small_glyph_heights(previous)
    wanted=unit.get('spans',[unit]);selected=[];others=[]
    for block in raw['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                text=''.join(c['c'] for c in span['chars'])
                match=any(text==s['source'] and span['font']==s['font'] and abs(span['bbox'][0]-s['bbox'][0])<.01 and abs(span['bbox'][2]-s['bbox'][2])<.01 and abs((span['bbox'][1]+span['bbox'][3])-(s['bbox'][1]+s['bbox'][3]))<s['size'] for s in wanted)
                target=selected if match else others
                target.extend(fitz.Rect(c['bbox']) for c in span['chars'] if c['c'].strip())
    if not selected:raise Blocked('Cannot identify source glyphs for safe redaction')
    requested=[fitz.Rect(s['bbox']) for s in wanted] if unit.get('kind')=='label' or unit.get('vector') else [fitz.Rect(unit['bbox'])]
    result=[]
    for rect in requested:
        own=[r for r in selected if r.intersects(rect)]
        top_center=min((r.y0+r.y1)/2 for r in own);bottom_center=max((r.y0+r.y1)/2 for r in own)
        safe=fitz.Rect(rect)
        # Whitespace advances are not glyphs; including them can reach the
        # following protected ordinal or symbol despite no ink overlap.
        safe.x0=max(safe.x0,min(r.x0 for r in own));safe.x1=min(safe.x1,max(r.x1 for r in own))
        for obstacle in others:
            if obstacle.x1<=safe.x0 or obstacle.x0>=safe.x1:continue
            if obstacle.y1<=top_center:safe.y0=max(safe.y0,obstacle.y1+.01)
            elif obstacle.y0>=bottom_center:safe.y1=min(safe.y1,obstacle.y0-.01)
            elif obstacle.intersects(safe):raise Blocked('No safe redaction band between adjacent glyphs')
        if safe.is_empty or any(safe.intersects(r) for r in others) or any(not safe.intersects(r) for r in own):raise Blocked('Cannot safely redact all selected source glyphs')
        result.append(safe)
    return result
