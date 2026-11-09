"""Summarize offline evidence and verification logs; never modify book state."""
import argparse,json,html,re,hashlib
from pathlib import Path
from collections import Counter
from astro_translate.core import atomic_json,sha256


def main():
    p=argparse.ArgumentParser();p.add_argument('--book',required=True);args=p.parse_args()
    book=Path(args.book).resolve();folder=book/'work/phase2e';report=json.loads((folder/'report.json').read_text())
    rows=report['pages'];links=[]
    for row in rows:
        path=folder/'evidence'/f'p{row["page"]:04d}-comparison.png'
        links.append(f'<section id="p{row["page"]}"><h2>PDF {row["page"]}: {html.escape(row["status"])}</h2><a href="{path.name}"><img loading="lazy" src="{path.name}" alt="Comparativa PDF {row["page"]}"></a></section>')
    nav=' '.join(f'<a href="#p{r["page"]}">{r["page"]}</a>' for r in rows)
    page='<!doctype html><html lang="es"><meta charset="utf-8"><title>Fase 2E · comparativas offline</title><style>body{font:16px system-ui;margin:24px;color:#222;background:#eee}nav{position:sticky;top:0;background:white;padding:12px;display:flex;gap:16px}img{max-width:100%;height:auto}section{margin:24px 0}h2{font-size:18px}</style><h1>Fase 2E · original / recomposición</h1><p>Sin inferencia ni VLM. El estado de cada página distingue pendientes, regiones diferidas y QA local. Sin candidato: original a ambos lados.</p><nav>'+nav+'</nav>'+''.join(links)+'</html>'
    (folder/'evidence/index.html').write_text(page)
    before=json.loads((folder/'protected-before.json').read_text());changed=[]
    for file,digest in before.items():
        path=Path(file)
        if not path.is_file() or sha256(path)!=digest:changed.append(file)
    qa=[json.loads((folder/f'p{r["page"]:04d}-qa.json').read_text()) for r in rows if r.get('fit_complete')]
    logs={}
    for name in ['pytest-final-verified.log','book-tests.log','targeted-final.log','golden.log']:
        path=folder/name
        if path.exists():logs[name]={'sha256':sha256(path),'last_lines':path.read_text().splitlines()[-3:]}
    golden=json.loads((folder/'golden/result.json').read_text())
    plan=json.loads((folder/'retranslation-plan.json').read_text())
    verification={'source_sha256':sha256(book/'source.pdf'),'pages':len(rows),'status_counts':dict(Counter(r['status'] for r in rows)),
        'candidate_pages':[r['page'] for r in rows if r.get('fit_complete')],
        'native_local_qa_ok':[r['page'] for r in rows if r.get('native_local_fidelity_ok')],
        'all_candidates_non_text_diff_zero':all(q['non_text_pixel_diff']==0 for q in qa),
        'all_candidates_typography_ok':all(q['typography_audit']['ok'] for q in qa),
        'inference_calls':0,'vlm_calls':0,'commits':0,'production_executed':False,
        'protected_files_compared':len(before),'changed_files':changed,
        'concurrent_cost_log':json.loads((folder/'concurrent-cost-log.json').read_text()),
        'golden_regression_ok':golden['regression_ok'],'golden_changes':[r['page'] for r in golden['pages'] if not r['byte_identical']],
        'final_field_rows_plan_equivalence':json.loads((folder/'golden/final_field_rows_plan_equivalence.json').read_text()),
        'phase2c_comparison':json.loads((folder/'golden/phase2c_comparison.json').read_text()),
        'logs':logs,'jobs':[{'page':j['unit']['page'],'id':j['request']['id'],'kind':'display_title' if j['unit'].get('display_title') else j['kind']} for j in plan['jobs']],
        'evidence_hashes':{p.name:sha256(p) for p in sorted((folder/'evidence').glob('*.png'))}}
    atomic_json(folder/'verification.json',verification)
    print(json.dumps({k:verification[k] for k in ('pages','status_counts','all_candidates_non_text_diff_zero','golden_regression_ok','changed_files')},ensure_ascii=False))

if __name__=='__main__':main()
