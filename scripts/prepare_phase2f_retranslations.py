"""Prepare whole spatial callouts offline; --execute alone makes model calls.

CAS under the book lock preserves concurrent and unrelated saved translations.
No production checkpoint, manifest or approval is changed by preparation.
"""
from astro_translate.emphasis import answer_value
from astro_translate.style_spans import decode_answer

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import pymupdf as fitz
from astro_translate import fidelity, llm
from astro_translate.core import atomic_json,sha256,Blocked,book_lock
from astro_translate.production import Production,selected_pages,read,digest
from astro_translate.style_spans import encode,decode
from astro_translate.retranslation import validate_answer
from astro_translate.resegmentation import _fields,block_slices,_atomic_bytes


def dependencies(r):
    return {k:sha256(p) for k,p in dict(source=r.book/'source.pdf',glossary=r.glossary,
        style=r.style,protected=r.protected,config=r.config_path).items()}


def build_plan(r,pages):
    records=[]
    with fitz.open(r.book/'source.pdf') as doc:
        for n in selected_pages(pages,len(doc)):
            path=r.path(n,'translation.json');saved=read(path,{})
            if not saved.get('blocks'):continue
            if saved.get('source_sha256')!=r.source_hash:raise Blocked('Saved caption PDF hash changed')
            prefix=f'p{n:04d}_';old={u['id'].removeprefix(prefix):u for u in saved['blocks']}
            units=fidelity.extract_fidelity_units(doc[n-1],r.extras)
            # Exact multiset coverage permits splitting/reordering source spans,
            # never dropping protected data, inventing source or changing style.
            key=lambda s:json.dumps(s,sort_keys=True,ensure_ascii=False)
            if Counter(key(s) for u in old.values() for s in u['spans'])!=Counter(key(s) for u in units for s in u['spans']):
                raise Blocked('Caption source span coverage changed')
            jobs=[]
            for u in units:
                if u['id'] in old:
                    if any(u[k]!=old[u['id']][k] for k in ('source','spans','style_runs')):raise Blocked('Unchanged caption identity drift')
                    continue
                if not u.get('spatial_caption'):raise Blocked('Unexpected non-caption topology change')
                marked,pairs=encode([{'text':u['source'][s['start']:s['end']],'style':s['style']} for s in u['style_runs']])
                jobs.append({'unit':u,'request':{'id':prefix+u['id'],'source':marked,'_style_pairs':pairs}})
            if jobs:records.append({'page':n,'payload_sha256':sha256(path),'units':units,'jobs':jobs})
    return {'dependencies':dependencies(r),'pages':records,'llm_calls':0}


def replace_bytes(raw,record,replacements):
    old=block_slices(raw);prefix=f'p{record["page"]:04d}_';parts=[]
    for u in record['units']:
        key=prefix+u['id']
        if key in replacements:parts.append(json.dumps(replacements[key],ensure_ascii=False,indent=2).encode())
        else:
            # Production may store unqualified ids; preserve the original bytes.
            a,b=old[key if key in old else u['id']];parts.append(raw[a:b])
    a,b=_fields(raw)['blocks'];return raw[:a]+b'[\n'+b',\n'.join(parts)+b'\n]'+raw[b:]


def execute(r,plan):
    r.gates();llm._check_pause(r.book)
    glossary=list(csv.DictReader(r.glossary.open()))
    for record in plan['pages']:
        path=r.path(record['page'],'translation.json');raw=path.read_bytes()
        if sha256(path)!=record['payload_sha256'] or dependencies(r)!=plan['dependencies']:raise Blocked('Plan changed; prepare again')
        jobs=record['jobs']
        if sum(len(j['request']['source']) for j in jobs)>6000:raise Blocked('Caption figure exceeds 6000 characters')
        result=llm.run_task('translate_page',{'_book':str(r.book),'_config':str(r.config_path),
            '_phase':'production','_pages':[record['page']],'blocks':[j['request'] for j in jobs],
            'coordination_instruction':'Each id is one complete, independent figure callout. Never merge or duplicate content between ids. Preserve emphasis and approved terminology.'})
        answers={x['id']:answer_value(x) for x in result.data['blocks']}
        if len(answers)!=len(result.data['blocks']) or set(answers)!={j['request']['id'] for j in jobs}:raise Blocked('Caption response ids differ')
        provenance={'entry_point':'astro_translate.llm.run_task','backend':result.backend,
                    'model':result.model,'response_sha256':digest(result.data)}
        replacements={}
        for j in jobs:
            answer=answers[j['request']['id']];validate_answer(j['request'],answer,extras=r.extras,glossary=glossary)
            runs=decode_answer(answer,j['request'])
            replacements[j['request']['id']]={**j['unit'],'id':j['request']['id'],
                'target':''.join(x['text'] for x in runs),'target_runs':runs,'provenance':provenance}
        after=replace_bytes(raw,record,replacements)
        with book_lock(r.book):
            if path.read_bytes()!=raw or dependencies(r)!=plan['dependencies']:raise Blocked('Concurrent caption/dependency update')
            history=r.folder/'history'/('phase2f-'+record['payload_sha256'][:16]);history.mkdir(parents=True,exist_ok=True)
            _atomic_bytes(history/path.name,raw)
            _atomic_bytes(path,after)
            atomic_json(history/'caption-migration.json',{'before':record['payload_sha256'],'after':sha256(path),'provenance':provenance})


def main():
    p=argparse.ArgumentParser();p.add_argument('--book',required=True);p.add_argument('--pages',required=True)
    p.add_argument('--plan',required=True,type=Path);p.add_argument('--execute',action='store_true');a=p.parse_args()
    r=Production(a.book)
    if not a.plan.resolve().is_relative_to(r.work):raise Blocked('Plan must be below book/work')
    fresh=build_plan(r,a.pages)
    if a.execute:
        if read(a.plan)!=fresh:raise Blocked('Stale caption plan; prepare again')
        execute(r,fresh)
    else:atomic_json(a.plan,fresh)
    print(json.dumps({'jobs':sum(len(x['jobs']) for x in fresh['pages']),
        'pages':[x['page'] for x in fresh['pages']],'inference_executed':a.execute}))

if __name__=='__main__':
    try:main()
    except llm.QuotaPaused as exc:
        print(str(exc));raise SystemExit(75)
