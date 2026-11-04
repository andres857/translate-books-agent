#!/usr/bin/env python3
"""Bounded backend/reinsertion sample; never authorizes pilot or production."""
import argparse
import json
import sys
import tomllib
from pathlib import Path
from astro_translate.adapters import native_translate
from astro_translate.core import atomic_json,Blocked
from astro_translate.llm import QuotaPaused
from estimate_quota import compare


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--book',type=Path,required=True);parser.add_argument('--pages',default='7,15,121');parser.add_argument('--phase',choices=['smoke','reinsertion','fidelity'],default='smoke');parser.add_argument('--config',type=Path,default=Path('config/pipeline.toml'));parser.add_argument('--no-reinsert',action='store_true');args=parser.parse_args(argv)
    cfg=tomllib.loads(args.config.read_text());paths=cfg['paths']
    try:
        result=native_translate(args.book,paths['glossary'],paths['style_guide'],paths['do_not_translate'],args.pages,config_path=args.config,phase=args.phase,reinsert_pages=not args.no_reinsert)
        folder=args.book/'work'/(args.phase if args.phase in ('reinsertion','fidelity') else 'backend_smoke')
        atomic_json(folder/'report.json',result)
        if args.phase in ('reinsertion','fidelity'):
            selected={int(p) for p in args.pages.split(',')};atomic_json(folder/'quota_comparison.json',compare(args.book,selected,after=args.phase))
        # Full unit contents remain in local artifacts; terminal output is metadata.
        print(json.dumps({'phase':args.phase,'batches':result['batches'],'pages':[{k:v for k,v in page.items() if k!='fit'}|{'fit_status':page.get('fit',{}).get('status'),'fit_reason':page.get('fit',{}).get('reason')} for page in result['pages']]},ensure_ascii=False,indent=2))
        return 0
    except QuotaPaused as exc:print(str(exc),file=sys.stderr);return 75
    except Blocked as exc:print(str(exc),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
