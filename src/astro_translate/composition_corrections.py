"""Prepare / validate source-bound joined-unit corrections; inference is explicit.

No response is invented. A correction file only exists after run_task returns a
valid answer, and is revalidated at composition against the current source/runs.
"""
from astro_translate.emphasis import answer_value
from astro_translate.style_spans import decode_answer

from copy import deepcopy
from pathlib import Path
import json
from .core import Blocked,atomic_json,sha256
from .style_spans import encode,decode
from .fidelity import composition_sources_sha256


def override_owns_targets(page):
    """A reviewed composition override supplies every target of its page."""
    from .profiles import profile_for_page
    return bool(profile_for_page(page).page_policy(page).get('composition_translations'))


def quote_job(page, units, source_hash):
    from .editorial_rules import quote_retranslation_units
    from .display_titles import title_retranslation_units
    jobs=[]
    if override_owns_targets(page):return jobs
    for unit in quote_retranslation_units(page,units)+title_retranslation_units(page,units):
        runs=[{'text':unit['source'][r['start']:r['end']],'style':r['style']} for r in unit['style_runs']]
        marked,pairs=encode(runs)
        jobs.append({'unit':unit,'source_sha256':source_hash,
                     'request':{'id':f'p{page.number+1:04d}_'+unit['id'],'source':marked,'_style_pairs':pairs},
                     'reason':('Complete stacked display title requires shared grammar across its lines.' if unit.get('display_title') else 'Complete display quotation was translated as independent line fragments; preserve equivalent emphasis in target order.')})
    return jobs


def apply_corrections(units,page,book,glossary,style,protected,config_path=None,path=None):
    file=Path(path) if path else Path(book)/'work/production/composition_corrections.json'
    if not file.exists():return units,[]
    if not file.resolve().is_relative_to((Path(book)/'work').resolve()):raise Blocked('Correction path outside book/work')
    data=json.loads(file.read_text())
    expected={'source_sha256':sha256(Path(book)/'source.pdf'),'glossary_sha256':sha256(glossary),
              'style_sha256':sha256(style),'protected_sha256':sha256(protected)}
    if any(data.get(k)!=v for k,v in expected.items()):raise Blocked('Composition correction dependencies changed')
    byid={u['id']:u for u in units};result=deepcopy(units);proofs=[]
    from .retranslation import validate_answer
    owned=override_owns_targets(page)
    for record in data['corrections']:
        unit=record['unit']
        if unit['page']!=page.number+1:continue
        if not record.get('provenance',{}).get('backend'):raise Blocked('Correction lacks run_task provenance')
        if owned:
            # The page's reviewed composition override replaces every target.
            proofs.append({'unit_id':unit['id'],'composed_ids':unit['composed_ids'],
                           'superseded_by':'composition_translations'})
            continue
        jobs=quote_job(page,units,expected['source_sha256'])
        job=next((j for j in jobs if j['unit']['id']==unit['id']),None)
        if job is None or job['unit']!=unit or {k:v for k,v in job['request'].items() if k!='_emphasis_protocol'}!={k:v for k,v in record['request'].items() if k!='_emphasis_protocol'}:raise Blocked('Correction source/style topology changed')
        validate_answer(record['request'],record['translation'])
        runs=decode_answer(record['translation'],record['request'])
        # Source members (id, source, spans) keep page bindings on unchanged content.
        members=[{k:byid[i][k] for k in ('id','source','spans')} for i in unit['composed_ids'] if i in byid]
        corrected={**unit,'target_runs':runs,'target':''.join(r['text'] for r in runs),
                   'correction_provenance':record['provenance'],'correction_sha256':sha256(file),
                   **({'source_members':members} if len(members)==len(unit['composed_ids']) else {})}
        ids=set(unit['composed_ids'])
        result=[u for u in result if u['id'] not in ids]+[corrected]
        proofs.append({'unit_id':unit['id'],'composed_ids':unit['composed_ids'],'response_sha256':record['provenance']['response_sha256']})
    return result,proofs


def translate_quote_jobs(jobs,book,glossary,style,protected,config_path,phase,runner):
    """Explicit adapter entry; source-bound quote corrections, grouped <=6000.

    Previous corrections are retained. A valid identical correction is reused
    locally; stale dependencies fail closed instead of relabelling old output.
    The caller supplies the sole run_task entry point for offline tests.
    """
    import hashlib
    from .core import book_lock
    from .retranslation import validate_answer
    if not jobs:return []
    book=Path(book);destination=book/'work/production/composition_corrections.json'
    deps={'source_sha256':sha256(book/'source.pdf'),'glossary_sha256':sha256(glossary),
          'style_sha256':sha256(style),'protected_sha256':sha256(protected)}
    previous=json.loads(destination.read_text()) if destination.exists() else {**deps,'corrections':[]}
    if any(previous.get(k)!=v for k,v in deps.items()):raise Blocked('Composition correction dependencies changed')
    pending=[]
    for job in jobs:
        record=next((r for r in previous['corrections'] if r['unit']==job['unit'] and r['request']==job['request']),None)
        if record and record.get('provenance',{}).get('backend'):
            try:validate_answer(record['request'],record['translation'])
            except (ValueError,Blocked):pending.append(job)
        else:pending.append(job)
    groups=[];current=[];length=0
    for job in pending:
        size=len(job['request']['source'])
        if size>6000:raise Blocked('Complete display quotation exceeds grouped request limit')
        if current and length+size>6000:groups.append(current);current=[];length=0
        current.append(job);length+=size
    if current:groups.append(current)
    results=[]
    for group in groups:
        result=runner('translate_page',{'_book':str(book),'_config':str(config_path),'_phase':phase,
            '_pages':sorted({j['unit']['page'] for j in group}),'blocks':[j['request'] for j in group],
            'coordination_instruction':'Translate each display quotation as one complete unit. Preserve source case and equivalent emphasis using the flat wire contract.'})
        answers={r['id']:answer_value(r) for r in result.data['blocks']}
        provenance={'backend':result.backend,'model':result.model,'input_tokens':result.input_tokens,
            'output_tokens':result.output_tokens,'entry_point':'astro_translate.llm.run_task',
            'response_sha256':hashlib.sha256(json.dumps(result.data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
        records=[]
        for job in group:
            if job['request']['id'] not in answers:raise Blocked('Missing complete quotation id')
            answer=answers[job['request']['id']];validate_answer(job['request'],answer)
            records.append({**job,'translation':answer,'provenance':provenance})
        with book_lock(book):
            latest=json.loads(destination.read_text()) if destination.exists() else {**deps,'corrections':[]}
            if any(latest.get(k)!=v for k,v in deps.items()):raise Blocked('Composition correction dependencies changed')
            ids={r['request']['id'] for r in records}
            latest['corrections']=[r for r in latest['corrections'] if r['request']['id'] not in ids]+records
            atomic_json(destination,latest)
        results.append(result)
    return results
