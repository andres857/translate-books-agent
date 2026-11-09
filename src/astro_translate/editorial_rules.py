"""Narrow, source-proven editorial exceptions and deterministic punctuation."""
from copy import deepcopy
import re
import pymupdf as fitz


def normalize_quotes(unit):
    """Normalize quote pairs across run boundaries without changing run styles."""
    out=deepcopy(unit);stack=[];offset=0;chars=[]
    text=out['target']
    for i,c in enumerate(text):
        if c in '«“':
            c='“' if not stack else '‘';stack.append(c)
        elif c in '»”':
            c='”' if not stack or stack.pop()=='“' else '’'
        elif c in '\"\'':
            if c=="'" and i and i+1<len(text) and text[i-1].isalnum() and text[i+1].isalnum():c='’'
            elif stack and (i+1==len(text) or not text[i+1].isalnum()):c='”' if stack.pop()=='“' else '’'
            else:c='“' if not stack else '‘';stack.append(c)
        chars.append(c)
    normalized=''.join(chars)
    for r in out.get('target_runs',[]):
        end=offset+len(r['text']);r['text']=normalized[offset:end];offset=end
    out['target']=normalized
    offset=0
    for p in out.get('paragraphs',[]):
        for r in p['target_runs']:
            end=offset+len(r['text']);r['text']=normalized[offset:end];offset=end
    return out


def partition_display_units(page,units):
    """PDF reading order can interleave title and subtitle. Split by exact runs."""
    from .fidelity import _make_unit
    result=[]
    for u in units:
        spans=u['spans'];runs=u['target_runs']
        reversed_lines=any(b['bbox'][1]<a['bbox'][1]-min(a['size'],b['size']) for a,b in zip(spans,spans[1:]))
        if not (reversed_lines and min(s['size'] for s in spans)>14 and len(spans)==len(runs)
                and all(s['style']['size']==r['style']['size'] for s,r in zip(spans,runs))):
            result.append(u);continue
        for i,(s,r) in enumerate(zip(spans,runs)):
            child=_make_unit([s],u['source_block'],False,[])
            child.update(id=u['id']+f'_display{i}',composed_ids=[u['id']],target=r['text'].strip(),
                         target_runs=[{**deepcopy(r),'text':r['text'].strip()}],style=r['style'],
                         scale_group=u['scale_group']+f'_display{i}',alignment='center',first_line_indent=0)
            result.append(child)
    return result


NAME=r"[A-ZÀ-Ý][\w’'&.-]*(?:[ \t]+(?:[A-ZÀ-Ý][\w’'&.-]*|de|van|and|&)){1,5}"


def protected_editorial_units(page,units):
    """A full attribution is preserved only with a quote + author/title pattern."""
    quoted=any(u.get('size',0)>=20 and re.search('[“\"]',u['source']) for u in units)
    result=[]
    if quoted:
        for u in units:
            if u['size']>=20:continue
            match=re.fullmatch('('+NAME+r'),\s+([A-Z][^\n]+:\s*[^\n]+)',u['source'])
            if match and len(match[2].split())>=5:
                result.append({'code':'published_work_title','unit_id':u['id'],'text':u['source'],
                               'title':match[2],'bbox':u['bbox'],'preserve_native':True})
    if quoted:
        ordered=sorted(units,key=lambda u:u['bbox'][1])
        for author,title in zip(ordered,ordered[1:]):
            if not re.fullmatch(NAME+r',',author['source'].strip()):continue
            if not (author['size']<20 and title['size']<20 and len(title['source'].split())>=2):continue
            if not all(s['style'].get('italic') for s in title['spans']):continue
            a,b=fitz.Rect(author['bbox']),fitz.Rect(title['bbox'])
            if not (0<=b.y0-a.y1<=author['size']*1.5 and abs((a.x0+a.x1-b.x0-b.x1)/2)<author['size']*2):continue
            for u in (author,title):
                result.append({'code':'published_work_title','unit_id':u['id'],'text':u['source'],
                               'title':title['source'],'bbox':u['bbox'],'preserve_native':True})
    return result


def quote_retranslation_units(page,units):
    """Complete display quotations, including unquoted pull quotes.

    Geometry and mixed emphasis identify unquoted display prose. Ordinary
    headings, captions and lists do not become a quotation merely by size.
    Already joined units retain their source members for QA/provenance.
    """
    from .fidelity import _make_unit
    groups=[];current=[]
    def flush():
        nonlocal current
        if current:
            text=' '.join(u['source'] for u in current)
            quoted=bool(re.match(r'^[\s“"…]',text) and re.search(r'[.!?][”"]\s*$',text))
            fonts={s['font'] for u in current for s in u['spans']}
            sizes=[s['size'] for u in current for s in u['spans']]
            from .display_quotes import source_lines
            lines=source_lines({'spans':[s for u in current for s in u['spans']]})
            prose_lines=2<=len(lines)<=4 and all(len(line['source'].strip())>=18 for line in lines[:2])
            unquoted=(prose_lines and len(current)>1 and not re.match(r'^[\s“"…]',text) and len(text.split())>=8
                      and len(fonts)>1 and max(sizes)-min(sizes)<.1
                      and not text.isupper())
            if quoted or unquoted:
                merged=_make_unit([s for u in current for s in u['spans']],current[0]['source_block'],False,[])
                merged.update(id=current[0]['id']+'_quote',composed_ids=[u['id'] for u in current],
                              scale_group=current[0]['scale_group'],page=page.number+1)
                groups.append(merged)
        current=[]
    for u in sorted(units,key=lambda u:u['bbox'][1]):
        if (u.get('native_ordinal') or u.get('protected_label') or u.get('field_rows') or u.get('contents_column')
                or re.search(r'_(?:row|field|cell)\d+',u['id']) or u.get('context')=='table_label'):
            flush();continue
        if u.get('display_quote'):
            flush();groups.append(u);continue
        if u.get('correction_provenance'):
            flush();continue
        if u.get('size',0)<20 or not u.get('spans'):continue
        if current:
            last=current[-1];a,b=fitz.Rect(last['bbox']),fitz.Rect(u['bbox'])
            compatible=(abs(u['size']-last['size'])<.1 and -1<=b.y0-a.y1<=u['size']*.7
                        and min(a.x1,b.x1)>max(a.x0,b.x0))
            if not compatible:flush()
        current.append(u)
        if re.search(r'[.!?][”"]\s*$',u['source']):flush()
    flush()
    return groups


def credit_exceptions(source,candidate,units):
    """Exact-source named entities in a page identified by multiple credit roles."""
    before=source.get_text();after=candidate.get_text()
    roles=re.findall(r'\b(?:editor|designer|publisher|copyright|art director|publishing|ISBN)\b',before,re.I)
    if len(roles)<4:return []
    findings=[]
    patterns=[('proper_name',r'(?m)^'+NAME+r'\s*$'),
              ('contact',r'(?i)\b(?:[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:https?://|www\.)[^\s]+)'),
              ('isbn',r'(?i)\bISBN[\s:]*[\d -]+')]
    # Names after a credit role and proper-name runs in native spans.
    for b in source.get_text('dict')['blocks']:
        for line in b.get('lines',[]):
            for s in line['spans']:
                value=s['text'].strip().strip(',;:.')
                if re.fullmatch(NAME,value) and value in after and not re.search(r'editor|design|publishing|printed|first|all rights',value,re.I):
                    findings.append(dict(code='proper_name',text=value,bbox=list(s['bbox']),source_identical=True))
    # Exact proper-name substrings remain exceptions when a role/attribution
    # around them was translated. Never exempt the surrounding prose.
    for m in re.finditer(NAME,before):
        value=m.group().strip().rstrip('.,;:')
        if value in after and not re.search(r'editor|design|publishing|printed|first|all rights|edition|astrology|zodiac|united states|great britain',value,re.I):
            findings.append(dict(code='proper_name',text=value,source_identical=True))
    # Source addresses: only identical components, including street/entity names;
    # translated city names are ordinary editorial text, not an exemption.
    for m in re.finditer(r'(?m)^.*\b(?:Broadway|Street|Road|Suite|Avenue)\b.*$',before):
        for component in re.split(r'[,\d]+',m.group()):
            value=component.strip()
            if value and value in after:
                findings.append(dict(code='address',text=value,source_identical=True))
    # An address entity can wrap across native lines. Bind its exact full name
    # to source + saved target + rendered text before treating it as an address.
    normalized_after=re.sub(r'\s+',' ',after)
    entity=r"((?:[A-Z][A-Za-z'-]*\s+){1,5}[A-Z][A-Za-z'-]*)[,]?\s+\d+\s+(?:Broadway|(?:[A-Z][a-z]+\s+)?(?:Street|Road|Avenue))\b"
    for unit in units:
        for match in re.finditer(entity,unit['source']):
            value=re.sub(r'\s+',' ',match.group(1))
            if value in re.sub(r'\s+',' ',unit['target']) and value in normalized_after:
                findings.append(dict(code='address',text=value,source_identical=True))
    for code,pattern in patterns:
        for m in re.finditer(pattern,before):
            value=m.group().strip()
            if value in after:findings.append(dict(code=code,text=value,source_identical=True))
    return list({(x['code'],x['text']):x for x in findings}.values())


def display_line_overlay(page,unit,box,factor,font_map):
    """Single native display line at the source baseline; avoid HTML edge clips."""
    from .safe_vlm import _glyph_ink_bounds
    out=fitz.open();p=out.new_page(width=page.rect.width,height=page.rect.height)
    width=sum(font_map[(unit['scale_group'],tuple(sorted(r['style'].items())))]['font'].text_length(
        r['text'],fontsize=r['style']['size']*factor) for r in unit['target_runs'])
    if width>box.width:return out,False
    x=(box.x0+box.x1-width)/2;y=unit['display_baseline']
    for i,r in enumerate(unit['target_runs']):
        style=r['style'];font=font_map[(unit['scale_group'],tuple(sorted(style.items())))];name=f'display{i}'
        p.insert_font(fontname=name,fontfile=font['path'])
        p.insert_text((x,y),r['text'],fontname=name,fontsize=style['size']*factor,
                      color=tuple(((style['color']>>shift)&255)/255 for shift in (16,8,0)))
        x+=font['font'].text_length(r['text'],fontsize=style['size']*factor)
    import numpy as np
    pix=p.get_pixmap(dpi=220,alpha=True)
    alpha=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
    ys,xs=np.where(alpha>20)
    inkbox=fitz.Rect(float(xs.min())*72/220,float(ys.min())*72/220,float(xs.max()+1)*72/220,float(ys.max()+1)*72/220) if len(xs) else fitz.Rect()
    ok=bool(len(xs)) and (box+(-.4,-.4,.4,.4)).contains(inkbox)
    return out,ok


def prepare_display_lines(page,units):
    """Only apply to the reversed title/subtitle topology diagnosed above."""
    if not any('_display' in u['id'] for u in units):return units
    from .safe_vlm import _glyph_ink_bounds
    ink,unsupported,failed=_glyph_ink_bounds(page)
    if failed:return units
    raw=[s for b in page.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
    for u in units:
        if len(u['spans'])!=1 or u['size']<14:continue
        ss=u['spans'][0]
        matches=[s for s in raw if s['font']==ss['font'] and tuple(s['bbox'])==tuple(ss['bbox'])]
        if len(matches)!=1:continue
        s=matches[0];rects=[]
        for c in s['chars']:
            key=(ord(c['c']),round(c['origin'][0],2),round(c['origin'][1],2))
            rects+=ink.get(key,[])
        if not rects:continue
        rect=fitz.Rect(rects[0])
        for r in rects[1:]:rect|=r
        u.update(display_baseline=s['origin'][1],display_ink_bbox=list(rect),alignment='center')
    return units
