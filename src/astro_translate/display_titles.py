"""Complete requests for native display titles split into stacked PDF units."""
import pymupdf as fitz


def title_retranslation_units(page,units):
    from .fidelity import _make_unit
    from .editorial_rules import quote_retranslation_units
    quote_ids={i for q in quote_retranslation_units(page,units) for i in q['composed_ids']}
    candidates=[u for u in units if u['id'] not in quote_ids and u.get('size',0)>=30
                and u['source'].isupper() and len(u['source'].split())<=6
                and fitz.Rect(u['bbox']).height<=u['size']*1.1]
    candidates.sort(key=lambda u:u['bbox'][1]);groups=[]
    for u in candidates:
        if groups:
            previous=groups[-1][-1];a=fitz.Rect(previous['bbox']);b=fitz.Rect(u['bbox']);size=u['size']
            if (abs(size-previous['size'])<.1 and -.1<=b.y0-a.y1<=size*.5
                    and abs(b.x1-a.x1)<size*.25 and abs(b.x0-a.x0)>size):
                groups[-1].append(u);continue
        groups.append([u])
    result=[]
    for group in groups:
        if len(group)<2:continue
        unit=_make_unit([s for u in group for s in u['spans']],group[0]['source_block'],False,[])
        unit.update(id=group[0]['id']+'_title',composed_ids=[u['id'] for u in group],
                    scale_group=group[0]['scale_group'],page=page.number+1,display_title=True,alignment='right')
        result.append(unit)
    return result
