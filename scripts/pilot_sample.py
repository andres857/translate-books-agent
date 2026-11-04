#!/usr/bin/env python3
"""Bounded pilot: translate, compose and assemble the explicitly selected pages."""
import argparse,json,tomllib,time,traceback
from pathlib import Path
import pymupdf as fitz
from astro_translate.core import atomic_json,sha256,Blocked,require_approval,_save_pdf,book_lock,read_extras
from astro_translate.adapters import native_translate,require_backend_approval
from astro_translate.fidelity import reinsert_fidelity,fidelity_qa,extract_fidelity_units
from astro_translate.llm import QuotaPaused


def user_corrections(payload):
    corrections=[]
    for block in payload['blocks']:
        if payload['page']==121 and block['source']=='Where the paths of the Sun and Moon cross in space. Symbolizes fated meetings and experiences and the balancing of past and future.':
            before=block['target']
            for old,new in [('Simboliza','Simbolizan'),('simboliza','simbolizan')]:
                import re
                block['target']=re.sub(r'\b'+old+r'\b',new,block['target'])
                for run in block['target_runs']:run['text']=re.sub(r'\b'+old+r'\b',new,run['text'])
            corrections.append({'id':block['id'],'source':block['source'],'source_context':'Nodes','before':before,'after':block['target'],'reason':'Concordancia con Nodos; corrección explícita del usuario','changed':before!=block['target']})
    payload['editorial_corrections']=corrections
    return payload


def rebase_payload(book,payload,extras):
    """Keep newly protected standalone ordinal suffixes as original PDF text."""
    def key(unit):return (unit['source'],json.dumps(unit['spans'],sort_keys=True))
    bykey={key(u):u for u in payload['blocks']}
    with fitz.open(Path(book)/'source.pdf') as doc:canonical=extract_fidelity_units(doc[payload['page']-1],extras)
    revised=[]
    for own in canonical:
        old=bykey.pop(key(own),None)
        if old is None:raise Blocked('Extraction changed beyond protected ordinal suffixes')
        revised.append({**old,**own,'id':f"p{payload['page']:04d}_"+own['id'],'target':old['target'],'target_runs':old['target_runs']})
    omitted=list(bykey.values())
    if any(u['source'].lower() not in ('st','nd','rd','th') for u in omitted):raise Blocked('Unapproved payload omission')
    payload['blocks']=revised;payload['protected_ordinal_suffixes']=payload.get('protected_ordinal_suffixes',[])+[{'source':u['source'],'bbox':u['bbox'],'reason':'Preserve original house-number ordinal notation; no translated replacement'} for u in omitted]
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--book',type=Path,default=Path('books/astrology-wisdom-stars'));parser.add_argument('--config',type=Path,default=Path('config/pipeline.toml'));parser.add_argument('--stage',choices=['translate','compose','assemble'],required=True);parser.add_argument('--pages');args=parser.parse_args()
    book=args.book;folder=book/'work/pilot';selection=json.loads((folder/'selection.json').read_text());config=tomllib.loads(args.config.read_text());paths=config['paths']
    require_approval(book,paths['glossary'],paths['style_guide'],paths['do_not_translate']);require_backend_approval(book,args.config,'pilot')
    pages=[int(n) for n in args.pages.split(',')] if args.pages else selection['native_pages']
    if not set(pages)<=set(selection['pages']):raise Blocked('Pages outside human pilot scope')
    if args.stage=='translate':
        report=native_translate(book,paths['glossary'],paths['style_guide'],paths['do_not_translate'],','.join(map(str,pages)),config_path=args.config,phase='pilot',reinsert_pages=False)
        atomic_json(folder/'translation-report.json',report)
        for n in pages:
            path=folder/f'p{n:04d}-translation.json';payload=user_corrections(rebase_payload(book,json.loads(path.read_text()),read_extras(paths['do_not_translate'])));atomic_json(path,payload)
        print(json.dumps({'translated_pages':pages,'batches':report['batches']},ensure_ascii=False));return
    if args.stage=='compose':
        for number in pages:
            started=time.monotonic();payload_path=folder/f'p{number:04d}-translation.json'
            try:
                payload=user_corrections(rebase_payload(book,json.loads(payload_path.read_text()),read_extras(paths['do_not_translate'])));atomic_json(payload_path,payload)
                override=folder/f'p{number:04d}-composition-translations.json'
                fit=reinsert_fidelity(book,payload,paths['glossary'],paths['style_guide'],paths['do_not_translate'],config,artifact_dir=folder,phase='pilot',composition_overrides=override if number==146 and override.exists() else None)
                if not fit['fit_complete']:raise Blocked('Fit failed: '+','.join(x['id'] for x in fit['failures']))
                qa=fidelity_qa(book,fit,paths['glossary'],paths['do_not_translate'],artifact_dir=folder,phase='pilot');qa['editorial_corrections']=payload.get('editorial_corrections',[])
                records=folder/'model-editorial-corrections.json'
                if records.exists():qa['editorial_corrections'] += [x for x in json.loads(records.read_text()) if number in x['pages']]
                atomic_json(folder/f'p{number:04d}-qa.json',qa)
                print(json.dumps({'page':number,'fit':True,'local_qa':qa['local_fidelity_ok'],'scale':qa['min_scale'],'seconds':round(time.monotonic()-started,2)},ensure_ascii=False),flush=True)
            except (Blocked,ValueError,KeyError,FileNotFoundError) as exc:
                atomic_json(folder/f'p{number:04d}-incident.json',{'page':number,'stage':'compose','reason':str(exc),'traceback':traceback.format_exc()})
                print(json.dumps({'page':number,'error':str(exc)},ensure_ascii=False),flush=True)
        return
    # Preserve transparency groups, including when each side becomes a PDF Form.
    from astro_translate.pilot_assembly import assemble
    rows=[];assembly_rows=[]
    for number in selection['pages']:
        fit_path=folder/f'p{number:04d}-fit.json';fit=json.loads(fit_path.read_text()) if fit_path.exists() else {}
        output=Path(fit.get('output',''));valid=fit.get('fit_complete') and output.is_file() and sha256(output)==fit.get('output_sha256')
        qa_path=folder/f'p{number:04d}-qa.json';qa=json.loads(qa_path.read_text()) if qa_path.exists() else {}
        label='Piloto - traduccion para revision' if valid else 'INCIDENCIA - original sin traducir'
        assembly_rows.append({'source_page':number,'candidate':str(output) if valid else str(book/'source.pdf'),'index':fit.get('output_page',number-1) if valid else number-1,'label':label})
        rows.append({'page':number,'translated':bool(valid),'qa':qa,'fit':str(fit_path) if fit else None})
    out=book/'out/piloto.pdf';comp=book/'out/piloto_comparativa.pdf'
    proof=assemble(book/'source.pdf',assembly_rows,out,comp)
    with fitz.open(comp) as doc:
        renders=folder/'comparisons';renders.mkdir(exist_ok=True)
        for index,number in enumerate(selection['pages']):doc[index].get_pixmap(dpi=110).save(renders/f'p{number:04d}.png')
    atomic_json(folder/'assembly-proof.json',proof)
    atomic_json(folder/'pilot-report.json',{'pages':rows,'source_sha256':sha256(book/'source.pdf'),'output':str(out),'output_sha256':sha256(out),'comparison':str(comp),'comparison_sha256':sha256(comp),'assembly_proof':str(folder/'assembly-proof.json'),'awaiting_human_visual_review':True,'production_authorized':False})
    print(json.dumps({'pages':len(rows),'translated':sum(x['translated'] for x in rows),'output':str(out),'comparison':str(comp)},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except QuotaPaused as exc:print(str(exc));raise SystemExit(75)
