"""Native straight rotated labels; true per-glyph curves remain inventoried.

Rawdict coalesces nearby tangents. Operator directions, not that approximate
line direction, determine whether a native span really has a straight baseline.
No page-number recipes and no inference in the offline compositor.
"""
from collections import Counter
from copy import deepcopy
import math
from pathlib import Path
import pymupdf as fitz
import numpy as np

from .core import Blocked, SYMBOL_FONT, sha256, atomic_json, _save_pdf
from .fonts import select_group_fonts


def _signature(span):
    return (span.get('text',span.get('source')),span['font'],
            tuple(round(v,3) for v in span['bbox']),
            tuple(round(v,5) for v in span['direction']))


def extract_straight_labels(page, extras=()):
    """Require a complete multi-letter span with a proven constant tangent.

    Single letters are not joined speculatively: that would turn circular
    artwork into straight words and lose its topology. Quarter-turn labels use
    the established vertical route; this function handles other orientations.
    """
    from .fidelity import span_style, immutable_span
    traces=[(c,s['dir']) for s in page.get_texttrace() for c in s['chars']]
    result=[]
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines',[]):
            direction=list(line['dir'])
            if tuple(direction) in ((1.,0.),(0.,-1.)):continue
            for span in line['spans']:
                text=''.join(c['c'] for c in span['chars'])
                if len(text.strip())<2 or immutable_span({'source':text,'font':span['font']},extras):continue
                characters=span['chars']; actual=[]
                for char in characters:
                    matches=[d for c,d in traces if c[0]==ord(char['c']) and
                             max(abs(a-b) for a,b in zip(c[2],char['origin']))<.002]
                    if len(matches)!=1:break
                    actual.append(matches[0])
                if len(actual)!=len(characters) or any(max(abs(a-b) for a,b in zip(d,actual[0]))>1e-5 for d in actual):continue
                direction=list(actual[0]);normal=(-direction[1],direction[0]);start=characters[0]['origin']
                if any(abs(sum((c['origin'][i]-start[i])*normal[i] for i in (0,1)))>.01 for c in characters):continue
                # Recover the final glyph's advance from its oriented quadrilateral.
                quad=fitz.recover_char_quad(tuple(direction),span,characters[-1])
                end=max(sum((p[i]-start[i])*direction[i] for i in (0,1)) for p in quad)
                center=[start[i]+direction[i]*end/2 for i in (0,1)]
                style=span_style(span);index=len(result)
                result.append({'id':f'p{page.number+1:04d}_rotated_{index}', 'page':page.number+1,
                    'source':text.strip(),'bbox':list(span['bbox']),'font':span['font'],
                    'size':span['size'],'color':span['color'],'style':style,
                    'direction':direction,'baseline_center':center,'source_advance':end,
                    'characters':[{**c,'font':span['font'],'size':span['size'],'color':span['color'],
                                   'direction':direction} for c in characters],
                    'spans':[{'source':text,'bbox':list(span['bbox']),'font':span['font'],
                              'size':span['size'],'color':span['color'],'style':style}],
                    'source_spans':[{'text':text,'bbox':list(span['bbox']),'font':span['font'],'direction':list(line['dir'])}],
                    'scale_group':f'p{page.number+1:04d}_rotated_{span["size"]:.2f}_{style["weight"]}',
                    'kind':'label','context':'figure_label','alignment':'center','vector':True})
    taken={(s['font'],tuple(round(v,3) for v in s['bbox'])) for u in result for s in u['spans']}
    for unit in _near_straight_labels(page,extras,taken):
        index=len(result)
        unit.update(id=f'p{page.number+1:04d}_rotated_{index}',
                    scale_group=f'p{page.number+1:04d}_rotated_{unit["size"]:.2f}_{unit["style"]["weight"]}')
        result.append(unit)
    return result


# Near-straight labels: glyphs set one by one along a gently warped ribbon.
# Per label the operator angles may drift slightly, but the baselines stay on
# the chord within a fraction of a point, so a single straight baseline at the
# chord angle reproduces the label. Wheel lettering drifts ~0.2 deg/pt.
NEAR_STRAIGHT_MAX_SPREAD_DEG = 3.5
NEAR_STRAIGHT_MAX_RATE_DEG_PER_PT = .06
NEAR_STRAIGHT_MAX_RESIDUAL_EM = .06


def _near_straight_labels(page, extras, taken):
    """Glyph runs in content-stream order on non-axis lines, accepted only
    when the run is geometrically a straight label (see constants above).

    A run never crosses a font/size/colour change (ribbon separators such as
    '|' in another face), a double space or a geometric jump; it must hold
    whole rawdict spans so identity removal can find every source glyph.
    """
    from .fidelity import span_style, immutable_span
    raw=[]
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines',[]):
            if tuple(line['dir']) in ((1.,0.),(0.,-1.),(0.,1.)):continue
            for span in line['spans']:
                if SYMBOL_FONT.search(span['font']):continue
                raw.append((span,list(line['dir'])))
    owner={}
    for index,(span,_) in enumerate(raw):
        for char in span['chars']:
            owner[(ord(char['c']),round(char['origin'][0],2),round(char['origin'][1],2))]=(index,char)
    stream=[]
    for trace in page.get_texttrace():
        for c in trace['chars']:
            hit=owner.get((c[0],round(c[2][0],2),round(c[2][1],2)))
            stream.append((hit,trace) if hit else (None,trace))
    runs=[];current=[]
    def flush():
        nonlocal current
        while current and not current[0]['char']['c'].strip():current.pop(0)
        while current and not current[-1]['char']['c'].strip():current.pop()
        if current:runs.append(current)
        current=[]
    for hit,trace in stream:
        if hit is None:
            flush();continue
        index,char=hit;span=raw[index][0]
        key=(span['font'],round(span['size'],2),span['color'])
        item={'char':char,'span_index':index,'key':key,'dir':tuple(trace['dir']),'size':span['size']}
        if current:
            prev=current[-1];p,q=prev['char']['origin'],char['origin'];d=prev['dir']
            along=(q[0]-p[0])*d[0]+(q[1]-p[1])*d[1];across=-(q[0]-p[0])*d[1]+(q[1]-p[1])*d[0]
            if (prev['key']!=key or not 0<along<=1.2*span['size'] or abs(across)>.2*span['size']
                    or (not char['c'].strip() and not prev['char']['c'].strip())):
                flush()
        current.append(item)
    flush()
    result=[]
    for run in runs:
        glyphs=[r for r in run if r['char']['c'].strip()]
        text=''.join(r['char']['c'] for r in run)
        if sum(c.isalpha() for c in text)<2:continue
        # Space-only spans carry no ink: identity removal never sees them.
        indices=sorted({r['span_index'] for r in run if raw[r['span_index']][0]['chars'] and
                        any(c['c'].strip() for c in raw[r['span_index']][0]['chars'])})
        spans=[raw[i][0] for i in indices]
        mine={id(r['char']) for r in run}
        # Whole spans only: a span shared with another run is ambiguous.
        if any(c['c'].strip() and id(c) not in mine for s in spans for c in s['chars']):continue
        if any((raw[i][0]['font'],tuple(round(v,3) for v in raw[i][0]['bbox'])) in taken for i in indices):continue
        if immutable_span({'source':text.strip(),'font':spans[0]['font']},extras):continue
        start=glyphs[0]['char']['origin'];stop=glyphs[-1]['char']['origin']
        length=math.hypot(stop[0]-start[0],stop[1]-start[1])
        if length<=0:continue
        direction=[(stop[0]-start[0])/length,(stop[1]-start[1])/length]
        normal=(-direction[1],direction[0])
        residual=max(abs((g['char']['origin'][0]-start[0])*normal[0]+(g['char']['origin'][1]-start[1])*normal[1]) for g in glyphs)
        angles=[math.degrees(math.atan2(g['dir'][1],g['dir'][0])) for g in glyphs]
        spread=max(angles)-min(angles);size=glyphs[0]['size']
        chord=math.degrees(math.atan2(direction[1],direction[0]))
        if (spread>NEAR_STRAIGHT_MAX_SPREAD_DEG or spread>NEAR_STRAIGHT_MAX_RATE_DEG_PER_PT*length
                or residual>NEAR_STRAIGHT_MAX_RESIDUAL_EM*size
                or not min(angles)-.5<=chord<=max(angles)+.5):continue
        last_span=raw[glyphs[-1]['span_index']][0]
        quad=fitz.recover_char_quad(tuple(direction),last_span,glyphs[-1]['char'])
        end=max(sum((p[i]-start[i])*direction[i] for i in (0,1)) for p in quad)
        center=[start[i]+direction[i]*end/2 for i in (0,1)]
        first=spans[0];style=span_style(first)
        box=fitz.Rect(spans[0]['bbox'])
        for s in spans[1:]:box|=fitz.Rect(s['bbox'])
        result.append({'page':page.number+1,'source':text.strip(),'bbox':list(box),'font':first['font'],
            'size':first['size'],'color':first['color'],'style':style,'direction':direction,
            'baseline_center':center,'source_advance':end,'geometry':'near_straight_chord',
            'angle_spread_deg':round(spread,4),'baseline_residual':round(residual,4),
            'characters':[{**g['char'],'font':raw[g['span_index']][0]['font'],'size':g['size'],
                           'color':raw[g['span_index']][0]['color'],'direction':direction} for g in glyphs],
            'spans':[{'source':''.join(c['c'] for c in s['chars']),'bbox':list(s['bbox']),'font':s['font'],
                      'size':s['size'],'color':s['color'],'style':span_style(s)} for s in spans],
            'source_spans':[{'text':''.join(c['c'] for c in raw[i][0]['chars']),'bbox':list(raw[i][0]['bbox']),
                             'font':raw[i][0]['font'],'direction':raw[i][1]} for i in indices],
            'kind':'label','context':'figure_label','alignment':'center','vector':True})
    return result


def _is_separator(item, labels):
    """Punctuation-only glyphs between straight labels on the same baseline
    (ribbon separators '|') stay in place untouched: nothing to translate."""
    if any(c.isalnum() for c in item['text']):return False
    box=fitz.Rect(item['bbox'])
    for u in labels:
        reach=fitz.Rect(u['bbox'])+(-u['size'],-u['size']*.3,u['size'],u['size']*.3)
        if box.intersects(reach):return True
    return False


def separator_spans(page, labels):
    """Rotated '|' separator spans of the given labels, as QA signatures."""
    found=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            if tuple(line['dir'])==(1.,0.):continue
            for span in line['spans']:
                if span['text']!='|' or SYMBOL_FONT.search(span['font']):continue
                item={'text':span['text'],'bbox':list(span['bbox']),'font':span['font'],'direction':list(line['dir'])}
                if _is_separator(item,labels):found.append(item)
    return found


def deferred_inventory(page, extras=(), resolved=()):
    """Inventory every native curved fragment, including protected numerals.

    Original values stay local. Phase 4 receives exact source glyph locations;
    no artwork-wide language exception is created by this inventory.
    """
    from .fidelity import immutable_span
    labels=extract_straight_labels(page,extras)
    straight={_signature(s) for u in labels for s in u['source_spans']}
    resolved={_signature(s) for s in resolved}
    separator=lambda item:_is_separator(item,labels)
    regions=[]
    for block in page.get_text('dict')['blocks']:
        fragments=[]
        for line in block.get('lines',[]):
            if tuple(line['dir'])==(1.,0.):continue
            for span in line['spans']:
                if not span['text'].strip() or SYMBOL_FONT.search(span['font']):continue
                item={'text':span['text'],'bbox':list(span['bbox']),'font':span['font'],'direction':list(line['dir'])}
                if _signature(item) in resolved:continue
                # Preserve the existing, explicitly enabled vertical route.
                if tuple(line['dir'])==(0.,-1.):continue
                if _signature(item) not in straight and separator(item):continue
                reason='straight_rotated_translation_pending' if _signature(item) in straight else 'curved_or_fragmented_native_text'
                fragments.append((reason,item))
        for reason in dict.fromkeys(r for r,s in fragments):
            spans=[s for r,s in fragments if r==reason]
            box=fitz.Rect(spans[0]['bbox'])
            for s in spans[1:]:box|=fitz.Rect(s['bbox'])
            if not page.rect.intersects(box):continue
            regions.append({'bbox':list(box & page.rect),'reason':reason,'phase':4,'source_spans':spans})
    return regions


def label_requests(units):
    """One figure batch, with dates resolved deterministically before inference.

    run_task masks numerical/symbol values in the returned request locally.
    This helper itself never calls a model.
    """
    from .dates import translate_date, looks_like_date
    local={};pending=[]
    for unit in units:
        target=translate_date(unit['source'])
        if target is not None:local[unit['id']]=target.upper() if unit['source'].isupper() else target
        elif looks_like_date(unit['source']):raise Blocked('Unsupported rotated date syntax')
        else:pending.append({'id':unit['id'],'source':unit['source']})
    return pending,local


def _draw(page, unit, choice, target, scale, offset=0.):
    """Centre on the original baseline centre, optionally moved ``offset`` pt
    along the same baseline (only to stay inside the book margins)."""
    direction=unit['direction'];size=unit['size']*scale
    advance=choice['font'].text_length(target,fontsize=size)
    point=fitz.Point([unit['baseline_center'][i]+direction[i]*(offset-advance/2) for i in (0,1)])
    name='Rotated'+choice['sha256'][:16]
    page.insert_font(fontname=name,fontfile=choice['path'])
    color=tuple(((unit['color']>>s)&255)/255 for s in (16,8,0))
    angle=-math.degrees(math.atan2(direction[1],direction[0]))
    page.insert_text(point,target,fontname=name,fontsize=size,color=color,morph=(point,fitz.Matrix(angle)))
    return point


# Clearance kept between a new label and native neighbours on its baseline
# (ribbon separators '|'). Every original A13 ribbon keeps >= 0.23 em.
LABEL_MIN_GAP_EM = .2


def _trial(source, unit, choice, scale, offset, limits):
    """Render one label alone; return its glyph box, real ink box and alpha."""
    from .rotated_pilot import _chars, _rect_union
    from .fonts import ink_rect
    overlay=fitz.open();op=overlay.new_page(width=source.rect.width,height=source.rect.height)
    _draw(op,unit,choice,unit['target'],scale,offset)
    chars=[c for c in _chars(op) if c['c'].strip()]
    if not chars:
        overlay.close();return None
    box=_rect_union(chars);ink,alpha=ink_rect(op)
    # Accented capitals rise above the tight glyph boxes: the edit area is the
    # union of glyph boxes and rendered ink, and every guard sees all of it.
    area=box|ink if ink is not None else box
    margin_ok=area.x0>=limits[0]-.5 and area.x1<=limits[1]+.5
    return {'overlay':overlay,'page':op,'chars':chars,'box':box,'ink':ink,'area':area,'alpha':alpha,
            'margin_ok':margin_ok}


def _margin_offset(trial, limits, direction):
    """Shift along the baseline that brings the label inside the margins."""
    area=trial['area'];dx=0.
    if area.x1>limits[1]-.01:dx=limits[1]-.01-area.x1
    if area.x0<limits[0]+.01:dx=limits[0]+.01-area.x0
    return dx/direction[0] if abs(direction[0])>.5 else 0.


def compose_labels(source, candidate, units, answers, font_root):
    """Transactional search, fixed angle, group scales at least .75.

    The label keeps its original baseline centre unless the book margins
    (editorial_margins.rotated_label_bounds) require a shift along that same
    baseline. Caller must supply a disposable candidate. Existing native
    glyphs (with a minimum clearance) and all illustration ink constrain new
    glyphs, accents included; no shifting into adjacent sectors.
    """
    from .fidelity import _normalized, NUMBER_PATTERN, SYMBOL_PATTERN, _base_vector_mask
    from .rotated_pilot import _chars
    from .native_redaction import remove_source_spans
    from .raster_obstacles import check_overlay
    from .editorial_margins import rotated_label_bounds
    if not units:return [],[]
    if any(u['id'] not in answers for u in units):raise Blocked('Missing saved rotated target')
    prepared=deepcopy(units)
    for u in prepared:
        target=answers[u['id']]
        if u['source'].isupper():target=target.upper()
        for pattern in (NUMBER_PATTERN,SYMBOL_PATTERN):
            if Counter(pattern.findall(target))!=Counter(pattern.findall(u['source'])):raise Blocked('Rotated protected values changed')
        u.update(target=target,target_runs=[{'text':target,'style':u['style']}])
    mapping,audit=select_group_fonts(source.parent,source,prepared,Path(font_root))
    wanted=[c for u in prepared for c in u['characters'] if c['c'].strip()]
    identity=lambda c:(c['c'],c['font'],tuple(round(v,3) for v in c['origin']))
    owned={identity(c) for c in wanted}
    neighbors=[fitz.Rect(c['bbox']) for c in _chars(candidate) if c['c'].strip() and identity(c) not in owned]
    vector=_base_vector_mask(source,include_rotated=False)
    rows=[];occupied=[]
    for group in dict.fromkeys(u['scale_group'] for u in prepared):
        members=[u for u in prepared if u['scale_group']==group];accepted=None
        for percent in range(100,74,-1):
            scale=percent/100;trial=[];group_boxes=[]
            for u in members:
                choice=mapping[(group,tuple(sorted(u['style'].items())))]
                limits=rotated_label_bounds(source,u);offset=0.
                attempt=_trial(source,u,choice,scale,offset,limits)
                if attempt is not None and not attempt['margin_ok']:
                    offset=_margin_offset(attempt,limits,u['direction']);attempt['overlay'].close()
                    attempt=_trial(source,u,choice,scale,offset,limits)
                if attempt is None:break
                box,area,alpha,op=attempt['box'],attempt['area'],attempt['alpha'],attempt['page']
                raster=check_overlay(source,op,area,ink_alpha=alpha)
                gap=LABEL_MIN_GAP_EM*u['size']*scale
                ok=(_normalized(op.get_text())==_normalized(u['target']) and source.rect.contains(area)
                    and attempt['margin_ok'] and not np.any((alpha>80)&vector) and raster['ok']
                    and not any(fitz.Rect(c['bbox']).intersects(n+(-gap,0,gap,0)) for c in attempt['chars'] for n in neighbors)
                    and not any(area.intersects(r) for r in occupied+group_boxes))
                if ok:trial.append({**u,'scale':scale,'render_size':u['size']*scale,'fit':True,
                    'candidate_bbox':list(box),'ink_bbox':list(area),'edit_bbox':list(area|fitz.Rect(u['bbox'])),
                    'baseline_offset':round(offset,4),'margin_bounds':list(limits),'raster_guard':raster,
                    'font_runs':[{'engine_name':attempt['chars'][0]['font'],'size':u['size']*scale,'sha256':choice['sha256']}],
                    'geometry_method':'fixed_operator_angle_baseline_center' if not offset else 'fixed_operator_angle_baseline_margin_shift'})
                attempt['overlay'].close()
                if not ok:break
                group_boxes.append(area)
            if len(trial)==len(members):accepted=trial;occupied+=group_boxes;break
        if accepted is None:raise Blocked('No safe straight rotated group placement >=0.75')
        rows+=accepted
    remove_source_spans(candidate,rows)
    for row in rows:_draw(candidate,row,mapping[(row['scale_group'],tuple(sorted(row['style'].items())))],row['target'],row['scale'],row['baseline_offset'])
    return rows,audit


def compose_saved_rotated(runner, fit):
    """Integrate only explicitly prepared saved figure targets; never inference."""
    from .production import read, digest
    number=fit['page']
    with fitz.open(runner.book/'source.pdf') as source:
        page=source[number-1]
        units=extract_straight_labels(page,runner.extras)
        if not units:return fit
        saved=read(runner.path(number,'rotated.json'),{})
        identity=digest({'source_sha256':fit['source_sha256'],'units':units})
        dependencies={name:sha256(path) for name,path in [('glossary',runner.glossary),('style',runner.style),('protected',runner.protected),('config',runner.config_path)]}
        if saved.get('identity')!=identity or saved.get('dependencies')!=dependencies:
            fit['rotated_pending']=[u['id'] for u in units]
            return fit
        with fitz.open(fit['output']) as candidate:
            rows,audit=compose_labels(page,candidate[number-1],units,saved['answers'],runner.folder/'fonts')
            separators=separator_spans(page,units)
            if separators:
                from .rotated_pilot import separator_character_proofs
                try:separator_character_proofs(page,candidate[number-1])
                except Blocked:separators=[]  # QA keeps reporting them; never assumed
            _save_pdf(candidate,fit['output'])
    fit['output_sha256']=sha256(fit['output']);fit['blocks']+=rows
    for row in rows:fit['group_scales'][row['scale_group']]=row['scale']
    fit.setdefault('extra_trusted_font_files',[]).extend(a['chosen']['path'] for a in audit)
    proofs=fit.setdefault('verified_rotated_spans',[])
    for u in units:
        proofs.extend({**s,'resolution':'translated','target_ids':[u['id']],
                       'source_sha256':fit['source_sha256'],'candidate_sha256':fit['output_sha256']} for s in u['source_spans'])
        # A per-glyph ribbon span may carry the word space ('R ' of OUR, p89).
        # The exact text proves coverage; its letters-only twin (same bbox)
        # lets QA release that lone letter from the immutable artifact set.
        proofs.extend({**s,'text':s['text'].strip(),'resolution':'translated','target_ids':[u['id']],
                       'source_sha256':fit['source_sha256'],'candidate_sha256':fit['output_sha256']}
                      for s in u['source_spans'] if s['text'].strip()!=s['text'] and s['text'].strip().isalpha())
    # Separators keep their exact native operators (verified above, and again by QA).
    proofs.extend({**s,'resolution':'preserved_separator','source_sha256':fit['source_sha256'],
                   'candidate_sha256':fit['output_sha256']} for s in separators)
    for proof in proofs:proof['candidate_sha256']=fit['output_sha256']
    atomic_json(runner.path(number,'fit.json'),fit)
    return fit


def remaining_regions(regions, fit):
    """Remove only source spans with a candidate-bound translated proof."""
    resolved={_signature(s) for s in fit.get('verified_rotated_spans',[])
              if s.get('resolution')=='translated' and s.get('source_sha256')==fit.get('source_sha256')
              and s.get('candidate_sha256')==fit.get('output_sha256')}
    result=[]
    for region in regions:
        if not region.get('source_spans'):
            result.append(region);continue
        spans=[s for s in region['source_spans'] if _signature(s) not in resolved]
        if spans:
            box=fitz.Rect(spans[0]['bbox'])
            for span in spans[1:]:box|=fitz.Rect(span['bbox'])
            result.append({**region,'source_spans':spans,'bbox':list(box)})
    return result
