"""Shared geometry-only composition; book policy is supplied by profiles."""
from __future__ import annotations
import copy,re,math
from collections import Counter
import pymupdf as fitz
from PIL import Image
from astro_translate.core import Blocked
import astro_translate.fidelity as fidelity

def book_composition(page,units):
    units=copy.deepcopy(units)
    def correct(style):
        result=dict(style);name=style['font'].lower()
        if any(x in name for x in ('dinnext','frutiger')):result.update(serif=False)
        if 'heavy' in name:result.update(weight=900,bold=True)
        # Lilita One is a heavy sans display face; the subset's generic 400
        # weight and PDF serif flag do not describe its original outlines.
        if re.sub(r'[^a-z]','',name).endswith('lilitaone'):result.update(serif=False,weight=900,bold=True)
        return result
    for u in units:
        u['style']=correct(u['style']);u['target_runs']=[{**r,'style':correct(r['style'])} for r in u['target_runs']]
        if u.get('merged_from') and u.get('alignment')=='center':
            u.update(centered_caption=True,first_line_indent=0,column_bounds=[u['bbox'][0],u['bbox'][2]])
        if u['kind']=='paragraph' and u.get('alignment')=='left':u['first_line_indent']=max(0,u['spans'][0]['bbox'][0]-u['bbox'][0])
        if len({r['style']['size'] for r in u['target_runs']})>1 and u.get('line_height',1.05)<1.05:
            u['original_extracted_line_height']=u['line_height'];u['line_height']=1.15
        if len(u['target_runs'])==3 and len(u['spans'])>=3 and u['target_runs'][0]['style']['size']>u['target_runs'][1]['style']['size'] and u['spans'][2]['bbox'][1]>u['spans'][1]['bbox'][3] and u['target_runs'][1]['style']['font']!=u['target_runs'][2]['style']['font']:
            u['paragraphs']=[{'target_runs':u['target_runs'][:2],'indent':u.get('first_line_indent',0)},{'target_runs':u['target_runs'][2:],'indent':max(0,u['spans'][2]['bbox'][0]-u['bbox'][0])}]
    rotated=[fitz.Rect(x['bbox']) for x in fidelity.omitted_rotated_text(page)]
    for u in units:
        if any(fitz.Rect(u['bbox']).intersects(r) for r in rotated):u['vector']=True
    result=[]
    for u in units:
        prev=result[-1] if result else None
        join=(prev is not None and u['size']>=15 and prev['size']==u['size'] and prev['scale_group']==u['scale_group'] and abs(prev['bbox'][0]-u['bbox'][0])<2 and ((prev['kind']==u['kind']=='paragraph' and abs(prev['bbox'][2]-u['bbox'][2])<5) or (prev.get('source_block') is not None and prev.get('source_block')==u.get('source_block'))) and -1<=u['bbox'][1]-prev['bbox'][3]<u['size']*.5 and not re.search(r'[.!?][”’]?$',prev['source'].rstrip()))
        if join:
            prev['kind']='paragraph';prev.setdefault('composed_ids',[prev['id']]).append(u['id']);prev['source']+=' '+u['source'];prev['target']+=' '+u['target'];prev['target_runs'][-1]['text']+=' ';prev['target_runs']+=u['target_runs'];prev['spans']+=u['spans'];prev['bbox']=list(fitz.Rect(prev['bbox'])|fitz.Rect(u['bbox']))
        else:result.append(u)
    for u in result:
        if u['size']<15 or u['kind']!='paragraph' or re.search(r'[.!?][”’]?$',u['source'].rstrip()):continue
        end=fitz.Rect(u['spans'][-1]['bbox'])
        matches=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if s['text'].strip()=='.' and abs(s['bbox'][0]-end.x1)<.1 and abs(s['size']-u['size'])<.01 and fitz.Rect(s['bbox']).intersects(end+(-1,-1,8,1))]
        if len(matches)==1:
            from astro_translate.fidelity import span_style
            raw=matches[0];span={'source':raw['text'],'bbox':list(raw['bbox']),'font':raw['font'],'size':raw['size'],'color':raw['color'],'flags':raw['flags'],'rotate':0,'style':span_style(raw)}
            u['spans'].append(span);u['bbox']=list(fitz.Rect(u['bbox'])|fitz.Rect(span['bbox']));u['source']+='.';u['target']+='.';u['target_runs'].append({'text':'.','style':span['style']});u['relocated_punctuation']=[span]
    # Single extracted lines have no alignment metadata. Recover a right
    # anchor only from a clear stack: equally sized uppercase display lines,
    # close vertical spacing, a shared right edge and materially different
    # left edges. This permits safe growth to the left of a wide translated
    # title instead of clipping its growth against the page's right edge.
    display=[u for u in result if u['kind']=='label' and u['size']>=30
             and u['source'].isupper() and abs(fitz.Rect(u['bbox']).height-u['size'])<1]
    for upper in display:
        a=fitz.Rect(upper['bbox'])
        for lower in display:
            if upper is lower or abs(upper['size']-lower['size'])>.1:continue
            b=fitz.Rect(lower['bbox']);em=upper['size']
            if (0<=b.y0-a.y1<=em*.5 and abs(a.x1-b.x1)<=em*.25
                    and abs(a.x0-b.x0)>em):
                upper.update(alignment='right',first_line_indent=0)
                lower.update(alignment='right',first_line_indent=0)
    return [part for u in result for part in split_cells(u)]


def verify_immutable_float_roundoff(source,candidate,page_number,extras,*,candidate_index=None):
    """Resolve only float32 bbox rounding when the actual protected ink is identical.

    Single-page candidates (output_page=0) pass candidate_index explicitly."""
    a=source[page_number-1];b=candidate[page_number-1 if candidate_index is None else candidate_index]
    old=fidelity._immutable_characters(a,extras);new=fidelity._immutable_characters(b,extras,False);missing=old-new
    if not missing:return {'ok':True,'resolved_characters':0}
    pa=a.get_pixmap(dpi=220);pb=b.get_pixmap(dpi=220)
    ia=Image.frombytes('RGB',(pa.width,pa.height),pa.samples);ib=Image.frombytes('RGB',(pb.width,pb.height),pb.samples)
    pairs=[];available=new.copy()
    for key,count in missing.items():
        options=[k for k in available if k[:2]==key[:2] and available[k]>=count and max(abs(x-y) for x,y in zip(k[2],key[2]))<=.00101]
        if len(options)!=1:return {'ok':False,'reason':'No unique sub-millipoint protected match'}
        near=options[0];rect=fitz.Rect(key[2])|fitz.Rect(near[2]);scale=220/72
        box=(max(0,math.floor(rect.x0*scale)-2),max(0,math.floor(rect.y0*scale)-2),min(ia.width,math.ceil(rect.x1*scale)+2),min(ia.height,math.ceil(rect.y1*scale)+2))
        if ia.crop(box).tobytes()!=ib.crop(box).tobytes():return {'ok':False,'reason':'Protected ink crop differs'}
        available[near]-=count;pairs.append({'source_bbox':list(key[2]),'candidate_bbox':list(near[2]),'bbox_max_delta_pt':max(abs(x-y) for x,y in zip(key[2],near[2])),'ink_crop_equal_220dpi':True,'count':count})
    return {'ok':True,'resolved_characters':sum(x['count'] for x in pairs),'matches':pairs,'scope':'Same character and font, unique bbox match <=0.00101pt, protected ink pixels exact at220dpi'}

def precise_redaction_rects(page,unit):
    """Match nominal source spans exactly, then avoid every unrelated full-metric glyph."""
    prior=fitz.TOOLS.set_small_glyph_heights()
    try:
        fitz.TOOLS.set_small_glyph_heights(True);small=page.get_text('rawdict',flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
        fitz.TOOLS.set_small_glyph_heights(False);full=page.get_text('rawdict',flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
    finally:fitz.TOOLS.set_small_glyph_heights(prior)
    flat=lambda doc:[s for b in doc['blocks'] for l in b.get('lines',[]) for s in l['spans']]
    a,b=flat(small),flat(full)
    if len(a)!=len(b):raise Blocked('Full-metric glyph mapping differs')
    selected=[];others=[];matches=0
    for sa,sb in zip(a,b):
        ta=''.join(c['c'] for c in sa['chars']);tb=''.join(c['c'] for c in sb['chars'])
        if ta!=tb or sa['font']!=sb['font'] or [c['origin'] for c in sa['chars']]!=[c['origin'] for c in sb['chars']]:raise Blocked('Full-metric glyph mapping differs')
        own=any(ta==s['source'] and sa['font']==s['font'] and max(abs(x-y) for x,y in zip(sa['bbox'],s['bbox']))<.01 for s in unit['spans'])
        if own:matches+=1
        (selected if own else others).extend(fitz.Rect(c['bbox']) for c in sb['chars'] if c['c'].strip())
    if matches!=len(unit['spans']) or not selected:raise Blocked('Cannot identify every source span exactly for precise redaction')
    rects=[]
    for rect in selected:
        chosen=None
        for fy in (.5,.65,.35,.8,.2):
            for fx in (.5,.3,.7):
                x=rect.x0+rect.width*fx;y=rect.y0+rect.height*fy;box=fitz.Rect(x-.01,y-.01,x+.01,y+.01)
                if not any(box.intersects(r) for r in others):chosen=box;break
            if chosen is not None:break
        if chosen is None:raise Blocked('No isolated point for selected source glyph')
        rects.append(chosen)
    return rects

def shifted_candidates(page,unit,units,guards):
    base=fitz.Rect(unit['bbox']);others=[fitz.Rect(u['bbox']) for u in units if u['id']!=unit['id']]+list(guards)
    if unit.get('native_ordinal') or unit.get('column_bounds') or unit.get('glyph_spacing_boundary'):return []
    # A quote framed by vector quote marks stays anchored to them.
    from astro_translate.display_quotes import framed
    if framed(page,unit):return []
    result=[]
    if unit.get('continuous_title'):
        # Positioned words may straddle a puzzle tab. Search for a whole line
        # alongside the tab, never join the words on top of its artwork. Keep
        # the original vertical anchor and one-line height; final ink guards
        # prove that the new position clears both raster and vector art.
        span_gaps=[max(0,b['bbox'][0]-a['bbox'][2]) for a,b in zip(unit['spans'],unit['spans'][1:])]
        reach=max(span_gaps,default=0)+unit['size']*4
        for dx in range(0,int(reach)+1,2):
            candidate=base+(dx,0,dx,1)
            if page.rect.contains(candidate) and not any(candidate.intersects(r) for r in others):
                result.append(candidate)
        return result
    distance=(1,2,4,6,8,10,12,16,20) if unit.get('kind')=='paragraph' else (1,2,4,6)
    for n in distance:
        for dx,dy in ((n,0),(-n,0),(0,n),(0,-n)):
            candidate=base+(dx,dy,dx,dy)
            if page.rect.contains(candidate) and not any((candidate&r).get_area()>(base&r).get_area()+.01 for r in others):result.append(candidate)
    if unit.get('kind')=='paragraph' and unit.get('size',0)<30:
        # Settle a little below a decoration touching the first line (accents
        # need it) while growing into the free space below; guards verify ink.
        for dy in (1,2):
            for down in (4,8,12,16,20):
                candidate=base+(0,dy,0,dy+down)
                if page.rect.contains(candidate) and not any((candidate&r).get_area()>(base&r).get_area()+.01 for r in others):result.append(candidate)
    if unit.get('kind')=='paragraph' and unit.get('size',0)>=30:
        for width in (8,16,24,32,40,60,80):
            candidate=base+(0,0,width,4)
            if page.rect.contains(candidate) and not any((candidate&r).get_area()>(base&r).get_area()+.01 for r in others):result.append(candidate)
    if unit.get('kind')=='label':
        for width in (8,16,24,32,48,64):
            for left,right in ((width,0),(width/2,width/2),(0,width)):
                for height in (2,4,8,12,16):
                    candidate=base+(-left,-1,right,height)
                    if page.rect.contains(candidate) and not any((candidate&r).get_area()>(base&r).get_area()+.01 for r in others):result.append(candidate)
    return result


def exact_vector_mask(page,dpi=110):
    from astro_translate.caption_growth import vector_mask
    return vector_mask(page,dpi)


def split_cells(unit):
    spans=unit['spans']
    # Nearby words form a continuous editorial title, even when PDF content
    # order is reversed or a puzzle tab separates the words. Distant column
    # headers are independent cells: measure the gaps in physical reading
    # order before exempting anything from the original table partitioner.
    ordered=sorted(spans,key=lambda span:span['bbox'][0])
    largest_gap=max((b['bbox'][0]-a['bbox'][2] for a,b in zip(ordered,ordered[1:])),default=0)
    if (unit.get('context')=='running_text' and unit.get('kind')=='paragraph'
        and len(spans)>1 and unit['source'].isupper()
        and max(s['bbox'][1] for s in spans)-min(s['bbox'][1] for s in spans)<.1
        and len({s['size'] for s in spans})==1 and largest_gap<=unit['size']*4):
        unit['continuous_title']=True
        # PDF content order is not reading order. A right-hand run emitted
        # first must not create a paragraph indent on a single title line.
        unit['first_line_indent']=0
        return [unit]
    if unit['size']>=15:return [unit]
    if len(spans)<2 or max(s['bbox'][1] for s in spans)-min(s['bbox'][1] for s in spans)>1 or any(len(s['source'].strip())>30 for s in spans):return [unit]
    groups=[]
    for index,span in enumerate(spans):
        if not groups or abs(span['bbox'][0]-spans[groups[-1][-1]]['bbox'][2])>(3 if unit.get('vector') else 20):groups.append([])
        groups[-1].append(index)
    if len(groups)<2:return [unit]
    runs=unit['target_runs'];mapped=[]
    if len(runs)==len(spans):mapped=[[copy.deepcopy(r)] for r in runs]
    elif len(runs)==1 and len(unit['target'].split())==sum(len(s['source'].split()) for s in spans):
        words=unit['target'].split();offset=0
        for span in spans:
            count=len(span['source'].split());mapped.append([{'text':' '.join(words[offset:offset+count]),'style':runs[0]['style']}]);offset+=count
    else:return [unit]
    result=[]
    for index,group in enumerate(groups):
        ss=[spans[i] for i in group];rr=[r for i in group for r in mapped[i]]
        for r in rr[:-1]:r['text']=r['text'].rstrip()+' '
        rr[-1]['text']=rr[-1]['text'].rstrip();box=fitz.Rect(ss[0]['bbox'])
        for span in ss[1:]:box|=fitz.Rect(span['bbox'])
        result.append({**unit,'id':unit['id']+f'_cell{index}','source':' '.join(s['source'].strip() for s in ss),'target':''.join(r['text'] for r in rr),'spans':ss,'target_runs':rr,'bbox':list(box),'kind':'label','context':'table_label','cell_partition':'exact style-run alignment or equal source/target word count; no new lexical content','first_line_indent':0})
    return result
