"""Phase 1d: style-preserving containers and granular protected values.

Kept separate from the frozen conservative Fase 1c extraction/rendering path.
"""
from __future__ import annotations
import re
import json
import html
import hashlib
from collections import Counter,defaultdict
from pathlib import Path
import pymupdf as fitz
from .core import Blocked,SYMBOL_FONT,PROTECTED,sha256,atomic_json,book_lock,require_approval,read_extras,_save_pdf
from .layout import drawing_obstacles,foreground_images,background_clear

NUMBER_PATTERN=re.compile(r'\d+(?:[.,:/−+\-]\d+)*')
SYMBOL_PATTERN=re.compile(r'[\u2600-\u27ff\ue000-\uf8ff°º′″]')
ABBREVIATIONS=re.compile(r'^(?:MC|IC|Ac|Dc|Mc|Ic|ASC|Asc|DSC|R|Rx|UT|GMT)$',re.I)

def span_style(span):
    name=span['font'];lower=name.lower();flags=span.get('flags',0)
    serif=('soho' in lower and 'gothic' not in lower) or bool(flags&4)
    if any(x in lower for x in ('helvetica','arial','sans')):serif=False
    weight=700 if ('bold' in lower or flags&16) else 500 if 'medium' in lower else 300 if 'light' in lower else 400
    return {'font':name,'size':span['size'],'color':span['color'],'weight':weight,'bold':weight>=600,'italic':bool(flags&2) or any(x in lower for x in ('italic','oblique')),'serif':serif,'width':60 if 'compressed' in lower else 75 if 'condensed' in lower else 100}

def _union(spans):
    rect=fitz.Rect(spans[0]['bbox'])
    for span in spans[1:]:rect|=fitz.Rect(span['bbox'])
    return rect

def immutable_span(span,extras=()):
    text=span['source'].strip()
    # Letter-spaced chart angles («D C», «I C») are the same protected tokens.
    spaced=re.fullmatch(r'[A-Za-z](?: [A-Za-z]){1,2}',text) and ABBREVIATIONS.fullmatch(text.replace(' ',''))
    return bool(re.fullmatch(r'(?:st|nd|rd|th)',text,re.I) or re.fullmatch(r'\d{3}[–-]\d{6}[–-][A-Za-z]{3}/\d{4}',text) or SYMBOL_FONT.search(span['font']) or (SYMBOL_PATTERN.search(text) and not re.search(r'[A-Za-zÀ-ÿ]',text)) or ABBREVIATIONS.fullmatch(text) or spaced or re.fullmatch(r'[\d\s.,:/−+\-]+',text) or text in extras)

def _make_unit(spans,number,table,drawings):
    source='';runs=[]
    for span in spans:
        text=span['source'].strip()
        if not text:continue
        if source and not re.match(r'^[,.;:!?]',text):source+=' '
        start=len(source);source+=text;style=span['style']
        if runs and runs[-1]['style']==style:runs[-1]['end']=len(source)
        else:runs.append({'start':start,'end':len(source),'style':style})
    # Attach inserted inter-run whitespace to the preceding run.
    for a,b in zip(runs,runs[1:]):a['end']=b['start']
    rect=_union(spans);vector=any(rect.intersects(r) for r in drawings)
    size=max(s['size'] for s in spans);style=spans[0]['style'];kind='paragraph' if len(spans)>1 else 'label'
    ys={round(s['bbox'][1],1) for s in spans};xs=[s['bbox'][0] for s in spans];ends=[s['bbox'][2] for s in spans]
    align='right' if len(ys)>1 and max(ends)-min(ends)<4 and max(xs)-min(xs)>4 else 'left'
    line_tops=[]
    for span in spans:
        y=span['bbox'][1]
        if not line_tops or abs(y-line_tops[-1])>size*.5:line_tops.append(y)
    leading=sorted(b-a for a,b in zip(line_tops,line_tops[1:]) if b>a)
    line_height=leading[len(leading)//2]/size if leading else 1.05
    return {'source':source,'line_height':line_height,'bbox':list(rect),'spans':spans,'style_runs':runs,'style':style,'font':style['font'],'size':size,'color':style['color'],'rotate':0,'kind':kind,'context':'table_label' if table else 'figure_label' if vector else 'running_text','vector':vector,'alignment':align,'source_block':number}

def extract_fidelity_units(page,extras=(),*,merge_captions=None,spatial_captions=True):
    """One paragraph container with aligned inline style runs, no symbol-font text."""
    containers=[];drawings=drawing_obstacles(page)
    for block in page.get_text('dict')['blocks']:
        if block.get('type')!=0:continue
        table=sum(bool(re.search(r'\d',s['text'])) for line in block['lines'] for s in line['spans'])>=3 and len(block['lines'])>8
        current=[];previous_line=None
        for line in block['lines']:
            line_items=[]
            for span in line['spans']:
                item={'source':span['text'],'bbox':list(span['bbox']),'font':span['font'],'size':span['size'],'color':span['color'],'flags':span['flags'],'rotate':0 if tuple(line['dir'])==(1.,0.) else None,'style':span_style(span)}
                if not item['source'].strip():continue
                if immutable_span(item,extras) or item['rotate'] is None:
                    if current:containers.append(_make_unit(current,block['number'],table,drawings));current=[]
                    continue
                if table:
                    if current:containers.append(_make_unit(current,block['number'],table,drawings));current=[]
                    containers.append(_make_unit([item],block['number'],table,drawings))
                else:line_items.append(item)
            if not line_items:previous_line=None;continue
            # Distinct-line heading/body styles create separate containers.
            if current and previous_line:
                prevstyles={json.dumps(x['style'],sort_keys=True) for x in previous_line};newstyles={json.dumps(x['style'],sort_keys=True) for x in line_items}
                y_previous=max(x['bbox'][3] for x in previous_line);y_current=min(x['bbox'][1] for x in line_items)
                heading_change=(len(prevstyles)==1 and len(newstyles)==1 and prevstyles!=newstyles and y_current>=y_previous-2)
                if heading_change:containers.append(_make_unit(current,block['number'],table,drawings));current=[]
            current.extend(line_items);previous_line=line_items
        if current:containers.append(_make_unit(current,block['number'],table,drawings))
    # Caption paragraphs can be split into several PDF blocks by their ragged
    # alignment. Rejoin matching condensed body styles, never separate headings.
    merged=[]
    for unit in containers:
        if merged:
            last=merged[-1];a=fitz.Rect(last['bbox']);b=fitz.Rect(unit['bbox']);same=last['style']==unit['style']
            continuation=same and unit['size']<=8.1 and unit['style']['width']<100 and -.1<=b.y0-a.y1<=4 and (abs(a.x1-b.x1)<4 or abs(a.x0-b.x0)<4)
            if continuation:
                merged[-1]=_make_unit(last['spans']+unit['spans'],last['source_block'],False,drawings);continue
        merged.append(unit)
    from .profiles import profile_for_page
    merged=profile_for_page(page).extract(page,merged,drawings)
    for unit in merged:
        # Per-line visible edges: span ends include trailing spaces and inner runs.
        if unit.get('alignment')=='left' and _right_aligned_lines(page,unit):unit['alignment']='right'
    for index,unit in enumerate(merged):
        if index+1<len(merged):
            following=merged[index+1]
            if unit['source_block']==following['source_block'] and unit['size']>following['size']:
                unit['alignment']=following['alignment']
                if abs(unit['bbox'][2]-following['bbox'][2])<3 and unit['bbox'][0]>following['bbox'][0]+4:unit['alignment']='right'
        if unit['bbox'][3]<page.rect.height*.07 and unit['bbox'][0]>page.rect.width/2:unit['alignment']='right'
        unit['id']=f'u{index:04d}';unit['container_id']=f'p{page.number+1:04d}_c{index:04d}'
        # Homogeneous lists/caption siblings share size and scale regardless of
        # whether individual labels happen to retain identical EN/ES wording.
        if unit['context']=='table_label':group=f'table_b{unit["source_block"]}_{unit["size"]}_{unit["style"]["weight"]}_{unit["style"]["width"]}'
        elif unit['style']['width']<100 and unit['size']<=9:group=f'caption_{unit["size"]}_{unit["style"]["weight"]}'
        elif unit['size']<=9:group=f'body_{unit["size"]}'
        else:group=f'display_{unit["size"]}'
        unit['scale_group']=f'p{page.number+1:04d}_{group}'
    if merge_captions is None:merge_captions=profile_for_page(page).data.get("editorial",{}).get("merge_captions",False)
    if merge_captions:
        from .captions import merge_caption_units
        merged=merge_caption_units(page,merged)
        if spatial_captions:
            from .figure_captions import resegment_captions
            merged=resegment_captions(page,merged)
    return merged

def _slice_runs(runs,start,end):
    result=[];offset=0
    for run in runs:
        stop=offset+len(run['text']);a=max(start,offset);b=min(end,stop)
        if a<b:result.append({**run,'text':run['text'][a-offset:b-offset]})
        offset=stop
    return result

def _index_entries(unit):
    references=re.compile(r'\d+(?:[–—−-]\d+)?(?:[,\s]+\d+(?:[–—−-]\d+)?)*,?')
    def pieces(text):
        bounds=[];start=0
        prefix=re.match(r'^\([^)]*\);\s*MC\s*\([^)]*\)',text)
        if prefix:bounds.append((0,prefix.end(),None));start=prefix.end()
        for match in references.finditer(text,start):
            bounds.append((start,match.end(),re.sub(r'\s','',match.group())));start=match.end()
        if text[start:].strip():bounds.append((start,len(text),None))
        return bounds
    source=pieces(unit['source']);target=pieces(unit['target'])
    if len(source)<=1:return [unit]
    if len(source)!=len(target) or [x[2] for x in source]!=[x[2] for x in target]:raise Blocked('Index reference sequences do not align')
    offsets=[];cursor=0
    for span in unit['spans']:
        text=span['source'].strip();start=unit['source'].find(text,cursor)
        if start<0:raise Blocked('Index span provenance does not align')
        offsets.append((start,start+len(text),span));cursor=start+len(text)
    result=[]
    for index,((a,b,_),(x,y,_)) in enumerate(zip(source,target)):
        spans=[span for start,end,span in offsets if start<b and end>a]
        if any(start<a and unit['source'][start:a].strip() or end>b and unit['source'][b:end].strip() for start,end,span in offsets if span in spans):raise Blocked('Index entry shares an ambiguous source span')
        while x<y and unit['target'][x].isspace():x+=1
        while y>x and unit['target'][y-1].isspace():y-=1
        child={**unit,'id':unit['id']+f'_entry{index}','source':unit['source'][a:b].strip(),'target':unit['target'][x:y],'target_runs':_slice_runs(unit['target_runs'],x,y),'spans':spans,'bbox':list(_union(spans)),'kind':'label','first_line_indent':0}
        result.append(child)
    return result

def _break_index(spans):
    """First span that starts a separate unit merged by the PDF block.

    A size change at a line break (an 8 pt cross-reference under 11 pt text) or
    another weight starting above, in another column (two captions in one
    block). A leading one-line part is a heading: run_in_layout owns it.
    """
    for k in range(1,len(spans)):
        a,b=spans[k-1],spans[k];ra,rb=fitz.Rect(a['bbox']),fitz.Rect(b['bbox'])
        size=abs(a['size']-b['size'])>.5 and rb.y0>=ra.y1-2
        gap=2*b['size']
        jump=(a['style']['weight']!=b['style']['weight'] and rb.y0<ra.y0-2
              and (rb.x0>ra.x1+gap or rb.x1<ra.x0-gap))
        if not (size or jump):continue
        head=spans[:k]
        if len({round(x['bbox'][1]) for x in head})==1 and not jump:continue
        return k
    return None


LINE_ITEM_SPECIAL=('paragraphs','story_frames','display_quote','protected_label','native_ordinal','contents_column',
                   'field_rows','chart_callout','display_lines','drop_cap','display_title','run_in_heading','story_members')
_SENTENCE_END=re.compile(r'[.!?…][”’"»)]*(?=\s+[¿¡“"(«A-ZÁÉÍÓÚÑ0-9]|\s*$)')

def _item_lines(unit):
    """Source lines with their offsets in ``unit['source']`` (as built by _make_unit)."""
    source='';lines=[]
    for span in unit['spans']:
        text=span['source'].strip()
        if not text:continue
        if source and not re.match(r'^[,.;:!?]',text):source+=' '
        start=len(source);source+=text
        if lines and abs(span['bbox'][1]-lines[-1]['spans'][-1]['bbox'][1])<unit['size']*.5:
            lines[-1]['spans'].append(span);lines[-1]['end']=len(source)
        else:lines.append({'spans':[span],'start':start,'end':len(source)})
    if source!=unit['source']:return None
    for line in lines:
        line['text']=source[line['start']:line['end']];line['bbox']=_union(line['spans'])
    return lines

def _nth(matches,k):
    matches=list(matches)
    return matches[k] if 0<=k<len(matches) else None

def _item_cut(unit,lines,i,terms,kind):
    """Target offset of the break between source lines i and i+1, from exact evidence only."""
    source,target,head,tail=unit['source'],unit['target'],lines[i],lines[i+1]
    if kind=='bullet':
        if source.count('•')!=target.count('•'):return None
        m=_nth(re.finditer('•',target),source[:tail['start']].count('•'));return m.start() if m else None
    if kind=='style':
        runs=unit['target_runs'];starts=[r['start'] for r in unit['style_runs']]
        if [r['style'] for r in runs]!=[r['style'] for r in unit['style_runs']] or tail['start'] not in starts:return None
        return sum(len(r['text']) for r in runs[:starts.index(tail['start'])])
    if kind=='number':
        last=list(NUMBER_PATTERN.finditer(head['text']))
        if not last or last[-1].end()!=len(head['text'].rstrip(' .,;:')):return None
        value=last[-1].group();before=[m for m in NUMBER_PATTERN.finditer(source[:head['start']+last[-1].end()]) if m.group()==value]
        found=[m for m in NUMBER_PATTERN.finditer(target) if m.group()==value]
        if len(found)!=len([m for m in NUMBER_PATTERN.finditer(source) if m.group()==value]):return None
        m=_nth(found,len(before)-1)
        if not m:return None
        stop=m.end()
        while stop<len(target) and target[stop] in '.,;:':stop+=1
        return stop
    if kind=='sentence':
        if not _SENTENCE_END.search(head['text']):return None
        ends=list(_SENTENCE_END.finditer(target))
        if len(ends)!=len(list(_SENTENCE_END.finditer(source))):return None
        m=_nth(ends,len(list(_SENTENCE_END.finditer(source[:head['end']])))-1);return m.end() if m else None
    if kind=='term':
        lower=target.lower();cuts=set()
        first=tail['text'].split()[0]
        if ABBREVIATIONS.fullmatch(first) and target.count(first)==1:cuts.add(target.index(first))
        for line,at_start in ((tail,True),(head,False)):
            for row in terms:
                if row['source'].lower()==line['text'].lower().strip(' .,;:') and row['target']:
                    hits=[m.start() for m in re.finditer(re.escape(row['target'].lower()),lower)]
                    if len(hits)==1:cuts.add(hits[0] if at_start else hits[0]+len(row['target']))
        normal=set()
        for cut in cuts:
            while cut<len(target) and target[cut]==' ':cut+=1
            if 0<cut<len(target):normal.add(cut)
        return normal.pop() if len(normal)==1 else None
    return None

def split_line_items(page,units,terms=()):
    """Keep the original line breaks of list-like units in composition.

    Bullets («• Item» per line), credit/record lines (role + name style runs),
    legend rows and forced breaks of centred legal blocks (blank line or short
    line ending a sentence or number) are composed one source line (segment)
    per child unit. The target is cut only on exact evidence (bullets, style-run
    sequence, protected numbers, sentence counts, approved glossary terms);
    otherwise the unit stays whole. Ids, sources and saved translations stay
    bound to the extraction, as in ``split_style_breaks``.
    """
    result=[]
    for unit in units:
        lines=None if any(unit.get(k) for k in LINE_ITEM_SPECIAL) or unit.get('rotate') or not unit.get('target_runs') else _item_lines(unit)
        if not lines or len(lines)<2:
            result.append(unit);continue
        upper=[bool(re.match(r'[•A-ZÁÉÍÓÚÑ]',l['text'])) or bool(ABBREVIATIONS.fullmatch(l['text'].split()[0])) for l in lines]
        widest=max(l['bbox'].width for l in lines)
        visible=visible_lines(page,unit);centers=[(l['x0']+l['x1'])/2 for l in visible]
        centred=unit.get('alignment')=='center' or (max(centers)-min(centers)<1.5 and max(l['x0'] for l in visible)-min(l['x0'] for l in visible)>4)
        styles={json.dumps(r['style'],sort_keys=True) for r in unit['style_runs']}
        cuts={}
        for i in range(len(lines)-1):
            head,tail=lines[i],lines[i+1]
            if tail['text'].startswith('•'):cuts[i]=_item_cut(unit,lines,i,terms,'bullet');continue
            if unit['size']>9 or not upper[i+1]:continue
            gap=tail['bbox'].y0-head['bbox'].y1
            forced=gap>unit['size']*.5 or (centred and head['bbox'].width<.8*widest)
            if forced:
                cut=_item_cut(unit,lines,i,terms,'number')
                cuts[i]=cut if cut is not None else _item_cut(unit,lines,i,terms,'sentence')
        cuts={i:c for i,c in cuts.items() if c is not None}
        if not cuts and unit['size']<=9 and all(upper):
            # Record lines (credits): every break falls on a style-run boundary.
            record={i:_item_cut(unit,lines,i,terms,'style') for i in range(len(lines)-1)} if len(styles)>1 else {}
            # Legend rows: single style, short rows, no prose punctuation, glossary/protected anchors.
            legend=({i:_item_cut(unit,lines,i,terms,'term') for i in range(len(lines)-1)}
                    if len(styles)==1 and all(len(l['text'].split())<=5 and not re.search(r'[,.;:]$',l['text']) for l in lines)
                    and max(l['bbox'].x0 for l in lines)-min(l['bbox'].x0 for l in lines)<1 else {})
            for candidate in (record,legend):
                if candidate and all(v is not None for v in candidate.values()):cuts=candidate;break
        order=[cuts[i] for i in sorted(cuts)]
        if not cuts or order!=sorted(set(order)) or order[0]<=0 or order[-1]>=len(unit['target']):
            result.append(unit);continue
        bounds=[0]+[i+1 for i in sorted(cuts)]+[len(lines)];offsets=[0]+order+[len(unit['target'])]
        segments=[(lines[a:b],offsets[k],offsets[k+1]) for k,(a,b) in enumerate(zip(bounds,bounds[1:]))]
        children=[];valid=True
        for index,(segment,a,b) in enumerate(segments):
            piece=[dict(r) for r in _slice_runs(unit['target_runs'],a,b)]
            text=''.join(r['text'] for r in piece);src=unit['source'][segment[0]['start']:segment[-1]['end']]
            if piece:piece[0]['text']=piece[0]['text'].lstrip();piece[-1]['text']=piece[-1]['text'].rstrip()
            piece=[r for r in piece if r['text']]
            if (not piece or Counter(NUMBER_PATTERN.findall(text))!=Counter(NUMBER_PATTERN.findall(src))
                    or text.count('•')!=src.count('•') or Counter(PROTECTED.findall(text))!=Counter(PROTECTED.findall(src))):
                valid=False;break
            part=_make_unit([s for l in segment for s in l['spans']],unit['source_block'],False,[])
            child={**unit,**{k:part[k] for k in ('source','spans','bbox','style_runs','style','font','size','color','line_height')},
                   # The parent's kind keeps its routing (caption growth, shifts).
                   'kind':unit['kind'],'alignment':'center' if centred else unit.get('alignment','left'),
                   'target':''.join(r['text'] for r in piece),'target_runs':piece,'split_from':unit['id'],'first_line_indent':0,
                   'line_item':True,'single_line':len(segment)==1}
            if index:
                child['id']=unit['id']+f'_line{index}'
                for extra in ('composed_ids','merged_from','relocated_punctuation'):child.pop(extra,None)
            children.append(child)
        result.extend(children if valid else [unit])
    return result

def center_stacked_lines(page,units):
    """Single-line units stacked on one centred axis are centred, not left-set.

    A one-line unit carries no alignment evidence of its own. A vertical chain
    of at least three adjacent small units (line items included) whose visible
    line centres agree within 1.5 pt, with distinct starts, is a centred column
    (credits and legal pages). Only ``left`` single-line members change.
    """
    eligible=[]
    for u in units:
        if any(u.get(k) for k in LINE_ITEM_SPECIAL) or u.get('rotate') or u['size']>10 or not u.get('spans'):continue
        lines=visible_lines(page,u)
        if not lines:continue
        centre=sum((l['x0']+l['x1'])/2 for l in lines)/len(lines)
        if max(abs((l['x0']+l['x1'])/2-centre) for l in lines)>=1.5:continue
        eligible.append((u,centre,fitz.Rect(u['bbox']),min(l['x0'] for l in lines),len(lines)))
    eligible.sort(key=lambda e:e[2].y0)
    chains=[]
    for entry in eligible:
        for chain in chains:
            last=chain[-1]
            if abs(last[1]-entry[1])<1.5 and -1<=entry[2].y0-last[2].y1<=entry[0]['size']*1.5:
                chain.append(entry);break
        else:chains.append([entry])
    result={id(u):u for u in units}
    for chain in chains:
        if len(chain)<3 or max(e[3] for e in chain)-min(e[3] for e in chain)<=4:continue
        for u,_,_,_,count in chain:
            if count==1 and u.get('alignment')=='left':
                result[id(u)]={**u,'alignment':'center','first_line_indent':0}
    return [result[id(u)] for u in units]

def split_style_breaks(page,units):
    """Compose units merged across a size/weight break as separate units.

    Ids, sources and saved translations stay bound to the extraction; only the
    composition splits, at the unique target style-run boundary that matches
    the two source parts (otherwise the unit is left for QA: merged_style_break).
    """
    result=[]
    for unit in units:
        special=any(unit.get(k) for k in ('paragraphs','story_frames','display_quote','protected_label','native_ordinal',
                                           'contents_column','field_rows','chart_callout','display_lines','drop_cap'))
        spans=[s for s in unit.get('spans',[]) if s['source'].strip()]
        k=None if special or unit.get('rotate') else _break_index(spans)
        runs=unit.get('target_runs')
        if k is None or not runs:
            result.append(unit);continue
        head,tail=spans[:k],spans[k:]
        key=lambda st:json.dumps(st,sort_keys=True)
        hs={key(x['style']) for x in head};ts={key(x['style']) for x in tail}
        cuts=[c for c in range(1,len(runs))
              if all(key(r['style']) in hs for r in runs[:c]) and all(key(r['style']) in ts for r in runs[c:])
              and ''.join(r['text'] for r in runs[:c]).strip() and ''.join(r['text'] for r in runs[c:]).strip()]
        parts=[_make_unit(head,unit['source_block'],False,[]),_make_unit(tail,unit['source_block'],False,[])]
        if len(cuts)!=1:
            result.append(unit);continue
        pieces=[[dict(r) for r in runs[:cuts[0]]],[dict(r) for r in runs[cuts[0]:]]]
        pieces[0][-1]['text']=pieces[0][-1]['text'].rstrip();pieces[1][0]['text']=pieces[1][0]['text'].lstrip()
        if any(Counter(NUMBER_PATTERN.findall(''.join(r['text'] for r in piece)))!=Counter(NUMBER_PATTERN.findall(part['source']))
               for piece,part in zip(pieces,parts)):
            result.append(unit);continue
        for index,(part,piece) in enumerate(zip(parts,pieces)):
            # Both parts keep the scale group of the unit they were printed in:
            # the split never changes the scale of unrelated siblings.
            child={**unit,**{k2:part[k2] for k2 in ('source','spans','bbox','style_runs','style','font','size','color','kind','line_height','alignment')},
                   'target':''.join(r['text'] for r in piece),'target_runs':piece,'split_from':unit['id'],
                   'first_line_indent':max(0,part['spans'][0]['bbox'][0]-part['bbox'][0]) if unit.get('first_line_indent') is not None else 0}
            if index:
                child['id']=unit['id']+'_split'
                for extra in ('composed_ids','merged_from','relocated_punctuation'):child.pop(extra,None)
            result.append(child)
    return result


def apply_editorial_composition(page, units):
    from .profiles import profile_for_page
    return profile_for_page(page).compose(page, units)


def immutable_boxes(page,extras):
    result=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                item={'source':span['text'],'font':span['font']}
                if span['text'].strip() and immutable_span(item,extras):result.append(fitz.Rect(span['bbox']))
    return result

def _candidate_boxes(page,unit,units,guards):
    from .profiles import profile_for_page
    from .editorial_margins import constrain
    boxes=constrain(page,unit,profile_for_page(page).candidates(page,unit,units,guards,_base_candidate_boxes))
    # Growth stops at the end of the block's rule and short of a flat side panel (p176).
    from .rule_limits import clamp
    boxes=clamp(page,unit,boxes)
    if unit.get('alignment')=='right' and not any(unit.get(k) for k in SPECIAL_GEOMETRY):
        # Right-aligned text ends at the box edge: never past the original glyphs.
        anchor=_ink_bbox(page,unit).x1;clamped=[]
        for box in boxes:
            box=fitz.Rect(box);box.x1=min(box.x1,anchor)
            if not box.is_empty and box not in clamped:clamped.append(box)
        boxes=clamped
    return _share_vertical_gaps(page,unit,units,boxes)

SPECIAL_GEOMETRY=('contents_column','chart_callout','run_in_heading','caption_growth','protected_label',
                  'native_ordinal','display_quote','display_ink_bbox','column_bounds','story_frames')

def _share_vertical_gaps(page,unit,units,boxes):
    """Growth or shifts towards a neighbouring caption claim at most half the gap.

    Bodies are fitted before captions (which grow into free space at 1.00);
    without this a body could take the whole gap and leave the caption no room.
    Other neighbours keep the joint group search. Sub-point moves stay allowed.
    """
    if not boxes or any(unit.get(k) for k in SPECIAL_GEOMETRY):return boxes
    rect=_ink_bbox(page,unit);limit_low,limit_high=page.rect.y1,page.rect.y0
    for other in units:
        if other['id']==unit['id'] or not (other.get('caption_growth') or other.get('kind')=='caption'):continue
        o=_ink_bbox(page,other)
        if min(o.x1,rect.x1)-max(o.x0,rect.x0)<=0:continue
        if o.y0>=rect.y1:limit_low=min(limit_low,rect.y1+(o.y0-rect.y1)/2)
        elif o.y1<=rect.y0:limit_high=max(limit_high,rect.y0-(rect.y0-o.y1)/2)
    return [b for b in boxes if (b.y1<=max(limit_low,rect.y1+1)+.01 and b.y0>=min(limit_high,rect.y0-1)-.01)]


def _base_candidate_boxes(page,unit,units,guards):
    if unit.get('display_quote'):
        from .display_quotes import candidate_boxes as quote_candidate_boxes
        return quote_candidate_boxes(page,unit,units,guards)
    if unit.get('display_ink_bbox'):
        rect=fitz.Rect(unit['display_ink_bbox'])+(-1,-1,1,1)
        # Horizontal whitespace can be used; vertical neighbors retain ink bounds.
        return [rect,rect+(-8,0,8,0),rect+(-16,0,16,0)]
    if unit.get('glyph_spacing_boundary'):
        from copy import deepcopy
        plain=deepcopy(unit);boundary=plain.pop('glyph_spacing_boundary')
        limit=boundary['glyph_bbox'][0]-boundary['minimum_gap']
        boxes=[]
        for box in _candidate_boxes(page,plain,units,guards):
            box=fitz.Rect(box);box.x1=min(box.x1,limit)
            if not box.is_empty and box not in boxes:boxes.append(box)
        return boxes
    if unit.get('protected_label'):
        from .protected_labels import candidate_boxes
        return candidate_boxes(unit)
    if unit.get('native_ordinal'):
        box=fitz.Rect(unit['native_ordinal']['edit_bbox'])
        # Real glyph ink of the other units, not their metric boxes (p170: «11th»
        # touches the 48 pt title's box, never its ink).
        others=[r for u in units if u['id']!=unit['id'] for r in unit_ink_rects(page,u)]+guards
        return [] if any(box.intersects(r) for r in others) else [box]
    if unit.get('caption_growth'):
        from .caption_growth import candidate_boxes, vector_mask
        return candidate_boxes(page,unit,units,guards,vector_mask(page))
    rect=_ink_bbox(page,unit)
    if unit.get('column_bounds'):rect.x0,rect.x1=unit['column_bounds']
    others=[_ink_bbox(page,u) for u in units if u['id']!=unit['id']]
    others+=guards+foreground_images(page)
    candidates=[rect]
    # Half-point increments solve tight original baseline gaps without moving
    # adjacent symbols. Left/right growth preserves right-aligned chart labels.
    growth=[(0,0,0,.5),(0,0,0,1),(0,.5,0,0),(0,1,0,0)]
    for amount in (4,8,16,24,40,60,80):
        growth.extend([(0,0,amount,.5),(amount,0,0,.5),(amount/2,0,amount/2,.5),(0,0,amount,1),(amount,0,0,1)])
    for amount in (2,4,8,12,20,32,48):growth.extend([(0,0,0,amount),(0,amount/2,0,amount/2),(8,0,8,amount)])
    # Pure horizontal growth for a block whose next block starts on its baseline.
    for amount in (4,8,12,16,24,40):growth.extend([(0,0,amount,0),(amount,0,0,0)])
    for left,up,right,down in growth:
        # Display quotations stay anchored to their decorative opening marks.
        # A modest uniform shrink is preferable to moving a whole paragraph up.
        if unit['kind']=='paragraph' and unit['style']['size']>=15 and up>1:continue
        if unit.get('column_bounds') and (left or right or up):continue
        if left and unit.get('alignment')!='right':continue
        if right and unit.get('alignment')=='right':continue
        box=fitz.Rect(max(2,rect.x0-left),max(2,rect.y0-up),min(page.rect.width-2,rect.x1+right),min(page.rect.height-2,rect.y1+down))
        if any(box.intersects(obstacle) and not rect.intersects(obstacle) for obstacle in others):continue
        if unit['context']=='table_label':
            row_obstacles=[o for o in others if o.y0<rect.y1 and o.y1>rect.y0 and (o.x0>=rect.x1 or o.x1<=rect.x0)]
            if any((o.x0>=rect.x1 and box.x1>o.x0-2) or (o.x1<=rect.x0 and box.x0<o.x1+2) for o in row_obstacles):continue
        if any((box&obstacle).get_area()>(rect&obstacle).get_area()+.01 for obstacle in others if rect.intersects(obstacle)):continue
        if not _growth_clear(page,rect,box):continue
        if box not in candidates:candidates.append(box)
    return candidates

def _growth_clear(page,rect,box):
    """Flat raster background is required only where ``box`` adds area to ``rect``.

    The unit's own footprint may contain an icon beside an indented first line;
    the raster guard of each fit trial still rejects any ink on artwork there.
    """
    strips=[fitz.Rect(box.x0,box.y0,box.x1,rect.y0),fitz.Rect(box.x0,rect.y1,box.x1,box.y1),
            fitz.Rect(box.x0,max(box.y0,rect.y0),rect.x0,min(box.y1,rect.y1)),
            fitz.Rect(rect.x1,max(box.y0,rect.y0),box.x1,min(box.y1,rect.y1))]
    added=[r for r in strips if r.width>0 and r.height>0]
    if not added:return _background_clear_cached(page,box)
    return all(_background_clear_cached(page,r) for r in added)

def _vector_mask(page,dpi=110):
    from .profiles import profile_for_page
    return profile_for_page(page).vector_mask(page,dpi,_base_vector_mask)


def _base_vector_mask(page,dpi=110,*,include_rotated=True):
    """Re-render actual paths, avoiding giant crop-mark/curved-arrow bbox errors."""
    import numpy as np
    doc=fitz.open();p=doc.new_page(width=page.rect.width,height=page.rect.height)
    for drawing in page.get_drawings():
        shape=p.new_shape()
        for item in drawing['items']:
            if item[0]=='l':shape.draw_line(item[1],item[2])
            elif item[0]=='c':shape.draw_bezier(*item[1:])
            elif item[0]=='re':shape.draw_rect(item[1])
            elif item[0]=='qu':shape.draw_quad(item[1])
        shape.finish(color=(0,0,0) if drawing['type'] in ('s','fs') else None,fill=(0,0,0) if drawing['type'] in ('f','fs') else None,width=drawing.get('width',1),closePath=drawing.get('closePath',False),even_odd=bool(drawing.get('even_odd',False)))
        shape.commit()
    for item in (omitted_rotated_text(page) if include_rotated else []):p.draw_rect(fitz.Rect(item['bbox']),color=None,fill=(0,0,0))
    pix=p.get_pixmap(dpi=dpi,colorspace=fitz.csGRAY);mask=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width)<220;doc.close();return mask

def _html_for(unit,font_map,font_root,factor):
    css=[f"body,div {{font-size:{unit['style']['size']*factor}pt;margin:0;padding:0;line-height:{unit.get('line_height',1.05)}}} p {{margin:0;padding:0}}"]
    pieces=[];used=[]
    paragraphs=unit.get('paragraphs',[{'target_runs':unit.get('target_runs',[{'text':unit['target'],'style':unit['style']}]),'indent':unit.get('first_line_indent',0)}])
    boundaries={};offset=0
    for paragraph in paragraphs:
        boundaries[offset]=paragraph if unit.get('story_frames') else paragraph['indent'];offset+=len(paragraph['target_runs'])
    for run_index,run in enumerate(unit.get('target_runs',[{'text':unit['target'],'style':unit['style']}])):
        if run_index in boundaries:
            if run_index:pieces.append('</p>')
            boundary=boundaries[run_index]
            if isinstance(boundary,dict):
                before=boundary.get('space_before',0)*factor;after=boundary.get('space_after',0)*factor
                keep='page-break-after:avoid;' if boundary.get('role')=='heading' else ''
                identity=f' id="story_heading_{run_index}"' if boundary.get('role')=='heading' else ''
                pieces.append(f'<p{identity} style="text-indent:{boundary["indent"]*factor}pt;margin-top:{before}pt;margin-bottom:{after}pt;{keep}">')
            else:pieces.append(f'<p style="text-indent:{boundary}pt">')
        style=run['style'];key=tuple(sorted(style.items()));choice=font_map[(unit['scale_group'],key)];family='F'+choice['sha256'][:14]
        relative=Path(choice['path']).relative_to(font_root).as_posix() if Path(choice['path']).is_relative_to(font_root) else str(Path(choice['path']).resolve())
        css.append(f'@font-face {{font-family:{family};src:url("{relative}")}}')
        color=f'#{style["color"]:06x}';size=style['size']*factor
        extra='float:left;line-height:1;margin-right:2pt;' if unit.get('drop_cap') and run_index==0 else ''
        render_text=run['text']
        pieces.append(f'<span style="{extra}font-family:{family};font-size:{size}pt;color:{color}">{html.escape(render_text)}</span>')
        used.append({'font':choice['name'],'engine_name':choice['font'].name,'sha256':choice['sha256'],'source':choice['source'],'size':size,'weight':style['weight'],'italic':style['italic'],'color':color,'width':style['width']})
    pieces.append('</p>')
    align=unit.get('alignment','left')
    return f'<div style="text-align:{align}">'+''.join(pieces)+'</div>',''.join(css),used

def _normalized(text):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKC',text).replace('\u2010','-').replace('\u2011','-') if not c.isspace() and c!='\u00ad')

def _dropcap_overlay(page,unit,box,factor,font_map,font_root):
    import io,copy
    caprun=unit['target_runs'][0];body=copy.deepcopy(unit)
    body['target_runs']=body['target_runs'][1:];body['target']=body['target'][1:];body.pop('drop_cap',None)
    if body.get('paragraphs'):body['paragraphs'][0]['target_runs']=body['paragraphs'][0]['target_runs'][1:]
    cap={**unit,'drop_cap':False,'target':caprun['text'],'target_runs':[caprun],'style':caprun['style'],'line_height':1.05,'first_line_indent':0};cap.pop('paragraphs',None)
    original=fitz.Rect(unit['drop_cap_bbox']);chosen=font_map[(unit['scale_group'],tuple(sorted(caprun['style'].items())))]['font']
    width=max(original.width,chosen.text_length(caprun['text'],fontsize=caprun['style']['size']*factor)+2)
    capbox=fitz.Rect(box.x0,box.y0,box.x0+width,box.y0+original.height+3)
    top=fitz.Rect(capbox.x1+2,box.y0,box.x1,capbox.y1);bottom=fitz.Rect(box.x0,capbox.y1,box.x1,box.y1)
    stream=io.BytesIO();writer=fitz.DocumentWriter(stream);device=writer.begin_page(page.rect)
    html_cap,css_cap,_=_html_for(cap,font_map,font_root,factor);story=fitz.Story(html_cap,user_css=css_cap,archive=fitz.Archive(str(font_root)))
    cap_more,_=story.place(capbox);story.draw(device)
    content,css,_=_html_for(body,font_map,font_root,factor);story=fitz.Story(content,user_css=css,archive=fitz.Archive(str(font_root)))
    more,_=story.place(top);story.draw(device)
    if more and not bottom.is_empty:more,_=story.place(bottom);story.draw(device)
    writer.end_page();writer.close()
    from .column_flow import _widen_clips
    from .fonts import unit_headroom
    return fitz.open(stream=_widen_clips(stream.getvalue(),unit_headroom(unit,factor)),filetype='pdf'),not (cap_more or more)

def _flow_overlay(page,unit,box,factor,font_map,font_root):
    import io
    content,css,_=_html_for(unit,font_map,font_root,factor)
    story=fitz.Story(content,user_css=css,archive=fitz.Archive(str(font_root)))
    stream=io.BytesIO();writer=fitz.DocumentWriter(stream);device=writer.begin_page(page.rect)
    top=fitz.Rect(unit['flow_start_x'],box.y0,box.x1,unit['flow_transition_y'])
    bottom=fitz.Rect(box.x0,unit['flow_transition_y'],box.x1,box.y1)
    more,_=story.place(top);story.draw(device)
    if more and not bottom.is_empty:more,_=story.place(bottom);story.draw(device)
    writer.end_page();writer.close()
    from .column_flow import _widen_clips
    from .fonts import unit_headroom
    return fitz.open(stream=_widen_clips(stream.getvalue(),unit_headroom(unit,factor)),filetype='pdf'),not more

def _insert_plain(page,unit,box,factor,content,css,font_root):
    """Plain block insertion; ink headroom keeps accented capitals unclipped."""
    from .fonts import insert_htmlbox_headroom,unit_headroom
    return insert_htmlbox_headroom(page,box,content,css,fitz.Archive(str(font_root)),unit_headroom(unit,factor))

def _test_fit(page,unit,box,factor,font_map,font_root,vector_mask,_leading_retry=0):
    import numpy as np
    doc=fitz.open();p=doc.new_page(width=page.rect.width,height=page.rect.height)
    content,css,fonts=_html_for(unit,font_map,font_root,factor)
    plain=False
    if unit.get('display_quote'):
        from .display_quotes import overlay as quote_overlay
        rendered,finished=quote_overlay(page,unit,box,factor,font_map)
        if finished:p.show_pdf_page(page.rect,rendered,0)
        rendered.close();spare=0 if finished else -1
    elif unit.get('display_ink_bbox'):
        from .editorial_rules import display_line_overlay
        rendered,finished=display_line_overlay(page,unit,box,factor,font_map)
        if finished:p.show_pdf_page(page.rect,rendered,0)
        rendered.close();spare=0 if finished else -1
    elif unit.get('story_frames'):
        from .column_flow import overlay
        rendered,finished,placements=overlay(page,unit,factor,font_map,font_root)
        p.show_pdf_page(page.rect,rendered,0);rendered.close();spare=0 if finished else -1
    elif unit.get('protected_label'):
        from .protected_labels import label_overlay
        overlay,finished=label_overlay(page,unit,box,factor,font_map)
        if finished:p.show_pdf_page(page.rect,overlay,0)
        overlay.close();spare=0 if finished else -1
    elif unit.get('native_ordinal'):
        if factor!=1:
            doc.close();return False,{}
        from .native_ordinals import heading_overlay
        overlay,finished=heading_overlay(page,unit,factor,font_map);p.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
    elif unit.get('flow_start_x'):
        overlay,finished=_flow_overlay(page,unit,box,factor,font_map,font_root);p.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
    elif unit.get('drop_cap'):
        overlay,finished=_dropcap_overlay(page,unit,box,factor,font_map,font_root);p.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
    elif unit.get('caption_growth'):
        from .caption_growth import insert_anchored
        from .fonts import unit_headroom
        spare,scale=insert_anchored(p,box,content,css,fitz.Archive(str(font_root)),unit['growth_anchor'],unit_headroom(unit,factor));plain=True
    else:spare,scale=_insert_plain(p,unit,box,factor,content,css,font_root);plain=True
    text=p.get_text();ok=spare>=0 and _normalized(unit['target']) in _normalized(text)
    if ok and unit.get('size',0)>=20:
        # Decorative vector quote marks are obstacles for the translated text.
        from .display_quotes import framed,clear_of_marks
        if framed(page,unit):ok=clear_of_marks(page,unit,p)
    if ok and unit.get('single_line'):
        # A source line item (bullet, credit, legend row) stays one line.
        tops=sorted({round(l['bbox'][1]) for b in p.get_text('dict')['blocks'] for l in b.get('lines',[]) if ''.join(s['text'] for s in l['spans']).strip()})
        ok=bool(tops) and tops[-1]-tops[0]<unit['size']*factor*.5
    if ok and plain and not unit.get('protected_label'):
        # Display type: accents of Spanish capitals must clear the line above
        # (p37 «INTRÉPIDO» under «VALIENTE E», p46 «CÁNCER» under the heading).
        # Raise the leading: the pitch grows by the full step and the first
        # baseline by half of it (half-leading). If the box cannot take it, the
        # box/scale is rejected (smaller scales follow).
        from .line_clearance import overlay_collision
        problem=overlay_collision(page,unit,p,unit['size']*factor)
        if problem is not None:
            if _leading_retry<4:
                base=unit.get('line_height',1.05);em=unit['style']['size']*factor
                if problem['internal']:step=(problem['limit']-problem['internal_gap']+.1)/em
                else:step=(problem['limit']-problem['gap']+.1)/(.5*em)
                taller={**unit,'line_height':base+step,'accent_leading':{'source_line_height':unit.get('accent_leading',{}).get('source_line_height',base)}}
                doc.close()
                ok,info=_test_fit(page,taller,box,factor,font_map,font_root,vector_mask,_leading_retry+1)
                if ok:
                    final=info.get('line_height',taller['line_height'])
                    info={**info,'line_height':final,'accent_leading':{'source_line_height':unit.get('accent_leading',{}).get('source_line_height',base),'line_height':final}}
                return ok,info
            ok=False
    region=box;ink_box=None
    if ok and (plain or unit.get('display_quote') or unit.get('story_frames') or unit.get('flow_start_x') or unit.get('drop_cap')):
        # Accents above the first line may now ink outside the box: guard them too.
        from .fonts import ink_rect
        ink_box,_alpha=ink_rect(p)
        if ink_box is not None and not (box+(-1,-1,1,1)).contains(ink_box):region=box|ink_box
        else:ink_box=None
    if ok and unit.get('protected_label'):
        # The regular vector/raster guards below do not include native text.
        ink=[fitz.Rect(s['bbox']) for b in p.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
        from .protected_labels import protected_ink_boxes
        ok=not any(r.intersects(g) for r in ink for g in protected_ink_boxes(page))
    overlap=0
    if ok and np.any(vector_mask[max(0,int(region.y0*110/72)):min(vector_mask.shape[0],int(region.y1*110/72)+1),max(0,int(region.x0*110/72)):min(vector_mask.shape[1],int(region.x1*110/72)+1)]):
        pix=p.get_pixmap(dpi=110,alpha=True);a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
        overlap=int(np.count_nonzero((a>80)&vector_mask))
        ok=overlap==0
    raster=None
    if ok:
        from .raster_obstacles import check_overlay
        # A verified pale watermark that the source text already overlaps is
        # background for every block (not only column stories).
        raster=check_overlay(page,p,region,native_underlay=True)
        if not raster['ok'] and raster['reason']=='native_ink_over_raster_artwork' and unit.get('spans'):
            # The original text sits on the same pale artwork: its own line band
            # (span boxes, 0.1 em taller for the cap height of a replacement font).
            band=[fitz.Rect(s['bbox'])+(0,-.1*s['size'],0,.1*s['size']) for s in unit['spans']]
            retry=check_overlay(page,p,region,native_underlay=True,pale_footprint=band)
            if retry['ok']:raster={**retry,'pale_footprint':True}
        ok=raster['ok']
    occupied=unit.get('story_frames',[list(box)])
    if unit.get('flow_start_x'):occupied=[[unit['flow_start_x'],box.y0,box.x1,unit['flow_transition_y']],[box.x0,unit['flow_transition_y'],box.x1,box.y1]]
    doc.close();return ok,{'box':list(box),'occupied_boxes':occupied,'scale':factor,'font_runs':fonts,'vector_overlap_pixels':overlap,'raster_guard':raster,'fit':ok,**({'ink_bbox':list(ink_box)} if ink_box else {}),**({'story_placements':placements} if unit.get('story_frames') else {})}

def composition_sources_sha256(units):
    sources=[{key:unit[key] for key in ('id','source','spans')} for unit in units]
    return hashlib.sha256(json.dumps(sources,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def apply_composition_overrides(book,units,path,page,glossary,style,do_not_translate):
    path=Path(path).resolve()
    if not path.is_relative_to((Path(book)/'work').resolve()):raise Blocked('Composition overrides must reside below book/work')
    payload=json.loads(path.read_text())
    # Reconstruct the legacy source-only view for hash validation. Caption
    # migrations never authorize rewriting or rebasing a saved override.
    legacy_units=units
    if any(u.get('merged_from') for u in units):
        with fitz.open(Path(book)/'source.pdf') as original:
            legacy={u['id']:u for u in extract_fidelity_units(original[page-1],read_extras(do_not_translate),merge_captions=False)}
        legacy_units=[]
        for unit in units:
            if not unit.get('merged_from'):
                legacy_units.append(unit);continue
            parts=[legacy[i] for i in unit['merged_from']]
            if unit['spans']!=[s for p in parts for s in p['spans']]:raise Blocked('Joined caption provenance changed')
            legacy_units.extend(parts)
    expected={'page':page,'source_sha256':sha256(Path(book)/'source.pdf'),'glossary_sha256':sha256(glossary),'style_sha256':sha256(style),'do_not_translate_sha256':sha256(do_not_translate) if do_not_translate else None,'composition_sha256':composition_sources_sha256(legacy_units)}
    if any(payload.get(k)!=v for k,v in expected.items()):raise Blocked('Composition override hashes or scope changed')
    if not payload.get('provenance'):raise Blocked('Composition override lacks model-call provenance')
    translated={item['id']:item for item in payload['blocks']}
    if len(translated)!=len(payload['blocks']) or set(translated)!={u['id'] for u in legacy_units}:raise Blocked('Composition override must cover each composed unit exactly once')
    for unit in legacy_units:
        item=translated[unit['id']]
        if item['source']!=unit['source'] or item['spans']!=unit['spans']:raise Blocked('Composition source provenance changed')
    result=[]
    for unit in units:
        if unit.get('merged_from'):
            result.append(unit);continue  # Keep the newly translated complete caption.
        item=translated[unit['id']]
        if item['source']!=unit['source'] or item['spans']!=unit['spans']:raise Blocked('Composition source provenance changed')
        target=item['target'];runs=item['target_runs'];allowed=[r['style'] for r in unit['target_runs']]
        if ''.join(r['text'] for r in runs)!=target or any(r['style'] not in allowed for r in runs):raise Blocked('Composition target styles/text invalid')
        for pattern in (NUMBER_PATTERN,SYMBOL_PATTERN,PROTECTED):
            if Counter(pattern.findall(target))!=Counter(pattern.findall(unit['source'])):raise Blocked('Composition protected values changed')
        revised={**unit,'target':target,'target_runs':runs,'composition_override_sha256':sha256(path),'composition_override_provenance':payload['provenance']}
        old_paragraphs=unit.get('paragraphs',[]);revised.pop('paragraphs',None)
        if item.get('paragraphs'):
            paragraphs=item['paragraphs']
            if len(paragraphs)!=len(old_paragraphs) or _normalized(''.join(r['text'] for p in paragraphs for r in p['target_runs']))!=_normalized(target):raise Blocked('Composition paragraph partition changed')
            if any(r['style'] not in allowed for p in paragraphs for r in p['target_runs']):raise Blocked('Composition paragraph style invalid')
            revised['paragraphs']=[{'target_runs':p['target_runs'],'indent':old.get('indent',0)} for p,old in zip(paragraphs,old_paragraphs)]
        result.append(revised)
    return result

def _artifact_folder(book,artifact_dir=None):
    work=(Path(book)/'work').resolve()
    folder=(Path(artifact_dir) if artifact_dir is not None else work/'fidelity').resolve()
    if not folder.is_relative_to(work) or folder==work:raise Blocked('Artifact directory must be a child of book/work')
    return folder

def finalize_editorial_composition(page,units):
    """Optional book-local final rules, after saved overrides pass provenance checks."""
    from .profiles import profile_for_page
    return profile_for_page(page).finalize(page,units)


def _short_form_request(number,unit,boxes,font_map):
    """A short form is requested only when the full target is too wide.

    A target whose text at the minimum scale already fits the widest candidate
    box fails for another reason (guards, height); a shorter text is no fix.
    """
    from .short_forms import make_request
    if boxes:
        width=0
        for run in unit.get('target_runs',[]):
            choice=font_map.get((unit['scale_group'],tuple(sorted(run['style'].items()))))
            if choice is None:width=None;break
            width+=choice['font'].text_length(run['text'],fontsize=run['style']['size']*.75)
        if width is not None and width<=max(b.width for b in boxes):return None
    return make_request(number,unit,boxes,font_map)

def reinsert_fidelity(book,payload,glossary,style,do_not_translate=None,config=None,artifact_dir=None,phase='fidelity',composition_overrides=None,corrections_path=None,approval_book=None):
    """Compose complete paragraphs with original styling and shared group scales."""
    from .fonts import select_group_fonts
    from .layout import safe_redaction_rects
    from .profiles import load_profile
    profile=load_profile(book)
    book=Path(book);config=config or {};require_approval(approval_book or book,glossary,style,do_not_translate);extras=read_extras(do_not_translate)
    folder=_artifact_folder(book,artifact_dir);folder.mkdir(parents=True,exist_ok=True);font_root=folder/'fonts';font_root.mkdir(exist_ok=True)
    number=payload['page'];units=[{**u,'translation_id':u['id'],'id':u['id'].removeprefix(f'p{number:04d}_')} for u in payload['blocks']]
    with fitz.open(book/'source.pdf') as original:
        page=original[number-1]
        if page.rotation:raise Blocked('Rotated PDF pages need explicit normalization')
        canonical={u['id']:u for u in extract_fidelity_units(page,extras)}
        # Saved units that are now protected tokens (e.g. «D C») stay original.
        units=[u for u in units if u['id'] in canonical or not u.get('spans')
               or not all(immutable_span(sp,extras) for sp in u['spans'])]
        if len(units)!=len(canonical) or {u['id'] for u in units}!=set(canonical):
            legacy={u['id']:u for u in extract_fidelity_units(page,extras,spatial_captions=False)}
            if ({u['id'] for u in units}==set(legacy) and len(units)==len(legacy)
                and all(u['source']==legacy[u['id']]['source'] and u['spans']==legacy[u['id']]['spans'] for u in units)):
                result={'page':number,'phase':phase,'fit_complete':False,'status':'review',
                        'source_sha256':sha256(book/'source.pdf'),'group_scales':{},
                        'failures':[{'reason':'caption_retranslation_required',
                                     'ids':[i for i in canonical if i not in legacy]}]}
                atomic_json(folder/f'p{number:04d}-fit.json',result)
                return result
            raise Blocked('Fidelity payload must cover every editable container exactly once')
        checked=[]
        for supplied in units:
            own=canonical[supplied['id']]
            if supplied['source']!=own['source'] or supplied['spans']!=own['spans']:raise Blocked('Fidelity source provenance changed')
            target=supplied['target'];runs=supplied.get('target_runs',[{'text':target,'style':own['style']}])
            if ''.join(r['text'] for r in runs)!=target:raise Blocked('Styled runs do not concatenate to the complete translation')
            allowed=[r['style'] for r in own['style_runs']]
            if any(r['style'] not in allowed for r in runs):raise Blocked('Translation changed an original style')
            if Counter(NUMBER_PATTERN.findall(target))!=Counter(NUMBER_PATTERN.findall(own['source'])):raise Blocked('Protected numeric values changed')
            if Counter(SYMBOL_PATTERN.findall(target))!=Counter(SYMBOL_PATTERN.findall(own['source'])):raise Blocked('Protected symbols changed')
            if Counter(PROTECTED.findall(target))!=Counter(PROTECTED.findall(own['source'])):raise Blocked('Protected abbreviation or value changed')
            checked.append({**own,'target':target,'target_runs':runs})
        from .editorial_rules import protected_editorial_units
        protected_editorial=protected_editorial_units(page,list(canonical.values())) if profile.data.get('composition')=='patterns' else []
        protected_ids={x['unit_id'] for x in protected_editorial}
        from .composition_corrections import apply_corrections
        checked,correction_proofs=apply_corrections(checked,page,book,glossary,style,do_not_translate,path=corrections_path)
        required={u['id'] for u in units if u.get('translation_method')=='requires_joined_quote_correction'}
        covered={identifier for proof in correction_proofs for identifier in proof['composed_ids']}
        if not required<=covered:raise Blocked('Joined quote correction required before composition')
        editable=[u for u in checked if u['id'] not in protected_ids]
        bound_topology=bool(profile.page_policy(page).get('units_sha256') or profile.page_policy(page).get('composition_translations'))
        if profile.data.get('composition')=='patterns' and not bound_topology:
            from .display_quotes import prepare_display_quotes
            editable=prepare_display_quotes(page,editable)
        units=apply_editorial_composition(page,editable)
        saved_overrides=profile.page_policy(page).get('composition_translations')
        if saved_overrides:
            expected_override=profile.checked_file(saved_overrides)
            if composition_overrides is None:composition_overrides=expected_override
        if composition_overrides:units=apply_composition_overrides(book,units,composition_overrides,number,glossary,style,do_not_translate)
        if profile.data.get('composition')=='patterns' and bound_topology:
            from .display_quotes import prepare_display_quotes
            units=prepare_display_quotes(page,units)
        import csv
        with Path(glossary).open() as stream:terms=[r for r in csv.DictReader(stream) if r.get('source') and r.get('target')]
        units=finalize_editorial_composition(page,center_stacked_lines(page,split_line_items(page,split_style_breaks(page,units),terms)))
        from .text_quality import repair_composition, source_emphasis, matrix_italics, constrain_glyph_spacing
        shears=matrix_italics(page)
        units=[repair_composition(u,emphasis=source_emphasis(page,u,shears=shears)) for u in units]
        units=constrain_glyph_spacing(page,units)
        if profile.data.get('composition')=='patterns':
            from .column_flow import prepare_stories,prepare_wrapped_paragraphs
            from .editorial_rules import partition_display_units,prepare_display_lines,normalize_quotes
            units=prepare_wrapped_paragraphs(page,prepare_stories(page,prepare_display_lines(page,partition_display_units(page,units))))
            units=[normalize_quotes(u) for u in units]
        font_map,audit=select_group_fonts(original,page,units,font_root)
        from .native_ordinals import prepare_headings
        prepare_headings(page,units,font_map)
        atomic_json(folder/f'p{number:04d}-fonts.json',audit)
        # Punctuation-only source spans touching a unit's text go with that unit.
        from .orphan_punctuation import absorb_adjacent_punctuation,redaction_unit
        units=absorb_adjacent_punctuation(page,units,extras)
        vector_mask=_vector_mask(page);guards=immutable_boxes(page,extras)
        if any(u.get('caption_growth') for u in units):
            from .caption_growth import vector_mask as caption_vector_mask
            vector_mask=caption_vector_mask(page)
        relocated=[fitz.Rect(s['bbox']) for u in units for s in u.get('relocated_punctuation',[])+u.get('absorbed_punctuation',[])]
        ordinal_owned=[fitz.Rect(s['bbox']) for u in units if u.get('native_ordinal') for s in u['spans']]
        guards=[r for r in guards if not any(r==owned for owned in relocated+ordinal_owned)]
        groups=defaultdict(list)
        for unit in units:groups[unit['scale_group']].append(unit)
        selections={};group_scales={};failures=[];short_requests=[]
        guards += [fitz.Rect(x['bbox']) for x in protected_editorial]
        # Frame-chained units (stories, wrapped paragraphs) carry their own geometry.
        candidates={u['id']:([fitz.Rect(u['bbox'])] if u.get('story_frames') else _candidate_boxes(page,u,units,guards)) for u in units}
        # Joint group selection makes all siblings, including unchanged Aries/Leo,
        # use the same baseline size, rather than shrinking each label separately.
        # Reserve body/column layouts before captions consume their whitespace.
        # Stable sorting leaves legacy profiles and sibling order untouched.
        ordered_groups=sorted(groups.items(),key=lambda item: all(u.get('caption_growth') for u in item[1]))
        for group,members in ordered_groups:
            if len(members)==1 and members[0].get('story_frames'):
                from .column_flow import fit_story
                unit=members[0]
                occupied=[fitz.Rect(r) for x in selections.values() for r in x.get('occupied_boxes',[x['box']])]
                selection=fit_story(page,unit,font_map,font_root,vector_mask,occupied)
                if selection is None:failures.append({'id':unit['id'],'group':group,'reason':'column_story_does_not_fit_at_0.75'})
                else:selections[unit['id']]=selection;group_scales[group]=selection['scale']
                continue
            accepted=None
            for short_pass in range(len(members)+1):
                failed_unit=None
                for percent in range(100,74,-1):
                    factor=percent/100;attempt={};occupied=[fitz.Rect(rect) for x in selections.values() for rect in x.get('occupied_boxes',[x['box']])]
                    for unit in members:
                        found=None
                        for box in candidates[unit['id']]:
                            if any(box.intersects(r) for r in occupied):continue
                            ok,info=_test_fit(page,unit,box,factor,font_map,font_root,vector_mask)
                            if ok:found=info;break
                        if found is None:
                            failed_unit=unit
                            atomic_json(folder/f'p{number:04d}-last-fit-diagnostic.json',{'group':group,'scale':factor,'id':unit['id'],'source':unit['source'],'target':unit['target'],'candidate_count':len(candidates[unit['id']])})
                            break
                        attempt[unit['id']]=found;occupied.extend(fitz.Rect(rect) for rect in found.get('occupied_boxes',[found['box']]))
                    if len(attempt)==len(members):accepted=attempt;group_scales[group]=factor;break
                if accepted is not None:break
                # Full targets exhausted every candidate at 0.75. No inference
                # here: only a signed supplement can authorize a second pass.
                from .short_forms import make_request,dependencies,load_approved,apply_approved
                request=_short_form_request(number,failed_unit,candidates[failed_unit['id']],font_map) if failed_unit else None
                if request is None:break
                deps=dependencies(book,glossary,style,do_not_translate)
                short_requests.append(request)
                approved=load_approved(book/'work/short_forms',deps)
                import csv
                terms=list(csv.DictReader(Path(glossary).open()))
                replacement=apply_approved(failed_unit,request,approved,terms,extras)
                if replacement is None:break
                failed_unit.clear();failed_unit.update(replacement)
                new_fonts,new_audit=select_group_fonts(original,page,units,font_root)
                font_map.update(new_fonts);audit=new_audit
                atomic_json(folder/f'p{number:04d}-fonts.json',audit)
            if accepted is None:
                from .protected_labels import FAILURE
                from .short_forms import make_request
                # Prepare every short-form request of the group at once (no
                # inference): siblings that cannot fit alone at 0.75 either.
                requested={r['unit_id']:r for r in short_requests if r['page']==number}
                occupied=[fitz.Rect(rect) for x in selections.values() for rect in x.get('occupied_boxes',[x['box']])]
                alone={}
                for unit in members:
                    if unit['id'] in requested:continue
                    alone[unit['id']]=any(_test_fit(page,unit,box,.75,font_map,font_root,vector_mask)[0]
                                          for box in candidates[unit['id']] if not any(box.intersects(r) for r in occupied))
                    if not alone[unit['id']]:
                        request=_short_form_request(number,unit,candidates[unit['id']],font_map)
                        if request is not None:short_requests.append(request);requested[unit['id']]=request
                def reason(u):
                    if u.get('protected_label'):return FAILURE
                    if u['id'] in requested:return 'needs_short_form'
                    return 'group_blocked_by_sibling' if alone.get(u['id']) else 'fit_failed_at_0.75'
                failures.extend({'id':u['id'],'source':u['source'],'target':u['target'],'group':group,'reason':reason(u),
                                 'needs_short_form':u['id'] in requested,
                                 **({'short_form_request_sha256':requested[u['id']]['request_sha256']} if u['id'] in requested else {})}
                                for u in members)
            else:selections.update(accepted)
        from .short_forms import dependencies
        atomic_json(folder/f'p{number:04d}-short-form-requests.json',{'dependencies':dependencies(book,glossary,style,do_not_translate),'requests':short_requests})
        if failures:
            result={'page':number,'phase':phase,'artifact_dir':str(folder),'status':'review','source_sha256':sha256(book/'source.pdf'),'failures':failures,'group_scales':group_scales,'fit_complete':False}
            atomic_json(folder/f'p{number:04d}-fit.json',result)
            from .protected_labels import stop_failed_labels
            stop_failed_labels(book,folder,page,result)
            return result
        working=fitz.open();working.insert_pdf(original,from_page=number-1,to_page=number-1);wp=working[0];reports=[]
        try:
            from .native_redaction import redact_source
            redaction_proofs=redact_source(page,wp,[redaction_unit(u) for u in units],profile,safe_redaction_rects)
            for unit in units:
                selection=selections[unit['id']];factor=selection['scale'];box=fitz.Rect(selection['box'])
                if selection.get('line_height'):
                    # Leading raised so accented capitals clear the line above.
                    unit['accent_leading']=selection['accent_leading'];unit['line_height']=selection['line_height']
                content,css,font_runs=_html_for(unit,font_map,font_root,factor)
                if unit.get('display_quote'):
                    from .display_quotes import overlay as quote_overlay
                    rendered,finished=quote_overlay(page,unit,box,factor,font_map)
                    wp.show_pdf_page(page.rect,rendered,0);rendered.close();spare=0 if finished else -1
                elif unit.get('display_ink_bbox'):
                    from .editorial_rules import display_line_overlay
                    rendered,finished=display_line_overlay(page,unit,box,factor,font_map)
                    wp.show_pdf_page(page.rect,rendered,0);rendered.close();spare=0 if finished else -1
                elif unit.get('story_frames'):
                    from .column_flow import overlay
                    rendered,finished,placements=overlay(page,unit,factor,font_map,font_root)
                    wp.show_pdf_page(page.rect,rendered,0);rendered.close();spare=0 if finished else -1
                elif unit.get('protected_label'):
                    from .protected_labels import label_overlay
                    overlay,finished=label_overlay(page,unit,box,factor,font_map);wp.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
                elif unit.get('native_ordinal'):
                    from .native_ordinals import heading_overlay
                    overlay,finished=heading_overlay(page,unit,factor,font_map);wp.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
                elif unit.get('flow_start_x'):
                    overlay,finished=_flow_overlay(page,unit,box,factor,font_map,font_root);wp.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
                elif unit.get('drop_cap'):
                    overlay,finished=_dropcap_overlay(page,unit,box,factor,font_map,font_root);wp.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
                elif unit.get('caption_growth'):
                    from .caption_growth import insert_anchored
                    from .fonts import unit_headroom
                    spare,scale=insert_anchored(wp,box,content,css,fitz.Archive(str(font_root)),unit['growth_anchor'],unit_headroom(unit,factor))
                else:spare,scale=_insert_plain(wp,unit,box,factor,content,css,font_root)
                if spare<0:raise Blocked('Previously fitted content failed transactional insertion')
                reports.append({**unit,'edit_bbox':list(box),**({'edit_boxes':unit['story_frames'],'story_placements':selection['story_placements']} if unit.get('story_frames') else {}),'fit':True,'scale':factor,'font_runs':font_runs,'raster_guard':selection.get('raster_guard'),**({'ink_bbox':selection['ink_bbox']} if selection.get('ink_bbox') else {}),'review':factor<.8})
            # insert_pdf omits the page transparency Group. Keep its original
            # reference in this same document so color blending remains exact.
            group_type,group_value=original.xref_get_key(page.xref,'Group')
            original.delete_page(number-1);original.insert_pdf(working,start_at=number-1)
            if group_type!='null':original.xref_set_key(original[number-1].xref,'Group',group_value)
            output=folder/f'p{number:04d}-translated.pdf';_save_pdf(original,output)
            result={'page':number,'phase':phase,'artifact_dir':str(folder),'status':'pending_qa','source_sha256':sha256(book/'source.pdf'),'output':str(output),'output_sha256':sha256(output),'blocks':reports,'protected_editorial':protected_editorial,'correction_proofs':correction_proofs,'group_scales':group_scales,'fit_complete':True,'failures':[],'font_audit':str(folder/f'p{number:04d}-fonts.json')}
            result['redaction_proofs']=redaction_proofs
            atomic_json(folder/f'p{number:04d}-fit.json',result);return result
        finally:working.close()

def _background_clear_cached(page,rect,tolerance=18):
    import io
    import numpy as np
    from PIL import Image
    if not hasattr(page,'_fidelity_rasters'):
        rasters=[]
        for item in page.get_images(full=True):
            data=page.parent.extract_image(item[0])['image'];im=Image.open(io.BytesIO(data)).convert('RGB');pixels=np.asarray(im)
            for box in page.get_image_rects(item[0]):rasters.append((box,pixels))
        page._fidelity_rasters=rasters
    for box,pixels in page._fidelity_rasters:
        overlap=rect&box
        if overlap.is_empty:continue
        if (box&page.rect).get_area()<page.rect.get_area()*.98:return False
        height,width=pixels.shape[:2]
        x0=max(0,int((overlap.x0-box.x0)/box.width*width));x1=min(width,int((overlap.x1-box.x0)/box.width*width)+1)
        y0=max(0,int((overlap.y0-box.y0)/box.height*height));y1=min(height,int((overlap.y1-box.y0)/box.height*height)+1)
        crop=pixels[y0:y1,x0:x1].reshape(-1,3).astype(float)
        if len(crop) and np.quantile(np.abs(crop-np.median(crop,axis=0)),.995)>tolerance:return False
    return True

def _immutable_characters(page,extras,only_immutable=True):
    counts=Counter()
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                text=''.join(c['c'] for c in span['chars'])
                if only_immutable and not immutable_span({'source':text,'font':span['font']},extras):continue
                for char in span['chars']:
                    if char['c'].strip():counts[(char['c'],span['font'],tuple(round(x,3) for x in char['bbox']))]+=1
    return counts

def omitted_rotated_text(page, extras=()):
    omitted=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            if tuple(line['dir'])==(1.,0.):continue
            for span in line['spans']:
                item={'source':span['text'],'font':span['font']}
                if span['text'].strip() and not immutable_span(item,extras):
                    omitted.append({'text':span['text'],'bbox':list(span['bbox']),'direction':list(line['dir']),'font':span['font']})
    return omitted

def verified_editorial_text(document,page,result,extras=(),*,quoted_terms=()):
    """Only exact embedded font hashes may bypass the legacy symbol-name regex."""
    expected={run['sha256'] for block in result['blocks'] for run in block['font_runs']}
    normalize=lambda name:re.sub(r'^[A-Z]{6}\+','',name).replace(' ','').replace('-','').lower()
    verified_names=set()
    for row in page.get_fonts(full=True):
        data=document.extract_font(row[0])[3]
        if data and hashlib.sha256(data).hexdigest() in expected:
            verified_names.add(normalize(row[3]))
            verified_names.add(normalize(fitz.Font(fontbuffer=data).name))
    editorial=[]
    # Curved labels are emitted one glyph at a time. Judge their reconstructed
    # words only after exact extraction and embedded-font provenance match.
    rotated=[]
    for block in result['blocks']:
        if block.get('positions') and _normalized(block['target']) in _normalized(page.get_text()):
            names={normalize(run['engine_name']) for run in block['font_runs'] if run['sha256'] in expected}
            if names and names<=verified_names:
                rotated.append((fitz.Rect(block['edit_bbox']),names));editorial.append(block['target'])
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                text=span['text'];font=span['font']
                if any(normalize(font) in names and rect.intersects(fitz.Rect(span['bbox'])) for rect,names in rotated):continue
                if normalize(font) in verified_names:font='verified-editorial-text'
                if immutable_span({'source':text,'font':font},extras):continue
                if any(proof.get('preserve_native') and fitz.Rect(proof['bbox']).contains(fitz.Rect(span['bbox'])) for proof in quoted_terms):continue
                for proof in quoted_terms:
                    if proof.get('source_identical'):text=re.sub(r'(?<!\w)'+re.escape(proof['text'])+r'(?!\w)',' ',text)
                for proof in sorted((p for p in quoted_terms if 'start' in p),key=lambda p:p['start'],reverse=True):
                    if (list(span['bbox'])==proof['span_bbox'] and span['flags']&2
                            and text[proof['start']:proof['end']]==proof['text']):
                        text=text[:proof['start']]+' '+text[proof['end']:]
                text=re.sub(r'\b\d+(?:st|nd|rd|th)\b',' ',text,flags=re.I)
                # Local Spanish ordinal morphology is editorial, not English.
                from .native_ordinals import ES_SUFFIX
                text=ES_SUFFIX.sub(' ',text)
                editorial.append(NUMBER_PATTERN.sub(' ',SYMBOL_PATTERN.sub(' ',text)))
    return editorial

def composition_line_issues(source_page,candidate_page,blocks):
    """Geometric checks of the batch-16 defects on the composed candidate.

    bullet_line_merge: a block whose source lines start with «•» must keep one
    bullet per line start (no line ending in «•», no extra bullet mid-line);
    quote_mark_overlap: translated glyphs on a decorative quote mark zone;
    right_edge_overflow: a right-aligned block past its original visible edge;
    line_ink_collision: display glyphs (accents) closer to the line above than
    allowed (``line_clearance``); rule_or_panel_overflow: ink past the end of the
    block's rule or into a side panel's clearance (``rule_limits``).
    """
    issues=[]
    def rows(rect):
        out=[]
        for block in candidate_page.get_text('dict',clip=rect)['blocks']:
            for line in block.get('lines',[]):
                text=''.join(s['text'] for s in line['spans']).strip()
                if text:out.append((text,fitz.Rect(line['bbox'])))
        out.sort(key=lambda r:(round(r[1].y0),r[1].x0));merged=[]
        for text,box in out:
            if merged and abs(merged[-1][1].y0-box.y0)<2:merged[-1]=(merged[-1][0]+' '+text,merged[-1][1]|box)
            else:merged.append((text,box))
        return merged
    for unit in blocks:
        if not unit.get('edit_bbox') or not unit.get('spans') or unit.get('story_frames'):continue
        box=fitz.Rect(unit['edit_bbox'])
        lines=_item_lines(unit) or []
        source_bullets=[l['text'].count('•') for l in lines if l['text'].startswith('•')]
        if source_bullets and len(source_bullets)==len(lines):
            found=rows(box+(-.5,-.5,.5,.5))
            if (len(found)!=len(lines) or any(t.rstrip().endswith('•') or not t.startswith('•') for t,_ in found)
                    or sorted(t.count('•') for t,_ in found)!=sorted(source_bullets)):
                issues.append({'code':'bullet_line_merge','unit_id':unit['id'],'action':'recompose','lines':[t for t,_ in found]})
        from .display_quotes import framed
        if framed(source_page,unit):
            # Inside a mark zone (mark, clearance and the band of the line it
            # follows) the candidate must look like the original: mark only.
            import numpy as np
            from .display_quotes import _mark_clearance
            for zone in _mark_clearance(source_page,unit):
                a=source_page.get_pixmap(dpi=110,clip=zone);b=candidate_page.get_pixmap(dpi=110,clip=zone)
                if (a.width,a.height,a.n)!=(b.width,b.height,b.n):continue
                diff=np.abs(np.frombuffer(a.samples,np.uint8).astype(int)-np.frombuffer(b.samples,np.uint8).astype(int)).reshape(a.height,a.width,a.n).max(axis=2)
                if np.count_nonzero(diff>48)>=3:
                    issues.append({'code':'quote_mark_overlap','unit_id':unit['id'],'action':'recompose'});break
        if unit.get('alignment')=='right' and not any(unit.get(k) for k in SPECIAL_GEOMETRY):
            edge=_ink_bbox(source_page,unit).x1
            chars=[c['bbox'][2] for b in candidate_page.get_text('rawdict',clip=box)['blocks']
                   for l in b.get('lines',[]) for sp in l['spans'] for c in sp['chars'] if c['c'].strip()]
            if chars and max(chars)>edge+1:
                issues.append({'code':'right_edge_overflow','unit_id':unit['id'],'action':'recompose','x1':max(chars),'source_x1':edge})
    from .line_clearance import collision_issues
    issues+=collision_issues(source_page,candidate_page,blocks)
    from .rule_limits import overflow_issues
    issues+=overflow_issues(source_page,candidate_page,blocks)
    from .native_ordinals import point_issues
    issues+=point_issues(candidate_page,blocks)
    return issues

def fidelity_qa(book,result,glossary,do_not_translate=None,artifact_dir=None,phase=None,*,update_manifest=True):
    """Local fidelity QA. Numeric prose may move; original symbol spans may not."""
    import csv
    import numpy as np
    from PIL import Image,ImageDraw
    from .language import residual_english,glossary_matches
    book=Path(book);number=result['page'];extras=read_extras(do_not_translate)
    if result['source_sha256']!=sha256(book/'source.pdf') or result['output_sha256']!=sha256(result['output']):raise Blocked('Fidelity QA hashes do not bind to source and candidate')
    folder=_artifact_folder(book,artifact_dir or result.get('artifact_dir'));renders=folder/'renders';renders.mkdir(parents=True,exist_ok=True)
    with fitz.open(book/'source.pdf') as source,fitz.open(result['output']) as candidate:
        a=source[number-1];b=candidate[result.get('output_page',number-1)];before=a.get_text();after=b.get_text()
        omitted_rotated=omitted_rotated_text(a,extras)
        def signature(item):
            return (item['text'],item['font'],tuple(round(v,3) for v in item['bbox']),tuple(round(v,5) for v in item['direction']))
        candidate_rotated={signature(item) for item in omitted_rotated_text(b,extras)}
        separators_verified=False
        if any(p.get('resolution')=='preserved_separator' for p in result.get('verified_rotated_spans',[])):
            from .rotated_pilot import separator_character_proofs
            separators_verified=bool(separator_character_proofs(a,b))
        block_ids={item['id']:item for item in result['blocks']}
        covered=set()
        for proof in result.get('verified_rotated_spans',[]):
            if proof.get('source_sha256')!=result['source_sha256'] or proof.get('candidate_sha256')!=result['output_sha256']:continue
            if proof.get('resolution')=='preserved_separator':
                if proof['text']=='|' and (separators_verified or signature(proof) in candidate_rotated):covered.add(signature(proof))
            elif proof.get('resolution')=='translated' and proof.get('target_ids'):
                translated=[block_ids.get(i) for i in proof['target_ids']]
                if all(item and _normalized(item['target']) in _normalized(after) for item in translated):covered.add(signature(proof))
        omitted_rotated=[item for item in omitted_rotated if signature(item) not in covered]
        immutable_original=_immutable_characters(a,extras)
        # A curved word may be extracted as isolated letters (e.g. MEMORY's R).
        # Only an exactly bound translated-span proof may resolve that artifact;
        # actual symbolic fonts and numeric fragments remain immutable.
        for proof in result.get('verified_rotated_spans',[]):
            if signature(proof) not in covered or proof.get('resolution')!='translated' or not proof['text'].isalpha() or SYMBOL_FONT.search(proof['font']):continue
            rect=fitz.Rect(proof['bbox'])+(-.02,-.02,.02,.02)
            for key in list(immutable_original):
                char,font,bbox=key
                if char in proof['text'] and font==proof['font'] and rect.contains(fitz.Rect(bbox)):del immutable_original[key]
        for block in result['blocks']:
            for span in block.get('relocated_punctuation',[])+block.get('absorbed_punctuation',[]):
                from .orphan_punctuation import ABSORBABLE
                absorbed=span in block.get('absorbed_punctuation',[]) and ABSORBABLE.fullmatch(span['source'].strip())
                if span['source'].strip() not in ('.',',',';',':','!','?') and not absorbed:raise Blocked('Only punctuation can be repositioned by this rule')
                for key in list(immutable_original):
                    char,font,bbox=key
                    if char==span['source'].strip() and font==span['font'] and (fitz.Rect(span['bbox'])+(-.02,-.02,.02,.02)).contains(fitz.Rect(bbox)):del immutable_original[key]
        from .native_ordinals import qa_adjustments
        ordinal_symbols,ordinal_proofs=qa_adjustments(a,b,result['blocks'],immutable_original)
        immutable_equal=not (immutable_original-_immutable_characters(b,extras,False))
        roundoff_proof=None
        if not immutable_equal and not ordinal_proofs:
            from .composition_patterns import verify_immutable_float_roundoff
            roundoff_proof=verify_immutable_float_roundoff(source,candidate,number,extras,candidate_index=result.get('output_page',number-1))
            immutable_equal=roundoff_proof['ok']
        from .contents_layout import verify_folios
        toc_proof=verify_folios(a,b,result['blocks'])
        immutable_equal=immutable_equal and toc_proof['ok']
        numbers_equal=Counter(NUMBER_PATTERN.findall(before))==Counter(NUMBER_PATTERN.findall(after))
        symbols_equal=Counter(SYMBOL_PATTERN.findall(before))+ordinal_symbols==Counter(SYMBOL_PATTERN.findall(after))
        image_hashes=lambda d,p:Counter(hashlib.sha256(d.extract_image(x[0])['image']).hexdigest() for x in p.get_images())
        images_equal=image_hashes(source,a)==image_hashes(candidate,b)
        keys=('items','type','color','fill','width','lineCap','lineJoin','closePath','dashes','rect','fill_opacity','stroke_opacity')
        drawings=lambda p:[tuple((key,repr(x.get(key))) for key in keys) for x in p.get_drawings()]
        vectors_equal=drawings(a)==drawings(b)
        px=a.get_pixmap(dpi=110);ia=Image.frombytes('RGB',(px.width,px.height),px.samples)
        px=b.get_pixmap(dpi=110);ib=Image.frombytes('RGB',(px.width,px.height),px.samples)
        ia.save(renders/f'p{number:04d}-before.png');ib.save(renders/f'p{number:04d}-after.png')
        side=Image.new('RGB',(ia.width+ib.width,ia.height),'white');side.paste(ia,(0,0));side.paste(ib,(ia.width,0));side.save(renders/f'p{number:04d}-comparison.png')
        mask=Image.new('L',ia.size,255);draw=ImageDraw.Draw(mask)
        for block in result['blocks']:
            for box in ([s['bbox'] for s in block['spans']]+block['edit_boxes'] if block.get('story_frames') else [block['bbox'],block['edit_bbox']]+([block['ink_bbox']] if block.get('ink_bbox') else [])):
                r=fitz.Rect(box);draw.rectangle([r.x0*110/72-2,r.y0*110/72-2,r.x1*110/72+2,r.y1*110/72+2],fill=0)
        delta=np.any(np.asarray(ia)!=np.asarray(ib),axis=2);valid=np.asarray(mask)>0;diff=float(delta[valid].mean()) if valid.any() else 0.
        with open(glossary) as stream:terms=list(csv.DictReader(stream))
        allowed=list(extras)+[row['target'] for row in terms]
        from .text_quality import quoted_foreign_terms
        from .profiles import load_profile
        language_exceptions=quoted_foreign_terms(a,b,result['blocks']) if load_profile(book).data.get('editorial',{}).get('quoted_foreign_term',False) else []
        from .editorial_rules import credit_exceptions
        language_exceptions+=credit_exceptions(a,b,result['blocks'])
        language_exceptions+=load_profile(book).language_exceptions(a,b)
        language_exceptions+=result.get('protected_editorial',[])
        from .language import source_identical_latin_terms
        language_exceptions+=source_identical_latin_terms(result['blocks'])
        allowed += [x['text'] for x in language_exceptions]
        editorial=verified_editorial_text(candidate,b,result,extras,quoted_terms=language_exceptions)
        language_texts=[]
        for text in editorial:
            for term in sorted(allowed,key=len,reverse=True):
                if term.strip():text=re.sub(r'(?<!\w)'+re.escape(term)+r'(?!\w)',' ',text,flags=re.I)
            language_texts.append(text)
        residual=residual_english(language_texts,allowed)
        # Phrase checks run per editable source unit, avoiding numeric page refs
        # and glyph-font tokens. Ambiguities remain visible for human review.
        missing=[]
        for block in result['blocks']:
            matches=glossary_matches(block['source'],terms)
            for term in matches:
                from .glossary_forms import glossary_form_present
                related=[item['target'] for item in matches if item['target']!=term['target']]
                if not glossary_form_present(block['target'],term['target'],related_terms=related):missing.append(term['source'])
        pdf_fonts={hashlib.sha256(candidate.extract_font(row[0])[3]).hexdigest():row[3] for row in b.get_fonts(full=True) if candidate.extract_font(row[0])[3]}
        expected={run['sha256'] for block in result['blocks'] for run in block['font_runs']}
        embedded_verified=expected<=set(pdf_fonts)
        # All styled translated text must be extractable, not merely declared fit.
        complete_text=all(_normalized(block['target']) in _normalized(after) for block in result['blocks'])
        groups=defaultdict(set)
        for block in result['blocks']:groups[block['scale_group']].add(round(block['scale'],8))
        uniform=all(len(scales)==1 for scales in groups.values())
        minimum=min(block['scale'] for block in result['blocks'])
        # Style colors observed in the final PDF (not just planned CSS).
        final_colors={span['color'] for bb in b.get_text('dict')['blocks'] for line in bb.get('lines',[]) for span in line['spans']}
        planned_colors={run['style']['color'] for block in result['blocks'] for run in block['target_runs']}
        colors_verified=planned_colors<=final_colors
        from .fidelity_audit import audit_rendered_styles
        typography=audit_rendered_styles(b,result)
        from .text_quality import page_issues
        textual=page_issues(result['blocks'],source_page=a,candidate_page=b,glossary=terms)
        from .column_flow import heading_issues
        textual+=heading_issues(b,result['blocks'])
        from .editorial_margins import margin_issues
        margins=margin_issues(a,b,result['blocks']);textual+=margins
        textual+=composition_line_issues(a,b,result['blocks'])
        from .orphan_punctuation import orphan_punctuation_issues
        textual+=orphan_punctuation_issues(a,b)
        from .editorial_rules import quote_retranslation_units
        for quote in quote_retranslation_units(a,result['blocks']):
            if not any(set(p['composed_ids'])==set(quote['composed_ids']) for p in result.get('correction_proofs',[])):
                textual.append({'code':'fragmented_display_quote','unit_id':quote['id'],'action':'retranslate','composed_ids':quote['composed_ids']})
        from .display_titles import title_retranslation_units
        for title in title_retranslation_units(a,extract_fidelity_units(a,extras)):
            if not any(set(p['composed_ids'])==set(title['composed_ids']) for p in result.get('correction_proofs',[])):
                textual.append({'code':'fragmented_display_title','unit_id':title['id'],'action':'retranslate','composed_ids':title['composed_ids']})
        native_texts=[]
        for block in result['blocks']:
            text=NUMBER_PATTERN.sub(' ',SYMBOL_PATTERN.sub(' ',block['target']))
            for term in sorted(allowed,key=len,reverse=True):
                if term.strip():text=re.sub(r'(?<!\w)'+re.escape(term)+r'(?!\w)',' ',text,flags=re.I)
            from .native_ordinals import ES_SUFFIX
            native_texts.append(ES_SUFFIX.sub(' ',text))
        native_residual=residual_english(native_texts,allowed)
        mechanical_ok=not textual and typography['ok'] and all((immutable_equal,numbers_equal,symbols_equal,images_equal,vectors_equal,embedded_verified,complete_text,uniform,colors_verified)) and diff==0 and minimum>=.75 and not missing
        native_ok=mechanical_ok and not native_residual
        local_ok=mechanical_ok and not omitted_rotated and not residual
        report={'page':number,'phase':phase or result.get('phase','fidelity'),'artifact_dir':str(folder),'verdict':'revisar','local_fidelity_ok':local_ok,'omitted_rotated_text':omitted_rotated,'residual_english':residual,'glossary_missing':sorted(set(missing)),'glyph_set_equal':symbols_equal,'numbers_equal':numbers_equal,'immutable_spans_equal':immutable_equal,'native_ordinal_proofs':ordinal_proofs,'min_scale':minimum,'non_text_pixel_diff':diff,'images_equal':images_equal,'vectors_equal':vectors_equal,'font_files_embedded_verified':embedded_verified,'embedded_font_names':sorted({pdf_fonts[h] for h in expected if h in pdf_fonts}),'complete_translated_text':complete_text,'uniform_scale_groups':uniform,'group_scales':result['group_scales'],'original_colors_verified':colors_verified,'typography_audit':typography,'vlm_complete':False,'vlm_notes':'Native judge pending; prepare and verify safe pair before inference.','art_exception':load_profile(book).art_exception(number),'source_sha256':sha256(book/'source.pdf'),'candidate_sha256':sha256(result['output'])}
        report['native_local_fidelity_ok']=native_ok
        report['native_residual_english']=native_residual
        report['toc_folio_proof']=toc_proof
        report['immutable_roundoff_proof']=roundoff_proof
        report['text_quality_issues']=textual
        report['editorial_margins_ok']=not margins
        report['editorial_margin_issues']=margins
        report['language_exceptions']=language_exceptions
    from .qa_evidence import preserve_vlm
    qa_path=folder/f'p{number:04d}-qa.json'
    if qa_path.exists():report=preserve_vlm(report,json.loads(qa_path.read_text()))
    atomic_json(qa_path,report)
    if update_manifest:
        with book_lock(book):
            mp=book/'work/manifest.json'
            if mp.exists():
                state=json.loads(mp.read_text());page=state['pages'][number-1];page['status']='review';page['attempts']+=1;page['fidelity_qa']=str(folder/f'p{number:04d}-qa.json');atomic_json(mp,state)
    return report


def unit_ink_rects(page,unit):
    """Real glyph ink rectangles of a unit's source spans (metric box if unknown)."""
    if not hasattr(page,'_fidelity_glyph_ink'):
        from .safe_vlm import _glyph_ink_bounds
        ink,_unsupported,failed=_glyph_ink_bounds(page)
        page._fidelity_glyph_ink=None if failed else ink
    ink=page._fidelity_glyph_ink;rects=[]
    for span in unit.get('spans',[]):
        found=[]
        if ink is not None:
            for raw in _raw_spans(page):
                if (raw['font']==span['font'] and abs(raw['bbox'][0]-span['bbox'][0])<.01 and abs(raw['bbox'][1]-span['bbox'][1])<.01
                        and ''.join(c['c'] for c in raw['chars'])==span['source']):
                    for c in raw['chars']:
                        if c['c'].strip():found+=[fitz.Rect(r) for r in ink.get((ord(c['c']),round(c['origin'][0],2),round(c['origin'][1],2)),[])]
                    break
        if not found and span['source'].strip():found=[fitz.Rect(span['bbox'])]
        if found:
            rect=found[0]
            for r in found[1:]:rect|=r
            rects.append(rect)
    return rects or ([fitz.Rect(unit['bbox'])] if unit.get('bbox') else [])

def _raw_spans(page):
    if not hasattr(page,'_fidelity_raw_spans'):
        page._fidelity_raw_spans=[s for b in page.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
    return page._fidelity_raw_spans

def _visible_x(page,span):
    """Left/right edge of the visible glyphs of a source span (no edge spaces)."""
    if span['source']==span['source'].strip():return span['bbox'][0],span['bbox'][2]
    for raw in _raw_spans(page):
        if (raw['font']==span['font'] and abs(raw['bbox'][0]-span['bbox'][0])<.01 and abs(raw['bbox'][1]-span['bbox'][1])<.01
                and ''.join(c['c'] for c in raw['chars'])==span['source']):
            chars=[c['bbox'] for c in raw['chars'] if c['c'].strip()]
            if chars:return min(c[0] for c in chars),max(c[2] for c in chars)
    return span['bbox'][0],span['bbox'][2]

def visible_lines(page,unit):
    """Source lines of a unit with the extent of their visible glyphs."""
    lines=[]
    for span in unit['spans']:
        if not span['source'].strip():continue
        x0,x1=_visible_x(page,span)
        if lines and abs(span['bbox'][1]-lines[-1]['y0'])<unit['size']*.5:
            lines[-1].update(x0=min(lines[-1]['x0'],x0),x1=max(lines[-1]['x1'],x1))
        else:lines.append({'y0':span['bbox'][1],'x0':x0,'x1':x1})
    return lines

def _right_aligned_lines(page,unit):
    """Several lines sharing the right edge of their visible glyphs, with distinct starts."""
    lines=visible_lines(page,unit)
    return (len(lines)>1 and max(l['x1'] for l in lines)-min(l['x1'] for l in lines)<1
            and max(l['x0'] for l in lines)-min(l['x0'] for l in lines)>4)

def _ink_bbox(page,unit):
    rect=fitz.Rect(unit['bbox'])
    if unit.get('alignment')=='right' and unit.get('spans'):
        # A trailing space inside a span bbox is not part of the right anchor.
        rect.x1=min(rect.x1,max(_visible_x(page,s)[1] for s in unit['spans'] if s['source'].strip()))
    if unit['context']!='table_label' or len(unit['spans'])!=1:return rect
    wanted=unit['spans'][0]
    if wanted['source']==wanted['source'].strip():return rect
    for span in _raw_spans(page):
        text=''.join(c['c'] for c in span['chars'])
        if text==wanted['source'] and span['font']==wanted['font'] and abs(span['bbox'][0]-wanted['bbox'][0])<.01 and abs(span['bbox'][1]-wanted['bbox'][1])<.01:
            chars=[fitz.Rect(c['bbox']) for c in span['chars'] if c['c'].strip()]
            if chars:rect.x0=min(r.x0 for r in chars);rect.x1=max(r.x1 for r in chars)
            break
    return rect
