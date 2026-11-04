"""Local adapters; every model request is routed through llm.run_task."""
import csv
import importlib.metadata
import json
import os
import tempfile
import time
import tomllib
from pathlib import Path
from collections import Counter
import pymupdf as fitz
from .core import Blocked, protected, extract_units, atomic_json, book_lock, require_approval, sha256, read_extras, reinsert
from .llm import run_task, ROOT, QuotaPaused, _check_pause


def doctor():
    versions={}
    for package in ['pymupdf','babeldoc','paddleocr','paddlepaddle','pillow','openai','openai-codex','openai-codex-cli-bin','pytest','jsonschema','pymupdf4llm','lingua-language-detector']:
        try:versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:versions[package]=None
    path=Path(importlib.metadata.distribution('babeldoc').locate_file('babeldoc/main.py'))
    source=path.read_text();flags=['--pages','--glossary-files','--no-dual','--watermark-output-mode','--max-pages-per-part','--formular-font-pattern','--formular-char-pattern']
    return {'versions':versions,'babeldoc_options':{f:f in source for f in flags},'babeldoc_protection_verified':False,'babeldoc_reason':'Direct BaseTranslator bridge has no reliable page boundary; own PyMuPDF page route is used.', 'openai_key_present':bool(os.environ.get('OPENAI_API_KEY')),'ocr_weights_checked':False,'batch_api_integration_verified':False}


def glossary_build(book,do_not_translate=None,use_llm=False,config_path=None):
    """Proposal only. Optional model work batches up to thirty extracted pages."""
    words=Counter();extras=read_extras(do_not_translate);chunks=[]
    with fitz.open(Path(book)/'source.pdf') as doc:
        for index,page in enumerate(doc):
            units=extract_units(page,extras);text='\n'.join(u['source'] for u in units);chunks.append(text)
            import re
            words.update(w.casefold() for w in re.findall(r'[A-Za-z][A-Za-z-]{2,}',text) if not protected(w,'',extras))
    result={'source_sha256':sha256(Path(book)/'source.pdf'),'approved':False,'method':'local_frequency_inventory','candidates':[{'source':w,'count':n,'target':None} for w,n in words.most_common(500)]}
    if use_llm:
        result['model_proposal']=[]
        for offset in range(0,len(chunks),30):
            payload={'_book':str(book),'text':'\n\n'.join(chunks[offset:offset+30])}
            if config_path:payload['_config']=str(config_path)
            result['model_proposal'].extend(run_task('glossary_candidates',payload).data['terms'])
    with book_lock(book):atomic_json(Path(book)/'work/glossary_candidates.json',result)
    return result


def backend_config_digest(config):
    import hashlib
    selected={'provider':config['provider'],'tasks':{name:{'model':value['model'],'effort':value['effort']} for name,value in config['tasks'].items()},'route':'pymupdf'}
    return hashlib.sha256(json.dumps(selected,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def require_backend_approval(book,config_path,phase):
    config=tomllib.loads(Path(config_path).read_text());backend=config['provider']['backend']
    path=Path(book)/'work/backend_approval.json'
    if not path.exists():raise Blocked('G1b: human backend approval required')
    approval=json.loads(path.read_text())
    valid=bool(approval.get('approved') and approval.get('approved_by') and approval.get('backend')==backend)
    if approval.get('backend_config_sha256'):
        valid=valid and approval['backend_config_sha256']==backend_config_digest(config) and approval.get('route')=='pymupdf' and phase in approval.get('authorized_phases',[])
    else:valid=valid and approval.get('config_sha256')==sha256(config_path)
    if not valid:raise Blocked('G1b: backend approval absent, stale or not authorized for this phase')
    if phase=='production':
        path=Path(book)/'work/pilot_approval.json'
        if not path.exists():raise Blocked('G4: human pilot approval required')
        pilot=json.loads(path.read_text())
        if not pilot.get('approved') or not pilot.get('approved_by') or pilot.get('config_sha256')!=sha256(config_path):raise Blocked('G4: pilot approval absent or stale')


def native_translate(book,glossary,style,do_not_translate=None,pages=None,*,config_path=None,phase='production',reinsert_pages=True):
    """Translate bounded batches of native layout units, then reinsert by page.

    Phase 1c is scoped to the three explicitly authorized sample pages. Shared
    call usage is reported once in batches, never duplicated as page usage.
    """
    if phase in ('fidelity','pilot'):return fidelity_translate(book,glossary,style,do_not_translate,pages,config_path=config_path or ROOT/'config/pipeline.toml',reinsert_pages=reinsert_pages,phase=phase)
    book=Path(book).resolve();config_path=Path(config_path or ROOT/'config/pipeline.toml').resolve();config=tomllib.loads(config_path.read_text())
    require_approval(book,glossary,style,do_not_translate);_check_pause(book)
    if phase not in ('smoke','reinsertion','pilot','production'):raise Blocked('Unknown execution phase')
    if not pages:raise Blocked('Explicit page selection is required')
    selected=[]
    for term in str(pages).split(','):
        bounds=term.strip().split('-');selected.extend(range(int(bounds[0]),int(bounds[-1])+1))
    selected=sorted(set(selected))
    if phase=='smoke' and len(selected)>5:raise Blocked('Backend smoke is limited to five explicit pages')
    if phase=='reinsertion' and not set(selected)<=set((7,15,121)):raise Blocked('Phase 1c is authorized only for pages 7, 15 and 121')
    if phase!='smoke':require_backend_approval(book,config_path,phase)
    extras=read_extras(do_not_translate);reports=[];batch_reports=[];plans=[]
    output=book/'work'/('reinsertion' if phase=='reinsertion' else 'backend_smoke' if phase=='smoke' else 'native');output.mkdir(parents=True,exist_ok=True)
    with fitz.open(book/'source.pdf') as doc:
        if any(p<1 or p>len(doc) for p in selected):raise ValueError('Page selection out of bounds')
        for number in selected:
            start=time.monotonic();page=doc[number-1]
            if phase=='smoke':units=extract_units(page,extras)
            else:
                from .layout import extract_layout_units
                units=extract_layout_units(page,extras)
            safe=[u for u in units if u['rotate'] in (0,90,180,270)]
            spans=[s for b in page.get_text('dict')['blocks'] for line in b.get('lines',[]) for s in line['spans'] if s['text'].strip()]
            protected_count=sum(protected(s['text'],s['font'],extras) for s in spans)
            blocks=[{'id':f'p{number:04d}_'+str(u.get('id',f'b{i:04d}')),'source':u['source']} for i,u in enumerate(safe)]
            plans.append({'page':number,'units':safe,'blocks':blocks,'characters':sum(len(u['source']) for u in safe),'total_spans':len(spans),'omitted_protected_spans':protected_count,'omitted_rotated_units':len(units)-len(safe),'extraction_s':time.monotonic()-start})
    groups=[];current=[];chars=0;limit=int(config.get('batching',{}).get('max_chars_per_call',6000))
    for plan in plans:
        if current and chars+plan['characters']>limit:groups.append(current);current=[];chars=0
        current.append(plan);chars+=plan['characters']
    if current:groups.append(current)
    for batch_index,group in enumerate(groups):
        page_numbers=[p['page'] for p in group];all_blocks=[b for p in group for b in p['blocks']]
        result=run_task('translate_page',{'_book':str(book),'_config':str(config_path),'_pages':page_numbers,'_phase':phase,'blocks':all_blocks})
        translated={x['id']:x['translation'] for x in result.data['blocks']};data_by_id={x['id']:x for x in result.data['blocks']}
        batch_id=f'{phase}_{batch_index:03d}'
        batch_report={'batch_id':batch_id,'pages':page_numbers,'backend':result.backend,'model':result.model,'effort':result.effort,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'cached':result.cached,'llm_latency_s':result.latency_s,'characters':sum(p['characters'] for p in group)}
        batch_reports.append(batch_report)
        for plan in group:
            number=plan['page'];safe=plan['units'];start=time.monotonic()
            payload={'page':number,'blocks':[{**unit,'target':translated[block['id']]} for unit,block in zip(safe,plan['blocks'])]}
            atomic_json(output/f'p{number:04d}-translation.json',{'page':number,'source_sha256':sha256(book/'source.pdf'),'data':{'blocks':[data_by_id[b['id']] for b in plan['blocks']]},'blocks':payload['blocks']})
            report={k:v for k,v in plan.items() if k not in ('blocks','units')}
            report.update({'phase':phase,'batch_id':batch_id,'backend':result.backend,'model':result.model,'effort':result.effort,'safe_units':len(safe),'safe_spans':sum(len(u.get('spans',[u])) for u in safe),'cached':result.cached,'input_tokens':result.input_tokens if len(group)==1 else None,'cached_input_tokens':result.cached_input_tokens if len(group)==1 else None,'output_tokens':result.output_tokens if len(group)==1 else None,'usage_note':'Exact usage is recorded once in the batch report; shared page attribution is unknown.','json_valid':True,'glossary_valid':True,'qa_verdict':'not_run'})
            if reinsert_pages:
                try:
                    shortforms=None
                    short_path=Path(config['paths'].get('short_glossary','glossary/astro_en_es_short.csv'))
                    if not short_path.is_absolute():short_path=ROOT/short_path
                    short_approval=book/'work/shortforms_approval.json'
                    if short_path.is_file() and short_approval.is_file():shortforms={'path':str(short_path),'approval':str(short_approval)}
                    fit=reinsert(book,payload,glossary,style,do_not_translate,font_path=config['paths'].get('font_regular'),layout=config.get('layout'),shortforms=shortforms,shortener=lambda blocks:shorten_page(book,number,blocks,config_path,phase=phase))
                    report['fit']=fit
                except QuotaPaused:raise
                except Blocked as exc:
                    report['fit']={'status':'review','reason':str(exc)}
                    with book_lock(book):
                        queuepath=book/'work/review_queue.json';queue=json.loads(queuepath.read_text()) if queuepath.exists() else []
                        queue.append({'page':number,'reason':'native_fit: '+str(exc),'artifacts':[str(output/f'p{number:04d}-translation.json')]+[str(p) for p in (book/'work/backend_selection').glob(f'p{number:03d}.png')],'suggested_action':'Review safe unit geometry and available space before editing'})
                        atomic_json(queuepath,queue)
            report['reinsertion_s']=time.monotonic()-start;atomic_json(output/f'p{number:04d}-report.json',report);reports.append(report)
    return {'phase':phase,'pages':reports,'batches':batch_reports,'qa_complete':False,'source_sha256':sha256(book/'source.pdf')}


def local_ocr(image_path):
    from paddleocr import PaddleOCR
    engine=PaddleOCR(lang='en',device='cpu',use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False)
    return [r.json for r in engine.predict(str(image_path))]


def shorten_page(book,page,blocks,config_path=None,phase='legacy'):
    payload={'_book':str(book),'_page':page,'_phase':phase,'blocks':blocks}
    if config_path:payload['_config']=str(config_path)
    return run_task('shorten',payload).data['blocks']


def masked_visual_judge(book,before,after,config_path=None,page=None):
    """Only core.visual_qa may call after verifying and masking BOTH renders."""
    folder=Path(book)/'work/.masked_qa';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        paths=[Path(temp)/'before.png',Path(temp)/'after.png'];before.save(paths[0]);after.save(paths[1])
        payload={'_book':str(book),'_page':page,'_images':[str(p) for p in paths],'_images_verified':True,'instruction':'Compare masked original and translated images; check remaining English, overlap and clipping.'}
        if config_path:payload['_config']=str(config_path)
        result=run_task('qa_judge',payload).data
        return {'ok':result['verdict']=='ok' and not result['overlaps'],'residual_english':result['residual_english'],'notes':result['notes']}


def fidelity_translate(book,glossary,style,do_not_translate,pages,*,config_path,reinsert_pages=True,phase='fidelity'):
    """Phase 1d: local dates, shared clauses, protected-value masking and style runs."""
    import hashlib
    from .consistency import build_plan,resolve_plan,SentenceMemory,MEMORY_VERSION
    from .dates import translate_date,looks_like_date
    from .fidelity import extract_fidelity_units
    from .llm import MASK_VERSION,LLMResult,_log
    book=Path(book).resolve();config_path=Path(config_path).resolve();config=tomllib.loads(config_path.read_text())
    require_approval(book,glossary,style,do_not_translate);require_backend_approval(book,config_path,phase);_check_pause(book)
    selected=[]
    for term in str(pages).split(','):
        bounds=term.strip().split('-');selected.extend(range(int(bounds[0]),int(bounds[-1])+1))
    selected=sorted(set(selected))
    if phase not in ('fidelity','pilot'):raise Blocked('Unsupported fidelity compositor phase')
    if not selected or (phase=='fidelity' and not set(selected)<={7,15,121}):raise Blocked('Phase 1d is authorized only for pages 7, 15 and 121')
    if phase=='pilot' and len(selected)>20:raise Blocked('Pilot is limited to twenty explicit pages')
    output=book/'work'/phase;output.mkdir(parents=True,exist_ok=True);extras=read_extras(do_not_translate)
    local_units=[];model_units=[];page_stats={}
    with fitz.open(book/'source.pdf') as doc:
        for number in selected:
            extracted=extract_fidelity_units(doc[number-1],extras);stats={'page':number,'units':len(extracted),'dates_local':0,'numeric_prose_units':0};page_stats[number]=stats
            for unit in extracted:
                unit={**unit,'id':f'p{number:04d}_'+unit['id'],'page':number}
                try:target=translate_date(unit['source'])
                except ValueError as exc:raise Blocked('Invalid date label on page '+str(number)+': '+str(exc)) from None
                if target is not None:
                    local_units.append({**unit,'target':target,'target_runs':[{'text':target,'style':unit['style']}],'translation_method':'deterministic_date'});stats['dates_local']+=1
                else:
                    if looks_like_date(unit['source']):raise Blocked('Unsupported date label must be handled locally before inference')
                    if any(c.isdigit() for c in unit['source']):stats['numeric_prose_units']+=1
                    model_units.append(unit)
    plan=build_plan(model_units)
    task=config['tasks']['translate_page']
    def configured_path(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    context_data={'memory_version':MEMORY_VERSION,'mask_version':MASK_VERSION,'backend':config['provider']['backend'],'task':task,'glossary':sha256(glossary),'style':sha256(style),'protected':sha256(do_not_translate),'prompt':sha256(configured_path(task['prompt'])),'schema':sha256(configured_path(task['schema']))}
    context=hashlib.sha256(json.dumps(context_data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    memory=SentenceMemory(book/'work',context);answers={};pending=[];hits=0
    entries_by_id={entry['id']:entry for entry in plan['entries'].values()}
    try:
        for request in plan['requests']:
            try:saved=memory.get(entries_by_id[request['id']])
            except ValueError as exc:raise Blocked(str(exc)) from None
            if saved is None:pending.append(request)
            else:answers[request['id']]=saved;hits+=1
        result=LLMResult({'blocks':[]},task['model'],task['effort'],0,0,config['provider']['backend'],not pending,0,cached_input_tokens=0)
        if pending:
            dependencies={request['id']:entries_by_id[request['id']]['pages'] for request in pending}
            result=run_task('translate_page',{'_book':str(book),'_config':str(config_path),'_phase':phase,'_pages':selected,'_page_dependencies':dependencies,'blocks':pending})
            answers.update({row['id']:row['translation'] for row in result.data['blocks']})
            editorial_records=[{'id':row['id'],'pages':entries_by_id[row['id']]['pages'],'source':entries_by_id[row['id']]['source'],'corrections':row.get('grammatical_corrections',[])} for row in result.data['blocks'] if row.get('grammatical_corrections')]
            atomic_json(output/'model-editorial-corrections.json',editorial_records)
        resolved=resolve_plan(plan,answers)
        if resolved['issues']:raise Blocked('Repeated-source consistency failed before reinsertion')
        if result.backend==config['provider']['backend']:
            for entry in plan['entries'].values():memory.put(entry,answers[entry['id']])
    finally:memory.close()
    if hits:_log(book,{'task':'translate_page','phase':phase,'page':None,'pages':selected,'backend':config['provider']['backend'],'model':task['model'],'effort':task['effort'],'cached':True,'sentence_cache_hits':hits,'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'latency_s':0,'cost_usd':0,'status':'sentence_cache_hit'})
    atomic_json(output/'sentence_plan.json',plan)
    atomic_json(output/'consistency.json',{'memory_context':context,'occurrences':resolved['occurrences'],'issues':resolved['issues'],'exact_memory_hits':hits,'unique_source_clauses':len(plan['requests'])})
    translated=resolved['units']+local_units;batch={'batch_id':phase+'_aggregate','pages':selected,'backend':result.backend,'model':result.model,'effort':result.effort,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'llm_latency_s':result.latency_s,'cached':result.cached,'requested_unique_clauses':len(pending),'exact_memory_hits':hits,'deterministic_dates':len(local_units)}
    reports=[]
    for number in selected:
        units=sorted([unit for unit in translated if unit['page']==number],key=lambda u:u['id'])
        payload={'page':number,'blocks':units};path=output/f'p{number:04d}-translation.json'
        atomic_json(path,{'page':number,'source_sha256':sha256(book/'source.pdf'),'memory_context':context,'blocks':units})
        report={**page_stats[number],'phase':phase,'batch_id':batch['batch_id'],'input_tokens':None,'cached_input_tokens':None,'output_tokens':None,'usage_note':'Exact shared usage is recorded once in batches.','json_valid':True,'glossary_valid':True,'consistency_ok':True,'qa_verdict':'not_run'}
        if reinsert_pages:
            from .fidelity import reinsert_fidelity
            report['fit']=reinsert_fidelity(book,payload,glossary,style,do_not_translate,config=config,artifact_dir=output,phase=phase)
        atomic_json(output/f'p{number:04d}-report.json',report);reports.append(report)
    return {'phase':phase,'pages':reports,'batches':[batch],'qa_complete':False,'source_sha256':sha256(book/'source.pdf')}
