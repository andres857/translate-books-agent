"""Snapshot, offline recompose, and group saved-book defects by cause.

The source book is read-only. Input JSON is copied to a private snapshot before
recomposition; all generated PDFs, QA and comparison images live under
``work/phase2h``. No provider or production command is invoked.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts'))
from astro_translate.core import atomic_json, sha256
from astro_translate.production import read
from recompose_production import recompose


def _copy_stable(source:Path,destination:Path,retries=3):
    """Copy atomic artifacts and reject a source that changes during capture."""
    for _ in range(retries):
        before=sha256(source);destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
        copied=sha256(destination);after=sha256(source)
        if before==copied==after:return copied
    raise RuntimeError(f'Input changed during snapshot: {source}')


def snapshot_inputs(book:Path,folder:Path):
    snapshot_root=folder/'input_snapshot'
    if snapshot_root.exists():shutil.rmtree(snapshot_root)
    snapshot=snapshot_root/'book';work=snapshot/'work'
    work.mkdir(parents=True)
    (snapshot/'source.pdf').symlink_to((book/'source.pdf').resolve())
    captured={}
    for name in ('profile.toml','pipeline.toml'):
        src=book/'work'/name
        if src.exists():captured[str(src.relative_to(book))]=_copy_stable(src,work/name)
    profile_path=book/'work'/'profile.toml'
    if profile_path.exists():
        profile=tomllib.loads(profile_path.read_text())
        def referenced(node):
            if isinstance(node,dict):
                if isinstance(node.get('path'),str) and node.get('sha256'):
                    source=book/node['path']
                    if source.is_file():
                        captured[str(source.relative_to(book))]=_copy_stable(source,snapshot/node['path'])
                for child in node.values():referenced(child)
            elif isinstance(node,list):
                for child in node:referenced(child)
        referenced(profile)
    pilot=book/'work'/'pilot'
    if pilot.exists():
        for src in sorted(pilot.glob('p*-translation.json')):
            captured[str(src.relative_to(book))]=_copy_stable(src,work/'pilot'/src.name)
    production=book/'work'/'production'
    if production.exists():
        for pattern in ('p*-translation.json','composition_corrections.json'):
            for src in sorted(production.glob(pattern)):
                captured[str(src.relative_to(book))]=_copy_stable(src,work/'production'/src.name)
        for pattern in ('p*-qa.json','p*-fit.json'):
            for src in sorted(production.glob(pattern)):
                captured[str(src.relative_to(book))]=_copy_stable(src,folder/'input_snapshot'/'prior_production'/src.name)
    batches={}
    if production.exists():
        for src in sorted(production.glob('batch-*.json')):
            batches[src.name]=_copy_stable(src,folder/'input_snapshot'/'batches'/src.name)
    validations={}
    source_validation=book/'work'/'llm_validation'
    if source_validation.exists():
        for src in sorted(source_validation.glob('*.json')):
            validations[src.name]=_copy_stable(src,folder/'input_snapshot'/'llm_validation'/src.name)
    atomic_json(folder/'snapshot.json',{'source_sha256':sha256(book/'source.pdf'),
        'captured':captured,'batches':batches,'llm_validation':validations})
    return snapshot,folder/'input_snapshot'/'batches',folder/'input_snapshot'/'llm_validation'


def _cause_text(value):
    value=str(value)
    value=re.sub(r' for id [^;]+','',value)
    value=re.sub(r' at /[^ )]+','',value)
    return value.strip()


_VALIDATION_CAUSES=(
    ('glossary_form','approved glossary form missing'),
    ('style_markers','style markers changed order or boundary'),
    ('placeholders','placeholders changed'),
    ('emphasis_pairs','Crossed or empty emphasis pair'),
    ('emphasis_pairs','Text outside paired emphasis or closing marker without opening'),
    ('emphasis_pairs','Incomplete emphasis pairs'),
    ('emphasis_pairs','Repeated emphasis marker'),
    ('repeated_ngram','repeated_ngram'),
    ('inline_style','inline_style'),
    ('character_limit','character limit exceeded'),
    ('response_ids','Returned ids differ or contain duplicates'),
)


def _validation_causes(value):
    found=[];lower=str(value).casefold()
    for code,cause in _VALIDATION_CAUSES:
        if cause.casefold() in lower and (code,cause) not in found:found.append((code,cause))
    return found or [('validation',_cause_text(value))]


def _group_resolved_validation(records):
    """Collapse repeated batch/validation records for the same saved unit."""
    grouped={}
    for item in records:
        key=(item.get('page'),item.get('unit_id'),item.get('reason'))
        row=grouped.setdefault(key,{
            'page':item.get('page'),'unit_id':item.get('unit_id'),
            'reason':item.get('reason'),'sources':[]})
        source=item.get('batch_file') or item.get('validation_file')
        if source and source not in row['sources']:
            row['sources'].append(source)
    return sorted(grouped.values(),key=lambda row:(row['page'] or 0,row['unit_id'] or '',row['reason'] or ''))


def _current_failure_matches(page,exception,page_rows):
    row=page_rows.get(page,{})
    current=[row.get('error','')]
    current.extend(item.get('reason','') for item in row.get('failures',[]))
    needle=_cause_text(exception).split(':',1)[-1].strip()
    return bool(needle and any(_cause_text(value).endswith(needle) for value in current if value))


def collect_defects(book:Path,folder:Path,batch_dir:Path,validation_dir:Path,recompose_report,*,recomposition=None):
    groups={}
    recomposition_dir=Path(recomposition) if recomposition else folder/'input_snapshot'/'book'/'work'/'phase2h'/'recomposition'
    page_rows={row['page']:row for row in recompose_report.get('pages',[])}
    active_pages={r['page'] for r in recompose_report.get('pages',[])
                  if r.get('status') in ('error','fit_failed','retranslation_required',
                                         'native_translation_pending','review')}
    from astro_translate.text_quality import text_issues
    false_positive_ids=set();resolved_validation=[];resolved_glossary=set()
    from astro_translate.glossary_forms import glossary_form_present
    try:
        from astro_translate.emphasis import occurrence_at as _occurrence_at
        occurrence_import_ok=callable(_occurrence_at)
    except ImportError:
        occurrence_import_ok=False
    for path in sorted(validation_dir.glob('*.json')):
        record=read(path,{})
        sources={b.get('id'):b for b in record.get('wire',{}).get('blocks',[])}
        responses={b.get('id'):b.get('translation','') for b in record.get('response',{}).get('blocks',[])}
        for identifier,source in sources.items():
            if identifier not in responses:continue
            target=responses[identifier]
            terms=source.get('glossary',[])
            missing={(identifier,t['source'],t['target']) for t in terms if not glossary_form_present(
                target,t['target'],related_terms=[x['target'] for x in terms if x['target']!=t['target']])}
            previous=set(re.findall(r'approved glossary form missing for id '+re.escape(identifier)+r': ([^;]+?) => ([^;]+?)(?=;|$)',record.get('error','')))
            for term_source,term_target in previous:
                pair=(identifier,term_source.strip(),term_target.strip())
                if pair not in missing:
                    resolved_glossary.add(pair)
                    match=re.search(r'p(\d{4})_',identifier)
                    resolved_validation.append({'page':int(match.group(1)) if match else None,
                        'unit_id':identifier,'reason':f'Approved glossary form replays successfully after grammar/coordination matcher update: {term_source.strip()} => {term_target.strip()}',
                        'validation_file':path.name})
        if 'repeated_ngram' not in record.get('error',''):continue
        source_texts={k:v.get('source','') for k,v in sources.items()}
        for identifier in record.get('ids',[]):
            if identifier not in responses or identifier not in source_texts:continue
            actual=[i for i in text_issues(source_texts[identifier],responses[identifier],identifier)
                    if i.get('code')=='repeated_ngram']
            if not actual:
                false_positive_ids.add(identifier)
                match=re.search(r'p(\d{4})_',identifier)
                resolved_validation.append({'page':int(match.group(1)) if match else None,
                    'unit_id':identifier,'reason':'Saved response repetition is present in source parallelism '
                    'or Spanish function-word scaffolding; local validator replay is clean.',
                    'validation_file':path.name})
    def current_error(text):
        for identifier,source,target in resolved_glossary:
            fragment=f'approved glossary form missing for id {identifier}: {source} => {target}'
            text=text.replace(fragment,'')
        for identifier in false_positive_ids:
            text=re.sub(r'repeated_ngram for id '+re.escape(identifier)+r'(?=;|$)','',text)
        text=re.sub(r';\s*;', ';', text).strip(' ;')
        if re.search(r'(?:ValidationFailure:|:)\s*$',text):return ''
        return text
    def add(code,cause,page,detail,*,action='review',unit_id=None,before=False,after=True):
        key=(code,cause)
        row=groups.setdefault(key,{'code':code,'cause':cause,'pages':set(),'before_pages':set(),'examples':[],
                                    'details':[],'action':action,'unit_ids':[]})
        if page and before:row['before_pages'].add(int(page))
        if page and after:row['pages'].add(int(page))
        if unit_id and unit_id not in row['unit_ids']:row['unit_ids'].append(unit_id)
        if detail and detail not in row['details'] and len(row['details'])<30:row['details'].append(detail)
    for row in recompose_report.get('pages',[]):
        page=row['page'];qa_path=recomposition_dir/f'p{page:04d}-qa.json'
        for failure in row.get('failures',[]):
            add('fit_failure',_cause_text(failure.get('reason','unknown fit failure')),page,failure,
                action='retranslate' if failure.get('action')=='retranslate' else 'recompose',unit_id=failure.get('unit_id'))
        if qa_path.exists():
            qa=json.loads(qa_path.read_text())
            issues=qa.get('text_quality_issues',[])+qa.get('editorial_margin_issues',[])
            for issue in issues:
                code=issue.get('code','text_quality_issue')
                add(code,_cause_text(issue.get('cause',code)),page,issue,
                    action=issue.get('action','retranslate'),unit_id=issue.get('unit_id'))
        if row.get('status') in ('error','fit_failed','retranslation_required','native_translation_pending'):
            add('composition_'+row['status'],_cause_text(row.get('error') or row['status']),page,
                {k:v for k,v in row.items() if k in ('error','failures','retranslation_required')},
                action='retranslate' if row['status']=='retranslation_required' else 'recompose')
    # Cross-page spelling evidence: an accented lower-case form anywhere in
    # the saved book can expose an unaccented all-caps rendering elsewhere.
    from astro_translate.text_quality import WORD, text_issues
    snapshot=folder/'input_snapshot'/'book'/'work'
    saved=[];accent_vocabulary=[]
    for base in (snapshot/'production',snapshot/'pilot'):
        if not base.exists():continue
        for path in sorted(base.glob('p*-translation.json')):
            payload=read(path,{})
            for unit in payload.get('blocks',[]):
                saved.append(unit)
                accent_vocabulary.extend(w.group() for w in WORD.finditer(unit.get('target',''))
                    if w.group()!=w.group().upper() and any(
                        __import__('unicodedata').category(c)=='Mn' for c in
                        __import__('unicodedata').normalize('NFD',w.group())))
    for unit in saved:
        for issue in text_issues(unit.get('source',''),unit.get('target',''),unit.get('id','unknown'),
                                 accent_vocabulary=accent_vocabulary):
            if issue.get('code')=='uppercase_accent':
                match=re.search(r'p(\d{4})',unit.get('id',''))
                page=int(match.group(1)) if match else unit.get('page')
                add('uppercase_accent','Missing accent evidenced elsewhere in saved book',page,issue,
                    action='retranslate',unit_id=unit.get('id'))
    saved_by_id={unit.get('id'):unit for unit in saved if unit.get('id')}
    for batch in sorted(batch_dir.glob('batch-*.json')):
        data=read(batch,{})
        status=data.get('status')
        if status and status!='complete':
            metrics=data.get('metrics',{})
            affected=set(metrics.get('review_pages',[]))
            if not affected:
                affected.update(error['page'] for error in data.get('errors',[]) if error.get('page'))
                for error in data.get('errors',[]):
                    affected.update(int(m.group(1)) for m in re.finditer(r'p(\d{4})_',error.get('exception','')))
            cause=(f'{status}; G5 ratio below threshold' if status=='needs_fix' else status)
            detail={'batch':batch.name,'status':status,'metrics':metrics,
                    'pages':data.get('pages',[]),'errors':len(data.get('errors',[]))}
            for page in sorted(affected):
                add('production_batch_status',cause,page,detail,action='review',before=True,after=False)
                if page in active_pages:
                    add('production_batch_status',cause,page,detail,action='review',before=False,after=True)
        for error in data.get('errors',[]):
            raw_exception=error.get('exception','unknown production error')
            error_ids=set(re.findall(r'p\d{4}_[A-Za-z0-9_]+',raw_exception))
            original_exception=_cause_text(raw_exception)
            if 'repeated_ngram' in raw_exception:
                for identifier in error_ids-false_positive_ids:
                    unit=saved_by_id.get(identifier)
                    if not unit:continue
                    actual=[i for i in text_issues(unit.get('source',''),unit.get('target',''),identifier)
                            if i.get('code')=='repeated_ngram']
                    if not actual:
                        false_positive_ids.add(identifier)
                        match=re.search(r'p(\d{4})_',identifier)
                        resolved_validation.append({'page':int(match.group(1)) if match else error.get('page'),
                            'unit_id':identifier,'reason':'Saved source/target replay is clean; the historical '
                            'repetition already occurs in the source parallelism.','batch_file':batch.name})
            exception=_cause_text(current_error(raw_exception))
            if 'ImportError' in exception and 'occurrence_at' in exception and occurrence_import_ok:
                page=error.get('page') or 137
                add('production_ImportError','Historical concurrent-edit import failure; current occurrence_at import resolves',
                    page,{'batch':batch.name,**error},action='recompose',before=True,after=False)
                continue
            if 'repeated_ngram' in exception and error_ids and error_ids<=false_positive_ids:
                for identifier in error_ids:
                    match=re.search(r'p(\d{4})_',identifier);page=int(match.group(1)) if match else error.get('page')
                    code='production_'+exception.split(':',1)[0].rsplit('.',1)[-1]
                    add(code,exception,page,{'batch':batch.name,**error},action='retranslate',before=True,after=False)
                continue
            if not exception:
                if 'ValidationFailure' in raw_exception:
                    for code_name,cause in _validation_causes(raw_exception):
                        pages=([error['page']] if error.get('page') else
                               [int(re.search(r'p(\d{4})_',identifier).group(1)) for identifier in error_ids])
                        for page in sorted(set(pages)) or [None]:
                            ids=[identifier for identifier in error_ids
                                 if not page or re.match(r'p%04d_'%page,identifier)]
                            add('production_'+code_name,cause,page,{'batch':batch.name,**error},
                                action='retranslate',before=True,after=False,
                                unit_id=ids[0] if len(ids)==1 else None)
                    continue
                original_code='production_'+original_exception.split(':',1)[0].rsplit('.',1)[-1]
                pages=([error['page']] if error.get('page') else
                       [int(re.search(r'p(\d{4})_',identifier).group(1)) for identifier in error_ids])
                for page in sorted(set(pages)) or [None]:
                    add(original_code,original_exception,page,{'batch':batch.name,**error},
                        action='retranslate',before=True,after=False)
                continue
            if 'ValidationFailure' in exception:
                current_causes=set(_validation_causes(exception))
                for code_name,cause in _validation_causes(raw_exception):
                    still_current=(code_name,cause) in current_causes
                    pages=([error['page']] if error.get('page') else
                           [int(re.search(r'p(\d{4})_',identifier).group(1)) for identifier in error_ids])
                    for page in sorted(set(pages)) or [None]:
                        ids=[identifier for identifier in error_ids
                             if not page or re.match(r'p%04d_'%page,identifier)]
                        add('production_'+code_name,cause,page,{'batch':batch.name,**error},
                            action='retranslate',before=True,
                            after=still_current and (page in active_pages if page else True),
                            unit_id=ids[0] if len(ids)==1 else None)
                continue
            code='production_'+exception.split(':',1)[0].rsplit('.',1)[-1]
            error_pages=([error['page']] if error.get('page') else
                         [int(re.search(r'p(\d{4})_',identifier).group(1)) for identifier in error_ids])
            for page in sorted(set(error_pages)) or [None]:
                add(code,exception,page,{'batch':batch.name,**error},
                    action='retranslate' if 'ValidationFailure' in exception else 'recompose',
                    before=True,after=(_current_failure_matches(page,exception,page_rows) if page else True))
    for path in sorted(validation_dir.glob('*.json')):
        record=read(path,{})
        raw_error=record.get('error','saved validation failure')
        original_error=_cause_text(raw_error)
        error=_cause_text(current_error(raw_error))
        validation_ids=record.get('ids') or []
        validation_pages={int(m.group(1)) for identifier in validation_ids
                          if (m:=re.search(r'p(\d{4})_',identifier))}
        if not error:
            for code_name,cause in _validation_causes(raw_error):
                for item in record.get('pages',[]):
                    add('llm_validation_'+code_name,cause,item,{'file':path.name,'ids':record.get('ids',[])},
                        action='retranslate',before=True,after=False,
                        unit_id=validation_ids[0] if len(validation_ids)==1 else None)
            continue
        if 'repeated_ngram' in error and record.get('ids') and set(record['ids'])<=false_positive_ids:
            code='llm_validation_'+(error.split()[0].split(':')[0] if error else 'unknown')
            for identifier in record['ids']:
                match=re.search(r'p(\d{4})_',identifier);page=int(match.group(1)) if match else None
                add(code,error,page,{'file':path.name,'ids':record.get('ids',[])},
                    action='retranslate',before=True,after=False,unit_id=identifier)
            continue
        current_causes=set(_validation_causes(error))
        for code_name,cause in _validation_causes(raw_error):
            for item in record.get('pages',[]):
                add('llm_validation_'+code_name,cause,item,{'file':path.name,'ids':record.get('ids',[])},
                    action='retranslate',before=True,
                    after=(code_name,cause) in current_causes and item in active_pages
                          and (not validation_pages or item in validation_pages),
                    unit_id=validation_ids[0] if len(validation_ids)==1 else None)
    image_by_page={r['page']:r.get('comparison') for r in recompose_report.get('pages',[])}
    # Existing production QA is the before baseline. New local rules naturally
    # have no prior observations; their before count is therefore zero.
    prior=folder/'input_snapshot'/'prior_production'
    for path in sorted(prior.glob('p*-qa.json')):
        qa=read(path,{})
        page=int(re.search(r'p(\d{4})',path.name).group(1))
        for issue in qa.get('text_quality_issues',[])+qa.get('editorial_margin_issues',[]):
            code=issue.get('code','text_quality_issue');cause=_cause_text(issue.get('cause',code))
            add(code,cause,page,issue,action=issue.get('action','review'),unit_id=issue.get('unit_id'),before=True,after=False)
    for path in sorted(prior.glob('p*-fit.json')):
        fit=read(path,{})
        page=int(re.search(r'p(\d{4})',path.name).group(1))
        for failure in fit.get('failures',[]):
            code='fit_failure';cause=_cause_text(failure.get('reason','unknown fit failure'))
            add(code,cause,page,failure,action='recompose',unit_id=failure.get('unit_id'),before=True,after=False)
    defects=[]
    for (code,cause),row in sorted(groups.items(),key=lambda kv:(-len(kv[1]['pages']),kv[0])):
        pages=sorted(row['pages']);before_pages=sorted(row['before_pages'])
        row['pages']=pages;row['page_count']=len(pages)
        row['before_pages']=before_pages;row['before_page_count']=len(before_pages)
        examples=[]
        for page in sorted(set(pages+before_pages)):
            image=image_by_page.get(page)
            if image and Path(image).is_file():examples.append({'page':page,'png':str(Path(image).relative_to(folder))})
            if len(examples)==2:break
        row['examples']=examples
        defects.append(row)
    resolved_validation_groups=_group_resolved_validation(resolved_validation)
    resolved_validation_units=len({(item.get('page'),item.get('unit_id')) for item in resolved_validation_groups})
    recomposition=folder/'input_snapshot'/'book'/'work'/'phase2h'/'recomposition'
    atomic_json(folder/'defects.json',{'book':book.name,'source_sha256':sha256(book/'source.pdf'),
        'recomposition':str(recomposition/'report.json'),'defects':defects,
        'resolved_historical_validation_records':len(resolved_validation),
        'resolved_historical_validation':resolved_validation_groups,
        'resolved_historical_validation_units':resolved_validation_units,
        'current_checks':{'emphasis_occurrence_at_import_ok':occurrence_import_ok}})
    lines=[f'# Defectos agrupados — {book.name}','',
           f'Informe offline; páginas recompuestas: {len(recompose_report.get("pages",[]))}. '
           'Sin llamadas LLM/VLM ni escritura en `work/production`.','',
           '| Código / causa | Páginas (antes → después) | # antes → después | Ejemplos | Acción |','|---|---|---:|---|---|']
    for d in defects:
        links=', '.join(f"[p{e['page']}]({e['png']})" for e in d['examples'])
        pages=', '.join(map(str,d['pages'])) or 'sin página atribuible'
        lines.append(f"| `{d['code']}` — {d['cause']} | {', '.join(map(str,d['before_pages'])) or '—'} → {pages} | {d['before_page_count']} → {d['page_count']} | {links} | {d['action']} |")
    lines += ['', '## Evidencia', '',
              'Cada grupo conserva hasta 30 detalles en `defects.json`; cada fila enlaza hasta dos comparativas lado a lado. '
              'Los errores de producción y validación aparecen aunque una página no tenga traducción guardada. '
              f"Registros históricos que ya pasan al reproducir respuesta/origen y formas de glosario: "
              f"{len(resolved_validation)} registros, agrupados en {len(resolved_validation_groups)} casos "
              f"para {resolved_validation_units} unidades.",'']
    lines += ['## Revisión de incidencias históricas','',
              f"- `occurrence_at` importable en el código actual: **{occurrence_import_ok}**."]
    for item in resolved_validation_groups:
        source_count=len(item['sources'])
        lines.append(f"- p{item['page'] or '?'} `{item['unit_id']}`: {item['reason']} "
                     f"({source_count} archivos históricos).")
    lines.append('')
    (folder/'defects.md').write_text('\n'.join(lines))
    return defects


def build_report(book,output=None):
    book=Path(book).resolve();folder=Path(output or book/'work/phase2h').resolve()
    if not folder.is_relative_to((book/'work').resolve()) or folder==book/'work':
        raise ValueError('Output must be a private directory below this book work/')
    folder.mkdir(parents=True,exist_ok=True)
    snapshot,batch_dir,validation_dir=snapshot_inputs(book,folder)
    recomposition=snapshot/'work'/'phase2h'/'recomposition'
    with __import__('pymupdf').open(book/'source.pdf') as doc:pages=f'1-{len(doc)}'
    rows=recompose(snapshot,pages,recomposition,approval_book=book,retain_candidates=False,prepare_safe_pairs=False)
    published=recomposition
    report={'pages':rows,'llm_calls':0,'vlm_calls':0,'production_executed':False}
    defects=collect_defects(book,folder,batch_dir,validation_dir,report)
    print(json.dumps({'pages_recomposed':len(rows),'defect_groups':len(defects),
                      'defects_md':str(folder/'defects.md'),'llm_calls':0,'vlm_calls':0},ensure_ascii=False))
    return defects


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--book',required=True);parser.add_argument('--output')
    args=parser.parse_args();build_report(args.book,args.output)
