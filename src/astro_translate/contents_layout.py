"""Opt-in contents columns: anchored names, uniform dates, fixed folios."""
from collections import defaultdict
from copy import deepcopy
from statistics import median
import re

import pymupdf as fitz

from .dates import looks_like_date


def prepare_contents(page, units):
    from .fidelity import immutable_boxes, _ink_bbox
    result = deepcopy(units)
    columns = defaultdict(list)
    for u in result:
        if u.get('context') == 'table_label' and looks_like_date(u['source']):
            columns[u['source_block']].append(u)
    guards = immutable_boxes(page, ())
    for block, dates in columns.items():
        if len(dates) < 3:
            continue
        names = [u for u in result if u.get('source_block') == block and u not in dates
                 and u.get('context') == 'table_label']
        # Require a real date/name/folio table; no page-number assumptions.
        rows = []
        for date in dates:
            b = fitz.Rect(date['bbox'])
            left = [u for u in names if u['bbox'][0] < b.x0 and
                    abs(u['bbox'][1]-b.y0) < 1]
            right = [g for g in guards if g.x0 >= b.x1 and g.y0 < b.y1 and g.y1 > b.y0]
            if len(left) != 1 or not right:
                break
            rows.append((left[0], date, min(g.x0 for g in right)))
        if len(rows) != len(dates):
            continue
        date_x = median(_ink_bbox(page, u).x0 for u in dates)
        name_x = median(u['bbox'][0] for u, _, _ in rows)
        right = min(x for _, _, x in rows)-4
        for name, date, _ in rows:
            for u, x0, x1 in ((name, name_x, date_x-4), (date, date_x, right)):
                u.update(contents_column=True, column_bounds=[x0, x1], alignment='left',
                         first_line_indent=0, minimum_column_gap=4)
    return prepare_toc(page, result)


def candidate_boxes(page, unit, units, guards):
    from .fidelity import _ink_bbox
    original = _ink_bbox(page, unit)
    left, right = unit['column_bounds']
    # One-line rows must shrink as a group, never wrap or shift sideways.
    boxes = [fitz.Rect(left, original.y0, right, original.y1+extra) for extra in (0, .5, 1)]
    others = [_ink_bbox(page, u) for u in units if u['id'] != unit['id']]
    others += list(guards)
    return [b for b in boxes if page.rect.contains(b) and not any(b.intersects(g) for g in others)]


def prepare_toc(page, units):
    """Detect repeated folio edges, merge each title row, reserve a fixed gap."""
    from .fidelity import _union
    result = deepcopy(units)
    if any(u.get('contents_column') for u in result):return result
    numbers=[];inline={}
    raw=page.get_text('rawdict')
    for b in raw['blocks']:
        for line in b.get('lines',[]):
            for span in line['spans']:
                text=''.join(c['c'] for c in span['chars'])
                match=re.search(r'(?<=\s)\d+\s*$',text)
                if not match or span['size']<10 or not re.search('[A-Za-z]',text[:match.start()]):continue
                chars=span['chars'][match.start():match.end()]
                rect=fitz.Rect(chars[0]['bbox'])
                for c in chars[1:]:rect|=fitz.Rect(c['bbox'])
                record={'text':match.group().strip(),'bbox':list(rect),'inline':True}
                numbers.append(record)
                inline[(span['font'],tuple(span['bbox']))]=(record,span['chars'][:match.start()])
    for b in page.get_text('dict')['blocks']:
        for line in b.get('lines',[]):
            for s in line['spans']:
                if s['text'].strip().isdigit() and 10<=s['size']<=16 and s['bbox'][1]>page.rect.height*.1:numbers.append(s)
    columns=[]
    for s in sorted(numbers,key=lambda s:s['bbox'][2]):
        col=next((c for c in columns if abs(c[0]['bbox'][2]-s['bbox'][2])<2),None)
        if col is None:columns.append([s])
        else:col.append(s)
    columns=[c for c in columns if len(c)>=4]
    if not columns:return result
    consumed=set();new=[];previous=page.rect.x0
    for col in columns:
        rows=[]
        for s in sorted(col,key=lambda s:s['bbox'][1]):
            box=fitz.Rect(s['bbox'])
            names=[u for u in result if u['id'] not in consumed and
                   u['bbox'][0]>=max(previous,box.x1-page.rect.width*.48) and u['bbox'][0]<box.x0 and
                   abs(u['bbox'][1]-box.y0)<2 and u['size']<=16]
            if names:rows.append((s,sorted(names,key=lambda u:u['bbox'][0])))
        if len(rows)<4:continue
        x0=min(u['bbox'][0] for _,us in rows for u in us)
        # Entry size dominates the column; a larger/smaller header row (title
        # with its own folio) is a different role and scales on its own.
        sizes=[us[0]['size'] for _,us in rows];body=max(set(sizes),key=sizes.count)
        for s,us in rows:
            # Each row ends at its own folio: right-aligned folios of other
            # sizes or digit counts must not narrow every entry of the column.
            x1=s['bbox'][0]-4
            merged=deepcopy(us[0]);rr=[];redactions=[];preserved=[]
            for u in us:
                consumed.add(u['id'])
                if rr:rr[-1]['text']=rr[-1]['text'].rstrip()+' '
                runs=deepcopy(u['target_runs'])
                from .fidelity import _slice_runs
                for sp in u['spans']:
                    entry=inline.get((sp['font'],tuple(sp['bbox'])))
                    if entry and entry[0]['bbox']==s['bbox']:
                        number,chars=entry
                        target=''.join(r['text'] for r in runs)
                        m=re.search(r'\s*'+re.escape(number['text'])+r'\s*$',target)
                        if not m:raise ValueError('TOC inline folio does not match saved target')
                        runs=_slice_runs(runs,0,m.start());preserved.append(number)
                    else:
                        chars=[c for b in raw['blocks'] for l in b.get('lines',[]) for rs in l['spans']
                               if rs['font']==sp['font'] and tuple(rs['bbox'])==tuple(sp['bbox']) for c in rs['chars']]
                    # Whitespace too: a native run of spaces before the kept
                    # folio would otherwise remain as a line under the title.
                    for c in chars:
                        r=fitz.Rect(c['bbox']);x=(r.x0+r.x1)/2;y=(r.y0+r.y1)/2
                        redactions.append([x-.01,y-.01,x+.01,y+.01])
                rr.extend(runs)
            merged.update(toc_row=True,contents_column=True,toc_number_bbox=list(s['bbox']),
                precise_edit_rects=redactions,preserved_inline_folios=preserved,
                composed_ids=[u['id'] for u in us],target_runs=rr,target=''.join(r['text'] for r in rr),
                spans=[sp for u in us for sp in u['spans']],source=' '.join(u['source'] for u in us),
                column_bounds=[x0,x1],minimum_column_gap=4,alignment='left',first_line_indent=0,
                scale_group=f'toc_{x0:.2f}'+('' if us[0]['size']==body else f"_{us[0]['size']:g}"),kind='label')
            merged['bbox']=list(_union(merged['spans']));new.append(merged)
        previous=max(s['bbox'][2] for s in col)+4
    return [u for u in result if u['id'] not in consumed]+new


def verify_folios(source,candidate,units):
    """Check fixed native digit operators and their rendered ink, including inline folios."""
    import math
    from PIL import Image
    rows=[u for u in units if u.get('toc_row')]
    if not rows:return {'ok':True,'rows':[]}
    def chars(page,rect):
        return [(c['c'],s['font'],s['size'],s['color'],c['origin'])
            for b in page.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans']
            for c in s['chars'] if c['c'].isdigit() and (rect+(-.01,-.01,.01,.01)).contains(fitz.Rect(c['bbox']))]
    imgs=[]
    for page in (source,candidate):
        pix=page.get_pixmap(dpi=220);imgs.append(Image.frombytes('RGB',(pix.width,pix.height),pix.samples))
    proofs=[]
    for u in rows:
        rect=fitz.Rect(u['toc_number_bbox']);a,b=chars(source,rect),chars(candidate,rect)
        equal=bool(a) and len(a)==len(b) and all(x[:4]==y[:4] and max(abs(v-w) for v,w in zip(x[4],y[4]))<.0001 for x,y in zip(a,b))
        factor=220/72;box=(math.floor(rect.x0*factor)-1,math.floor(rect.y0*factor)-1,math.ceil(rect.x1*factor)+1,math.ceil(rect.y1*factor)+1)
        ink=imgs[0].crop(box).tobytes()==imgs[1].crop(box).tobytes()
        proofs.append({'unit_id':u['id'],'native_digits_equal':equal,'ink_equal_220dpi':ink,'bbox':list(rect),'minimum_gap_pt':u['minimum_column_gap']})
    return {'ok':all(r['native_digits_equal'] and r['ink_equal_220dpi'] for r in proofs),'rows':proofs}
