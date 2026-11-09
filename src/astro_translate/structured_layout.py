"""Pure, parameterized legend/field-row/display-punctuation composition."""
import copy,re,csv
from pathlib import Path
import pymupdf as fitz
from .core import Blocked
from .fidelity import _make_unit,span_style
from .language import glossary_matches

def partition_at_terms(text,terms):
    result=[];offset=0
    for term in terms:
        pattern=re.compile(re.escape(term).replace(r'\ ',r'\s+'),re.I);match=pattern.search(text,offset)
        if match is None:raise Blocked('Existing translation lacks an unambiguous approved row boundary')
        end=match.end()+len(re.match(r'[\s)\],.;:]*',text[match.end():]).group());result.append(text[offset:end].strip());offset=end
    if text[offset:].strip():raise Blocked('Unassigned legend text after last approved term')
    return result

def partition_legend(page,units,glossary,identifier):
    terms=list(csv.DictReader(Path(glossary).open()));result=[]
    for u in units:
        if u['id']!=identifier:result.append(u);continue
        if len(u['spans'])!=3 or len(u['target_runs'])!=1:raise Blocked('Legend source geometry changed')
        boundaries=[]
        for span in u['spans']:
            matches=glossary_matches(span['source'],terms)
            if len(matches)!=1:raise Blocked('Legend row must have exactly one approved term')
            boundaries.append(matches[0]['target'])
        targets=partition_at_terms(u['target'],boundaries)
        for i,(span,target) in enumerate(zip(u['spans'],targets)):
            child=_make_unit([span],u['source_block'],False,[]);child.update(id=u['id']+f'_row{i}',target=target,target_runs=[{'text':target,'style':u['target_runs'][0]['style']}],scale_group=u['scale_group'],kind='label',context='figure_label',first_line_indent=0,layout_correction='Exact existing target partitions at approved glossary boundaries; no lexical changes')
            result.append(child)
    return result



def field_rows(unit):
    rows=[]
    for span in unit['spans']:
        if not rows or abs(span['bbox'][1]-rows[-1][0]['bbox'][1])>2:rows.append([])
        rows[-1].append(span)
    return rows

def partition_field_rows(page,units,*,size,source_block,split_header=False,clearance=3):
    result=[]
    for u in units:
        selected=u['size']==size and u.get('source_block')==source_block and len(u['spans'])>=10
        header=split_header and u['size']==11 and len(u['spans'])==2
        if not selected and not header:result.append(u);continue
        rows=field_rows(u) if selected else [[s] for s in u['spans']]
        for i,spans in enumerate(rows):
            child=_make_unit(spans,u['source_block'],False,[]);child.update(id=u['id']+f'_row{i}',scale_group=u['scale_group'],kind='label',context='figure_label',target=child['source'],target_runs=[{'text':child['source'][r['start']:r['end']],'style':r['style']} for r in child['style_runs']],first_line_indent=0,row_composition=True)
            result.append(child)
    for u in result:
        if u['size']==11 and u['bbox'][0]>80:
            u['bbox']=list(fitz.Rect(u['bbox'])+(clearance,0,clearance,0));u['ordinal_clearance_pt']=clearance
    return result



def restore_quote_punctuation(page,units,*,size,source_block,count):
    quotes=[u for u in units if u['size']==size and u.get('source_block')==source_block]
    if len(quotes)!=1:raise Blocked('Quote repair scope changed')
    u=quotes[0];box=fitz.Rect(u['bbox'])+(-1,-1,8,1)
    raw=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if s['text'].strip()==',' and s['size']==size and box.intersects(fitz.Rect(s['bbox']))]
    if len(raw)!=count:raise Blocked('Expected three traced source commas')
    punct=[{'source':s['text'],'bbox':list(s['bbox']),'font':s['font'],'size':s['size'],'color':s['color'],'flags':s['flags'],'rotate':0,'style':span_style(s)} for s in raw]
    spans=u['spans']+punct;spans.sort(key=lambda s:(round((s['bbox'][1]-box.y0)/size),s['bbox'][0]))
    repaired=_make_unit(spans,u['source_block'],False,[])
    u.update(source=repaired['source'],spans=spans,bbox=list(fitz.Rect(u['bbox'])|fitz.Rect(punct[1]['bbox'])),relocated_punctuation=punct)
    return units


def validate_nonempty_styles(unit,runs):
    for style in [r['style'] for r in unit['style_runs'] if r['end']>r['start']]:
        if not any(r['style']==style and r['text'].strip() for r in runs):raise Blocked('An original nonempty heading style segment disappeared')


def _field_table_rows(unit):
    """Logical rows of a light-label/bold-value display table, or None."""
    if not unit.get('spans'):return None
    rows=field_rows(unit)
    paired=[row for row in rows if len(row)==2
            and row[0]['style']['weight']<row[1]['style']['weight']]
    labels=[row for row in rows if row[0]['style']['weight']<600]
    candidate=(len(rows)>=5 and len(paired)>=3 and len(labels)>=4
               and len({s['size'] for s in unit['spans']})==1
               and max(row[0]['bbox'][0] for row in labels)-min(row[0]['bbox'][0] for row in labels)<2
               and all(len(row[0]['source'].strip())<=40 for row in labels))
    if not candidate:return None
    # Consecutive bold-only physical lines are one wrapped value cell.
    logical=[]
    for row in rows:
        if (logical and all(s['style']['weight']>=600 for s in row)
            and logical[-1][-1]['style']==row[0]['style']):
            logical[-1].extend(row)
        else:logical.append(list(row))
    return logical


def _cell_start_spans(rows):
    """Indexes of spans opening a row with the previous span's style (hidden cell boundary)."""
    spans=[s for row in rows for s in row];starts=set();index=0
    for row in rows:
        if index and spans[index-1]['style']==row[0]['style']:starts.add(index)
        index+=len(row)
    return starts


def field_cell_starts(unit):
    """Source offsets where a same-style run would hide a table cell boundary.

    The translation wire gives each such cell its own style marker, so the
    saved target partitions by exact runs instead of guessed word boundaries.
    """
    rows=_field_table_rows(unit)
    if rows is None or [s for row in rows for s in row]!=unit['spans']:return []
    offsets=[];cursor=0
    for span in unit['spans']:
        text=span['source'].strip();start=unit['source'].find(text,cursor)
        if not text or start<0:return []
        offsets.append(start);cursor=start+len(text)
    return sorted(offsets[i] for i in _cell_start_spans(rows))


def prepare_saved_field_rows(page, units):
    """Partition display field tables using only aligned saved style runs.

    A sequence of aligned short rows with repeated light-label/bold-value
    pairs is a table, not a reflowable paragraph. A same-style run spanning
    multiple rows may be divided only when each original cell is one word
    and the saved target has exactly one word for each cell. Any other
    unresolved style boundary blocks instead of guessing or translating.
    """
    result=[]
    for unit in units:
        # Wrapped value cells keep their entire saved run; no word boundary is guessed.
        rows=_field_table_rows(unit)
        if rows is None:
            result.append(unit);continue
        row_index={id(span):i for i,row in enumerate(rows) for span in row}
        # Runs translated with one marker per cell carry ``cell_start``.
        cells=_cell_start_spans(rows) if any(r.get('cell_start') for r in unit['target_runs']) else set()
        source_groups=[]
        for index,span in enumerate(unit['spans']):
            if not source_groups or source_groups[-1][0]!=span['style'] or index in cells:
                source_groups.append((span['style'],[]))
            source_groups[-1][1].append(index)
        target_groups=[]
        for run in unit['target_runs']:
            if not target_groups or target_groups[-1]['style']!=run['style'] or (cells and run.get('cell_start')):
                target_groups.append({'text':run['text'],'style':copy.deepcopy(run['style'])})
            else:target_groups[-1]['text']+=run['text']
        if [style for style,_ in source_groups]!=[r['style'] for r in target_groups]:
            raise Blocked('Ambiguous saved field row style alignment')
        mapped={}
        for (style,indices),run in zip(source_groups,target_groups):
            if len(indices)==1:
                mapped[indices[0]]=copy.deepcopy(run);continue
            if len({row_index[id(unit['spans'][i])] for i in indices})==1:
                mapped[indices[0]]=copy.deepcopy(run)
                for index in indices[1:]:mapped[index]=None
                continue
            words=list(re.finditer(r'\S+',run['text']))
            if (len(words)!=len(indices)
                    or any(len(unit['spans'][i]['source'].split())!=1 for i in indices)):
                raise Blocked('Ambiguous saved field row boundary; grouped retranslation required')
            for j,index in enumerate(indices):
                start=0 if j==0 else words[j].start()
                end=words[j+1].start() if j+1<len(words) else len(run['text'])
                mapped[index]={'text':run['text'][start:end],'style':copy.deepcopy(style)}
        offset=0
        for index,spans in enumerate(rows):
            runs=[mapped[i] for i in range(offset,offset+len(spans)) if mapped[i] is not None];offset+=len(spans)
            runs[0]['text']=runs[0]['text'].lstrip();runs[-1]['text']=runs[-1]['text'].rstrip()
            child=_make_unit(copy.deepcopy(spans),unit['source_block'],False,[])
            child.update(id=unit['id']+f'_row{index}',scale_group=unit['scale_group'],
                         target_runs=runs,target=''.join(r['text'] for r in runs),
                         kind='label',context='figure_label',alignment='left',first_line_indent=0,
                         row_composition=True,row_parent_id=unit['id'],
                         layout_correction='Saved target runs partitioned by exact styles and unambiguous word boundaries')
            result.append(child)
    return result
