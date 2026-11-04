#!/usr/bin/env python3
"""Judge only verified native editorial images; do not claim full-image QA."""
import argparse,json,tomllib
from pathlib import Path
from astro_translate.core import Blocked,read_extras,atomic_json,sha256
from astro_translate.safe_vlm import build_safe_pair,judge_safe_pair
from astro_translate.llm import QuotaPaused


def main():
    p=argparse.ArgumentParser();p.add_argument('--book',type=Path,default=Path('books/astrology-wisdom-stars'));p.add_argument('--config',type=Path,default=Path('config/pipeline.toml'));p.add_argument('--pages',required=True);a=p.parse_args();folder=a.book/'work/pilot';cfg=tomllib.loads(a.config.read_text());extras=[*read_extras(cfg['paths']['do_not_translate']),'st','nd','rd','th']
    for n in map(int,a.pages.split(',')):
        qp=folder/f'p{n:04d}-qa.json';fp=folder/f'p{n:04d}-fit.json'
        if not fp.exists():continue
        fit=json.loads(fp.read_text());qa=json.loads(qp.read_text()) if qp.exists() else {'page':n,'verdict':'revisar'}
        if not fit.get('fit_complete'):continue
        snapshot=sha256(fit['output'])
        if snapshot!=fit.get('output_sha256'):
            print(json.dumps({'page':n,'blocked':'Stale fit metadata; wait for compositor'}),flush=True);continue
        try:
            fonts=folder/f'p{n:04d}-fonts.json';trusted=[r['chosen']['path'] for r in json.loads(fonts.read_text())] if fonts.exists() else []
            trusted+=fit.get('extra_trusted_font_files',[])
            pair=build_safe_pair(a.book,a.book/'source.pdf',fit['output'],n,fit.get('output_page',n-1)+1,protected_terms=extras,trusted_text_font_files=trusted)
            judge=judge_safe_pair(a.book,pair,a.config,phase='pilot')
            if sha256(fit['output'])!=snapshot:
                print(json.dumps({'page':n,'blocked':'Candidate changed during VLM; result remains historical only'}),flush=True);continue
            qa['vlm_complete']=True;qa['vlm_scope']='native_editorial_only';qa['vlm']=judge;qa['vlm_notes']=judge['native_vlm']['notes'];qa['verdict']='revisar';qa['human_pilot_visual_review_pending']=True
            atomic_json(qp,qa);print(json.dumps({'page':n,'verdict':judge['native_vlm']['verdict'],'coverage':judge['coverage'],'cache':judge['cached']},ensure_ascii=False),flush=True)
        except QuotaPaused:raise
        except (Blocked,ValueError) as exc:
            qa['vlm_complete']=False;qa['vlm_notes']=str(exc);qa['verdict']='revisar';atomic_json(qp,qa);print(json.dumps({'page':n,'blocked':str(exc)},ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    except QuotaPaused as e:print(str(e));raise SystemExit(75)
