"""Flat wire emphasis. Font identities/values remain local; no text is translated."""
from collections import Counter
from copy import deepcopy
import re

VERSION='flat-v1'
INSTRUCTION=(
    'For rows with emphasis_source this supersedes ALL template style-marker and pair-order rules. '
    'Return the complete natural translation as PLAIN text, without v markers, plus emphasis: '
    'an ordered array of {style, text, occurrence}. text is an exact nonempty substring of translation. '
    'Use occurrence=null for a unique substring, otherwise its one-based occurrence. '
    'List only emphasized phrases in target reading order, never overlapping. Unlisted text uses the base style. '
    'Use only the supplied style keys; preserve equivalent emphasis and case. Each style may merge/split '
    'one phrase (source count +/-1, minimum one). Single-style rows return emphasis=[]. '
    'Keep protected n markers once in translation; annotations may reference them, never their hidden values. '
    'Glossary and grammar apply to the complete plain translation, not to individual phrases.'
)


def contract(request, source=None):
    from .style_spans import decode
    runs=decode(source if source is not None else request['source'],request['_style_pairs'])
    styles=[]
    for r in runs:
        if r['style'] not in styles:styles.append(r['style'])
    # Light/regular is the base even when the first phrase is Bold.
    base=min(styles,key=lambda s:(s.get('bold',False),s.get('italic',False),s.get('weight',400)))
    others=[s for s in styles if s!=base];keys={};registry={}
    for index,s in enumerate(others):
        label='bold' if s.get('bold') else 'italic' if s.get('italic') else 'accent'
        key=label if sum(bool(t.get('bold'))==bool(s.get('bold')) and bool(t.get('italic'))==bool(s.get('italic')) for t in others)==1 else f'{label}_{index+1}'
        keys[index]=key;registry[key]=s
    merged=[]
    for r in runs:
        if merged and merged[-1]['style']==r['style']:merged[-1]['text']+=r['text']
        else:merged.append(deepcopy(r))
    phrases=[{'style':keys[others.index(r['style'])],'text':r['text'].strip()} for r in merged if r['style']!=base and r['text'].strip()]
    return {'base':base,'styles':registry,'phrases':phrases,'source':''.join(r['text'] for r in runs)}


def intervals(text, annotations):
    if not isinstance(annotations,list):raise ValueError('emphasis must be a list')
    found=[];end=0
    for item in annotations:
        phrase=item.get('text')
        if not isinstance(phrase,str) or not phrase.strip():raise ValueError('emphasis phrase is empty')
        matches=[m.start() for m in re.finditer('(?='+re.escape(phrase)+')',text)]
        occurrence=item.get('occurrence')
        if occurrence is None:
            if len(matches)!=1:raise ValueError('emphasis substring must be unique or specify occurrence')
            start=matches[0]
        else:
            if type(occurrence) is not int or not 1<=occurrence<=len(matches):raise ValueError('emphasis occurrence out of bounds')
            start=matches[occurrence-1]
        if start<end:raise ValueError('emphasis phrases overlap or are not ordered')
        end=start+len(phrase)
        if any(m.start()<start<m.end() or m.start()<end<m.end() for m in re.finditer(r'\{[nv]\d+\}',text)):
            raise ValueError('emphasis cannot split a protected marker')
        found.append((start,end,item['style']))
    return found


def occurrence_at(text,phrase,start):
    """Keep a locally known range when masking/restoration changes uniqueness."""
    matches=[m.start() for m in re.finditer('(?='+re.escape(phrase)+')',text)]
    if start not in matches:raise ValueError('emphasis range changed during local transformation')
    return matches.index(start)+1 if len(matches)>1 else None


def reconstruct(answer,request):
    c=contract(request);text=answer['translation'];annotations=answer.get('emphasis')
    if not isinstance(text,str) or not text.strip():raise ValueError('emphasis: empty translation')
    if re.search(r'\{v\d+\}',text):raise ValueError('emphasis requires plain translation, no v markers')
    ranges=intervals(text,annotations)
    expected=Counter(p['style'] for p in c['phrases']);actual=Counter(s for _,_,s in ranges)
    if set(actual)!=set(expected):raise ValueError('emphasis style missing or unknown')
    if any(not max(1,n-1)<=actual[s]<=n+1 for s,n in expected.items()):raise ValueError('emphasis phrase count differs by more than one')
    runs=[];offset=0
    for start,end,key in ranges:
        if start>offset:runs.append({'text':text[offset:start],'style':deepcopy(c['base'])})
        runs.append({'text':text[start:end],'style':deepcopy(c['styles'][key])});offset=end
    if offset<len(text):runs.append({'text':text[offset:],'style':deepcopy(c['base'])})
    return runs


def wire_schema(schema,array):
    schema=deepcopy(schema)
    item=schema.get('properties',{}).get(array,{}).get('items')
    if item is None:return schema
    item.setdefault('properties',{})['emphasis']={'type':'array','items':{'type':'object','properties':{
        'style':{'type':'string'},'text':{'type':'string','minLength':1},
        'occurrence':{'type':['integer','null'],'minimum':1}},'required':['style','text','occurrence'],'additionalProperties':False}}
    # Mixed batches use [] for plain rows; strict provider schemas require all keys.
    item.setdefault('required',[]).append('emphasis')
    return schema


def answer_value(row):
    return {k:deepcopy(row[k]) for k in ('translation','emphasis') } if 'emphasis' in row else row['translation']
