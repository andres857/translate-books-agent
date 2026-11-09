"""Prepare source-bound complete units offline. Only --execute calls run_task.

Preserves unrelated corrections and saved native translations. A plan is bound
both to dependencies and to the current saved payloads; changed inputs require
preparation again. No edits to terminology, approvals or original documents.
"""
from astro_translate.emphasis import answer_value
from astro_translate.style_spans import decode_answer

import argparse
import hashlib
import json
from pathlib import Path
import pymupdf as fitz
from astro_translate.production import Production,read,selected_pages,digest
from astro_translate.core import atomic_json,sha256,book_lock,Blocked
from astro_translate.composition_corrections import quote_job
from astro_translate.fidelity import extract_fidelity_units
from astro_translate.style_spans import encode,decode


def build_plan(runner,pages,diagnostics,requested_ids=(),include_ids=()):
    requested_ids=set(requested_ids)
    include_ids=set(include_ids)
    if requested_ids and include_ids:raise Blocked('Use either --ids or --include-ids, not both')
    forced_ids=requested_ids|include_ids
    dependencies={'source_sha256':runner.source_hash,'glossary_sha256':sha256(runner.glossary),
                  'style_sha256':sha256(runner.style),'protected_sha256':sha256(runner.protected),
                  'config_sha256':sha256(runner.config_path)}
    saved_corrections=read(runner.folder/'composition_corrections.json',{})
    jobs=[];input_hashes={}
    with fitz.open(runner.book/'source.pdf') as doc:
        for n in selected_pages(pages,len(doc)):
            file=runner.path(n,'translation.json');payload=read(file,{})
            if not payload.get('blocks'):
                historical=runner.work/f'pilot/p{n:04d}-translation.json'
                if historical.exists():file=historical;payload=read(file,{})
            input_hashes[str(n)]=sha256(file) if file.exists() else None
            units=[{**u,'id':u['id'].removeprefix(f'p{n:04d}_')} for u in payload.get('blocks',[])]
            extracted=extract_fidelity_units(doc[n-1],runner.extras)
            saved={u['id']:u for u in units}
            for u in extracted:
                if u['id'] in saved and any(saved[u['id']][k]!=u[k] for k in ('source','spans')):
                    raise Blocked('Saved native source topology changed')
            canonical=[saved.get(u['id'],u) for u in extracted]
            quotes=quote_job(doc[n-1],canonical,runner.source_hash)
            covered={i for j in quotes for i in j['unit']['composed_ids']}
            for job in quotes:
                canonical_request=lambda row:{k:v for k,v in row.items() if k!='_emphasis_protocol'}
                if any(old.get('unit')==job['unit'] and canonical_request(old.get('request',{}))==canonical_request(job['request'])
                       and old.get('provenance',{}).get('backend') for old in saved_corrections.get('corrections',[])):
                    continue
                jobs.append({**job,'kind':'quote','missing_members':[{**u,'page':n} for u in canonical if u['id'] not in saved and u['id'] in job['unit']['composed_ids']]})
            qa=read(Path(diagnostics)/f'p{n:04d}-qa.json',{})
            needs=set()
            if qa.get('source_sha256')==runner.source_hash:
                for issue in qa.get('text_quality_issues',[]):
                    if issue.get('action')=='retranslate':
                        needs.add(issue['unit_id'].removeprefix(f'p{n:04d}_'))
            from astro_translate.text_quality import unit_issues,repair_composition
            for unit in canonical:
                if unit.get('target') and any(i['code']=='missing_opening_punctuation' for i in unit_issues(repair_composition(unit))):needs.add(unit['id'])
                if unit['id'] in covered:continue
                exists=unit['id'] in saved
                full_id=f'p{n:04d}_'+unit['id']
                if exists and unit['id'] not in needs and full_id not in forced_ids and unit['id'] not in forced_ids:continue
                marked,pairs=encode([{'text':unit['source'][r['start']:r['end']],'style':r['style']} for r in unit['style_runs']])
                jobs.append({'kind':'native_retranslation' if exists else 'missing_native',
                    'unit':{**unit,'page':n},'source_sha256':runner.source_hash,
                    'request':{'id':f'p{n:04d}_'+unit['id'],'source':marked,'_style_pairs':pairs,'_emphasis_protocol':'flat-v1'},
                    'reason':'Current local text QA requires a complete-unit retry.' if exists else 'No saved native translation after failed batch.'})
    for job in jobs:job['request']['_emphasis_protocol']='flat-v1'
    if requested_ids:
        jobs=[j for j in jobs if j['request']['id'] in requested_ids or j['unit']['id'] in requested_ids]
    return {'emphasis_protocol':'flat-v1','requested_ids':sorted(requested_ids),
            'included_ids':sorted(include_ids),**dependencies,'saved_payload_hashes':input_hashes,
            'corrections_sha256':sha256(runner.folder/'composition_corrections.json') if saved_corrections else None,
            'jobs':jobs,'llm_calls':0,'execution_required':bool(jobs)}


def execute(runner,plan,destination):
    from astro_translate import llm
    runner.gates();llm._check_pause(runner.book)
    groups=[];current=[];size=0
    for job in plan['jobs']:
        length=len(job['request']['source'])
        if length>6000:raise Blocked('One complete unit exceeds 6000 characters; review required')
        if current and size+length>6000:groups.append(current);current=[];size=0
        current.append(job);size+=length
    if current:groups.append(current)
    corrections=read(destination,{})
    dependencies={k:plan[k] for k in ('source_sha256','glossary_sha256','style_sha256','protected_sha256','config_sha256')}
    if corrections and any(corrections.get(k)!=v for k,v in dependencies.items()):
        raise Blocked('Existing correction dependencies changed')
    records=corrections.get('corrections',[])
    # Each bounded call is validated and persisted before another begins.
    for group in groups:
        before_corrections=read(destination,{})
        before_pages={n:(sha256(runner.path(n,'translation.json')) if runner.path(n,'translation.json').exists() else None) for n in {j['unit']['page'] for j in group}}
        result=llm.run_task('translate_page',{'_book':str(runner.book),'_config':str(runner.config_path),
            '_phase':'production','_pages':sorted({j['unit']['page'] for j in group}),
            'blocks':[j['request'] for j in group],
            'coordination_instruction':'Translate each complete unit once. Return flat complete text and exact emphasized substrings using the wire contract. Do not repeat content across pairs.'})
        byid={row['id']:answer_value(row) for row in result.data['blocks']}
        provenance={'backend':result.backend,'model':result.model,'input_tokens':result.input_tokens,
            'output_tokens':result.output_tokens,'entry_point':'astro_translate.llm.run_task',
            'response_sha256':digest(result.data)}
        native={}
        for job in group:
            answer=byid[job['request']['id']]
            if job['kind']=='quote':
                records=[r for r in records if (r['unit']['page'],r['unit']['id'])!=(job['unit']['page'],job['unit']['id'])]
                records.append({**job,'translation':answer,'provenance':provenance})
                for member in job.get('missing_members',[]):
                    runs=[{'text':member['source'][r['start']:r['end']],'style':r['style']} for r in member['style_runs']]
                    native.setdefault(member['page'],[]).append({**member,'id':f'p{member["page"]:04d}_'+member['id'],
                        'target':member['source'],'target_runs':runs,'translation_method':'requires_joined_quote_correction'})
            else:
                runs=decode_answer(answer,job['request'])
                n=job['unit']['page'];unit={**job['unit'],'id':job['request']['id'],
                    'target':''.join(r['text'] for r in runs),'target_runs':runs,'provenance':provenance}
                native.setdefault(n,[]).append(unit)
        with book_lock(runner.book):
            if hasattr(runner,'glossary'):
                current={'source_sha256':sha256(runner.book/'source.pdf'),'glossary_sha256':sha256(runner.glossary),
                         'style_sha256':sha256(runner.style),'protected_sha256':sha256(runner.protected),
                         'config_sha256':sha256(runner.config_path)}
                if current!=dependencies:raise Blocked('Dependencies changed during inference')
            latest=read(destination,{})
            if latest and any(latest.get(k)!=v for k,v in dependencies.items()):raise Blocked('Correction dependencies changed during inference')
            changed_ids={j['request']['id'] for j in group if j['kind']=='quote'}
            select=lambda value:[r for r in value.get('corrections',[]) if r.get('request',{}).get('id') in changed_ids]
            if select(latest)!=select(before_corrections):raise Blocked('Selected corrections changed during inference')
            for n,expected in before_pages.items():
                file=runner.path(n,'translation.json')
                if (sha256(file) if file.exists() else None)!=expected:raise Blocked('Selected native payload changed during inference')
            records=[r for r in latest.get('corrections',[]) if r.get('request',{}).get('id') not in changed_ids]+[r for r in records if r.get('request',{}).get('id') in changed_ids]
            atomic_json(destination,{**dependencies,'corrections':records})
            for n,replacements in native.items():
                payload=read(runner.path(n,'translation.json'),{})
                mapped={u['id']:u for u in replacements}
                existing=[{**u,'id':f'p{n:04d}_'+u['id'].removeprefix(f'p{n:04d}_')} for u in payload.get('blocks',[])]
                blocks=[mapped.pop(u['id'],u) for u in existing]+list(mapped.values())
                atomic_json(runner.path(n,'translation.json'),{**payload,'page':n,'source_sha256':runner.source_hash,'blocks':blocks})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--book',required=True);parser.add_argument('--pages',required=True)
    parser.add_argument('--plan',required=True);parser.add_argument('--execute',action='store_true');parser.add_argument('--output');parser.add_argument('--diagnostics',type=Path)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--ids',help='comma-separated exact saved unit ids; limits the grouped request')
    selection.add_argument('--include-ids',help='comma-separated exact unit ids to add to the generated grouped request')
    args=parser.parse_args();runner=Production(args.book);path=Path(args.plan).resolve()
    if not path.is_relative_to(runner.work.resolve()):raise ValueError('Plan must be below book/work')
    selected_ids=[v.strip() for v in (args.ids or '').split(',') if v.strip()]
    included_ids=[v.strip() for v in (args.include_ids or '').split(',') if v.strip()]
    plan=build_plan(runner,args.pages,args.diagnostics or path.parent,selected_ids,included_ids)
    if args.execute:
        if read(path)!=plan:raise Blocked('Plan/source/dependencies changed; prepare again')
        if not args.output:raise ValueError('--execute requires --output')
        output=Path(args.output).resolve()
        if not output.is_relative_to(runner.work.resolve()):raise ValueError('Output must be below book/work')
        execute(runner,plan,output)
    else:atomic_json(path,plan)
    print(json.dumps({'jobs':len(plan['jobs']),'units':[{'page':j['unit']['page'],'id':j['request']['id'],'kind':j['kind']} for j in plan['jobs']],
                      'inference_executed':args.execute},ensure_ascii=False))

if __name__=='__main__':
    from astro_translate.llm import QuotaPaused
    try:main()
    except QuotaPaused as exc:
        print(json.dumps({'status':'paused','reason':str(exc),'exit_code':75}))
        raise SystemExit(75)
