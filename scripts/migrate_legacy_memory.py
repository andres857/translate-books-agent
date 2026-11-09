"""Migrate the exact current legacy adapter context, locally and without inference."""
import argparse,hashlib,json,re,sqlite3,tomllib
from pathlib import Path
from astro_translate.core import book_lock,atomic_json,sha256
from astro_translate.llm import ROOT,validate_cached_translation,ValidationFailure
from astro_translate.consistency import STYLE_MARKER
from astro_translate.translation_context import (digest,context_data,legacy_memory_context,
    translation_context,migrate_memory,migrate_block_cache,migrate_responses)


def migrate(book,config_path):
    book=Path(book).resolve();work=book/'work';cfg=tomllib.loads(Path(config_path).read_text())
    def resolve(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    g,s,d=(resolve(cfg['paths'][k]) for k in ('glossary','style_guide','do_not_translate'))
    old=legacy_memory_context(cfg,g,s,d);new=translation_context(cfg,g,s,d)
    schema=json.loads(resolve(cfg['tasks']['translate_page']['schema']).read_text())
    out=work/'phase2a';out.mkdir(exist_ok=True)
    with book_lock(book):
        db=sqlite3.connect(work/'llm_cache.sqlite');backup=out/'llm_cache_before.sqlite'
        if not backup.exists():
            dest=sqlite3.connect(backup);db.backup(dest);dest.close()
        before={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('sentence_memory','blocks','responses')}
        entries=[];requests=[];sources={}
        for source,raw in db.execute('SELECT source,entry FROM sentence_memory WHERE context=?',(old,)):
            saved=json.loads(raw);markers=STYLE_MARKER.findall(saved['marked_source']);key=hashlib.sha256(source.encode()).hexdigest()[:20]
            entry={'id':'memory_'+key,'source':source,'marked_source':saved['marked_source'],'markers':markers,'styles':[{} for _ in markers[:-1]]}
            entries.append(entry);row={'id':entry['id'],'source':entry['marked_source']}
            if markers:row['_style_markers']=markers
            requests.append(row);sources[key]=row
        # Response ids retain the canonical source hash; exact old-key matching
        # proves each proposed request before any response is migrated.
        for raw, in db.execute('SELECT result FROM responses'):
            for row in json.loads(raw).get('data',{}).get('blocks',[]):
                match=re.search(r'_s([a-f0-9]{20})$',row['id'])
                if match and match[1] in sources:requests.append({**sources[match[1]],'id':row['id']})
        db.close()
        def validate(entry,target):
            row={'id':entry['id'],'source':entry['marked_source']}
            if entry['markers']:row['_style_markers']=entry['markers']
            answer={'id':row['id'],'translation':target,'glossary_terms_used':[],'grammatical_corrections':[]}
            try:validate_cached_translation('translate_page',{'blocks':[answer]},{'blocks':[row]},g,s,d,schema)
            except ValidationFailure as exc:raise ValueError(str(exc)) from exc
        report={'source_sha256':sha256(book/'source.pdf'),'semantic_dependencies':context_data(cfg,g,s,d),'llm_calls':0,'old_rows_deleted':0}
        report['memory']=migrate_memory(work,old,new,entries,validate)
        report['blocks']=migrate_block_cache(work,cfg,g,s,d,{'blocks':list({r['id']:r for r in requests}.values())})
        report['responses']=[]
        for plan_path in (work/'fidelity/sentence_plan.json',work/'pilot/sentence_plan.json'):
            if plan_path.exists():
                plan=json.loads(plan_path.read_text())
                report['responses'].append(migrate_responses(work,cfg,g,s,d,{'blocks':plan['requests']}))
        db=sqlite3.connect(work/'llm_cache.sqlite');after={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in before};db.close()
        report.update(before=before,after=after)
        atomic_json(out/'cache_migration.json',report)
    print(json.dumps({'before':before,'after':after,'memory_copied':report['memory']['copied'],'memory_invalid':len(report['memory']['invalid']),'blocks_copied':report['blocks']['copied']}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('book');parser.add_argument('--config',default='config/pipeline.toml');a=parser.parse_args();migrate(a.book,a.config)
