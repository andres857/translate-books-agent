"""Bounded, audited raster pilot and external ephemeris appendix.

OCR is evidence, never permission to erase arbitrary pixels. The cover pilot
only edits locally inspected horizontal text on a demonstrably flat background.
Curved labels remain explicit review items, rather than guessed masks.
"""
from __future__ import annotations

import io
import json
import html
import math
import hashlib
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import pymupdf as fitz

from .core import Blocked, atomic_json, require_approval, sha256, _save_pdf
from .fonts import fallback_font


def page_usage(book,page):
    """Count actual inference once; cached rerenders are not new model usage."""
    path=Path(book)/'work/costs.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    selected=[r for r in rows if r.get('phase')=='pilot' and r.get('task')=='translate_page'
              and (r.get('page')==page or r.get('pages')==[page])]
    real=[r for r in selected if not r.get('cached') and r.get('status')=='ok']
    return {'model_calls':len(real),'input_tokens':sum(r.get('input_tokens') or 0 for r in real),
            'output_tokens':sum(r.get('output_tokens') or 0 for r in real),
            'cached_input_tokens':sum(r.get('cached_input_tokens') or 0 for r in real),
            'latency_s':sum(r.get('latency_s') or 0 for r in real),
            'cache_hits':sum(bool(r.get('cached')) for r in selected),
            'failed_calls_with_unknown_usage':sum(r.get('status')=='backend_error' and r.get('input_tokens') is None for r in selected),
            'billing':'ChatGPT subscription; no paid API fallback.'}


def clear_reviewed_flat_rectangles(image, regions, *, tolerance=20):
    """Return a copy; require audited regions and flat full boundary rings.

    The source image stays untouched. Protected content inside a region or
    missing local review is an error, including when a provider supplied a box.
    """
    original=image.convert('RGB'); cleaned=original.copy(); draw=ImageDraw.Draw(cleaned)
    mask=Image.new('1',original.size); md=ImageDraw.Draw(mask)
    reports=[]
    for region in regions:
        if not region.get('reviewed_text_only') or not region.get('reviewed_by'):
            raise Blocked('Raster cleanup needs a local text-only audit')
        if region.get('protected_content'):
            raise Blocked('Raster cleanup intersects protected content')
        box=region['pixel_bbox']; x0,y0,x1,y1=map(int,box)
        if not (1<=x0<x1<original.width-1 and 1<=y0<y1<original.height-1):
            raise Blocked('Raster cleanup box out of bounds')
        border=[original.getpixel((x,y)) for x in range(x0,x1) for y in (y0,y1-1)]
        border += [original.getpixel((x,y)) for y in range(y0,y1) for x in (x0,x1-1)]
        color=Counter(border).most_common(1)[0][0]
        if any(max(abs(c[k]-color[k]) for k in range(3))>tolerance for c in border):
            raise Blocked('Raster text boundary is not flat; do not erase artwork')
        draw.rectangle((x0,y0,x1-1,y1-1),fill=color)
        md.rectangle((x0,y0,x1-1,y1-1),fill=1)
        reports.append({'pixel_bbox':box,'background_rgb':list(color),'boundary_samples':len(border)})
    a=np.asarray(original);b=np.asarray(cleaned);m=np.asarray(mask,dtype=bool)
    outside=int(np.count_nonzero(np.any(a!=b,axis=2)&~m))
    if outside:raise Blocked('Raster cleanup altered pixels outside reviewed text')
    return cleaned, {'outside_text_changed_pixels':outside,'regions':reports}


def cover_plan(book):
    """Explicit, hash-bound local inspection of the actual raster cover."""
    book=Path(book); source=book/'source.pdf'
    expected='d13ab81406a66d3ab9863a9c2350fb326e8356d914f2f303ba3e6d4cbdc88046'
    if sha256(source)!=expected:raise Blocked('Cover plan belongs to another source')
    ocr_path=book/'work/pilot_selection/cover-ocr.json'
    result=json.loads(ocr_path.read_text())[0]['res']
    texts=result['rec_texts']; title_index=texts.index('ASTROLOGY')
    if result['rec_scores'][title_index]<.95:raise Blocked('Cover title OCR confidence insufficient')
    with fitz.open(source) as doc:
        page=doc[0]
        if page.get_text().strip() or len(page.get_images())!=1:raise Blocked('Cover is not the expected pure raster page')
        xref=page.get_images()[0][0];image_data=doc.extract_image(xref)['image']
        image=Image.open(io.BytesIO(image_data)).convert('RGB')
        if image.size!=(2303,2752):raise Blocked('Unexpected cover raster resolution')
    audit='figure_specialist_pilot: local source pixels inspected; text-only horizontal boxes, 2026-11-04'
    regions=[
        {'id':'p0001_raster_title','source':texts[title_index], 'pixel_bbox':[160,1190,2204,1445],
         'size':76,'weight':900,'width':100,'line_height':1,'color':[177,177,175],
         'ocr_index':title_index,'ocr_confidence':result['rec_scores'][title_index]},
        {'id':'p0001_raster_subtitle','source':'Using the wisdom of the stars in your everyday life',
         'pixel_bbox':[160,1465,2205,1594],'size':29,'weight':400,'width':60,'color':[204,207,202],
         'ocr_index':4,'ocr_confidence':result['rec_scores'][4],
         'transcription_correction':'Low confidence OCR replaced by exact local visual transcription; not a translation.'},
    ]
    for region in regions:region.update(reviewed_text_only=True,reviewed_by=audit,protected_content=False)
    clear_reviewed_flat_rectangles(image,regions)
    issues=[{'reason':'curved_raster_labels','source_labels':['charismatic','practical','caring','progressive','mystical','courageous','focused','optimistic'],
             'action':'Preserved pending curved-text masks and typography; not an approved artwork exception.'},
            {'reason':'ocr_false_positive','ocr_text':'10','action':'Rejected as image pattern, not a numeral or translatable text.'},
            {'reason':'raster_font_unavailable','action':'OFL style approximation; original cover has no embedded text font.'}]
    plan={'page':1,'route':'C','source_sha256':expected,'image_xref':xref,'ocr_artifact':str(ocr_path),
          'ocr_sha256':sha256(ocr_path),'ocr_engine':'PaddleOCR 3.7.0 / PP-OCRv6 medium CPU, enable_mkldnn=False',
          'regions':regions,'preserved':['DK publisher mark','CAROLE TAYLOR','all illustrations'], 'issues':issues}
    atomic_json(book/'work/pilot/raster/cover-plan.json',plan)
    return plan


def build_cover(book,config_path='config/pipeline.toml'):
    """Execute the safe portion of Route C; never call it a completed page."""
    import tomllib
    from .adapters import require_backend_approval
    from .llm import run_task
    book=Path(book).resolve();config_path=Path(config_path).resolve()
    cfg=tomllib.loads(config_path.read_text());paths=cfg['paths']
    require_approval(book,paths['glossary'],paths['style_guide'],paths['do_not_translate'])
    require_backend_approval(book,config_path,'pilot')
    plan=cover_plan(book);folder=book/'work/pilot/raster';folder.mkdir(parents=True,exist_ok=True)
    result=run_task('translate_page',{'_book':str(book),'_config':str(config_path),'_phase':'pilot','_page':1,
        'blocks':[{'id':r['id'],'source':r['source']} for r in plan['regions']]})
    targets={r['id']:r['translation'] for r in result.data['blocks']}
    atomic_json(folder/'cover-translation.json',{'data':result.data,'style_sha256':sha256(paths['style_guide'])})
    with fitz.open(book/'source.pdf') as source:
        candidate=fitz.open();candidate.insert_pdf(source,from_page=0,to_page=0)
        page=candidate[0];xref=page.get_images()[0][0];raw=candidate.extract_image(xref)['image']
        original=Image.open(io.BytesIO(raw)).convert('RGB');cleaned,cleanup=clear_reviewed_flat_rectangles(original,plan['regions'])
        rectangle=page.get_image_rects(xref)[0];sx=rectangle.width/original.width;sy=rectangle.height/original.height
        # Keep the original JPEG and its ICC profile byte-for-byte. Even when
        # preserving ICC, whole-image JPEG->PNG replacement changes decoder
        # downsampling at illustration edges. Flat opaque patches confined to
        # audited text boxes avoid touching those pixels entirely.
        cleaned.save(folder/'cover-cleaned-audit.png')
        sampled=page.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
        pixel=sampled.pixel(20,20)
        background=tuple(c/255 for c in pixel[:3])
        blocks=[]
        for region in plan['regions']:
            target=targets[region['id']];x0,y0,x1,y1=region['pixel_bbox']
            rect=fitz.Rect(rectangle.x0+x0*sx,rectangle.y0+y0*sy,rectangle.x0+x1*sx,rectangle.y0+y1*sy)
            page.draw_rect(rect,color=None,fill=background,overlay=True)
            style={'serif':True,'weight':region['weight'],'italic':False,'width':region['width'],'size':region['size']}
            font=fallback_font(style,folder/'fonts');path=Path(font['path']).resolve()
            color='#'+''.join(f'{c:02x}' for c in region['color'])
            css=f'@font-face{{font-family:Cover;src:url({path.name})}} body,div{{margin:0;padding:0;font-family:Cover;font-size:{region["size"]}pt;line-height:{region.get("line_height",1)};color:{color};text-align:center;}}'
            spare,scale=page.insert_htmlbox(rect,'<div>'+html.escape(target)+'</div>',css=css,archive=fitz.Archive(str(path.parent)),scale_low=.75)
            if spare<0:raise Blocked('Raster cover title cannot fit safely at scale>=.75')
            blocks.append({**region,'target':target,'bbox':list(rect),'edit_bbox':list(rect),'scale':scale,'fit':True,'font_sha256':font['sha256']})
        output=folder/'p0001-translated.pdf';_save_pdf(candidate,output);candidate.close()
    with fitz.open(book/'source.pdf') as source,fitz.open(output) as after:
        source_image=source.extract_image(source[0].get_images()[0][0])['image']
        output_image=after.extract_image(after[0].get_images()[0][0])['image']
        image_equal=hashlib.sha256(source_image).digest()==hashlib.sha256(output_image).digest()
        if not image_equal:raise Blocked('Original raster image bytes changed')
        before_pix=source[0].get_pixmap(dpi=110,alpha=False);after_pix=after[0].get_pixmap(dpi=110,alpha=False)
        before=Image.frombytes('RGB',(before_pix.width,before_pix.height),before_pix.samples)
        after_image=Image.frombytes('RGB',(after_pix.width,after_pix.height),after_pix.samples)
        mask=Image.new('1',before.size);draw=ImageDraw.Draw(mask)
        for block in blocks:
            x0,y0,x1,y1=block['edit_bbox'];draw.rectangle((math.floor(x0*110/72)-2,math.floor(y0*110/72)-2,math.ceil(x1*110/72)+2,math.ceil(y1*110/72)+2),fill=1)
        different=np.any(np.asarray(before)!=np.asarray(after_image),axis=2);outside=different&~np.asarray(mask,dtype=bool)
        before.save(folder/'p0001-before.png');after_image.save(folder/'p0001-after.png')
        comparison=Image.new('RGB',(before.width*2,before.height),'white');comparison.paste(before,(0,0));comparison.paste(after_image,(before.width,0));comparison.save(folder/'p0001-comparison.png')
        outside_count=int(outside.sum())
        if outside_count:raise Blocked('Cover rendering changed pixels outside authorized text boxes')
    report={'page':1,'candidate_page':1,'route':'C','output':str(output),'output_sha256':sha256(output),'source_sha256':plan['source_sha256'],
        'blocks':blocks,'fit_complete':True,'translated_regions':len(blocks),'status':'review','verdict':'revisar',
        'non_text_changed_pixels':outside_count,'non_text_pixel_diff':0.,'cleanup':cleanup,'issues':plan['issues'],
        'raster_method':'Original JPEG/ICC unchanged; opaque flat background patches in audited text boxes, vector translated text.',
        'original_image_bytes_equal':image_equal,'min_scale':min(b['scale'] for b in blocks),
        'ocr_artifact':plan['ocr_artifact'],'vlm_executed':False,'review_reason':'Curved decorative English labels remain; full raster VLM protection not certified.',
        'usage':{'backend':result.backend,'model':result.model,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'latency_s':result.latency_s,'cached':result.cached}}
    report['real_usage']=page_usage(book,1)
    atomic_json(folder/'p0001-report.json',report)
    return report


def build_mercury_annex(book,config_path='config/pipeline.toml'):
    """Translate the sole editorial label of p501; never send table contents."""
    import tomllib
    from .adapters import require_backend_approval
    from .llm import run_task
    from .fonts import original_font,covers
    from .core import PROTECTED
    book=Path(book).resolve();config_path=Path(config_path).resolve()
    cfg=tomllib.loads(config_path.read_text());paths=cfg['paths']
    require_approval(book,paths['glossary'],paths['style_guide'],paths['do_not_translate'])
    require_backend_approval(book,config_path,'pilot')
    approval=json.loads((book/'work/glossary_approval.json').read_text())
    if approval.get('scope')!='Mercury Tables editorial footer only, source page 501':
        raise Blocked('External appendix needs its explicit limited approval scope')
    expected='cbae3b1513511999e919ead880c347462fe6932de27b0a4e6a7f90ab59f1ac01'
    if sha256(book/'source.pdf')!=expected:raise Blocked('Mercury appendix source mismatch')
    folder=book/'work/pilot';folder.mkdir(parents=True,exist_ok=True)
    with fitz.open(book/'source.pdf') as source:
        page=source[500];rects=page.search_for('Mercury Tables')
        if len(rects)!=1:raise Blocked('Unexpected external footer occurrences')
        rect=rects[0]
        matched=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if 'Mercury Tables' in s['text']]
        if len(matched)!=1:raise Blocked('Unexpected footer font')
        span=matched[0];source_text=page.get_text();before_numbers=Counter(PROTECTED.findall(source_text))
        font=original_font(source,page,span['font'],folder/'fonts')
    result=run_task('translate_page',{'_book':str(book),'_config':str(config_path),'_phase':'pilot','_page':501,
        'blocks':[{'id':'p0501_editorial_footer','source':'Mercury Tables'}]})
    target=result.data['blocks'][0]['translation']
    if not covers(font['font'],target):raise Blocked('External footer original font lacks coverage')
    with fitz.open(book/'source.pdf') as source:
        candidate=fitz.open();candidate.insert_pdf(source,from_page=500,to_page=500);page=candidate[0]
        # PyMuPDF redaction normalizes the complete content stream, shifting
        # untouched table coordinates by ~0.001pt. This hash-bound appendix
        # instead removes only its known footer literal, preserving every table
        # operator byte. It is intentionally not a generic PDF string editor.
        contents=page.get_contents()
        if len(contents)!=1:raise Blocked('Unexpected appendix content stream count')
        candidate.update_stream(contents[0],remove_mercury_footer_literal(candidate.xref_stream(contents[0])))
        # Original embedded small-cap face is retained. Insert on its exact
        # baseline, with no horizontal scale or table reconstruction.
        path=Path(font['path']);page.insert_font(fontname='MercuryFooter',fontfile=str(path))
        color=tuple(((span['color']>>s)&255)/255 for s in (16,8,0))
        point=fitz.Point(rect.x0,span['origin'][1])
        width=font['font'].text_length(target,fontsize=span['size'])
        if point.x+width>page.rect.width-25:raise Blocked('External footer exceeds safe right margin')
        page.insert_text(point,target,fontname='MercuryFooter',fontsize=span['size'],color=color)
        after_numbers=Counter(PROTECTED.findall(page.get_text()))
        if before_numbers!=after_numbers:raise Blocked('External ephemeris protected values changed')
        output=book/'out/anexo_mercurio.pdf';_save_pdf(candidate,output);candidate.close()
    with fitz.open(book/'source.pdf') as source,fitz.open(output) as candidate:
        before=source[500];after=candidate[0]
        # Every character in the table and the entire protected footer prefix
        # must retain its font, position and color. Only the title is excluded.
        def chars(p):
            return [(c['c'],tuple(round(v,3) for v in c['bbox']),s['font'],s['color'])
                    for b in p.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] for c in s['chars']
                    if c['bbox'][1]<590 or c['bbox'][2]<=rect.x0]
        identical=chars(before)==chars(after)
        if not identical:raise Blocked('External ephemeris characters, positions or styles changed')
        page_rect=before.rect
        comparison=fitz.open();p=comparison.new_page(width=page_rect.width*2,height=page_rect.height+25)
        p.insert_text((12,17),'ORIGINAL | TRADUCCION - ANEXO TECNICO',fontsize=9)
        p.show_pdf_page(fitz.Rect(0,25,page_rect.width,page_rect.height+25),source,500)
        p.show_pdf_page(fitz.Rect(page_rect.width,25,page_rect.width*2,page_rect.height+25),candidate,0)
        comparison_output=book/'out/anexo_mercurio_comparativa.pdf';_save_pdf(comparison,comparison_output);comparison.close()
        pix=before.get_pixmap(dpi=110,alpha=False);original=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        pix=after.get_pixmap(dpi=110,alpha=False);translated=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        mask=Image.new('1',original.size);draw=ImageDraw.Draw(mask)
        edit_rect=rect|fitz.Rect(point.x,rect.y0,point.x+width,rect.y1)
        draw.rectangle((math.floor(edit_rect.x0*110/72)-2,math.floor(edit_rect.y0*110/72)-2,math.ceil(edit_rect.x1*110/72)+2,math.ceil(edit_rect.y1*110/72)+2),fill=1)
        outside=int((np.any(np.asarray(original)!=np.asarray(translated),axis=2)&~np.asarray(mask,dtype=bool)).sum())
        if outside:raise Blocked('External appendix changed pixels outside editorial label')
        original.save(folder/'p0501-before.png');translated.save(folder/'p0501-after.png')
        pair=Image.new('RGB',(original.width*2,original.height),'white');pair.paste(original,(0,0));pair.paste(translated,(original.width,0));pair.save(folder/'p0501-comparison.png')
    report={'source_page':501,'printed_page':486,'source_sha256':expected,'separate_technical_appendix':True,
        'source_title':'The Only Astrology Book You’ll Ever Need — Joanna Martine Woolfolk',
        'subtype':'Mercury sign ingress table; not daily longitude ephemeris',
        'output':str(output),'comparison_output':str(comparison_output),'source':'Mercury Tables','target':target,
        'font_original_reused':True,'scale':1.,'table_characters_positions_styles_equal':identical,
        'protected_values_equal':True,'non_text_pixel_diff':0.,'verdict':'revisar','review_reason':'Human review of technical appendix pending; table data intentionally unchanged.',
        'usage':{'backend':result.backend,'model':result.model,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'latency_s':result.latency_s,'cached':result.cached}}
    report['real_usage']=page_usage(book,501)
    atomic_json(folder/'p0501-report.json',report)
    return report


def remove_mercury_footer_literal(data):
    before=b'[(486)-630.3(\\201 Mer)6.6(cur)36.6(y )250.9(T)59.4(ables)] TJ'
    after=b'[(486)-630.3(\\201 )] TJ'
    if data.count(before)!=1:raise Blocked('Known hash-bound Mercury footer literal missing or repeated')
    return data.replace(before,after,1)
