"""Offline pilot golden. With --rebuild, execute the real twenty-page compositor."""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
from astro_translate.core import atomic_json,sha256

ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/'books/the-secrets-of-astrology'
PILOT=BOOK/'work/pilot'
GOLDEN=ROOT/'tests/golden/secrets_pilot.json'


def verify(*,rebuild=False):
    frozen=json.loads(GOLDEN.read_text())
    previous={n:json.loads((PILOT/f'p{int(n):04d}-qa.json').read_text()) for n in frozen}
    if rebuild:
        subprocess.run([sys.executable,str(PILOT/'rebuild_pilot.py'),'--pages',','.join(sorted(frozen,key=int)),'--stage','compose'],cwd=ROOT,check=True)
    rows=[]
    for n,expected in frozen.items():
        fit=json.loads((PILOT/f'p{int(n):04d}-fit.json').read_text())
        qa=json.loads((PILOT/f'p{int(n):04d}-qa.json').read_text())
        actual=sha256(Path(fit['output'])) if fit.get('output') else fit.get('output_sha256')
        same=actual==fit.get('output_sha256')==expected['output_sha256'] and fit['fit_complete']==expected['fit_complete']
        keys=[k for k in previous[n] if k=='vlm' or k.startswith('vlm_')]
        retained=all(qa.get(k)==previous[n][k] for k in keys)
        rows.append({'page':int(n),'expected_sha256':expected['output_sha256'],'actual_sha256':actual,'byte_identical':same,'vlm_evidence_retained':retained})
    report={'rebuild_executed':rebuild,'golden_sha256':sha256(GOLDEN),'source_sha256':sha256(BOOK/'source.pdf'),'llm_calls':0,'pages':rows,'ok':len(rows)==20 and all(r['byte_identical'] and r['vlm_evidence_retained'] for r in rows)}
    if rebuild:atomic_json(BOOK/'work/phase2a/golden_result.json',report)
    if not report['ok']:raise AssertionError([r for r in rows if not r['byte_identical'] or not r['vlm_evidence_retained']])
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--rebuild',action='store_true');args=parser.parse_args()
    result=verify(rebuild=args.rebuild)
    print(json.dumps({'ok':result['ok'],'pages':len(result['pages']),'rebuild_executed':result['rebuild_executed']}))
