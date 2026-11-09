#!/usr/bin/env python3
"""Project measured usage without inventing a subscription token allowance."""
import argparse
import json
from pathlib import Path
import pymupdf
from astro_translate.core import atomic_json
from astro_translate.llm import SUBSCRIPTION_BACKENDS


def row_pages(row):
    return sorted(set(row.get('pages') or ([row['page']] if isinstance(row.get('page'),int) else [])))


def estimate(book,pages=None,phase=None):
    book=Path(book);log=book/'work/costs.jsonl'
    rows=[json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.exists() else []
    for row in rows:
        if row.get('backend') in SUBSCRIPTION_BACKENDS and row.get('cost_usd')==0 and row.get('status') not in ('cache_hit','block_cache_hit','sentence_cache_hit'):
            row['cost_usd']=None;row['cost_status']='unknown_legacy_zero'
    selected=[r for r in rows if r.get('task')=='translate_page' and (phase is None or r.get('phase','legacy')==phase) and (pages is None or set(row_pages(r))&set(pages))]
    measured=[r for r in selected if not r.get('cached') and r.get('status') in ('ok','validation_error')]
    sample=sorted({p for r in measured for p in row_pages(r)})
    with pymupdf.open(book/'source.pdf') as doc:total=len(doc)
    result={'book_pages':total,'sample_pages':sample,'requested_pages':sorted(pages) if pages else None,'phase':phase,'calls':len(measured),'cache_hits':sum(bool(r.get('cached')) for r in selected),'subscription_quota_remaining':None,'subscription_quota_percentage':None,
        'allocation':'Usage is summed once per model call, including shared page calls. Per-page means divide by distinct covered pages; no exact page-level token attribution is claimed.',
        'limitation':'Token/call projection only. ChatGPT quota is not a published token allowance. OCR, labels, shortening and QA are additional work; prompt-cache reuse is observed, not guaranteed.'}
    if not sample:return {**result,'status':'insufficient_measurements'}
    for metric in ('input_tokens','cached_input_tokens','output_tokens','latency_s'):
        known=all(r.get(metric) is not None for r in measured)
        summed=sum(r[metric] for r in measured) if known else None
        average=summed/len(sample) if known else None
        result[metric]={'measured_total':summed,'mean_per_page':average,'projected_book_total':average*total if known else None,'complete':known}
    inputs=result['input_tokens']['measured_total'];cached=result['cached_input_tokens']['measured_total']
    result['uncached_input_tokens_measured']=inputs-cached if inputs is not None and cached is not None else None
    result['projected_calls']=len(measured)/len(sample)*total
    result['backend_counts']={b:sum(r['backend']==b for r in measured) for b in {r['backend'] for r in measured}}
    result['validation_errors']=sum(r.get('status')=='validation_error' for r in measured)
    costs=[r.get('cost_usd') for r in measured]
    result['cost_usd_known']=sum(v for v in costs if isinstance(v,(int,float)))
    result['cost_usd_unknown_records']=sum(v is None for v in costs)
    result['cost_usd_complete']=result['cost_usd_unknown_records']==0
    estimated=[r.get('estimated_cost_usd') for r in measured if r.get('backend')=='claude_cli']
    result['estimated_subscription_usd']={'measured_total':sum(v for v in estimated if isinstance(v,(int,float))),
        'unknown_records':sum(not isinstance(v,(int,float)) for v in estimated),
        'note':'Claude CLI total_cost_usd is an API-equivalent estimate for subscription calls, not an incremental charge.'}
    result['subscription_cost_note']='Subscription calls (Codex, Claude CLI) have no incremental API charge; subscription price and remaining capacity are not estimated.'
    return result


def compare(book,pages,before='legacy',after='reinsertion'):
    old=estimate(book,pages,before);new=estimate(book,pages,after)
    result={'before':old,'after':new,'comparison_note':'Same selected sample; historical cached-input tokens remain unknown when they were not recorded.'}
    for metric in ('calls','input_tokens','cached_input_tokens','output_tokens'):
        a=old.get(metric);b=new.get(metric)
        if isinstance(a,dict):a=a.get('measured_total')
        if isinstance(b,dict):b=b.get('measured_total')
        result[metric+'_delta']=b-a if isinstance(a,(int,float)) and isinstance(b,(int,float)) else None
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--book',type=Path,required=True);parser.add_argument('--pages');parser.add_argument('--phase');parser.add_argument('--compare-before');parser.add_argument('--output',type=Path);args=parser.parse_args()
    selected={int(x) for x in args.pages.split(',')} if args.pages else None
    result=compare(args.book,selected,args.compare_before,args.phase or 'reinsertion') if args.compare_before else estimate(args.book,selected,args.phase)
    if args.output:atomic_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
