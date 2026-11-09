"""Plan source-bound figure batches offline; --execute explicitly translates.

Dates resolve locally. All remaining figure labels in one page travel together
through run_task, which masks protected values before any backend sees them.
"""
import argparse
from pathlib import Path
import pymupdf as fitz
from astro_translate.core import atomic_json, sha256, Blocked, book_lock
from astro_translate.production import Production, selected_pages, digest, read
from astro_translate.native_rotated import extract_straight_labels, label_requests


def save_answers(runner,row,answers):
    """Commit only against the same source/dependencies observed before inference."""
    with book_lock(runner.book):
        dependencies={name:sha256(path) for name,path in [('glossary',runner.glossary),('style',runner.style),('protected',runner.protected),('config',runner.config_path)]}
        if dependencies!=row['dependencies'] or sha256(runner.book/'source.pdf')!=runner.source_hash:
            raise Blocked('Rotated label dependencies changed during request; answers not installed')
        atomic_json(runner.path(row['page'],'rotated.json'),{'identity':row['identity'],
                    'dependencies':row['dependencies'],'answers':answers})


def prepare(book,pages,plan,execute=False):
    runner=Production(book)
    if execute:runner.gates()
    rows=[]
    with fitz.open(runner.book/'source.pdf') as source:
        for n in selected_pages(pages,len(source)):
            units=extract_straight_labels(source[n-1],runner.extras)
            if not units:continue
            requests,local=label_requests(units)
            identity=digest({'source_sha256':runner.source_hash,'units':units})
            dependencies={name:sha256(path) for name,path in [('glossary',runner.glossary),('style',runner.style),('protected',runner.protected),('config',runner.config_path)]}
            row={'page':n,'identity':identity,'dependencies':dependencies,'units':units,
                 'figure_batches':[{'labels':requests}] if requests else [],'deterministic_answers':local}
            rows.append(row)
    prepared={'source_sha256':runner.source_hash,'pages':rows,'inference_executed':False}
    if execute:
        previous=read(plan,{})
        if previous!=prepared:raise Blocked('Rotated label plan changed; prepare and inspect the current plan first')
        from astro_translate import llm
        for row in rows:
            answers=dict(row['deterministic_answers'])
            for batch in row['figure_batches']:
                result=llm.run_task('translate_labels',{'_book':str(runner.book),'_config':str(runner.config_path),
                    '_phase':'production','_pages':[row['page']],**batch})
                answers.update({r['id']:r['translation'] for r in result.data['labels']})
            if set(answers)!={u['id'] for u in row['units']}:raise Blocked('Rotated label ids changed')
            save_answers(runner,row,answers)
    else:
        with book_lock(runner.book):atomic_json(plan,prepared)
    return prepared


def main(argv=None):
    from astro_translate.llm import QuotaPaused
    parser=argparse.ArgumentParser();parser.add_argument('--book',required=True)
    parser.add_argument('--pages',required=True);parser.add_argument('--plan',required=True)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args(argv)
    try:
        plan=prepare(args.book,args.pages,Path(args.plan),args.execute)
    except QuotaPaused as exc:
        # run_task already persisted quota_pause.json and affected page state.
        print(str(exc))
        return 75
    print(f'{len(plan["pages"])} pages with complete straight rotated labels')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
