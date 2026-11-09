"""Real golden replay with restoration of the approved pilot after comparison.

The existing replay runner is used verbatim, with its inference guard. Changed
candidates and fresh QA are retained separately; frozen expectations never change.
"""
import argparse,json,shutil,subprocess,sys
from pathlib import Path
from astro_translate.core import sha256,atomic_json

ROOT=Path(__file__).resolve().parents[1];BOOK=ROOT/'books/the-secrets-of-astrology'
WORK=BOOK/'work';PILOT=WORK/'pilot';OUT=WORK/'phase2c/golden';BACKUP=OUT/'snapshot'

def comparison_row(number, expected, pilot, backup):
    fit=json.loads((pilot/f'p{number:04d}-fit.json').read_text())
    candidate=Path(fit['output']) if fit.get('output') else None
    actual=sha256(candidate) if candidate and candidate.is_file() else None
    qa=json.loads((pilot/f'p{number:04d}-qa.json').read_text())
    previous=json.loads((backup/f'pilot/p{number:04d}-qa.json').read_text())
    keys=[k for k in previous if k=='vlm' or k.startswith('vlm_')]
    return {'page':number,'expected_sha256':expected['output_sha256'],'actual_sha256':actual,
        'byte_identical':actual==fit.get('output_sha256')==expected['output_sha256'] and fit.get('fit_complete')==expected['fit_complete'],
        'vlm_evidence_retained':all(qa.get(k)==previous[k] for k in keys),
        'local_fidelity_ok':qa.get('local_fidelity_ok'),'fit_complete':bool(fit.get('fit_complete')),
        'fit_failures':fit.get('failures',[])}


def copy_changed_artifacts(row,pilot,out):
    if row['byte_identical']:return
    n=row['page'];dest=out/f'p{n:04d}';dest.mkdir(parents=True,exist_ok=True)
    fit=json.loads((pilot/f'p{n:04d}-fit.json').read_text())
    files=[pilot/f'p{n:04d}-fit.json',pilot/f'p{n:04d}-qa.json',pilot/f'renders/p{n:04d}-comparison.png']
    for path in files:
        if path.is_file():shutil.copy2(path,dest/path.name)
    if fit.get('output'):
        import pymupdf as fitz
        candidate=Path(fit['output']);target=dest/f'p{n:04d}-translated-page.pdf'
        with fitz.open(candidate) as full,fitz.open() as page:
            page.insert_pdf(full,from_page=fit.get('output_page',n-1),to_page=fit.get('output_page',n-1))
            page.save(target,garbage=4,deflate=True,clean=True)
        fit.update(output=str(target),output_page=0,output_sha256=sha256(target))
        atomic_json(dest/f'p{n:04d}-fit.json',fit)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--expected-changes',default='')
    parser.add_argument('--output',type=Path,default=OUT)
    args=parser.parse_args();expected_changes={int(n) for n in args.expected_changes.split(',') if n}
    out=args.output.resolve()
    if not out.is_relative_to(WORK.resolve()) or out==PILOT.resolve():
        raise ValueError('Golden evidence must use a separate directory below book/work')
    backup=out/'snapshot'
    if backup.exists():raise RuntimeError('Snapshot exists: inspect/recover it before replay')
    backup.mkdir(parents=True)
    files=[p for p in PILOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'history' not in p.parts]
    files += [p for p in (WORK/'manifest.json',WORK/'review_queue.json',WORK/'phase2a/golden_result.json') if p.exists()]
    original={str(p.relative_to(WORK)):sha256(p) for p in files}
    atomic_json(out/'snapshot_hashes.json',original)
    for p in files:
        dest=backup/p.relative_to(WORK);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    try:
        with (out/'rebuild.log').open('w') as stream:
            status=subprocess.run([sys.executable,str(PILOT/'rebuild_pilot.py'),'--pages',','.join(json.loads((ROOT/'tests/golden/secrets_pilot.json').read_text())),'--stage','compose'],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT).returncode
        frozen=json.loads((ROOT/'tests/golden/secrets_pilot.json').read_text())
        rows=[comparison_row(int(n),expected,PILOT,backup) for n,expected in frozen.items()]
        changed={r['page'] for r in rows if not r['byte_identical']}
        ok=status==0 and changed==expected_changes and all(r['vlm_evidence_retained'] if r['byte_identical'] else r['local_fidelity_ok'] for r in rows)
        report={'runner_exit_code':status,'fully_rebuilt':status==0,'pages':rows,'llm_calls':0,
                'expected_changes':sorted(expected_changes),'regression_ok':ok,
                'golden_sha256':sha256(ROOT/'tests/golden/secrets_pilot.json')}
        # Persist the honest result before copying optional candidate evidence.
        atomic_json(out/'result.json',report)
        for row in rows:copy_changed_artifacts(row,PILOT,out)
    finally:
        for rel,h in original.items():
            target=WORK/rel
            if not target.exists() or sha256(target)!=h:
                if 'approval' in target.name:raise RuntimeError('Unexpected approval mutation; do not overwrite')
                shutil.copy2(backup/rel,target)
        restored=all((WORK/rel).exists() and sha256(WORK/rel)==h for rel,h in original.items())
        atomic_json(out/'restoration.json',{'restored':restored,'files':len(original)})
        if restored:shutil.rmtree(backup)
    print(json.dumps({'identical':[r['page'] for r in report['pages'] if r['byte_identical']],
                      'changed':[r['page'] for r in report['pages'] if not r['byte_identical']],
                      'pilot_restored':restored,'regression_ok':report['regression_ok']}))
    if not report['regression_ok']:raise SystemExit(1)

if __name__=='__main__':main()
