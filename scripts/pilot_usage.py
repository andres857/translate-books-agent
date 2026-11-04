#!/usr/bin/env python3
"""Summarize actual pilot service usage, preserving unknown error usage."""
import argparse,json
from pathlib import Path
from astro_translate.core import atomic_json


def summarize(rows):
    calls=[r for r in rows if not r.get('cached')]
    metrics={}
    for key in ('input_tokens','output_tokens','cached_input_tokens','latency_s'):
        known=[r[key] for r in calls if isinstance(r.get(key),(int,float))]
        metrics[key]={'known_sum':sum(known),'unknown_calls':len(calls)-len(known),'complete':len(known)==len(calls)}
    return {'calls':len(calls),'cache_events':sum(bool(r.get('cached')) for r in rows),'statuses':{s:sum(r.get('status')==s for r in rows) for s in sorted({r.get('status','unknown') for r in rows})},'metrics':metrics,'models':sorted({r.get('model','unknown') for r in calls}),'backends':sorted({r.get('backend','unknown') for r in calls})}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--book',type=Path,default=Path('books/astrology-wisdom-stars'));args=parser.parse_args();book=args.book;folder=book/'work/pilot'
    all_rows=[json.loads(l) for l in (book/'work/costs.jsonl').read_text().splitlines() if l.strip()];rows=[r for r in all_rows if r.get('phase')=='pilot']
    selection=json.loads((folder/'selection.json').read_text());baseline=json.loads((folder/'projection-before.json').read_text());native=set(selection['native_pages'])
    native_rows=[r for r in rows if r.get('task')=='translate_page' and set(r.get('pages') or [r.get('page')])&native]
    bytask={task:summarize([r for r in rows if r.get('task')==task]) for task in sorted({r['task'] for r in rows})}
    expected={metric:baseline[metric]['mean_per_page']*len(native) for metric in ('input_tokens','output_tokens','latency_s')}
    native_summary=summarize(native_rows)
    result={'all_primary_book':summarize(rows),'by_task':bytask,'native_translation':native_summary,'projection_before_native_pages':len(native),'projection_before':expected,'baseline':'Measured Fase1d pages7/15/121. Different selection, additional editorial metadata and new style hash; not a controlled benchmark.','native_actual_minus_projection':{k:native_summary['metrics'][k]['known_sum']-v for k,v in expected.items()},'subscription_quota_remaining':None,'paid_api_used':any(r.get('backend')=='openai_api' for r in rows),'notes':['Unknown usage from failed requests is not zero; known sums are lower bounds.','Latency sums are service time, not wall-clock duration.','OCR, rendering and local tests are outside token projection.','External Mercury appendix is accounted separately.']}
    result['vlm_probe']=summarize([r for r in all_rows if r.get('phase')=='pilot_vlm_probe'])
    annex=Path('books/mercury-technical-annex/work/costs.jsonl')
    if annex.exists():result['external_annex']=summarize([json.loads(l) for l in annex.read_text().splitlines() if l.strip() and json.loads(l).get('phase')=='pilot'])
    atomic_json(folder/'usage-report.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
