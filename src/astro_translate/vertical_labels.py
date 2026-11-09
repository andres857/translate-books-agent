"""Offline quarter-turn geometry, collision guards and transactional redaction."""
import copy,math
from collections import Counter
import pymupdf as fitz
import numpy as np
from .core import Blocked,sha256
from .rotated_pilot import _chars,_rect_union
from .fidelity import _normalized
from .fonts import original_font,covers,fallback_font,profile_font_options,ink_rect,INK_HEADROOM_EM
from .raster_obstacles import check_overlay

def vector_paths_mask(page):
    from .fidelity import _base_vector_mask
    return _base_vector_mask(page,include_rotated=False)

def draw_vertical(page,unit,target,font_path,size,extension=0):
    name='Vertical'+sha256(font_path)[:16]
    page.insert_font(fontname=name,fontfile=font_path)
    point=fitz.Point(unit['characters'][0]['origin']);point.y+=extension
    color=tuple(((unit['color']>>shift)&255)/255 for shift in (16,8,0))
    page.insert_text(point,target,fontname=name,fontsize=size,color=color,rotate=90)
    return point
def _band_ink(alpha,rect,headroom,dpi=110):
    """Ink bounds of one run inside its own baseline interval (y) of a group
    overlay, widened across the line by ``headroom`` for accented capitals."""
    k=dpi/72;h,w=alpha.shape
    x0=max(0,int((rect.x0-headroom)*k));x1=min(w,int(math.ceil((rect.x1+headroom)*k)))
    y0=max(0,int(rect.y0*k));y1=min(h,int(math.ceil(rect.y1*k)))
    ys,xs=np.where(alpha[y0:y1,x0:x1]>80)
    if not ys.size:return fitz.Rect(rect)
    return fitz.Rect(rect)|fitz.Rect((x0+xs.min())/k,(y0+ys.min())/k,(x0+xs.max()+1)/k,(y0+ys.max()+1)/k)


def vertical_groups(units):
    """Contiguous quarter-turn style runs share one baseline and placement."""
    groups=[]
    for unit in units:
        previous=groups[-1][-1] if groups else None
        if (previous and abs(previous['characters'][0]['origin'][0]-unit['characters'][0]['origin'][0])<.02
                and abs(previous['bbox'][1]-unit['bbox'][3])<.1):
            groups[-1].append(unit)
        else:groups.append([unit])
    return groups


def fit_vertical(page,before,units,answers,font_root):
    """Pure offline search; guard the actual base candidate, never write it.

    Style fragments (Moon + its explanation) reflow together. Both upward and
    downward shifts are tested, while x stays fixed over the original planet.
    Missing saved targets block; this function never translates or shortens.
    """
    fonts={}
    if any(u['id'] not in answers for u in units):raise Blocked('Missing saved vertical target; run explicit translate stage separately')
    options=profile_font_options(page)
    for name in {u['font'] for u in units}:
        own=[u for u in units if u['font']==name]
        choice=original_font(page.parent,page,name,font_root/'original')
        alltext=' '.join(answers[u['id']] for u in own)
        fonts[name]=choice if covers(choice['font'],alltext) else fallback_font(own[0]['style'],font_root/'fallback',choice['font'],**options)
    owned=[c for u in units for c in u['characters']]
    others=[c for c in _chars(before,full_metrics=True) if c['c'].strip() and not any(c['c']==o['c'] and c['font']==o['font'] and max(abs(a-b) for a,b in zip(c['origin'],o['origin']))<.015 for o in owned)]
    vector=vector_paths_mask(page)
    diagnostics=[]
    for percent in range(100,74,-1):
        scale=percent/100;proposed=[];used=[];diagnostics=[]
        for group in vertical_groups(units):
            found=None
            for shift in (0,-2,-4,-6,-8,-12,-16,2,4,6,8,12,16):
                overlay=fitz.open();op=overlay.new_page(width=page.rect.width,height=page.rect.height)
                rows=[];y=group[0]['characters'][0]['origin'][1]+shift
                for u in group:
                    size=u['size']*scale;choice=fonts[u['font']]
                    extension=y-u['characters'][0]['origin'][1]
                    draw_vertical(op,u,answers[u['id']],choice['path'],size,extension)
                    rows.append({**u,'target':answers[u['id']],'extension':extension,'scale':scale,'render_size':size})
                    y-=choice['font'].text_length(answers[u['id']]+' ',fontsize=size)
                chars=[c for c in _chars(op) if c['c'].strip()];box=_rect_union(chars) if chars else fitz.Rect()
                # Accents rise beyond the tight glyph boxes: guards see the real ink.
                ink,alpha=ink_rect(op)
                if chars and ink is not None:box=box|ink
                raster=check_overlay(page,op,box,ink_alpha=alpha)
                collisions=sum(fitz.Rect(c['bbox']).intersects(fitz.Rect(o['bbox'])) for c in chars for o in others)
                pixels=int(np.count_nonzero((alpha>80)&vector))
                ok=bool(chars) and _normalized(''.join(answers[u['id']] for u in group))==_normalized(op.get_text()) and page.rect.contains(box) and not pixels and raster['ok'] and not collisions and not any(box.intersects(r) for r in used)
                diagnostics.append({'ids':[u['id'] for u in group],'scale':scale,'shift':shift,'vector_pixels':pixels,'native_neighbor_bbox_intersections':collisions,'raster_ok':raster['ok'],'within_page':page.rect.contains(box),'earlier_group_box_collision':any(box.intersects(r) for r in used)})
                if ok:
                    for row in rows:
                        # Select this run by its known baseline interval.
                        start=row['characters'][0]['origin'][1]+row['extension']
                        length=fonts[row['font']]['font'].text_length(row['target'],fontsize=row['render_size'])
                        own=[c for c in chars if start-length-.01<c['origin'][1]<=start+.01]
                        r=_rect_union(own);ink=_band_ink(alpha,r,INK_HEADROOM_EM*row['render_size'])
                        row.update(candidate_bbox=list(r),ink_bbox=list(ink),edit_bbox=list(r|ink|fitz.Rect(row['bbox'])),raster_guard=raster)
                    found=rows
                overlay.close()
                if found is not None:break
            if found is None:break
            proposed.extend(found);used.append(box)
        if len(proposed)==len(units):return proposed,fonts,diagnostics
    return None,fonts,diagnostics


def redact_vertical(page,units):
    """Find an isolated point per source glyph, including close full-metric boxes.

    Full line-height metrics falsely overlap a neighboring translated heading.
    Use tight glyph boxes, then verify every unrelated character survives and
    every selected character is removed before accepting the transaction.
    """
    wanted=[c for u in units for c in u['characters'] if c['c'].strip()]
    selected=[];others=[]
    for c in _chars(page):
        own=any(c['c']==w['c'] and c['font']==w['font'] and max(abs(a-b) for a,b in zip(c['origin'],w['origin']))<.015 for w in wanted)
        (selected if own else others).append(c)
    if len(selected)!=len(wanted):raise Blocked('Rotated source glyphs absent or changed in candidate')
    boxes=[]
    obstacles=[fitz.Rect(c['bbox']) for c in others if c['c'].strip()]
    for c in selected:
        r=fitz.Rect(c['bbox']);chosen=None
        for fy in (.5,.2,.8,.05,.95):
            for fx in (.5,.2,.8,.05,.95):
                x=r.x0+r.width*fx;y=r.y0+r.height*fy
                tiny=fitz.Rect(x-.01,y-.01,x+.01,y+.01)
                if r.contains(tiny) and not any(tiny.intersects(o) for o in obstacles):
                    chosen=tiny;break
            if chosen is not None:break
        if chosen is None:raise Blocked('No isolated point for vertical source glyph')
        boxes.append(chosen)
    # All points are validated before mutating the candidate.
    for r in boxes:page.add_redact_annot(r,fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    if not redaction_preserved([c for c in others if c['c'].strip()],selected,
                               [c for c in _chars(page) if c['c'].strip()]):
        raise Blocked('Vertical redaction changed unrelated text or left source glyphs')
    return [list(r) for r in boxes]


# apply_redactions rewrites the content streams (and instances the Form
# XObjects of composed text): origins are recomputed in float32, so a glyph
# may move by one ulp (3e-5 pt near x=500, p86). Exact rounding to 1e-3 then
# flips at a .0005 boundary; compare within a tolerance far below any visible
# displacement instead.
ORIGIN_TOLERANCE=.001


def redaction_preserved(others,selected,after,tolerance=ORIGIN_TOLERANCE):
    """Every unrelated glyph survives once (same value, font, origin within
    tolerance) and no selected source glyph is left on the page."""
    pool={}
    for c in after:pool.setdefault((c['c'],c['font']),[]).append(c['origin'])
    near=lambda a,b:max(abs(x-y) for x,y in zip(a,b))<=tolerance
    if any(near(c['origin'],o) for c in selected for o in pool.get((c['c'],c['font']),())):return False
    for c in others:
        candidates=pool.get((c['c'],c['font']),[])
        best=min(range(len(candidates)),key=lambda i:max(abs(x-y) for x,y in zip(c['origin'],candidates[i])),default=None)
        if best is None or not near(c['origin'],candidates[best]):return False
        candidates.pop(best)
    return True


def extract_vertical(page, extras=()):
    """Detect editable native quarter-turn runs on any page; curves stay deferred."""
    from .fidelity import span_style, immutable_span
    result=[]
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines',[]):
            if tuple(line['dir']) != (0.,-1.):continue
            for span in line['spans']:
                text=''.join(c['c'] for c in span['chars']).strip()
                if not text or immutable_span({'source':text,'font':span['font']},extras):continue
                characters=[{**c,'font':span['font'],'size':span['size'],'color':span['color'],
                             'direction':[0.,-1.]} for c in span['chars']]
                result.append({'id':f'p{page.number+1:04d}_vertical_{len(result)}','page':page.number+1,
                               'source':text,'bbox':list(span['bbox']),'font':span['font'],
                               'size':span['size'],'color':span['color'],'style':span_style(span),
                               'characters':characters})
    return result


def compose_production_vertical(runner, fit):
    """Translate a detected figure group, then atomically install a fitted candidate."""
    from . import llm
    from .core import atomic_json, _save_pdf, PROTECTED
    from .production import read, digest
    n=fit['page'];book=runner.book
    with fitz.open(book/'source.pdf') as source:
        page=source[n-1]
        if not runner.profile.page_policy(page).get('vertical'):return fit
        units=extract_vertical(page,runner.extras)
        if not units:return fit
        identity=digest({'source':sha256(book/'source.pdf'),'units':units,
                         'glossary':sha256(runner.glossary),'style':sha256(runner.style),
                         'config':sha256(runner.config_path),'protected':sha256(runner.protected)})
        target_path=runner.path(n,'vertical.json');saved=read(target_path,{})
        if saved.get('identity')==identity:
            answers=saved['answers']
        else:
            from .dates import translate_date, looks_like_date
            local={};pending=[]
            for unit in units:
                target=translate_date(unit['source'])
                if target is not None:local[unit['id']]=target
                elif looks_like_date(unit['source']):raise Blocked('Unsupported vertical date label')
                else:pending.append(unit)
            result=llm.run_task('translate_labels',{'_book':str(book),'_config':str(runner.config_path),
                       '_phase':'production','_pages':[n],
                       'labels':[{'id':u['id'],'source':u['source']} for u in pending]}) if pending else None
            answers={**local,**({r['id']:r['translation'] for r in result.data['labels']} if result else {})}
            atomic_json(target_path,{'identity':identity,'answers':answers})
        with fitz.open(fit['output']) as before:
            accepted,fonts,diagnostics=fit_vertical(page,before[n-1],units,answers,runner.folder/'fonts')
            if accepted is None:
                # Explicit evidence; local QA will retain these unresolved labels.
                fit['vertical_failure']={'reason':'No safe vertical placement >=0.75','diagnostics':diagnostics}
                atomic_json(runner.path(n,'fit.json'),fit)
                return fit
            candidate=fitz.open(stream=before.tobytes(),filetype='pdf')
            try:
                cp=candidate[n-1];numbers=Counter(PROTECTED.findall(cp.get_text()))
                redact_vertical(cp,accepted)
                for u in accepted:
                    choice=fonts[u['font']]
                    point=draw_vertical(cp,u,u['target'],choice['path'],u['render_size'],u['extension'])
                    length=choice['font'].text_length(u['target'],fontsize=u['render_size'])
                    actual=[c for c in _chars(cp) if c['c'].strip() and abs(c['origin'][0]-point.x)<.03
                            and point.y-length-.01<c['origin'][1]<=point.y+.01]
                    if _normalized(''.join(c['c'] for c in actual))!=_normalized(u['target']):
                        raise Blocked('Vertical extraction mismatch')
                    u.update(fit=True,scale_group=f'p{n:04d}_vertical',
                             target_runs=[{'text':u['target'],'style':u['style']}],
                             font_runs=[{'engine_name':actual[0]['font'],'size':u['render_size'],'sha256':choice['sha256']}])
                if Counter(PROTECTED.findall(cp.get_text()))!=numbers:
                    raise Blocked('Vertical composition changed protected values')
                _save_pdf(candidate,fit['output'])
            finally:candidate.close()
    fit.update(output_sha256=sha256(fit['output']))
    fit['blocks']+=accepted
    fit['group_scales'][f'p{n:04d}_vertical']=accepted[0]['scale']
    fit['extra_trusted_font_files']=[c['path'] for c in fonts.values()]
    fit['verified_rotated_spans']=[{'text':''.join(c['c'] for c in u['characters']),
        'font':u['font'],'bbox':u['bbox'],'direction':[0.,-1.],'resolution':'translated',
        'target_ids':[u['id']],'source_sha256':fit['source_sha256'],'candidate_sha256':fit['output_sha256']} for u in units]
    atomic_json(runner.path(n,'fit.json'),fit)
    return fit
