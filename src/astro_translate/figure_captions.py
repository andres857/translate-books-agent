"""Recover spatial callout paragraphs without trusting PDF block boundaries.

Source spans/legacy ids remain immutable. A changed partition needs new saved
translations; mixed targets are never split by word count or concatenated.
"""
from copy import deepcopy
import hashlib
import json
import math
import pymupdf as fitz


def _key(span):
    return json.dumps(span,sort_keys=True,ensure_ascii=False)


def _guide_ends(page):
    from collections import Counter
    result=[]
    for drawing in page.get_drawings():
        if drawing['type'] not in ('s','fs') or not drawing.get('stroke_opacity',1):continue
        pts=[p for item in drawing['items'] if item[0] in ('l','c') for p in (item[1],item[-1])]
        counts=Counter((round(p.x,2),round(p.y,2)) for p in pts)
        result += [p for p in pts if counts[(round(p.x,2),round(p.y,2))]==1]
    return result


def _distance(point,box):
    return math.hypot(max(box.x0-point.x,0,point.x-box.x1),max(box.y0-point.y,0,point.y-box.y1))


def caption_units(page, units):
    """Small italic guide labels, grouped by baseline and a shared text anchor.

    Center/right/left alignment is inferred over the whole cluster. Wide jumps,
    parallel rows and disconnected guides cannot become a single paragraph.
    Other editorial paragraphs are outside this narrow detector.
    """
    from .fidelity import _make_unit
    ends=_guide_ends(page)
    if not ends:return []
    owners={};spans=[]
    for unit in units:
        if unit.get('context')=='table_label' or not (6<=unit['size']<=10):continue
        if not all(s['style']['italic'] and s.get('rotate')==0 for s in unit['spans']):continue
        for span in unit['spans']:
            box=fitz.Rect(span['bbox'])
            if box.width>page.rect.width*.3 or not page.rect.height*.1<box.y0<page.rect.height*.94:continue
            key=_key(span)
            if key not in owners:spans.append(span)
            owners.setdefault(key,[]).append(unit['id'])
    spans.sort(key=lambda s:(s['bbox'][1],s['bbox'][0]))
    groups=[]
    for span in spans:
        b=fitz.Rect(span['bbox']);size=span['size'];matches=[]
        for index,group in enumerate(groups):
            a=fitz.Rect(group[-1]['bbox'])
            if group[-1]['style']!=span['style'] or not .85*size<=b.y0-a.y0<=1.6*size:continue
            # Two independently attached leaders identify different callouts,
            # even when the text columns and line spacing happen to match.
            ga={i for i,p in enumerate(ends) if any(_distance(p,fitz.Rect(s['bbox']))<=size*.4 for s in group)}
            gb={i for i,p in enumerate(ends) if _distance(p,b)<=size*.4}
            if ga and gb and ga.isdisjoint(gb):continue
            boxes=[fitz.Rect(s['bbox']) for s in group]+[b]
            anchors=[[r.x0 for r in boxes],[r.x1 for r in boxes],[(r.x0+r.x1)/2 for r in boxes]]
            if not any(max(values)-min(values)<=3 for values in anchors):continue
            # Separate spatial columns even when their rows are interleaved.
            if min(a.x1,b.x1)-max(a.x0,b.x0)<=0:continue
            matches.append(index)
        if len(matches)==1:groups[matches[0]].append(span)
        else:groups.append([span])
    found=[]
    for group in groups:
        unit=_make_unit(deepcopy(group),-1,False,[]);box=fitz.Rect(unit['bbox'])
        if min((_distance(p,box) for p in ends),default=float('inf'))>unit['size']*2.5:continue
        boxes=[fitz.Rect(s['bbox']) for s in group]
        variations={name:max(v)-min(v) for name,v in (
            ('center',[(r.x0+r.x1)/2 for r in boxes]),('left',[r.x0 for r in boxes]),('right',[r.x1 for r in boxes]))}
        alignment=min(variations,key=variations.get)
        members=[u for u in units if any(u['id'] in owners[_key(s)] for s in group)]
        exact=next((u for u in members if u['spans']==group),None)
        identifier=exact['id'] if exact else 'caption_'+hashlib.sha256(''.join(_key(s) for s in group).encode()).hexdigest()[:12]
        unit.update(id=identifier,container_id=f'p{page.number+1:04d}_{identifier}',page=page.number+1,
                    source_block=members[0]['source_block'],scale_group=members[0]['scale_group'],
                    alignment=alignment,spatial_caption=True,caption_growth=True,first_line_indent=0,
                    context='figure_label',kind='caption',caption_parent_ids=[u['id'] for u in members])
        if exact:
            # Keep the canonical source identity and saved semantics unchanged.
            unit={**deepcopy(exact),**{k:unit[k] for k in ('alignment','spatial_caption','caption_growth','first_line_indent','context','kind','caption_parent_ids')}}
        found.append(unit)
    return found


def resegment_captions(page,units):
    caps=caption_units(page,units)
    consumed={i for u in caps for i in u['caption_parent_ids']}
    # Only replace entire source owners, never drop an unclassified span.
    covered={_key(s) for u in caps for s in u['spans']}
    if any(any(_key(s) not in covered for s in u['spans']) for u in units if u['id'] in consumed):return units
    if not consumed:return units
    result=[];emitted=set()
    for u in units:
        if u['id'] not in consumed:result.append(u);continue
        for cap in caps:
            if cap['id'] not in emitted and u['id'] in cap['caption_parent_ids']:
                result.append(cap);emitted.add(cap['id'])
    return result
