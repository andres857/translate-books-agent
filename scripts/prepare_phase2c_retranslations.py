"""Prepare joined quote requests; --execute is the sole explicit LLM entry point."""
from astro_translate.emphasis import answer_value
from astro_translate.style_spans import decode_answer

import argparse,json,hashlib,re
from pathlib import Path
import pymupdf as fitz
from astro_translate.production import Production,read,selected_pages
from astro_translate.core import atomic_json,sha256,book_lock
from astro_translate.composition_corrections import quote_job
from astro_translate import llm


def main():
    p=argparse.ArgumentParser();p.add_argument('--book',required=True);p.add_argument('--pages',required=True)
    p.add_argument('--plan',required=True);p.add_argument('--execute',action='store_true');p.add_argument('--output')
    args=p.parse_args();r=Production(args.book);path=Path(args.plan).resolve()
    if not path.is_relative_to(r.work.resolve()):raise ValueError('Plan must be below book/work')
    deps={'source_sha256':r.source_hash,'glossary_sha256':sha256(r.glossary),'style_sha256':sha256(r.style),
          'protected_sha256':sha256(r.protected),'config_sha256':sha256(r.config_path)}
    jobs=[]
    with fitz.open(r.book/'source.pdf') as doc:
        for n in selected_pages(args.pages,len(doc)):
            payload=read(r.path(n,'translation.json'),{})
            units=[{**u,'id':u['id'].removeprefix(f'p{n:04d}_')} for u in payload.get('blocks',[])]
            jobs+=[{**j,'kind':'quote'} for j in quote_job(doc[n-1],units,r.source_hash)]
            # Only current, source-bound local QA may request a saved native
            # unit again. Names/contacts already excluded by the narrow QA rules.
            diagnostic=read(path.parent/f'p{n:04d}-qa.json',{})
            residual=diagnostic.get('residual_english',[]) if diagnostic.get('source_sha256')==r.source_hash else []
            from astro_translate.style_spans import encode
            for u in units:
                # QA has removed protected values/names; its remaining punctuation
                # need not be a literal substring of the unmasked saved target.
                target_words=set(re.findall(r'[^\W\d_]+',u['target'].casefold()))
                residual_words=[set(re.findall(r'[^\W\d_]+',t.casefold())) for t in residual]
                if not any(words and words<=target_words for words in residual_words):continue
                marked,pairs=encode([{'text':u['source'][s['start']:s['end']],'style':s['style']} for s in u['style_runs']])
                jobs.append({'kind':'residual_native','unit':{**u,'page':n},'source_sha256':r.source_hash,
                    'request':{'id':f'p{n:04d}_'+u['id'],'source':marked,'_style_pairs':pairs},
                    'reason':'Residual English outside source-proven names, contacts or published titles.'})
            if not units and doc[n-1].get_text().strip():
                from astro_translate.fidelity import extract_fidelity_units
                from astro_translate.style_spans import encode
                for u in extract_fidelity_units(doc[n-1],r.extras):
                    marked,pairs=encode([{'text':u['source'][s['start']:s['end']],'style':s['style']} for s in u['style_runs']])
                    jobs.append({'kind':'missing_native','unit':{**u,'page':n},'source_sha256':r.source_hash,
                        'request':{'id':f'p{n:04d}_'+u['id'],'source':marked,'_style_pairs':pairs},
                        'reason':'Sparse native chapter text misclassified as raster; no saved target available.'})
    planned={**deps,'jobs':jobs,'llm_calls':0,'execution_required':bool(jobs)}
    if not args.execute:
        atomic_json(path,planned);print(json.dumps({'prepared_jobs':len(jobs),'llm_calls':0}));return
    previous=read(path)
    if previous!=planned:raise ValueError('Source/dependency plan changed; prepare again')
    if not args.output:raise ValueError('--execute requires --output')
    destination=Path(args.output).resolve()
    if not destination.is_relative_to(r.work.resolve()):raise ValueError('Output must be below book/work')
    r.gates();corrections=[]
    if not jobs:return
    if sum(len(j['request']['source']) for j in jobs)>6000:raise ValueError('Prepare a smaller page range')
    result=llm.run_task('translate_page',{'_book':str(r.book),'_config':str(r.config_path),'_phase':'production',
                   '_pages':sorted({j['unit']['page'] for j in jobs}),'blocks':[j['request'] for j in jobs],
                   'coordination_instruction':'Translate each complete unit once. Return flat complete text with exact emphasized substrings using the wire contract. Preserve quote punctuation as English curly double and inner single quotes.'})
    byid={row['id']:row for row in result.data['blocks']};native={}
    provenance={'backend':result.backend,'model':result.model,'input_tokens':result.input_tokens,'output_tokens':result.output_tokens,
        'response_sha256':hashlib.sha256(json.dumps(result.data,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'entry_point':'astro_translate.llm.run_task'}
    for job in jobs:
        row=byid[job['request']['id']]
        if job['kind'] in ('missing_native','residual_native'):
            from astro_translate.style_spans import decode
            runs=decode_answer(answer_value(row),job['request'])
            native.setdefault(job['unit']['page'],[]).append({**job['unit'],'id':row['id'],
                'target':''.join(x['text'] for x in runs),'target_runs':runs,'provenance':provenance})
        else:corrections.append({**job,'translation':answer_value(row),'provenance':provenance})
    with book_lock(r.book):
        atomic_json(destination,{**deps,'corrections':corrections})
        for n,units in native.items():
            previous=read(r.path(n,'translation.json'),{})
            replacements={u['id']:u for u in units}
            existing=[{**u,'id':f'p{n:04d}_'+u['id'].removeprefix(f'p{n:04d}_')} for u in previous.get('blocks',[])]
            blocks=[replacements.pop(u['id'],u) for u in existing]+list(replacements.values())
            atomic_json(destination.parent/f'p{n:04d}-translation.json',{**previous,'page':n,'source_sha256':r.source_hash,'blocks':blocks})
    print(json.dumps({'corrected_jobs':len(corrections),'output':str(destination)}))

if __name__=='__main__':main()
