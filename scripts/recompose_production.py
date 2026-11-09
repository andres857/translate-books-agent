"""Offline recomposition of saved production translations; never inference.

Does not update the production manifest, gates or frozen pilot artifacts.
"""
import argparse,json
from pathlib import Path
import pymupdf as fitz
from PIL import Image,ImageDraw
from astro_translate import fidelity,safe_vlm
from astro_translate.core import atomic_json,sha256
from astro_translate.production import Production,selected_pages,read
from astro_translate.editorial_rules import quote_retranslation_units
from astro_translate.display_titles import title_retranslation_units


def recompose(book,pages,output,approval_book=None,*,retain_candidates=True,prepare_safe_pairs=True):
    runner=Production(book);folder=Path(output).resolve()
    if not folder.is_relative_to(runner.work.resolve()) or folder==runner.folder:
        raise ValueError('Use a separate directory below book/work')
    folder.mkdir(parents=True,exist_ok=True);evidence=folder/'evidence';evidence.mkdir(exist_ok=True)
    reports=[]
    with fitz.open(runner.book/'source.pdf') as source:
        for n in selected_pages(pages,len(source)):
            row={'page':n,'llm_calls':0}
            try:
                page=source[n-1]
                payload=read(runner.path(n,'translation.json'),{})
                if not payload.get('blocks'):
                    payload=read(runner.work/f'pilot/p{n:04d}-translation.json',{})
                if not page.get_text().strip():
                    row.update(status='deferred_raster',deferred_regions=[{'bbox':list(page.rect),'reason':'no_native_text','phase':4}])
                elif not payload.get('blocks'):
                    row.update(status='native_translation_pending',deferred_regions=runner.inventory({'page':n,'route':'C'}))
                    pair=safe_vlm.build_safe_pair(runner.book,runner.book/'source.pdf',runner.book/'source.pdf',n,n,protected_terms=runner.extras)
                    row.update(source_mask_ready=pair.report['ready_for_native_judge'],source_mask_manifest=str(pair.manifest),
                               native_judge_ready=False,reason='No translated candidate; native targets prepared for later inference')
                else:
                    source_units=[{**u,'id':u['id'].removeprefix(f'p{n:04d}_')} for u in payload['blocks']]
                    requests=quote_retranslation_units(page,source_units)+title_retranslation_units(page,source_units)
                    fit=fidelity.reinsert_fidelity(runner.book,payload,runner.glossary,runner.style,runner.protected,
                                                  config=runner.config,artifact_dir=folder,phase='production',
                                                  approval_book=approval_book)
                    row.update(status='composed' if fit['fit_complete'] else 'fit_failed',fit_complete=fit['fit_complete'],failures=fit.get('failures',[]))
                    if any(f.get('reason')=='caption_retranslation_required' for f in fit.get('failures',[])):
                        row['status']='retranslation_required'
                    unresolved=[u for u in requests if not any(set(p['composed_ids'])==set(u['composed_ids']) for p in fit.get('correction_proofs',[]))]
                    if unresolved:row.update(retranslation_required=[u['composed_ids'] for u in unresolved],status='retranslation_required')
                    if fit['fit_complete']:
                        from astro_translate.native_rotated import compose_saved_rotated
                        production_folder=runner.folder
                        runner.folder=folder
                        try:fit=compose_saved_rotated(runner,fit)
                        finally:runner.folder=production_folder
                        from astro_translate.candidate_storage import extract_page
                        extract_page(fit['output'],n,fit['output'],source_index=n-1)
                        fit.update(output_page=0,output_sha256=sha256(fit['output']))
                        atomic_json(folder/f'p{n:04d}-fit.json',fit)
                        qa=fidelity.fidelity_qa(runner.book,fit,runner.glossary,runner.protected,artifact_dir=folder,phase='production',update_manifest=False)
                        regions=runner.inventory({'page':n})
                        unresolved_native=qa.get('native_residual_english',qa['residual_english']) if regions else qa['residual_english']
                        if unresolved_native:
                            row.update(status='retranslation_required',residual_english=unresolved_native)
                        elif regions and qa.get('native_local_fidelity_ok'):
                            row.update(status='partial_deferred')
                        elif not qa['local_fidelity_ok'] and row['status']=='composed':
                            row.update(status='review')
                        row.update(deferred_regions=regions,native_local_fidelity_ok=qa.get('native_local_fidelity_ok'),
                                   text_quality_issues=qa.get('text_quality_issues',[]))
                        fonts=read(folder/f'p{n:04d}-fonts.json',[])
                        if prepare_safe_pairs:
                            pair=safe_vlm.build_safe_pair(runner.book,runner.book/'source.pdf',fit['output'],n,1,
                                  protected_terms=runner.extras,trusted_text_font_files=[r['chosen']['path'] for r in fonts])
                            row.update(safe_pair_manifest=str(pair.manifest),native_judge_ready=pair.report['ready_for_native_judge'],
                                       coverage={k:pair.report[k]['editorial_char_coverage'] for k in ('before','after')})
                        else:
                            row.update(safe_pair_manifest=None,native_judge_ready=False)
                        row.update(candidate=fit['output'],local_fidelity_ok=qa['local_fidelity_ok'],min_scale=qa['min_scale'])
                        qa.update(vlm_complete=False,
                                  vlm_notes=('Not called: offline recomposition; verified safe pair prepared' if prepare_safe_pairs
                                             else 'Not called: offline defect report; safe pair intentionally omitted'),
                                  safe_pair_manifest=(str(pair.manifest) if prepare_safe_pairs else None),
                                  ordinal_mask_verification=(pair.report['ordinal_mask_verification'] if prepare_safe_pairs else None))
                        atomic_json(folder/f'p{n:04d}-qa.json',qa)
            except Exception as exc:row.update(status='error',error=f'{type(exc).__name__}: {exc}')
            before=source[n-1].get_pixmap(dpi=110)
            a=Image.frombytes('RGB',(before.width,before.height),before.samples)
            candidate=read(folder/f'p{n:04d}-fit.json',{})
            if candidate.get('fit_complete'):
                with fitz.open(candidate['output']) as dst:
                    pix=dst[candidate.get('output_page',n-1)].get_pixmap(dpi=110);b=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            else:b=a.copy()
            side=Image.new('RGB',(a.width+b.width,a.height+32),'white');side.paste(a,(0,32));side.paste(b,(a.width,32))
            ImageDraw.Draw(side).text((10,8),f'PDF {n} | ORIGINAL / {row["status"]} | offline, no VLM',fill='black')
            dest=evidence/f'p{n:04d}-comparison.png';side.save(dest);row['comparison']=str(dest)
            if not retain_candidates:
                fit_path=folder/f'p{n:04d}-fit.json';saved_fit=read(fit_path,{})
                candidate=saved_fit.get('output')
                if candidate:Path(candidate).unlink(missing_ok=True)
                if saved_fit:
                    saved_fit['output']=None;saved_fit['candidate_discarded_after_comparison']=True
                    atomic_json(fit_path,saved_fit)
                row['candidate']=None;row['candidate_discarded_after_comparison']=bool(candidate)
            reports.append(row);atomic_json(folder/'report.json',{'pages':reports,'llm_calls':0,'source_sha256':runner.source_hash})
            summary={k:v for k,v in row.items() if k!='deferred_regions'}
            summary['deferred_region_count']=len(row.get('deferred_regions',[]))
            print(json.dumps(summary,ensure_ascii=False),flush=True)
    return reports


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--book',required=True);p.add_argument('--pages',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();recompose(args.book,args.pages,args.output)
