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
    return bool(re.fullmatch(r'(?:st|nd|rd|th)',text,re.I) or re.fullmatch(r'\d{3}[–-]\d{6}[–-][A-Za-z]{3}/\d{4}',text) or SYMBOL_FONT.search(span['font']) or (SYMBOL_PATTERN.search(text) and not re.search(r'[A-Za-zÀ-ÿ]',text)) or ABBREVIATIONS.fullmatch(text) or re.fullmatch(r'[\d\s.,:/−+\-]+',text) or text in extras)

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

def extract_fidelity_units(page,extras=()):
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
    if page.number==33:
        # The protected 1ST ordinal lies between two separately positioned
        # heading fragments; their redaction/insertion boxes must stay separate.
        split=[]
        for unit in merged:
            if unit['source']=='THE SIGN OF THE ZODIAC' and unit['size']==11 and len(unit['spans'])==2:
                split.extend(_make_unit([span],unit['source_block'],False,drawings) for span in unit['spans'])
            else:split.append(unit)
        merged=split
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

def apply_editorial_composition(page, units):
    """Human-requested typography overrides; translated payloads stay immutable."""
    import copy
    units=copy.deepcopy(units)
    zodiac={'Aries','Taurus','Gemini','Cancer','Leo','Virgo','Libra','Scorpio','Sagittarius','Capricorn','Aquarius','Pisces'}
    planets={'The Sun','The Moon','Mercury','Venus','Mars','Jupiter','Saturn','Uranus','Neptune','Pluto','Chiron'}
    # Fix PDF font flags locally after source/payload provenance validation.
    # These families are sans regardless of an incorrect PDF serif flag.
    def correct_style(style):
        result=dict(style);name=style['font'].lower()
        if any(x in name for x in ('dinnext','frutiger')):result.update(serif=False,force_fallback=True)
        if 'heavy' in name:result.update(weight=900,bold=True,force_fallback=True)
        return result
    for unit in units:
        unit['style']=correct_style(unit['style'])
        unit['target_runs']=[{**run,'style':correct_style(run['style'])} for run in unit['target_runs']]
        if unit['kind']=='paragraph' and unit.get('alignment')=='left':
            unit['first_line_indent']=max(0,unit['spans'][0]['bbox'][0]-unit['bbox'][0])
    if page.number==6:
        for u in units:
            if u['context']=='table_label' and u['source'] in zodiac|planets:
                u['editorial_override']='human_requested_bold_sign_and_planet_names'
                u['style']={**u['style'],'weight':700,'bold':True,'force_fallback':True}
                u['target_runs']=[{**r,'style':{**r['style'],'weight':700,'bold':True,'force_fallback':True}} for r in u['target_runs']]
    rotated_obstacles=[fitz.Rect(item['bbox']) for item in omitted_rotated_text(page)]
    for unit in units:
        if any(fitz.Rect(unit['bbox']).intersects(rect) for rect in rotated_obstacles):unit['vector']=True
    if page.number==34:
        for unit in units:
            if unit['source'].startswith('The ruler of Aries is the warrior god Mars.'):
                unit['column_bounds']=[max(s['bbox'][0] for s in unit['spans']),unit['bbox'][2]];unit['first_line_indent']=0
    if page.number==249:
        units=[child for unit in units for child in _index_entries(unit)]
        for unit in units:
            if unit['size']==8:unit['scale_group']='p0250_index_8.0'
    if page.number==24:
        for unit in units:
            if unit['kind']!='paragraph':continue
            lines=[]
            for span in unit['spans']:
                line=next((line for line in lines if abs(line['y']-span['bbox'][1])<1),None)
                if line:line['x']=min(line['x'],span['bbox'][0])
                else:lines.append({'y':span['bbox'][1],'x':span['bbox'][0]})
            prefix=[]
            for line in lines:
                if line['x']<=unit['bbox'][0]+1:break
                prefix.append(line)
            if len(prefix)>=2 and len(prefix)<len(lines):
                unit['flow_start_x']=max(line['x'] for line in prefix);unit['flow_transition_y']=lines[len(prefix)]['y'];unit['first_line_indent']=0
    if page.number==62:
        for u in units:
            if u['source']=='BRIDGING SOCIAL & COLLECTIVE':
                u['alignment']='center';u['column_bounds']=[u['bbox'][0],u['bbox'][2]]
    if page.number==145:
        owner=next(u for u in units if u['id']=='u0026')
        punctuation=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if s['text'].strip()=='.' and fitz.Rect(s['bbox']).intersects(fitz.Rect(owner['bbox']))]
        if len(punctuation)!=1:raise Blocked('Case-study sentence punctuation provenance changed')
        raw=punctuation[0];span={'source':raw['text'],'bbox':list(raw['bbox']),'font':raw['font'],'size':raw['size'],'color':raw['color'],'flags':raw['flags'],'rotate':0,'style':span_style(raw)}
        runs=owner['target_runs'];boundary=next((i for i,r in enumerate(runs) if r['text'].rstrip().endswith('MC')),None)
        if boundary is None:raise Blocked('Case-study punctuation target boundary is ambiguous')
        runs[boundary]['text']=runs[boundary]['text'].rstrip()+'. '
        owner['target']=''.join(r['text'] for r in runs);owner['spans'].append(span);owner['relocated_punctuation']=[span]
        # A prose paragraph was classified as a table because of house numbers.
        # Rejoin its cached fragments and keep the neighboring column untouched.
        ids=[f'u{i:04d}' for i in range(1,19)];members=[u for u in units if u['id'] in ids];first=members[0]
        first['composed_ids']=ids;first['kind']='paragraph';first['context']='running_text'
        first['paragraphs']=[{'target_runs':list(first['target_runs']),'indent':0}]
        fragment_runs=[]
        for other in members[1:]:
            if fragment_runs:fragment_runs[-1]['text']+=' '
            fragment_runs+=other['target_runs']
            first['source']+=' '+other['source'];first['spans']+=other['spans'];first['bbox']=list(fitz.Rect(first['bbox'])|fitz.Rect(other['bbox']))
        first['target']+=' '+''.join(r['text'] for r in fragment_runs);first['target_runs']+=fragment_runs;first['paragraphs'].append({'target_runs':fragment_runs,'indent':8.5})
        units=[u for u in units if u is first or u not in members]
        # The last line is a continuation, not a separate caption: keep MC in
        # the same paragraph and relocate only its original terminal period.
        owner=next(u for u in units if u['id']=='u0021');tail=next(u for u in units if u['id']=='u0022')
        near=fitz.Rect(tail['bbox'])+(-.5,-.5,3,.5)
        punctuation=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if s['text'].strip()=='.' and near.intersects(fitz.Rect(s['bbox']))]
        if len(punctuation)!=1:raise Blocked('MC continuation terminal punctuation changed')
        raw=punctuation[0];period={'source':raw['text'],'bbox':list(raw['bbox']),'font':raw['font'],'size':raw['size'],'color':raw['color'],'flags':raw['flags'],'rotate':0,'style':span_style(raw)}
        owner['source']+=' '+tail['source']+'.';owner['target']+=' '+tail['target']+'.'
        owner['target_runs'][-1]['text']+=' ';owner['target_runs']+=tail['target_runs']+[{'text':'.','style':period['style']}]
        owner['spans']+=tail['spans']+[period];owner['bbox']=list(_union(owner['spans']));owner['relocated_punctuation']=[period];owner['composed_ids']=['u0021','u0022'];units.remove(tail)
        for unit in units:
            if unit['size']==8.5 and unit['kind']=='paragraph':
                lines=[]
                for span in unit['spans']:
                    line=next((line for line in lines if abs(line['y']-span['bbox'][1])<1),None)
                    if line:line['x']=min(line['x'],span['bbox'][0])
                    else:lines.append({'y':span['bbox'][1],'x':span['bbox'][0]})
                prefix=[]
                for line in lines:
                    if line['x']<=unit['bbox'][0]+1:break
                    prefix.append(line)
                if len(prefix)>=2 and len(prefix)<len(lines):
                    unit['flow_start_x']=max(line['x'] for line in prefix);unit['flow_transition_y']=lines[len(prefix)]['y'];unit['first_line_indent']=0;unit['vector']=True
        # Diagram caption consists of two separately extracted lines.
        caps=[u for u in units if u['id'] in ('u0028','u0029')]
        if len(caps)==2:
            first,other=caps;first['source']+=' '+other['source'];first['target']+=' '+other['target'];first['target_runs'][-1]['text']+=' ';first['target_runs']+=other['target_runs'];first['spans']+=other['spans'];first['bbox']=list(fitz.Rect(first['bbox'])|fitz.Rect(other['bbox']));first['kind']='paragraph';first['alignment']='center';units.remove(other)
    if page.number==134:
        revised=[]
        for u in units:
            if u['id'] not in ('u0004','u0005'):revised.append(u);continue
            parts=u['target'].split('. ',1)
            if len(parts)!=2 or len(u['target_runs'])!=1 or len(u['spans'])!=4:raise Blocked('Diagram label partition is ambiguous')
            parts[0]+='.'
            for index,part in enumerate(parts):
                spans=u['spans'][index*2:index*2+2]
                child={**u,'id':u['id']+f'_label{index}','source':' '.join(s['source'].strip() for s in spans),'spans':spans,'bbox':list(_union(spans)),'target':part,'target_runs':[{'text':part,'style':u['target_runs'][0]['style']}],'kind':'label','first_line_indent':0,'alignment':'left'}
                revised.append(child)
        units=revised
    if page.number==31:
        revised=[]
        for unit in units:
            if unit['source']=='HIGH ELEMENT':
                words=unit['target'].split()
                if len(words)!=2 or len(unit['spans'])!=2:raise Blocked('Two-column header partition is ambiguous')
                for index,span in enumerate(unit['spans']):
                    target=words[1] if span['source']=='HIGH' else words[0]
                    revised.append({**unit,'id':unit['id']+f'_column{index}','source':span['source'],'spans':[span],'bbox':span['bbox'],'target':target,'target_runs':[{'text':target,'style':unit['target_runs'][0]['style']}],'kind':'label','first_line_indent':0})
            else:revised.append(unit)
        units=revised
    if page.number==33:
        heading=next(u for u in units if u['id']=='u0002');heading['column_bounds']=[heading['bbox'][0]+2,heading['bbox'][2]]
        u=next(u for u in units if u['id']=='u0006');runs=u['target_runs'];words=runs[0]['text'].strip().split()
        if len(words)!=2 or len(runs)!=10 or len(u['spans'])!=11:raise Blocked('Table row partition is ambiguous')
        runrows=[[{'text':words[0],'style':runs[0]['style']}],[{'text':words[1]+' ','style':runs[0]['style']},runs[1]]]+[runs[i:i+2] for i in (2,4,6,8)]
        spanrows=[u['spans'][:1],u['spans'][1:3]]+[u['spans'][i:i+2] for i in (3,5,7,9)]
        children=[]
        for index,(rr,ss) in enumerate(zip(runrows,spanrows)):
            child={**u,'id':u['id']+f'_row{index}','source':' '.join(s['source'].strip() for s in ss),'spans':ss,'bbox':list(_union(ss)),'target':''.join(r['text'] for r in rr),'target_runs':rr,'kind':'label','context':'table_label','line_height':1.05}
            children.append(child)
        units=[child for old in units for child in (children if old is u else [old])]
    if page.number==24:
        revised=[]
        for unit in units:
            if unit['source']=='YOUR ASCENDANT OR RISING SIGN':
                pieces=unit['target'].split(' ',1)
                if len(pieces)!=2 or len(unit['spans'])!=2 or len(unit['target_runs'])!=1:raise Blocked('Icon-separated heading partition is ambiguous')
                for index,(span,text) in enumerate(zip(unit['spans'],pieces)):
                    revised.append({**unit,'id':unit['id']+f'_fragment{index}','source':span['source'].strip(),'spans':[span],'bbox':span['bbox'],'target':text,'target_runs':[{'text':text,'style':unit['target_runs'][0]['style']}],'kind':'label','first_line_indent':0})
            else:revised.append(unit)
        units=revised
        quotes=[u for u in units if u['size']==19]
        if len(quotes)!=2:raise Blocked('Quote composition geometry changed')
        first,last=quotes;first['source']+=' '+last['source'];first['target']+=' '+last['target'];first['target_runs'][-1]['text']+=' ';first['target_runs']+=last['target_runs'];first['spans']+=last['spans'];first['bbox']=list(fitz.Rect(first['bbox'])|fitz.Rect(last['bbox']));units.remove(last)
        punctuation=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if s['text'].strip()=='.' and fitz.Rect(s['bbox']).intersects(fitz.Rect(first['bbox'])+(-4,-4,40,4))]
        if len(punctuation)!=1:raise Blocked('Quote terminal punctuation provenance is ambiguous')
        raw=punctuation[0];span={'source':raw['text'],'bbox':list(raw['bbox']),'font':raw['font'],'size':raw['size'],'color':raw['color'],'flags':raw['flags'],'rotate':0,'style':span_style(raw)}
        first['spans'].append(span);first['bbox']=list(_union(first['spans']));first['source']+='.';first['target']+='.';first['target_runs'].append({'text':'.','style':span['style']});first['relocated_punctuation']=[span]
    if page.number==5:
        # PDF blocks split continuous legal/copyright prose mid-sentence.
        # Rejoin only source-adjacent fragments in these reviewed credit groups.
        for identifiers in (['u0019','u0020','u0021','u0022'],['u0023','u0024'],['u0025','u0026','u0027','u0028']):
            members=[u for u in units if u['id'] in identifiers]
            if len(members)!=len(identifiers):raise Blocked('Reviewed credit fragment geometry changed')
            first=members[0];first['composed_ids']=identifiers;first['alignment']='center'
            for other in members[1:]:
                first['source']+=' '+other['source'];first['target']+=' '+other['target']
                first['target_runs'][-1]['text']+=' '
                first['target_runs']+=other['target_runs'];first['spans']+=other['spans']
                first['bbox']=list(fitz.Rect(first['bbox'])|fitz.Rect(other['bbox']))
            units=[u for u in units if u is first or u not in members]
        return units
    if page.number==9:
        drop=next(u for u in units if u['source']=='F' and u['size']>30)
        body_start=next(u for u in units if u['source'].startswith('or many centuries'))
        first=body_start['target_runs'][0];letter=first['text'][0]
        body_start['target_runs']=[{'text':letter,'style':drop['style']},{**first,'text':first['text'][1:]}]+body_start['target_runs'][1:]
        body_start['drop_cap']=True;body_start['drop_cap_bbox']=drop['bbox'];body_start['source']='F'+body_start['source']
        body_start['spans']=drop['spans']+body_start['spans'];body_start['bbox']=list(fitz.Rect(drop['bbox'])|fitz.Rect(body_start['bbox']))
        units.remove(drop)
    if page.number not in (9,14):return units
    body=[u for u in units if u['kind']=='paragraph' and u['size']==(9.5 if page.number==9 else 8.5)]
    columns={}
    for u in body:
        x=u['bbox'][0]
        key=next((key for key in columns if abs(key-x)<3),round(x,1))
        columns.setdefault(key,[]).append(u)
    for siblings in columns.values():
        left=min(u['bbox'][0] for u in siblings);right=max(u['bbox'][2] for u in siblings)
        for u in siblings:
            u['column_bounds']=[left,right]
            u['first_line_indent']=max(0,u['spans'][0]['bbox'][0]-left)
    result=[]
    for u in units:
        previous=result[-1] if result else None
        join=previous and u in body and previous.get('column_bounds')==u.get('column_bounds') and previous['style']==u['style'] and 0<=u['bbox'][1]-previous['bbox'][3]<u['size']
        if join:
            previous.setdefault('paragraphs',[{'target_runs':list(previous['target_runs']),'indent':previous.get('first_line_indent',0)}]).append({'target_runs':u['target_runs'],'indent':u.get('first_line_indent',0)})
            previous.setdefault('composed_ids',[previous['id']]).append(u['id'])
            previous['source']+=' '+u['source'];previous['target']+=' '+u['target']
            previous['target_runs']+=u['target_runs'];previous['spans']+=u['spans']
            previous['bbox']=list(fitz.Rect(previous['bbox'])|fitz.Rect(u['bbox']))
        else:result.append(u)
    return result

def immutable_boxes(page,extras):
    result=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                item={'source':span['text'],'font':span['font']}
                if span['text'].strip() and immutable_span(item,extras):result.append(fitz.Rect(span['bbox']))
    return result

def _candidate_boxes(page,unit,units,guards):
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
        if not _background_clear_cached(page,box):continue
        if box not in candidates:candidates.append(box)
    return candidates

def _vector_mask(page,dpi=110):
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
    for item in omitted_rotated_text(page):p.draw_rect(fitz.Rect(item['bbox']),color=None,fill=(0,0,0))
    pix=p.get_pixmap(dpi=dpi,colorspace=fitz.csGRAY);mask=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width)<220;doc.close();return mask

def _html_for(unit,font_map,font_root,factor):
    css=[f"body,div {{font-size:{unit['style']['size']*factor}pt;margin:0;padding:0;line-height:{unit.get('line_height',1.05)}}} p {{margin:0;padding:0}}"]
    pieces=[];used=[]
    paragraphs=unit.get('paragraphs',[{'target_runs':unit.get('target_runs',[{'text':unit['target'],'style':unit['style']}]),'indent':unit.get('first_line_indent',0)}])
    boundaries={};offset=0
    for paragraph in paragraphs:
        boundaries[offset]=paragraph['indent'];offset+=len(paragraph['target_runs'])
    for run_index,run in enumerate(unit.get('target_runs',[{'text':unit['target'],'style':unit['style']}])):
        if run_index in boundaries:
            if run_index:pieces.append('</p>')
            pieces.append(f'<p style="text-indent:{boundaries[run_index]}pt">')
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
    return fitz.open(stream=stream.getvalue(),filetype='pdf'),not (cap_more or more)

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
    return fitz.open(stream=stream.getvalue(),filetype='pdf'),not more

def _test_fit(page,unit,box,factor,font_map,font_root,vector_mask):
    import numpy as np
    doc=fitz.open();p=doc.new_page(width=page.rect.width,height=page.rect.height)
    content,css,fonts=_html_for(unit,font_map,font_root,factor)
    if unit.get('flow_start_x'):
        overlay,finished=_flow_overlay(page,unit,box,factor,font_map,font_root);p.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
    elif unit.get('drop_cap'):
        overlay,finished=_dropcap_overlay(page,unit,box,factor,font_map,font_root);p.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
    else:spare,scale=p.insert_htmlbox(box,content,css=css,archive=fitz.Archive(str(font_root)),scale_low=1)
    text=p.get_text();ok=spare>=0 and _normalized(unit['target']) in _normalized(text)
    overlap=0
    if ok and np.any(vector_mask[max(0,int(box.y0*110/72)):min(vector_mask.shape[0],int(box.y1*110/72)+1),max(0,int(box.x0*110/72)):min(vector_mask.shape[1],int(box.x1*110/72)+1)]):
        pix=p.get_pixmap(dpi=110,alpha=True);a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
        overlap=int(np.count_nonzero((a>80)&vector_mask))
        ok=overlap==0
    raster=None
    if ok:
        from .raster_obstacles import check_overlay
        raster=check_overlay(page,p,box)
        ok=raster['ok']
    occupied=[list(box)]
    if unit.get('flow_start_x'):occupied=[[unit['flow_start_x'],box.y0,box.x1,unit['flow_transition_y']],[box.x0,unit['flow_transition_y'],box.x1,box.y1]]
    doc.close();return ok,{'box':list(box),'occupied_boxes':occupied,'scale':factor,'font_runs':fonts,'vector_overlap_pixels':overlap,'raster_guard':raster,'fit':ok}

def composition_sources_sha256(units):
    sources=[{key:unit[key] for key in ('id','source','spans')} for unit in units]
    return hashlib.sha256(json.dumps(sources,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def apply_composition_overrides(book,units,path,page,glossary,style,do_not_translate):
    path=Path(path).resolve()
    if not path.is_relative_to((Path(book)/'work').resolve()):raise Blocked('Composition overrides must reside below book/work')
    payload=json.loads(path.read_text())
    expected={'page':page,'source_sha256':sha256(Path(book)/'source.pdf'),'glossary_sha256':sha256(glossary),'style_sha256':sha256(style),'do_not_translate_sha256':sha256(do_not_translate) if do_not_translate else None,'composition_sha256':composition_sources_sha256(units)}
    if any(payload.get(k)!=v for k,v in expected.items()):raise Blocked('Composition override hashes or scope changed')
    if not payload.get('provenance'):raise Blocked('Composition override lacks model-call provenance')
    translated={item['id']:item for item in payload['blocks']}
    if len(translated)!=len(payload['blocks']) or set(translated)!={u['id'] for u in units}:raise Blocked('Composition override must cover each composed unit exactly once')
    result=[]
    for unit in units:
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

def reinsert_fidelity(book,payload,glossary,style,do_not_translate=None,config=None,artifact_dir=None,phase='fidelity',composition_overrides=None):
    """Compose complete paragraphs with original styling and shared group scales."""
    from .fonts import select_group_fonts
    from .layout import safe_redaction_rects
    book=Path(book);config=config or {};require_approval(book,glossary,style,do_not_translate);extras=read_extras(do_not_translate)
    folder=_artifact_folder(book,artifact_dir);folder.mkdir(parents=True,exist_ok=True);font_root=folder/'fonts';font_root.mkdir(exist_ok=True)
    number=payload['page'];units=[{**u,'translation_id':u['id'],'id':u['id'].removeprefix(f'p{number:04d}_')} for u in payload['blocks']]
    with fitz.open(book/'source.pdf') as original:
        page=original[number-1]
        if page.rotation:raise Blocked('Rotated PDF pages need explicit normalization')
        canonical={u['id']:u for u in extract_fidelity_units(page,extras)}
        if len(units)!=len(canonical) or {u['id'] for u in units}!=set(canonical):raise Blocked('Fidelity payload must cover every editable container exactly once')
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
        units=apply_editorial_composition(page,checked)
        if composition_overrides:units=apply_composition_overrides(book,units,composition_overrides,number,glossary,style,do_not_translate)
        font_map,audit=select_group_fonts(original,page,units,font_root);atomic_json(folder/f'p{number:04d}-fonts.json',audit)
        vector_mask=_vector_mask(page);guards=immutable_boxes(page,extras)
        relocated=[fitz.Rect(s['bbox']) for u in units for s in u.get('relocated_punctuation',[])]
        guards=[r for r in guards if not any(r==owned for owned in relocated)]
        groups=defaultdict(list)
        for unit in units:groups[unit['scale_group']].append(unit)
        selections={};group_scales={};failures=[]
        candidates={u['id']:_candidate_boxes(page,u,units,guards) for u in units}
        # Joint group selection makes all siblings, including unchanged Aries/Leo,
        # use the same baseline size, rather than shrinking each label separately.
        for group,members in groups.items():
            accepted=None
            for percent in range(100,74,-1):
                factor=percent/100;attempt={};occupied=[fitz.Rect(rect) for x in selections.values() for rect in x.get('occupied_boxes',[x['box']])]
                for unit in members:
                    found=None
                    for box in candidates[unit['id']]:
                        if any(box.intersects(r) for r in occupied):continue
                        ok,info=_test_fit(page,unit,box,factor,font_map,font_root,vector_mask)
                        if ok:found=info;break
                    if found is None:
                        atomic_json(folder/f'p{number:04d}-last-fit-diagnostic.json',{'group':group,'scale':factor,'id':unit['id'],'source':unit['source'],'target':unit['target'],'candidate_count':len(candidates[unit['id']])})
                        break
                    attempt[unit['id']]=found;occupied.extend(fitz.Rect(rect) for rect in found.get('occupied_boxes',[found['box']]))
                if len(attempt)==len(members):accepted=attempt;group_scales[group]=factor;break
            if accepted is None:
                failures.extend({'id':u['id'],'source':u['source'],'target':u['target'],'group':group} for u in members)
            else:selections.update(accepted)
        if failures:
            result={'page':number,'phase':phase,'artifact_dir':str(folder),'status':'review','source_sha256':sha256(book/'source.pdf'),'failures':failures,'group_scales':group_scales,'fit_complete':False}
            atomic_json(folder/f'p{number:04d}-fit.json',result);return result
        working=fitz.open();working.insert_pdf(original,from_page=number-1,to_page=number-1);wp=working[0];reports=[]
        try:
            for unit in units:
                # Whole source container is editable, including numeric fragments;
                # only original symbol-font/standalone protected tokens are immutable.
                rects=safe_redaction_rects(page,unit)
                for rect in rects:wp.add_redact_annot(rect,fill=False)
            wp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            for unit in units:
                selection=selections[unit['id']];factor=selection['scale'];box=fitz.Rect(selection['box'])
                content,css,font_runs=_html_for(unit,font_map,font_root,factor)
                if unit.get('flow_start_x'):
                    overlay,finished=_flow_overlay(page,unit,box,factor,font_map,font_root);wp.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
                elif unit.get('drop_cap'):
                    overlay,finished=_dropcap_overlay(page,unit,box,factor,font_map,font_root);wp.show_pdf_page(page.rect,overlay,0);overlay.close();spare=0 if finished else -1
                else:spare,scale=wp.insert_htmlbox(box,content,css=css,archive=fitz.Archive(str(font_root)),scale_low=1)
                if spare<0:raise Blocked('Previously fitted content failed transactional insertion')
                reports.append({**unit,'edit_bbox':list(box),'fit':True,'scale':factor,'font_runs':font_runs,'raster_guard':selection.get('raster_guard'),'review':factor<.8})
            # insert_pdf omits the page transparency Group. Keep its original
            # reference in this same document so color blending remains exact.
            group_type,group_value=original.xref_get_key(page.xref,'Group')
            original.delete_page(number-1);original.insert_pdf(working,start_at=number-1)
            if group_type!='null':original.xref_set_key(original[number-1].xref,'Group',group_value)
            output=folder/f'p{number:04d}-translated.pdf';_save_pdf(original,output)
            result={'page':number,'phase':phase,'artifact_dir':str(folder),'status':'pending_qa','source_sha256':sha256(book/'source.pdf'),'output':str(output),'output_sha256':sha256(output),'blocks':reports,'group_scales':group_scales,'fit_complete':True,'failures':[],'font_audit':str(folder/f'p{number:04d}-fonts.json')}
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

def verified_editorial_text(document,page,result,extras=()):
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
                text=re.sub(r'\b\d+(?:st|nd|rd|th)\b',' ',text,flags=re.I)
                editorial.append(NUMBER_PATTERN.sub(' ',SYMBOL_PATTERN.sub(' ',text)))
    return editorial

def fidelity_qa(book,result,glossary,do_not_translate=None,artifact_dir=None,phase=None):
    """Local fidelity QA. Numeric prose may move; original symbol spans may not."""
    import csv
    import numpy as np
    from PIL import Image,ImageDraw
    from .language import residual_english,glossary_matches
    book=Path(book);number=result['page'];extras=read_extras(do_not_translate)
    if result['source_sha256']!=sha256(book/'source.pdf') or result['output_sha256']!=sha256(result['output']):raise Blocked('Fidelity QA hashes do not bind to source and candidate')
    folder=_artifact_folder(book,artifact_dir or result.get('artifact_dir'));renders=folder/'renders';renders.mkdir(parents=True,exist_ok=True)
    with fitz.open(book/'source.pdf') as source,fitz.open(result['output']) as candidate:
        a=source[number-1];b=candidate[number-1];before=a.get_text();after=b.get_text()
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
            for span in block.get('relocated_punctuation',[]):
                if span['source'].strip() not in ('.',',',';',':','!','?'):raise Blocked('Only punctuation can be repositioned by this rule')
                for key in list(immutable_original):
                    char,font,bbox=key
                    if char==span['source'].strip() and font==span['font'] and (fitz.Rect(span['bbox'])+(-.02,-.02,.02,.02)).contains(fitz.Rect(bbox)):del immutable_original[key]
        immutable_equal=not (immutable_original-_immutable_characters(b,extras,False))
        numbers_equal=Counter(NUMBER_PATTERN.findall(before))==Counter(NUMBER_PATTERN.findall(after))
        symbols_equal=Counter(SYMBOL_PATTERN.findall(before))==Counter(SYMBOL_PATTERN.findall(after))
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
            for box in [block['bbox'],block['edit_bbox']]:
                r=fitz.Rect(box);draw.rectangle([r.x0*110/72-2,r.y0*110/72-2,r.x1*110/72+2,r.y1*110/72+2],fill=0)
        delta=np.any(np.asarray(ia)!=np.asarray(ib),axis=2);valid=np.asarray(mask)>0;diff=float(delta[valid].mean()) if valid.any() else 0.
        with open(glossary) as stream:terms=list(csv.DictReader(stream))
        allowed=list(extras)+[row['target'] for row in terms]
        editorial=verified_editorial_text(candidate,b,result,extras)
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
            for term in glossary_matches(block['source'],terms):
                if not re.search(r'(?<!\w)'+re.escape(term['target'])+r'(?!\w)',block['target'],re.I):missing.append(term['source'])
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
        local_ok=not omitted_rotated and typography['ok'] and all((immutable_equal,numbers_equal,symbols_equal,images_equal,vectors_equal,embedded_verified,complete_text,uniform,colors_verified)) and diff==0 and minimum>=.75 and not residual and not missing
        report={'page':number,'phase':phase or result.get('phase','fidelity'),'artifact_dir':str(folder),'verdict':'revisar','local_fidelity_ok':local_ok,'omitted_rotated_text':omitted_rotated,'residual_english':residual,'glossary_missing':sorted(set(missing)),'glyph_set_equal':symbols_equal,'numbers_equal':numbers_equal,'immutable_spans_equal':immutable_equal,'min_scale':minimum,'non_text_pixel_diff':diff,'images_equal':images_equal,'vectors_equal':vectors_equal,'font_files_embedded_verified':embedded_verified,'embedded_font_names':sorted({pdf_fonts[h] for h in expected if h in pdf_fonts}),'complete_translated_text':complete_text,'uniform_scale_groups':uniform,'group_scales':result['group_scales'],'original_colors_verified':colors_verified,'typography_audit':typography,'vlm_complete':False,'vlm_notes':'Not executed: protected raster masks unverified. Local checks plus human comparison required.','art_exception':'Decorative raster PLANETS on source page 7 is explicitly preserved.' if number==7 else None,'source_sha256':sha256(book/'source.pdf'),'candidate_sha256':sha256(result['output'])}
    atomic_json(folder/f'p{number:04d}-qa.json',report)
    with book_lock(book):
        mp=book/'work/manifest.json'
        if mp.exists():
            state=json.loads(mp.read_text());page=state['pages'][number-1];page['status']='review';page['attempts']+=1;page['fidelity_qa']=str(folder/f'p{number:04d}-qa.json');atomic_json(mp,state)
    return report


def _ink_bbox(page,unit):
    rect=fitz.Rect(unit['bbox'])
    if unit['context']!='table_label' or len(unit['spans'])!=1:return rect
    wanted=unit['spans'][0]
    if wanted['source']==wanted['source'].strip():return rect
    if not hasattr(page,'_fidelity_raw_spans'):
        page._fidelity_raw_spans=[s for b in page.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
    for span in page._fidelity_raw_spans:
        text=''.join(c['c'] for c in span['chars'])
        if text==wanted['source'] and span['font']==wanted['font'] and abs(span['bbox'][0]-wanted['bbox'][0])<.01 and abs(span['bbox'][1]-wanted['bbox'][1])<.01:
            chars=[fitz.Rect(c['bbox']) for c in span['chars'] if c['c'].strip()]
            if chars:rect.x0=min(r.x0 for r in chars);rect.x1=max(r.x1 for r in chars)
            break
    return rect
