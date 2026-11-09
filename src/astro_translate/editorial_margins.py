"""Horizontal book margins inferred only from original editorial text.

Facing leaves are mirrored into the current leaf coordinates. Folios, symbols,
vertical labels and running furniture cannot enlarge the editorial measure.
"""
import pymupdf as fitz
from .core import SYMBOL_FONT


def _edges(page):
    boxes=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            if tuple(line.get('dir',(1,0)))!=(1,0):continue
            for span in line['spans']:
                text=span['text'];box=fitz.Rect(span['bbox'])
                if SYMBOL_FONT.search(span['font']) or not any(c.isalnum() for c in text):continue
                if box.y0<page.rect.height*.055 or box.y1>page.rect.height*.945:continue
                boxes.append(box)
    return (min(b.x0 for b in boxes),max(b.x1 for b in boxes)) if boxes else None


def editorial_bounds(page):
    edges=[];own=_edges(page)
    if own:return own  # A populated leaf supplies the tighter, evidenced book measure.
    # PDF p2/p3 are facing, p1 is the cover.
    facing=page.number+1 if page.number%2 else page.number-1
    if 0<=facing<len(page.parent):
        other=page.parent[facing];pair=_edges(other)
        if pair and abs(other.rect.width-page.rect.width)<.1:
            edges.append((page.rect.width-pair[1],page.rect.width-pair[0]))
    return (min(e[0] for e in edges),max(e[1] for e in edges)) if edges else (0,page.rect.width)


def constrain(page,unit,boxes):
    left,right=editorial_bounds(page)
    original=fitz.Rect(unit['bbox'])
    # Existing native display text outside the body measure retains its extent.
    left=min(left,original.x0);right=max(right,original.x1)
    # Native insertion padding is not growth: retain the first source footprint.
    if boxes:
        initial=fitz.Rect(boxes[0])
        if original.x0-initial.x0<=1.1:left=min(left,initial.x0)
        if initial.x1-original.x1<=1.1:right=max(right,initial.x1)
    result=[]
    for candidate in boxes:
        box=fitz.Rect(candidate)
        # A sub-point source-sized positioning correction does not borrow width.
        # Actual ink is independently checked by QA; larger shifts still clamp.
        local_left,local_right=left,right
        unchanged_marker=(unit.get('kind')=='label' and bool(unit.get('source'))
                          and unit.get('source')==unit.get('target')
                          and not any(c.isalnum() for c in unit['source']))
        if unchanged_marker and box.width<=original.width+.01 and original.x0-box.x0<=1.1 and box.x1-original.x1<=1.1:
            local_left=min(left,box.x0);local_right=max(right,box.x1)
        if unit.get('alignment')=='center':
            center=(original.x0+original.x1)/2
            radius=min(box.width/2,center-left,right-center)
            box.x0,box.x1=center-radius,center+radius
        else:
            box.x0=max(box.x0,local_left);box.x1=min(box.x1,local_right)
        if not box.is_empty and box not in result:result.append(box)
    return result


def leaf_bounds(page):
    """Widest evidenced measure of the leaf: own edges joined with the
    mirrored facing leaf. A short sidebar list must not stand for the book
    margins of the artwork beside it (p89: right edge 237 pt, facing 516 pt)."""
    edges=[e for e in (_edges(page),) if e]
    facing=page.number+1 if page.number%2 else page.number-1
    if 0<=facing<len(page.parent):
        other=page.parent[facing];pair=_edges(other)
        if pair and abs(other.rect.width-page.rect.width)<.1:
            edges.append((page.rect.width-pair[1],page.rect.width-pair[0]))
    return (min(e[0] for e in edges),max(e[1] for e in edges)) if edges else (0,page.rect.width)


def is_rotated_label(unit):
    """Straight labels at an arbitrary angle (ribbons), drawn on their own baseline."""
    direction=unit.get('direction')
    return bool(direction) and unit.get('kind')=='label' and tuple(direction)!=(1.,0.)


def rotated_label_bounds(page,unit):
    """Horizontal limits for a rotated figure label: the book margins of the
    leaf, or the original label extent where the original already exceeds them."""
    left,right=leaf_bounds(page);original=fitz.Rect(unit['bbox'])
    return min(left,original.x0),max(right,original.x1)


def margin_issues(source_page,candidate_page,units):
    left,right=editorial_bounds(source_page);issues=[]
    words=candidate_page.get_text('words')
    for unit in units:
        if is_rotated_label(unit) and unit.get('candidate_bbox'):
            # Only the label's own rendered words: a tilted neighbour on the
            # same ribbon shares the rows of its axis-aligned box.
            limit=rotated_label_bounds(source_page,unit)
            own=fitz.Rect(unit['candidate_bbox'])|fitz.Rect(unit.get('ink_bbox') or unit['candidate_bbox'])
            boxes=[fitz.Rect(word[:4]) for word in words if any(c.isalpha() for c in word[4])
                   and own.contains(fitz.Point((word[0]+word[2])/2,(word[1]+word[3])/2))]
            if unit.get('ink_bbox'):boxes.append(fitz.Rect(unit['ink_bbox']))
            if any(b.x0<limit[0]-.5 or b.x1>limit[1]+.5 for b in boxes):
                issues.append({'code':'editorial_margin','unit_id':unit['id'],'action':'recompose','bounds':list(limit)})
            continue
        original=fitz.Rect(unit['bbox']);limit=(min(left,original.x0),max(right,original.x1))
        box=fitz.Rect(unit.get('edit_bbox',unit.get('bbox')))
        # Measure rendered lexical words, including words crossing the fit box.
        # Insertion padding alone is not ink and must not change old headings.
        boxes=[fitz.Rect(word[:4]) for word in words
               if any(c.isalpha() for c in word[4])
               and box.y0<=(word[1]+word[3])/2<=box.y1
               and word[0]<box.x1 and word[2]>box.x0]
        if any(b.x0<limit[0]-.5 or b.x1>limit[1]+.5 for b in boxes):
            issues.append({'code':'editorial_margin','unit_id':unit['id'],'action':'recompose','bounds':list(limit)})
    return issues
