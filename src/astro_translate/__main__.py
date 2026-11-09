"""CLI. Exit codes: 0 ok; 1 doctor found errors; 2 blocked or stopped after three
repeated errors; 75 subscription quota pause (work/quota_pause.json, resume with
produce --resume); 76 G5 block (work/production/g5_block.json; recompose the same
batch or record work/production/g5_decision-<batch>.json)."""
import argparse
import json
import sys
import tomllib
from pathlib import Path
from .core import read_extras, Blocked, triage, reinsert, visual_qa, assemble
from .adapters import doctor, glossary_build, native_translate, shorten_page, masked_visual_judge
from .raster import rebuild
from .llm import QuotaPaused

def main(argv=None):
    parser=argparse.ArgumentParser(description='Gated EN→ES PDF pipeline; source.pdf is immutable')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ['triage','glossary-build','translate-native-pages','fit-and-reinsert','translate-figure-text','scanned-page-rebuild','visual-qa','assemble-book','produce','assemble-final','review-visual','doctor']:
        p=commands.add_parser(name)
        p.add_argument('--book',type=Path,required=name!='doctor'); p.add_argument('--config',type=Path,default=Path('config/pipeline.toml'))
        p.add_argument('--glossary'); p.add_argument('--style'); p.add_argument('--protected')
        if name=='glossary-build': p.add_argument('--api',action='store_true')
        if name=='translate-native-pages':
            p.add_argument('--pages');p.add_argument('--phase',choices=['smoke','reinsertion','fidelity','pilot','production'],default='production')
        if name=='fit-and-reinsert': p.add_argument('--blocks',type=Path,required=True)
        if name in ('translate-figure-text','scanned-page-rebuild'): p.add_argument('--regions',type=Path,required=True)
        if name=='visual-qa': p.add_argument('--candidate',type=Path,required=True); p.add_argument('--edits',type=Path,required=True)
        if name=='assemble-book':
            p.add_argument('--parts',type=Path,required=True); p.add_argument('--toc',type=Path,required=True); p.add_argument('--qa',type=Path,required=True); p.add_argument('--manual-pages',default='')
        if name=='produce':
            p.add_argument('--batch-size',type=int,default=20);p.add_argument('--pages');p.add_argument('--resume',action='store_true');p.add_argument('--recompose',action='store_true');p.add_argument('--dry-run',action='store_true')
        if name=='review-visual':
            p.add_argument('--page',type=int,required=True);p.add_argument('--reviewer',required=True)
            p.add_argument('--candidate-sha256',required=True);p.add_argument('--notes',required=True)
        if name=='assemble-final':p.add_argument('--include-review',action='store_true',help='use current candidates of review pages (translated_unreviewed)')
        if name in ('produce','assemble-final','review-visual'):p.set_defaults(config=None)
        if name=='doctor':
            p.add_argument('--json',action='store_true');p.add_argument('--probe-models',action='store_true')
            p.add_argument('--min-free-gib',type=float);p.add_argument('--warn-free-gib',type=float);p.set_defaults(config=None)
    # Offline structure survey and pseudo-translation; never call an LLM.
    p=commands.add_parser('survey');p.add_argument('--book',type=Path,required=True);p.add_argument('--pages')
    # Re-triage merges the current classifier into an existing manifest; dry run unless --apply.
    p=commands.add_parser('retriage');p.add_argument('--book',type=Path,required=True);p.add_argument('--apply',action='store_true')
    p=commands.add_parser('compose-offline');p.add_argument('--book',type=Path,required=True);p.add_argument('--pages')
    p.add_argument('--pseudo',action='store_true',required=True);p.add_argument('--expansion',type=float,default=1.25)
    args=parser.parse_args(argv)
    if args.command=='retriage':
        from .core import retriage
        try:
            print(json.dumps(retriage(args.book,apply=args.apply),ensure_ascii=False,indent=2));return 0
        except (Blocked,FileNotFoundError,ValueError,KeyError) as exc:
            print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
    if args.command in ('survey','compose-offline'):
        try:
            if args.command=='survey':
                from .survey import survey_book
                r=survey_book(args.book,pages=args.pages)
                result={'survey':str(Path(args.book).resolve()/'work/structure/survey.json'),'pages':len(r['pages']),
                        'archetypes':len(r['archetypes']),'font_risks':len(r['font_risks']),'elapsed_s':r['elapsed_s'],'llm_calls':0}
            else:
                from .pseudo import compose_offline
                r=compose_offline(args.book,pages=args.pages,expansion=args.expansion)
                result={k:v for k,v in r.items() if k!='pages'}
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        except (Blocked,FileNotFoundError,ValueError,KeyError) as exc:
            print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
    if args.command=='doctor':
        from .doctor import cli as doctor_cli
        return doctor_cli(args)
    if args.command in ('produce','assemble-final','review-visual'):
        from .production import produce,assemble_final,record_visual_review,G5Blocked
        try:
            if args.command=='produce' and args.dry_run:
                from .production import preview_production
                result=preview_production(args.book,pages=args.pages,batch_size=args.batch_size,recompose=args.recompose)
            elif args.command=='produce':result=produce(args.book,batch_size=args.batch_size,pages=args.pages,resume=args.resume,config_path=args.config,recompose=args.recompose)
            elif args.command=='assemble-final':result=assemble_final(args.book,config_path=args.config,include_review=args.include_review)
            else:result=record_visual_review(args.book,args.page,args.reviewer,args.candidate_sha256,args.notes,config_path=args.config)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 2 if result.get('status')=='stopped_repeated_error' else G5Blocked.exit_code if result.get('status')=='g5_blocked' else 0
        except QuotaPaused as exc:
            print(json.dumps({'status':'paused','reason':str(exc),'exit_code':75}),file=sys.stderr);return 75
        except G5Blocked as exc:
            print(json.dumps({'status':'g5_blocked','reason':str(exc),'exit_code':G5Blocked.exit_code},ensure_ascii=False),file=sys.stderr);return G5Blocked.exit_code
        except (Blocked,FileNotFoundError,ValueError,KeyError) as exc:
            print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
    config=tomllib.loads(args.config.read_text()) if args.config.exists() else {}; paths=config.get('paths',{})
    g=args.glossary or paths.get('glossary','glossary/astro_en_es.csv'); s=args.style or paths.get('style_guide','glossary/style_guide.md'); d=args.protected or paths.get('do_not_translate','glossary/do_not_translate.txt')
    read=lambda p:json.loads(p.read_text())
    try:
        match args.command:
            case 'doctor': result=doctor()
            case 'triage': result=triage(args.book,config.get('triage',{}))
            case 'glossary-build':
                result=glossary_build(args.book,d,args.api,args.config)
            case 'translate-native-pages': result=native_translate(args.book,g,s,d,args.pages,config_path=args.config,phase=args.phase)
            case 'fit-and-reinsert':
                result=reinsert(args.book,read(args.blocks),g,s,d,font_path=paths.get('font_regular'),layout=config.get('layout'),shortener=lambda blocks:shorten_page(args.book,read(args.blocks)['page'],blocks,args.config))
            case 'translate-figure-text'|'scanned-page-rebuild': result=rebuild(args.book,read(args.regions),g,s,d,font_path=paths.get('font_regular'))
            case 'visual-qa':
                judge=lambda before,after,page:masked_visual_judge(args.book,before,after,args.config,page)
                result=visual_qa(args.book,args.candidate,read(args.edits),g,vlm=judge,do_not_translate=d)
            case 'assemble-book': result=assemble(args.book,read(args.parts),read(args.toc),read(args.qa),[int(x) for x in args.manual_pages.split(',') if x])
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    except QuotaPaused as exc:
        print(json.dumps({'status':'paused','reason':str(exc),'exit_code':75}),file=sys.stderr);return 75
    except (Blocked,FileNotFoundError,ValueError,KeyError) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False),file=sys.stderr); return 2

if __name__=='__main__': sys.exit(main())
