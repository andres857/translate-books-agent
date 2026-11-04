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
    for name in ['triage','glossary-build','translate-native-pages','fit-and-reinsert','translate-figure-text','scanned-page-rebuild','visual-qa','assemble-book','doctor']:
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
    args=parser.parse_args(argv)
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
