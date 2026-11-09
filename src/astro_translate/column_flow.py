"""Geometry-derived editorial stories, laid out once across an ordered frame chain.

Saved units remain the translation authority. This module only partitions runs at
verified style boundaries and joins column continuations; it never translates.
"""
from collections import Counter
from copy import deepcopy
from statistics import median
import io
import re
import pymupdf as fitz
from .core import Blocked


def _family(style):
    return re.sub(r'[- ]?(light|regular|medium|bold|heavy|italic|oblique).*$', '', style['font'], flags=re.I)


def _parts(unit, body_size):
    """Split standalone subheads even when extraction attached one to body end."""
    from .fidelity import _slice_runs, _make_unit
    spans=deepcopy(unit['spans']);groups=[]
    for s in spans:
        role='heading' if s['size']>body_size*1.08 else 'body'
        if not groups or groups[-1][0]!=role:groups.append((role,[]))
        groups[-1][1].append(s)
    if len(groups)==1:
        return [dict(role=groups[0][0],spans=spans,target_runs=deepcopy(unit['target_runs']),source=unit['source'])]
    # Match the same size roles in the translated runs. Inline weight changes
    # at body size remain inside the paragraph, including bold and italic runs.
    targets=[]
    for r in unit['target_runs']:
        role='heading' if r['style']['size']>body_size*1.08 else 'body'
        if not targets or targets[-1][0]!=role:targets.append((role,[]))
        targets[-1][1].append(deepcopy(r))
    if [r for r,_ in groups]!=[r for r,_ in targets]:
        raise Blocked('Story heading boundary requires a validated retranslation')
    return [dict(role=role,spans=ss,target_runs=rr,source=_make_unit(ss,unit['source_block'],False,[])['source'])
            for (role,ss),(_,rr) in zip(groups,targets)]


def prepare_stories(page, units):
    """Find prose columns sharing family, size, baseline region and reading frame.

    Require normal-width body and substantial vertical extent. A single-column
    frame also requires a distinct subhead. Lists/captions, arbitrary tables and display text are not stories.
    Column bottoms use the original body frame; no growth into artwork.
    """
    units=deepcopy(units)
    candidates=[u for u in units if not any(u.get(k) for k in ('native_ordinal','run_in_heading','chart_callout','contents_column','protected_label','figure_legend'))
                and u['bbox'][1]>page.rect.height*.1 and u['bbox'][2]-u['bbox'][0]<page.rect.width*.48
                and all(s['style']['width']==100 and 9<=s['size']<=16 for s in u['spans'])]
    weights=Counter()
    for u in candidates:
        for s in u['spans']:
            if s['size']<=12 and s['style']['weight']<=400:
                weights[(_family(s['style']),round(s['size'],2))]+=len(s['source'])
    if not weights:return units
    (family,size),count=weights.most_common(1)[0]
    if count<180:return units
    eligible=[u for u in candidates if all(_family(s['style'])==family and size-.1<=s['size']<=size*1.5 for s in u['spans'])]
    has_heading=any(any(s['size']>size*1.08 for s in u['spans']) for u in eligible)
    columns=[]
    for u in sorted(eligible,key=lambda u:(u['bbox'][0],u['bbox'][1])):
        col=next((c for c in columns if abs(c[0]['bbox'][0]-u['bbox'][0])<=size),None)
        if col is None:columns.append([u])
        else:col.append(u)
    columns=[sorted(c,key=lambda u:u['bbox'][1]) for c in columns
             if max(u['bbox'][3] for u in c)-min(u['bbox'][1] for u in c)>size*5]
    if not columns:return units
    top=median(min(u['bbox'][1] for u in c) for c in columns)
    columns=[c for c in columns if abs(min(u['bbox'][1] for u in c)-top)<size*2]
    if not columns:return units
    if len(columns)==1 and not has_heading:return units
    # A frame chain must have separate horizontal columns, never a staggered
    # set of overlapping body boxes mistaken for independent columns.
    if any(max(u['bbox'][2] for u in left)>min(u['bbox'][0] for u in right)
           for left,right in zip(columns,columns[1:])):return units
    members=[u for c in columns for u in c];ids={u['id'] for u in members}
    # Avoid sweeping unrelated labels or protected native values into a frame.
    from .fidelity import immutable_boxes
    guards=immutable_boxes(page,())
    frames=[];bands=[];paragraphs=[];last=None
    for ci,col in enumerate(columns):
        x0=min(u['bbox'][0] for u in col);x1=max(u['bbox'][2] for u in col)
        y0=min(u['bbox'][1] for u in col);y1=max(u['bbox'][3] for u in col)
        frame=fitz.Rect(x0,y0,x1,y1)
        if any(frame.intersects(fitz.Rect(u['bbox'])) for u in units if u['id'] not in ids):return units
        if any(frame.intersects(g) for g in guards):return units
        from .panel_frames import extend_bottom
        frame=extend_bottom(page,frame,[u['bbox'] for u in units if u['id'] not in ids]+guards,em=size)
        frames.append(list(frame));bands.append(None)
        for u in col:
            for part in _parts(u,size):
                ss=part['spans'];box=fitz.Rect(ss[0]['bbox'])
                for s in ss[1:]:box|=fitz.Rect(s['bbox'])
                indent=max(0,ss[0]['bbox'][0]-x0) if part['role']=='body' else 0
                part.update(indent=indent,source_bbox=list(box),column=ci,unit_id=u['id'],space_before=0,space_after=0)
                # A first-line offset larger than two ems clears a nearby
                # icon; it is spatial clearance, not typographic indentation.
                # Preserve its points when the group scales down.
                if indent>size*2:part['fixed_indent']=True
                # At the column top that clearance is an obstacle band, not a
                # first-line indent: lines beside the icon start at its right
                # edge down to where full-width lines started in the original.
                if (part.get('fixed_indent') and (last is None or last['column']!=ci)
                        and abs(ss[0]['bbox'][1]-frame.y0)<size*.5):
                    indented=[t for t in ss if t['bbox'][0]>=x0+indent-1]
                    lead=0
                    while lead<len(ss) and ss[lead]['bbox'][0]>=x0+indent-1:lead+=1
                    below=ss[lead]['bbox'][1] if lead<len(ss) else max(t['bbox'][3] for t in indented)
                    bands[ci]=[x0+indent,max(below,max(t['bbox'][3] for t in ss[:lead]))]
                    part.update(indent=0,icon_band=bands[ci]);part.pop('fixed_indent')
                if part['role']=='heading':
                    # Original heading gaps, in points (scaled with the story).
                    if last and last['column']==ci:part['space_before']=max(0,box.y0-last['source_bbox'][3])
                    else:part['space_before']=0
                    subsequent=[s for v in col for s in v['spans'] if s['bbox'][1]>=box.y1-.1 and s['size']<=size*1.08]
                    part['space_after']=max(0,min((s['bbox'][1] for s in subsequent),default=box.y1)-box.y1)
                merge=(paragraphs and part['role']==last['role']=='body' and indent<1
                       and (last['column']!=ci or last['unit_id']!=u['id'])
                       and not re.search(r'[.!?][”’]?\s*$',last['source']))
                if merge:
                    p=paragraphs[-1];p['target_runs'][-1]['text']+=' '
                    p['target_runs']+=part['target_runs'];p['source']+=' '+part['source'];p['spans']+=ss
                    p['source_bbox']=list(fitz.Rect(p['source_bbox'])|box)
                else:
                    if paragraphs:paragraphs[-1]['target_runs'][-1]['text']+=' '
                    paragraphs.append(part)
                last=part
    first=deepcopy(members[0]);allspans=[s for u in members for s in u['spans']]
    box=fitz.Rect(frames[0])
    for f in frames[1:]:box|=fitz.Rect(f)
    body=next(s['style'] for s in allspans if abs(s['size']-size)<.1)
    if any(bands):first['story_bands']=bands
    first.update(story_members=deepcopy(members),id=first['id']+'_story',composed_ids=[u['id'] for u in members],story_frames=frames,
                 bbox=list(box),spans=allspans,style=body,size=size,font=body['font'],kind='paragraph',
                 context='running_text',scale_group='story_'+members[0]['id'],alignment='left',
                 source=' '.join(u['source'] for u in members),paragraphs=paragraphs,
                 target_runs=[r for p in paragraphs for r in p['target_runs']],line_height=median(
                     u.get('line_height',1.25) for u in members if len(u['spans'])>2 and u['size']==size) if any(len(u['spans'])>2 and u['size']==size for u in members) else 14/11)
    first['target']=''.join(r['text'] for r in first['target_runs'])
    return [u for u in units if u['id'] not in ids]+[first]


WRAP_EXCLUDED=('native_ordinal','run_in_heading','chart_callout','contents_column','protected_label','figure_legend',
               'row_composition','display_quote','display_ink_bbox','caption_growth','flow_start_x','drop_cap',
               'story_frames','paragraphs','vector','centered_caption','display_title')


def _line_rects(unit):
    lines=[]
    for span in unit['spans']:
        rect=fitz.Rect(span['bbox'])
        if lines and abs(rect.y0-lines[-1].y0)<.5:lines[-1]|=rect
        else:lines.append(rect)
    return lines


def _wraps(a,b):
    """``b`` continues ``a``'s sentence on the next line of the same column."""
    if any(u.get(k) for u in (a,b) for k in WRAP_EXCLUDED):return False
    if a['scale_group']!=b['scale_group'] or a['kind']!='paragraph' or b['kind']!='paragraph':return False
    styles={(s['style']['font'],s['style']['size'],s['style']['color']) for u in (a,b) for s in u['spans']}
    if len(styles)!=1 or not 9<=a['size']<=16:return False
    if re.search(r'[.!?;:\-‐‑\xad][”’"\']?$',a['source'].rstrip()):return False
    last,first=_line_rects(a)[-1],_line_rects(b)[0]
    pitch=first.y0-last.y0
    overlap=min(last.x1,first.x1)-max(last.x0,first.x0)
    return .85*a['size']<=pitch<=1.6*a['size'] and first.y0>=last.y1-.5 and overlap>.5*min(last.width,first.width)


def _shape_frames(lines):
    """Rects following the original left edge of consecutive line runs."""
    right=max(r.x1 for r in lines);groups=[]
    for r in lines:
        if groups and abs(groups[-1][0].x0-r.x0)<1:groups[-1].append(r)
        else:groups.append([r])
    frames=[]
    for i,group in enumerate(groups):
        bottom=groups[i+1][0].y0 if i+1<len(groups) else group[-1].y1
        frames.append(fitz.Rect(min(r.x0 for r in group),group[0].y0,right,bottom+.5))
    return frames


def prepare_wrapped_paragraphs(page, units):
    """Join one paragraph split where its lines wrap around an icon or puzzle tab.

    Extraction cuts a sentence wherever the left edge changes. Each fragment
    alone has no room to grow. The joined paragraph flows through rects with
    the original left edges (obstacle bands kept in points); only the last
    rect may grow down into measured free panel space. Targets are only
    concatenated at the verified continuation; nothing is translated.
    """
    units=deepcopy(units);chains=[]
    for u in units:
        if chains and _wraps(chains[-1][-1],u):chains[-1].append(u)
        else:chains.append([u])
    if all(len(c)==1 for c in chains):return units
    from .fidelity import immutable_boxes
    from .panel_frames import extend_bottom
    guards=immutable_boxes(page,());result=[]
    for chain in chains:
        if len(chain)==1:
            result.append(chain[0]);continue
        lines=[r for u in chain for r in _line_rects(u)]
        frames=_shape_frames(lines)
        if len(frames)<2 or len({round(f.x0) for f in frames})<2:
            result.extend(chain);continue
        ids={u['id'] for u in chain}
        others=[u['bbox'] for u in units if u['id'] not in ids]
        if any(f.intersects(fitz.Rect(o)) for f in frames for o in others):
            result.extend(chain);continue
        frames[-1]=extend_bottom(page,frames[-1],others+guards,em=chain[0]['size'])
        first=deepcopy(chain[0]);runs=[]
        for u in chain:
            if runs:runs[-1]['text']=runs[-1]['text'].rstrip()+' '
            runs+=deepcopy(u['target_runs'])
        spans=[s for u in chain for s in u['spans']];box=fitz.Rect(frames[0])
        for f in frames[1:]:box|=f
        source=' '.join(u['source'].strip() for u in chain)
        first.update(id=first['id']+'_wrap',composed_ids=[u['id'] for u in chain],story_members=deepcopy(chain),
                     story_frames=[list(f) for f in frames],bbox=list(box),spans=spans,source=source,
                     target_runs=runs,target=''.join(r['text'] for r in runs),alignment='left',first_line_indent=0,
                     paragraphs=[{'role':'body','spans':spans,'target_runs':runs,'source':source,'indent':0,
                                  'space_before':0,'space_after':0,'column':0,'unit_id':first['id']}],
                     wrapped_paragraph=True)
        result.append(first)
    return result


CLIP=re.compile(rb'(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) re\nW n')


def _widen_clips(data,headroom):
    """Accent headroom for Story frames (cf. fonts.insert_htmlbox_headroom).

    Story clips every placed frame to its rectangle and puts the first line
    0.8 em x line-height below the top, so accented capitals at the top of any
    frame (including continuation frames) lose their marks. Only the clip
    rectangles of the written overlay move out (top and sides); placement,
    line breaks and baselines are unchanged. Real ink is still guarded.
    """
    if headroom<=0:return data
    doc=fitz.open(stream=data,filetype='pdf');page=doc[0];h=headroom
    def widen(m):
        x,y,w,hh=(float(v) for v in m.groups())
        return f'{x-h:g} {y-h:g} {w+2*h:g} {hh+h:g} re\nW n'.encode()
    for xref in page.get_contents():
        stream=doc.xref_stream(xref);new=CLIP.sub(widen,stream)
        if new!=stream:doc.update_stream(xref,new)
    out=doc.tobytes();doc.close();return out


def overlay(page,unit,factor,font_map,font_root):
    from .fidelity import _html_for
    layout=deepcopy(unit)
    for paragraph in layout['paragraphs']:
        if paragraph.get('fixed_indent'):
            paragraph['indent']/=factor
    content,css,_=_html_for(layout,font_map,font_root,factor)
    from .fonts import unit_headroom
    headroom=unit_headroom(unit,factor)
    story=fitz.Story(content,user_css=css,archive=fitz.Archive(str(font_root)))
    stream=io.BytesIO();writer=fitz.DocumentWriter(stream);device=writer.begin_page(page.rect)
    more=True;placements=[];heights={};offset=0;heading_after={}
    for i,p in enumerate(unit['paragraphs']):
        if p['role']=='heading':
            heading_after[f'story_heading_{offset}']=(p.get('space_after',0)+unit['size']*unit['line_height'] if i+1<len(unit['paragraphs']) else 0)*factor
        offset+=len(p['target_runs'])
    snippets={m[1]:m[0] for m in re.finditer(r'<p id="(story_heading_\d+)"[^>]*>.*?</p>',content,re.S)}
    # (rect, continues the previous rect of the same column around an obstacle)
    rects=[]
    for frame,band in zip(unit['story_frames'],unit.get('story_bands') or [None]*len(unit['story_frames'])):
        frame=fitz.Rect(frame)
        if band and frame.y0<band[1]<frame.y1 and frame.x0<band[0]<frame.x1:
            rects+=[(fitz.Rect(band[0],frame.y0,frame.x1,band[1]),False),(fitz.Rect(frame.x0,band[1],frame.x1,frame.y1),True)]
        else:rects.append((frame,bool(unit.get('wrapped_paragraph')) and bool(rects)))
    pitch=unit['size']*unit.get('line_height',1.05)*factor;last_filled=None
    for index,(frame,chained) in enumerate(rects):
        if not more:break
        frame=fitz.Rect(frame)
        # Keep the scaled leading continuous across obstacle bands: a narrower
        # rect continues right below the previous line and may hold one line
        # straddling its band end; a wider rect never starts above that end.
        if chained and last_filled is not None and last_filled.height>0:
            if frame.x0>rects[index-1][0].x0+.5:frame.y0=min(frame.y0,last_filled.y1)
            else:frame.y0=max(frame.y0,last_filled.y1)
        # Only a line starting before the band end straddles it (float-safe).
        if index+1<len(rects) and rects[index+1][1] and rects[index+1][0].x0<frame.x0-.5:frame.y1+=pitch-.25
        trial=fitz.Rect(frame);moved=[]
        # MuPDF ignores CSS keep-with-next/keep-inside for paragraphs. Measure
        # complete headings, then retry placement before committing the frame.
        # draw() advances the story; repeated place() does not consume its text.
        for ident,snippet in snippets.items():
            key=(ident,trial.width)
            if key in heights:continue
            probe=fitz.Story(snippet,user_css=css,archive=fitz.Archive(str(font_root)))
            probe.place(fitz.Rect(0,0,trial.width,10000));positions=[]
            probe.element_positions(lambda p:positions.append(p))
            heights[key]=max(fitz.Rect(p.rect).height for p in positions if p.id==ident)
        for attempt in range(len(snippets)+2):
            more,filled=story.place(trial);positions=[]
            story.element_positions(lambda p:positions.append(p))
            bad=[]
            for pos in positions:
                if pos.id not in snippets:continue
                rect=fitz.Rect(pos.rect)
                if abs(rect.x0-trial.x0)>.1 or rect.y0<trial.y0-.1:continue
                if rect.height+.05<heights[(pos.id,trial.width)] or rect.y1+heading_after[pos.id]>trial.y1+.05:
                    bad.append((rect.y0,pos.id))
            if not bad:break
            top,ident=min(bad);moved.append(ident)
            if top<=trial.y0+.1:
                writer.end_page();writer.close()
                return fitz.open(stream=_widen_clips(stream.getvalue(),headroom),filetype='pdf'),False,placements
            trial.y1=min(trial.y1-.1,top-.01)
        else:
            writer.end_page();writer.close()
            return fitz.open(stream=_widen_clips(stream.getvalue(),headroom),filetype='pdf'),False,placements
        story.draw(device);last_filled=fitz.Rect(filled)
        placements.append({'frame':list(frame),'placed_frame':list(trial),'filled':list(filled),'more':bool(more),'headings_moved':sorted(set(moved))})
    writer.end_page();writer.close()
    return fitz.open(stream=_widen_clips(stream.getvalue(),headroom),filetype='pdf'),not more,placements


def heading_issues(page,units):
    """Verify actual PDF text: each subhead belongs wholly to one frame."""
    from .fidelity import _normalized
    issues=[]
    for u in units:
        if not u.get('story_frames'):continue
        texts=[_normalized(page.get_text(clip=fitz.Rect(f)+(-1,-1,1,1))) for f in u['story_frames']]
        for i,p in enumerate(u['paragraphs']):
            if p['role']!='heading':continue
            heading=_normalized(''.join(r['text'] for r in p['target_runs']))
            if not any(heading in text for text in texts):
                issues.append({'code':'split_story_heading','unit_id':u['id'],'paragraph':i,'action':'recompose'})
    return issues


def fit_story(page,unit,font_map,font_root,vector_mask,occupied=()):
    from .fidelity import _test_fit
    frames=[fitz.Rect(f) for f in unit['story_frames']]
    if any(f.intersects(r) for f in frames for r in occupied):return None
    def test(scale):return _test_fit(page,unit,fitz.Rect(unit['bbox']),scale,font_map,font_root,vector_mask)
    ok,info=test(1)
    if ok:return info
    ok,best=test(.75)
    if not ok:return None
    lo,hi=.75,1.
    for _ in range(12):
        middle=(lo+hi)/2;ok,info=test(middle)
        if ok:lo=middle;best=info
        else:hi=middle
    return best
