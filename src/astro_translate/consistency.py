"""Exact source memory and deterministic reuse across repeated clauses.

This module never performs inference. Sentence/overlap planning yields blocks
for one bounded multi-page run_task call, then restores location punctuation and
source typography without translating anything locally.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

STYLE_MARKER=re.compile(r'\{v\d+\}')
MEMORY_VERSION='fidelity-clauses-v1'


def normalize_source(text):
    text=unicodedata.normalize('NFC',text)
    text=re.sub(r'\s+',' ',text).strip()
    return re.sub(r'(?<=[^\d\s]) +([.,!?;:])',r'\1',text)


def _ranges(text):
    start=0
    for match in re.finditer(r'[.!?](?=\s|$)',text):
        token=re.search(r'([A-Za-z.]+)$',text[:match.start()])
        if token and token.group(1).lower() in ('mr','mrs','ms','dr','prof','e.g','i.e','st'):continue
        yield start,match.end();start=match.end()
    if start<len(text):yield start,len(text)


def _core_range(text,start,end):
    while start<end and (text[start].isspace() or text[start] in ',;:'):start+=1
    while end>start and (text[end-1].isspace() or text[end-1] in '.!?'):end-=1
    return start,end


def _style_at(unit,index):
    for run in unit.get('style_runs',[]):
        if run['start']<=index<run['end']:return run['style']
    return unit.get('style',{})


def _annotate(unit,start,end):
    text=unit['source'];runs=[]
    for index in range(start,end):
        style=_style_at(unit,index)
        if runs and runs[-1]['style']==style:runs[-1]['text']+=text[index]
        else:runs.append({'text':text[index],'style':style})
    if len(runs)<=1:return normalize_source(text[start:end]),[],[]
    marked='';markers=[];styles=[]
    for index,run in enumerate(runs,1):
        marker='{v'+str(index)+'}';markers.append(marker);styles.append(run['style']);marked+=marker+run['text']
    markers.append('{v'+str(len(runs)+1)+'}');marked+=markers[-1]
    return marked,markers,styles


def build_plan(units):
    """Deduplicate complete sentences and shared quoted prefixes/clauses."""
    sources=[]
    for unit in units:
        for start,end in _ranges(unit['source']):
            a,b=_core_range(unit['source'],start,end)
            if a<b:sources.append(unit['source'][a:b])
    # A shorter complete sentence reused as a prefix of a longer body sentence
    # remains one memory entry; location-specific full stop/comma stays local.
    anchors=set()
    for candidate in sources:
        if len(candidate)<20 or len(candidate.split())<4:continue
        pattern=r'(?<!\w)'+re.escape(candidate)+r'(?!\w)'
        if any(len(other)>len(candidate) and re.search(pattern,other) for other in sources):anchors.add(candidate)
    occurrences=[];groups={};unit_plans=[]
    for unit in units:
        pieces=[];text=unit['source']
        for start,end in _ranges(text):
            cuts={start,end}
            for anchor in sorted(anchors,key=len,reverse=True):
                for match in re.finditer(r'(?<!\w)'+re.escape(anchor)+r'(?!\w)',text[start:end]):
                    cuts.update((start+match.start(),start+match.end()))
            ordered=sorted(cuts)
            for a,b in zip(ordered,ordered[1:]):
                core_a,core_b=_core_range(text,a,b)
                if core_a==core_b:
                    pieces.append({'literal':text[a:b],'style':_style_at(unit,a)});continue
                canonical=normalize_source(text[core_a:core_b]);marked,markers,styles=_annotate(unit,core_a,core_b)
                key=hashlib.sha256(canonical.encode()).hexdigest()
                occurrence={'key':key,'source':canonical,'unit_id':unit['id'],'page':unit['page'],'prefix':text[a:core_a],'suffix':text[core_b:b],'style':_style_at(unit,core_a),'marked':marked,'markers':markers,'styles':styles}
                index=len(occurrences);occurrences.append(occurrence);pieces.append({'occurrence':index})
                groups.setdefault(key,[]).append(index)
        unit_plans.append({'unit':unit,'pieces':pieces})
    requests=[];entries={}
    for key,indexes in groups.items():
        selected=max((occurrences[i] for i in indexes),key=lambda x:len(x['markers']))
        identifier=f'p{selected["page"]:04d}_s{key[:20]}'
        request={'id':identifier,'source':selected['marked']}
        if selected['markers']:request['_style_markers']=selected['markers']
        requests.append(request)
        entries[key]={'id':identifier,'source':selected['source'],'marked_source':selected['marked'],'markers':selected['markers'],'styles':selected['styles'],'pages':sorted({occurrences[i]['page'] for i in indexes})}
    return {'version':MEMORY_VERSION,'requests':requests,'entries':entries,'occurrences':occurrences,'units':unit_plans}


def _decode(entry,translation):
    markers=entry['markers']
    if not markers:return re.sub(r'[.!?]+$','',translation.strip()),None
    if STYLE_MARKER.findall(translation)!=markers or not translation.strip().startswith(markers[0]) or not translation.strip().endswith(markers[-1]):raise ValueError('Style markers changed order or boundary')
    runs=[]
    for index,marker in enumerate(markers[:-1]):
        text=translation.split(marker,1)[1].split(markers[index+1],1)[0]
        runs.append({'text':text,'style':entry['styles'][index]})
    if runs:
        runs[0]['text']=runs[0]['text'].lstrip();runs[-1]['text']=re.sub(r'[.!?]+$','',runs[-1]['text'].rstrip())
    return ''.join(r['text'] for r in runs),runs


def _append(runs,text,style):
    if not text:return
    if runs and runs[-1]['style']==style:runs[-1]['text']+=text
    else:runs.append({'text':text,'style':style})


def resolve_plan(plan,answers):
    decoded={key:_decode(entry,answers[entry['id']]) for key,entry in plan['entries'].items()}
    units=[];occurrences=[]
    for item in plan['units']:
        unit=item['unit'];runs=[]
        for piece in item['pieces']:
            if 'literal' in piece:_append(runs,piece['literal'],piece['style']);continue
            occurrence=plan['occurrences'][piece['occurrence']];entry=plan['entries'][occurrence['key']];plain,styled=decoded[occurrence['key']]
            _append(runs,occurrence['prefix'],occurrence['style'])
            if occurrence['markers']:
                if occurrence['marked']!=entry['marked_source']:raise ValueError('Repeated styled clause has a different style partition; alignment review required')
                for index,run in enumerate(styled or []):_append(runs,run['text'],occurrence['styles'][index])
            else:_append(runs,plain,occurrence['style'])
            _append(runs,occurrence['suffix'],runs[-1]['style'] if runs else occurrence['style'])
            occurrences.append({'source':occurrence['source'],'target':plain,'page':unit['page'],'id':unit['id'],'sentence_key':occurrence['key']})
        units.append(normalize_complete_question({**unit,'target':''.join(r['text'] for r in runs),'target_runs':runs}))
    return {'units':units,'occurrences':occurrences,'issues':check_consistency(occurrences)}


def check_consistency(occurrences):
    groups={}
    for row in occurrences:groups.setdefault(normalize_source(row['source']),[]).append(row)
    return [{'source':source,'reason':'different_translations_for_same_source','occurrences':rows} for source,rows in groups.items() if len({normalize_source(row['target']) for row in rows})>1]


class SentenceMemory:
    """Exact canonical-source memory bound to all translation dependencies."""
    def __init__(self,work:Path,context:str):
        Path(work).mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(Path(work)/'llm_cache.sqlite',timeout=30);self.context=context
        self.db.execute('CREATE TABLE IF NOT EXISTS sentence_memory (context TEXT, source TEXT, entry TEXT NOT NULL, PRIMARY KEY(context,source))')
    def get(self,entry):
        found=self.db.execute('SELECT entry FROM sentence_memory WHERE context=? AND source=?',(self.context,entry['source'])).fetchone()
        if not found:return None
        saved=json.loads(found[0])
        if not entry['markers']:return saved['plain']
        if saved['marked_source']==entry['marked_source']:return saved['translation']
        raise ValueError('Exact translation exists but new inline-style alignment needs review')
    def put(self,entry,translation):
        plain,_=_decode(entry,translation)
        found=self.db.execute('SELECT entry FROM sentence_memory WHERE context=? AND source=?',(self.context,entry['source'])).fetchone()
        if found and json.loads(found[0])['plain']!=plain:raise ValueError('Exact sentence memory conflict')
        saved={'plain':plain,'translation':translation,'marked_source':entry['marked_source']}
        self.db.execute('INSERT OR REPLACE INTO sentence_memory VALUES (?,?,?)',(self.context,entry['source'],json.dumps(saved,ensure_ascii=False)));self.db.commit()
    def close(self):self.db.close()


def check_rendered_consistency(occurrences,page_texts):
    """Verify reused targets actually appear at each required page occurrence.

    This supplements memory validation: comparing only planned targets cannot
    detect missing or altered text in the rendered PDF. Typography whitespace,
    ligatures and case are normalized, while words and their order stay fixed.
    """
    issues=check_consistency(occurrences);groups={}
    for row in occurrences:groups.setdefault((row['page'],normalize_source(row['source'])),[]).append(row)
    for (page,source),rows in groups.items():
        # Distinct sources translated identically do not consume each other's
        # expected occurrence counts; repeated copies of one source do.
        targets={normalize_source(row['target']) for row in rows}
        if len(targets)!=1:continue
        target=next(iter(targets));text=normalize_source(unicodedata.normalize('NFKC',page_texts.get(page,''))).casefold()
        needle=normalize_source(unicodedata.normalize('NFKC',target)).casefold()
        actual=len(re.findall(r'(?<!\w)'+re.escape(needle)+r'(?!\w)',text)) if needle else 0
        if actual<len(rows):issues.append({'source':source,'target':target,'page':page,'reason':'expected_translation_missing_in_render','expected_occurrences':len(rows),'observed_occurrences':actual})
    return issues


def normalize_complete_question(unit):
    """Restore Spanish opening punctuation on a complete question unit only.

    This is punctuation composition, not translation. Sentence memory keeps its
    punctuation-free core; source quotation/fragment units are left untouched.
    """
    source=unit.get('source','').strip();target=unit.get('target','')
    if (not source.endswith('?') or len(list(_ranges(source)))!=1
            or source.startswith(('"','“','‘',"'"))
            or unit.get('kind') in ('quote','fragment') or unit.get('context') in ('quote','fragment')
            or not target.rstrip().endswith('?') or '¿' in target):return unit
    copied=json.loads(json.dumps(unit));runs=copied.get('target_runs',[])
    for run in runs:
        if run['text'].strip():
            first=len(run['text'])-len(run['text'].lstrip());run['text']=run['text'][:first]+'¿'+run['text'][first:];break
    if runs:copied['target']=''.join(run['text'] for run in runs)
    else:
        first=len(target)-len(target.lstrip());copied['target']=target[:first]+'¿'+target[first:]
    copied['editorial_punctuation']='opening_inverted_question_mark'
    return copied
