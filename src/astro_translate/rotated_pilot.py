"""Audited supplemental composition for native rotated pilot labels.

The existing native page is the input transaction. Only the omitted labels on
p63 and p69 are added; curve-heavy zodiac wheels remain explicit review items.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from collections import Counter

import numpy as np
import pymupdf as fitz
from PIL import Image,ImageDraw

from .core import Blocked,atomic_json,sha256,require_approval,PROTECTED,_save_pdf
from .fonts import original_font,fallback_font,covers


def _chars(page,full_metrics=False):
    old=fitz.TOOLS.set_small_glyph_heights()
    try:
        fitz.TOOLS.set_small_glyph_heights(not full_metrics)
        return [{**c,'font':s['font'],'size':s['size'],'color':s['color'],'direction':list(l['dir'])}
                for b in page.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] for c in s['chars']]
    finally:fitz.TOOLS.set_small_glyph_heights(old)


def _rect_union(chars):
    rect=fitz.Rect(chars[0]['bbox'])
    for c in chars[1:]:rect|=fitz.Rect(c['bbox'])
    return rect


def _safe_redact(page,units):
    """Tiny glyph-center annotations avoid collateral adjacent text deletion."""
    wanted=[c for u in units for c in u['characters'] if c['c'].strip()]
    selected=[];others=[]
    for c in _chars(page,full_metrics=True):
        found=any(c['c']==w['c'] and c['font']==w['font'] and abs(c['origin'][0]-w['origin'][0])<.015 and abs(c['origin'][1]-w['origin'][1])<.015 for w in wanted)
        (selected if found else others).append(c)
    if len(selected)!=len(wanted):raise Blocked('Rotated source glyphs absent or changed in candidate')
    boxes=[]
    for c in selected:
        r=fitz.Rect(c['bbox']);center=(r.tl+r.br)/2
        tiny=fitz.Rect(center.x-.015,center.y-.015,center.x+.015,center.y+.015)
        if any(tiny.intersects(fitz.Rect(x['bbox'])) for x in others if x['c'].strip()):
            raise Blocked('Rotated redaction overlaps unrelated glyph')
        boxes.append(tiny)
    for r in boxes:page.add_redact_annot(r,fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    return [list(r) for r in boxes]


def curve_coefficients(characters):
    """Least-squares original baseline; source positions are never inferred by LLM."""
    points=np.asarray([c['origin'] for c in characters if c['c'].strip()],dtype=float)
    if len(points)<3 or np.ptp(points[:,0])<10:raise Blocked('Insufficient original curve geometry')
    coeff=np.polyfit(points[:,0],points[:,1],2)
    residual=float(np.max(np.abs(np.polyval(coeff,points[:,0])-points[:,1])))
    if residual>.3:raise Blocked('Original curve cannot be represented by a smooth quadratic safely')
    return coeff.tolist(),residual


def curved_positions(target,font,size,coeff,start):
    positions=[];x=start
    for character in target:
        y=float(np.polyval(coeff,x));slope=2*coeff[0]*x+coeff[1]
        angle=-math.degrees(math.atan(slope))
        positions.append({'character':character,'origin':[x,y],'rotate_degrees':angle})
        x+=font.text_length(character,fontsize=size)
    return positions


def separator_character_proofs(source_page,candidate_page):
    """Actual per-character operators, independent of rawdict line grouping."""
    def visible_bars(page):
        rows=[]
        for span in page.get_texttrace():
            for char in span['chars']:
                if char[0]!=ord('|') or not page.rect.contains(fitz.Point(char[2])):continue
                rows.append({'unicode':char[0],'glyph_id':char[1],'origin':list(char[2]),'bbox':list(char[3]),
                             **{k:list(span[k]) if isinstance(span[k],tuple) else span[k]
                                for k in ('dir','font','size','color','type','opacity')}})
        return sorted(rows,key=lambda x:x['origin'])
    before=visible_bars(source_page);after=visible_bars(candidate_page)
    if len(before)!=len(after):raise Blocked('Original separators missing or duplicated')
    proof=[{'source':a,'candidate':b,'exact_equal':a==b} for a,b in zip(before,after)]
    if not all(p['exact_equal'] for p in proof):raise Blocked('Original separator character operators changed')
    return proof


def prepare_plan(book,number):
    book=Path(book).resolve();folder=book/'work/pilot/rotated';folder.mkdir(parents=True,exist_ok=True)
    if number not in (63,69):raise Blocked('Rotated pilot plan is limited to pages 63 and 69')
    with fitz.open(book/'source.pdf') as doc:
        page=doc[number-1];raw=_chars(page);units=[]
        source_spans=[{'text':s['text'],'bbox':list(s['bbox']),'direction':list(l['dir']),'font':s['font']}
                      for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']
                      if tuple(l['dir'])!=(1.,0.) and s['text'].strip()]
        if number==63:
            glossary={r['source']:r['target'] for r in csv.DictReader(open('glossary/astro_en_es.csv'))}
            for line in [l for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) if tuple(l['dir'])==(0.,-1.)]:
                span=line['spans'][0];source=span['text']
                if source not in ('Neptune','Uranus','Pluto'):raise Blocked('Unexpected vertical planet label')
                chars=[c for c in raw if c['font']==span['font'] and fitz.Rect(c['bbox']).intersects(fitz.Rect(span['bbox']))]
                units.append({'id':f'p0063_rotated_{source.lower()}','source':source,'target':glossary[source],
                              'characters':chars,'bbox':list(_rect_union(chars)),'font':span['font'],'size':span['size'],
                              'color':span['color'],'origin':list(span['origin']),'rotate':90})
            if len(units)!=3:raise Blocked('Expected three vertical planet labels')
            original=original_font(doc,page,units[0]['font'],folder/'fonts/original')
            style={'font':units[0]['font'],'serif':True,'weight':400,'italic':False,'width':75,'size':7.,'color':units[0]['color'],'bold':False}
            union=' '.join(u['target'] for u in units)
            selected=original if covers(original['font'],union) else fallback_font(style,folder/'fonts/fallback',original['font'])
            scale=1.
            for unit in units:
                width=selected['font'].text_length(unit['target'],fontsize=7)
                if width>32:scale=min(scale,32/width)
            scale=math.floor(scale*100)/100
            if scale<.75:raise Blocked('Vertical sibling labels require scale below .75')
            for unit in units:
                size=7*scale;width=selected['font'].text_length(unit['target'],fontsize=size)
                unit['positions']=[{'character':unit['target'],'origin':[unit['origin'][0],unit['bbox'][1]+width],'rotate_degrees':90}]
                unit['render_size']=size;unit['scale']=scale;unit['style']=style
        else:
            chars=[c for c in raw if abs(c['direction'][1])>.001 and 370<c['origin'][1]<410]
            full=''.join(c['c'] for c in chars)
            if ' '.join(full.split())!='THOUGHTS | OPINIONS | MIND | FACTUAL MEMORY':raise Blocked('Unexpected curved legend text')
            separator=[c for c in chars if c['c']=='|'];groups=[[]]
            for char in chars:
                if char['c']=='|':groups.append([])
                else:groups[-1].append(char)
            data=json.loads((folder/'p0069-translation.json').read_text());targets={x['id']:x['translation'] for x in data['data']['labels']}
            coeff,residual=curve_coefficients([c for c in chars if c['font'].startswith('Futura')])
            original=original_font(doc,page,'FuturaCom-MediumCondensed',folder/'fonts/original')
            style={'font':'FuturaCom-MediumCondense','serif':False,'weight':500,'italic':False,'width':75,'size':13.,'color':7303023,'bold':False}
            union=' '.join(targets.values());selected=original if covers(original['font'],union) else fallback_font(style,folder/'fonts/fallback',original['font'])
            scale=1.
            for i,group in enumerate(groups):
                while group and not group[0]['c'].strip():group=group[1:]
                while group and not group[-1]['c'].strip():group=group[:-1]
                source=''.join(c['c'] for c in group);target=targets[f'p0069_legend_{i}'];rect=_rect_union(group)
                left=rect.x0-18 if i==0 else separator[i-1]['origin'][0]+4
                right=rect.x1+20 if i==3 else separator[i]['origin'][0]-4
                advance=selected['font'].text_length(target,fontsize=13)
                scale=min(scale,(right-left)/advance)
                units.append({'id':f'p0069_legend_{i}','source':source,'target':target,'characters':group,'bbox':list(rect),
                              'font':style['font'],'size':13.,'color':style['color'],'style':style,'allowed_x':[left,right],
                              'curve_coefficients':coeff,'curve_residual_pt':residual})
            scale=math.floor(scale*100)/100
            if scale<.75:raise Blocked('Curved sibling labels require scale below .75')
            for unit in units:
                size=13*scale;advance=selected['font'].text_length(unit['target'],fontsize=size)
                center=(unit['bbox'][0]+unit['bbox'][2])/2
                start=max(unit['allowed_x'][0],min(center-advance/2,unit['allowed_x'][1]-advance))
                unit['positions']=curved_positions(unit['target'],selected['font'],size,unit['curve_coefficients'],start)
                unit['render_size']=size;unit['scale']=scale
        if not covers(selected['font'],' '.join(u['target'] for u in units)):raise Blocked('Rotated font coverage incomplete')
        plan={'page':number,'source_sha256':sha256(book/'source.pdf'),'units':units,'group_scale':scale,
              'source_spans':source_spans,
              'chosen_font':{k:v for k,v in selected.items() if k!='font'},'style':style,
              'original_font_reused':selected['source']=='original','glossary_sha256':sha256('glossary/astro_en_es.csv'),
              'style_sha256':sha256('glossary/style_guide.md')}
        atomic_json(folder/f'p{number:04d}-plan.json',plan)
        return plan


def apply_plan(book,number,candidate,*,candidate_page=None):
    """Transactional supplement; returns blocks/regions for the parent QA union."""
    import tomllib
    from .adapters import require_backend_approval
    book=Path(book).resolve();candidate=Path(candidate).resolve()
    if candidate==book/'source.pdf':raise Blocked('Refuse to modify the original source')
    config=Path('config/pipeline.toml');cfg=tomllib.loads(config.read_text());paths=cfg['paths']
    require_approval(book,paths['glossary'],paths['style_guide'],paths['do_not_translate']);require_backend_approval(book,config,'pilot')
    plan=prepare_plan(book,number);folder=book/'work/pilot/rotated';output=folder/f'p{number:04d}-translated.pdf'
    before=fitz.open(candidate);index=candidate_page-1 if candidate_page is not None else number-1 if len(before)>1 else 0
    if not 0<=index<len(before):raise Blocked('Rotated candidate page index invalid')
    doc=fitz.open(stream=before.tobytes(),filetype='pdf');page=doc[index]
    protected_before=Counter(PROTECTED.findall(page.get_text()));image_before=[hashlib.sha256(doc.extract_image(x[0])['image']).hexdigest() for x in page.get_images()]
    redactions=_safe_redact(page,plan['units']);font=plan['chosen_font'];page.insert_font(fontname='PilotRotated',fontfile=font['path'])
    blocks=[]
    for unit in plan['units']:
        color=tuple(((unit['color']>>shift)&255)/255 for shift in (16,8,0))
        for position in unit['positions']:
            if not position['character'].strip():continue
            point=fitz.Point(position['origin']);angle=position['rotate_degrees']
            if abs(angle-90)<.00001:page.insert_text(point,position['character'],fontname='PilotRotated',fontsize=unit['render_size'],color=color,rotate=90)
            else:page.insert_text(point,position['character'],fontname='PilotRotated',fontsize=unit['render_size'],color=color,morph=(point,fitz.Matrix(angle)))
        blocks.append({**unit,'scale_group':f'rotated_p{number}','font_runs':[{'engine_name':font['name'],'size':unit['render_size'],'sha256':font['sha256']}],
                       'target_runs':[{'text':unit['target'],'style':unit['style']}],'fit':True})
    # Get actual glyph extents and text per group; expanded mask is derived from
    # real output glyphs, not an arbitrary whole strip or diagram rectangle.
    rendered=[c for c in _chars(page) if c['font'].replace(' ','').startswith(font['name'].replace(' ',''))]
    # Engine may expose a CFF family name without its redundant Regular suffix.
    if not rendered:
        rendered=[c for c in _chars(page) if c['font'].startswith('FuturaCom') and number==69 or c['font'].startswith('AstroSerif') and number==63]
    for block in blocks:
        positions=block['positions'];anchors=[fitz.Point(p['origin']) for p in positions]
        if number==63:
            relevant=[c for c in rendered if abs(c['origin'][0]-anchors[0].x)<.03 and block['bbox'][1]-1<c['origin'][1]<block['bbox'][1]+35]
        else:
            relevant=[c for c in rendered if any(abs(c['origin'][0]-p.x)<.03 and abs(c['origin'][1]-p.y)<.03 for p in anchors)]
        if not relevant:raise Blocked('Rotated translated glyphs missing')
        text=''.join(c['c'] for c in relevant)
        if ''.join(text.split())!=''.join(block['target'].split()):raise Blocked('Rotated translation extraction mismatch')
        block['edit_bbox']=list(fitz.Rect(block['bbox'])|_rect_union(relevant))
        block['font_runs'][0]['engine_name']=relevant[0]['font']
        if any(c['color']!=block['color'] for c in relevant):raise Blocked('Rotated label color changed')
    if Counter(PROTECTED.findall(page.get_text()))!=protected_before:raise Blocked('Rotated edit changed protected values')
    if [hashlib.sha256(doc.extract_image(x[0])['image']).hexdigest() for x in page.get_images()]!=image_before:raise Blocked('Rotated edit changed image bytes')
    _save_pdf(doc,output);doc.close()
    with fitz.open(output) as after:
        with fitz.open(book/'source.pdf') as original:
            separator_proof=separator_character_proofs(original[number-1],after[index]) if number==69 else []
        bp=before[index].get_pixmap(dpi=110,alpha=False);ap=after[index].get_pixmap(dpi=110,alpha=False)
        a=Image.frombytes('RGB',(bp.width,bp.height),bp.samples);b=Image.frombytes('RGB',(ap.width,ap.height),ap.samples)
        mask=Image.new('1',a.size);draw=ImageDraw.Draw(mask)
        for block in blocks:
            x0,y0,x1,y1=block['edit_bbox'];draw.rectangle((math.floor(x0*110/72)-2,math.floor(y0*110/72)-2,math.ceil(x1*110/72)+2,math.ceil(y1*110/72)+2),fill=1)
        count=int((np.any(np.asarray(a)!=np.asarray(b),axis=2)&~np.asarray(mask,dtype=bool)).sum())
        if count:raise Blocked('Rotated composition altered pixels outside supplementary text')
        a.save(folder/f'p{number:04d}-before.png');b.save(folder/f'p{number:04d}-after.png')
    before.close()
    report={'page':number,'candidate_page':index+1,'source_sha256':plan['source_sha256'],'input_candidate':str(candidate),
            'input_candidate_sha256':sha256(candidate),'output':str(output),'output_sha256':sha256(output),'blocks':blocks,
            'non_text_pixel_diff':0.,'protected_values_equal':True,'image_bytes_equal':True,'group_scale':plan['group_scale'],
            'fit_complete':True,'original_font_reused':plan['original_font_reused'],'redactions':redactions,
            'preserved_separator_proofs':separator_proof,
            'qa_scope':'Supplement vs input candidate only; parent must run full original/combined QA.'}
    report['verified_rotated_spans']=[{**span,'resolution':'preserved_separator' if span['text']=='|' else 'translated',
        'target_ids':[u['id'] for u in plan['units'] if any(fitz.Rect(c['bbox']).intersects(fitz.Rect(span['bbox'])) for c in u['characters'])],
        'source_sha256':plan['source_sha256'],'candidate_sha256':report['output_sha256']} for span in plan['source_spans']]
    atomic_json(folder/f'p{number:04d}-report.json',report)
    return report
