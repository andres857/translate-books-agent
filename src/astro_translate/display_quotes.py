"""Source-bound display quotes: one semantic unit, original line rhythm.

This module never translates. Legacy fragments are joined for diagnostics and
remain flagged until a run_task correction is available.
"""
from copy import deepcopy
import re
import pymupdf as fitz
from .text_quality import normalize_local_typography


def source_lines(unit,page=None):
    lines=[]
    for span in unit['spans']:
        box=fitz.Rect(span['bbox'])
        if lines and abs(box.y0-lines[-1]['bbox'][1])<span['size']*.3:
            lines[-1]['bbox']=list(fitz.Rect(lines[-1]['bbox'])|box)
            lines[-1]['source']+=span['source']
        else:lines.append({'bbox':list(box),'source':span['source'],'size':span['size']})
    if page is not None:
        raw=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
        for line in lines:
            matches=[s for s in raw if abs(s['bbox'][1]-line['bbox'][1])<line['size']*.3 and fitz.Rect(line['bbox']).intersects(fitz.Rect(s['bbox']))]
            if matches:line['baseline']=min(s['origin'][1] for s in matches)
    return lines


def prepare_display_quotes(page,units):
    from .editorial_rules import quote_retranslation_units
    result=deepcopy(units)
    for quote in quote_retranslation_units(page,units):
        members=[u for u in units if u['id'] in quote['composed_ids']]
        if not members and quote.get('display_quote'):continue
        runs=[]
        for u in members:
            child=normalize_local_typography(u)
            if runs and not runs[-1]['text'].endswith(' '):runs[-1]['text']+=' '
            runs.extend(deepcopy(child['target_runs']))
        # Source emphasis pairs already decoded by apply_corrections take
        # precedence over the diagnostic join of historical fragments.
        prepared={**quote,'target_runs':runs,'target':''.join(r['text'] for r in runs)}
        lines=source_lines(quote,page)
        centers=[(l['bbox'][0]+l['bbox'][2])/2 for l in lines]
        centered=max(centers)-min(centers)<=max(l['size'] for l in lines)*.35
        prepared.update(display_quote=True,display_lines=lines,alignment='center' if centered else 'left',
                        first_line_indent=0,legacy_quote_fragments=True)
        ids=set(quote['composed_ids'])
        result=[u for u in result if u['id'] not in ids]+[prepared]
    # Corrected units have already replaced their members. Attach only derived
    # geometry; source/request identity remains compatible with older records.
    for unit in result:
        if unit.get('display_title') or not unit.get('correction_provenance') or not unit.get('composed_ids'):continue
        lines=source_lines(unit,page)
        if len(lines)<2 or unit['size']<20:continue
        centers=[(l['bbox'][0]+l['bbox'][2])/2 for l in lines]
        centered=max(centers)-min(centers)<=max(l['size'] for l in lines)*.35
        unit.update(display_quote=True,display_lines=lines,alignment='center' if centered else 'left',first_line_indent=0)
        corrected=normalize_local_typography(unit);unit.update(corrected)
    return result


def _tokens(unit,font_map,factor):
    chars=[]
    for r in unit['target_runs']:
        style=r['style'];choice=font_map[(unit['scale_group'],tuple(sorted(style.items())))]
        size=style['size']*factor
        for c in r['text']:
            chars.append((c,style,choice,choice['font'].text_length(c,fontsize=size)))
    text=''.join(c[0] for c in chars)
    return [chars[m.start():m.end()] for m in re.finditer(r'\S+\s*',text)]


def line_runs(unit,font_map,factor,container=None):
    """Dynamic partition by source line widths; never cuts a word/style run.

    ``container`` is the candidate box (a symmetric growth of the quote box).
    """
    words=_tokens(unit,font_map,factor);lines=unit['display_lines'];count=len(lines)
    if len(words)<count:return None
    widths=[sum(c[3] for c in word) for word in words]
    prefix=[0]
    for width in widths:prefix.append(prefix[-1]+width)
    capacities=[l['bbox'][2]-l['bbox'][0] for l in lines]
    ratio=prefix[-1]/sum(capacities)
    # Fit to source rhythm, retaining nonempty lines and permitting expansion
    # only within the quote's proven container, centered around source centers.
    box=fitz.Rect(container if container is not None else unit['bbox']);dp={(0,0):(0,[])}
    for row,line in enumerate(lines):
        for consumed in range(len(words)+1):
            state=dp.get((row,consumed))
            if state is None:continue
            for stop in range(consumed+1,len(words)-(count-row-1)+1):
                chars=[c for word in words[consumed:stop] for c in word]
                while chars and chars[-1][0].isspace():chars.pop()
                width=sum(c[3] for c in chars)
                center=(line['bbox'][0]+line['bbox'][2])/2
                limit=2*min(center-box.x0,box.x1-center) if unit['alignment']=='center' else box.x1-line['bbox'][0]
                if width>limit+.01:continue
                cost=state[0]+((width-capacities[row]*ratio)/max(capacities[row],1))**2
                key=(row+1,stop)
                if key not in dp or cost<dp[key][0]:dp[key]=(cost,state[1]+[chars])
    answer=dp.get((count,len(words)))
    return answer[1] if answer else None


def quote_mark_rects(page,box,size):
    """Decorative quote marks drawn as filled glyph-like vectors around a display box.

    Same shape test as ``survey_detectors.vector_quote_marks``: height 0.4–1.3 em,
    narrower than tall, within 2 em horizontally and 1.5 em vertically.
    """
    cache=page.__dict__.setdefault('_astro_quote_marks',{})
    key=(tuple(round(v,2) for v in box),round(size,2))
    if key not in cache:
        area=fitz.Rect(box)+(-2*size,-1.5*size,2*size,1.5*size);marks=[]
        for d in page.get_drawings():
            if d.get('fill') is None or d['type'] not in ('f','fs'):continue
            r=fitz.Rect(d['rect'])
            if .4*size<=r.height<=1.3*size and r.width<=.8*r.height and area.contains(r):marks.append(r)
        cache[key]=marks
    return cache[key]


def _mark_clearance(page,unit):
    """Each mark with its clearance: 1 pt, never more than the original text kept.

    A mark that trails a source line (closing mark after the last word) also
    owns that line's band above it: new text may not sit on top of the mark.
    """
    result=[]
    spans=[fitz.Rect(s['bbox']) for s in unit['spans']]
    for mark in quote_mark_rects(page,unit['bbox'],unit['size']):
        gaps=[max(mark.x0-r.x1,r.x0-mark.x1,mark.y0-r.y1,r.y0-mark.y1) for r in spans]
        c=min(1.0,max(0.0,min(gaps,default=1.0)))
        zone=mark+(-c,-c,c,c)
        beside=[r for r in spans if r.y0<mark.y1 and r.y1>mark.y0 and r.x1<=mark.x0]
        if beside:zone.y0=min(zone.y0,min(r.y0 for r in beside))
        result.append(zone)
    return result


def framed(page,unit):
    """Display text (>=20 pt) framed by vector quote marks."""
    return unit.get('size',0)>=20 and bool(unit.get('spans')) and bool(quote_mark_rects(page,unit['bbox'],unit['size']))


def clear_of_marks(page,unit,rendered):
    """No new glyph ink on a decorative quote mark (or its clearance).

    ``rendered`` is a page holding only the proposed text of ``unit``.
    """
    import numpy as np
    zones=_mark_clearance(page,unit)
    if not zones:return True
    pix=rendered.get_pixmap(dpi=220,alpha=True)
    alpha=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
    k=220/72
    for zone in zones:
        x0,y0=max(0,int(zone.x0*k)),max(0,int(zone.y0*k))
        if np.any(alpha[y0:int(np.ceil(zone.y1*k)),x0:int(np.ceil(zone.x1*k))]>20):return False
    return True


def overlay(page,unit,box,factor,font_map):
    out=fitz.open();p=out.new_page(width=page.rect.width,height=page.rect.height)
    if unit['alignment']!='center':
        from .fidelity import _html_for
        from pathlib import Path
        import os
        root=Path(os.path.commonpath([str(Path(c['path']).parent) for c in font_map.values()]))
        content,css,_=_html_for(unit,font_map,root,factor)
        from .fonts import insert_htmlbox_headroom,unit_headroom  # accented capitals are not clipped
        spare,_=insert_htmlbox_headroom(p,box,content,css,fitz.Archive(str(root)),unit_headroom(unit,factor))
        return out,spare>=0
    partition=line_runs(unit,font_map,factor,box)
    if partition is None:return out,False
    for line,chars in zip(unit['display_lines'],partition):
        width=sum(c[3] for c in chars)
        x=(line['bbox'][0]+line['bbox'][2]-width)/2 if unit['alignment']=='center' else line['bbox'][0]
        # PDF small glyph-height extraction places the source baseline at
        # y0 + size * ascender/(ascender-descender). Use the chosen font metric.
        main=chars[0][2]['font'];size=line['size']
        baseline=line.get('baseline',line['bbox'][1]+size*main.ascender/(main.ascender-main.descender))
        for c,style,choice,advance in chars:
            name='quote'+choice['sha256'][:14];p.insert_font(fontname=name,fontfile=choice['path'])
            p.insert_text((x,baseline),c,fontname=name,fontsize=style['size']*factor,
                          color=tuple(((style['color']>>shift)&255)/255 for shift in (16,8,0)))
            x+=advance
    import numpy as np
    pix=p.get_pixmap(dpi=220,alpha=True)
    alpha=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
    ys,xs=np.where(alpha>20)
    ink=fitz.Rect(float(xs.min())*72/220,float(ys.min())*72/220,float(xs.max()+1)*72/220,float(ys.max()+1)*72/220) if len(xs) else fitz.Rect()
    return out,bool(len(xs)) and fitz.Rect(box).contains(ink)


def candidate_boxes(page,unit,units,guards):
    """Reserve accent ink above native capitals only in proven free space."""
    if unit['alignment']!='center':
        from .fidelity import _base_candidate_boxes
        return _base_candidate_boxes(page,{**unit,'display_quote':False},units,guards)
    box=fitz.Rect(unit['bbox']);result=[box]
    obstacles=[fitz.Rect(u['bbox']) for u in units if u['id']!=unit['id']]+list(guards)
    vertical=[]
    for margin in (2,4,8,unit['size']*.22):
        grown=(box+(0,-margin,0,margin))&page.rect
        if any((grown&r).get_area()>(box&r).get_area()+.01 for r in obstacles):continue
        result.append(grown);vertical.append(grown)
    # Symmetric horizontal growth (same line centers) into free flat space;
    # the overlay's real ink is still guarded against vectors and rasters.
    from .fidelity import _growth_clear
    from .editorial_margins import editorial_bounds
    left,right=editorial_bounds(page)
    for base in [box]+vertical:
        for amount in (8,16,24,32,48,64,80):
            grown=fitz.Rect(base.x0-amount,base.y0,base.x1+amount,base.y1)
            if grown.x0<max(left,page.rect.x0+2) or grown.x1>min(right,page.rect.x1-2):break
            if any((grown&r).get_area()>(base&r).get_area()+.01 for r in obstacles):break
            if not _growth_clear(page,base,grown):break
            result.append(grown)
    return result
