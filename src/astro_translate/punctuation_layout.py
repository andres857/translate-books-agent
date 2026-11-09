"""Attach native terminal punctuation to its exact preceding editorial line."""
from copy import deepcopy
import pymupdf as fitz


def attach_terminal_punctuation(page, units):
    from .fidelity import span_style
    raw=[s for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
    owned=[s for u in units for s in u['spans']]
    result=deepcopy(units)
    for unit in result:
        if not unit['spans'] or unit.get('relocated_punctuation'):
            continue
        last=unit['spans'][-1]
        matches=[s for s in raw if s['text']==last['source'] and s['font']==last['font']
                 and max(abs(a-b) for a,b in zip(s['bbox'],last['bbox']))<.01]
        if len(matches)!=1:continue
        tail=matches[0]
        punctuation=[s for s in raw if s['text'].strip() in ('.',',',';',':','!','?')
                     and abs(s['origin'][1]-tail['origin'][1])<.1
                     and -.1<=s['bbox'][0]-tail['bbox'][2]<=1
                     and .6 <= s['size']/tail['size'] <= 1.05
                     and s['font']==tail['font'] and s['color']==tail['color']
                     and not any(s['text']==o['source'] and list(s['bbox'])==o['bbox'] for o in owned)]
        if len(punctuation)!=1:continue
        s=punctuation[0];value=s['text'].strip()
        # Only move an actually separate source operator; lexical text stays as saved.
        style=span_style(s)
        span={'source':s['text'],'bbox':list(s['bbox']),'font':s['font'],'size':s['size'],
              'color':s['color'],'flags':s['flags'],'rotate':0,'style':style}
        unit['spans'].append(span);unit['bbox']=list(fitz.Rect(unit['bbox'])|fitz.Rect(span['bbox']))
        unit['source']=unit['source'].rstrip()+value
        run={'text':value,'style':style}
        if not unit['target'].rstrip().endswith(value):
            unit['target_runs'][-1]['text']=unit['target_runs'][-1]['text'].rstrip()
            unit['target_runs'].append(run)
            if unit.get('paragraphs'):unit['paragraphs'][-1]['target_runs'].append(run)
        unit['target']=''.join(r['text'] for r in unit['target_runs'])
        unit['relocated_punctuation']=[span]
    return result
