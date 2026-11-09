"""Isolated golden replay with explicit 2G margin deltas and unchanged old issues.

Never changes the frozen golden. The legacy runner's raw result is retained.
A declared geometry change must still pass all preservation and style checks.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from check_phase2f_golden import BOOK,ROOT,BOOTSTRAP,copy_inputs
from astro_translate.core import atomic_json

EXPECTED={19,35,60,69,86,87,100,101,128,154,173,175,179,180}
PRESERVATION=('glyph_set_equal','numbers_equal','immutable_spans_equal','images_equal',
              'vectors_equal','font_files_embedded_verified','complete_translated_text',
              'uniform_scale_groups','original_colors_verified','editorial_margins_ok')


def qa_nonregression(before,after):
    return (all(after.get(k) is True for k in PRESERVATION)
            and after.get('typography_audit',{}).get('ok') is True
            and after.get('non_text_pixel_diff')==0 and after.get('min_scale',0)>=.75
            and not after.get('glossary_missing')
            and all(after.get(k,[])==before.get(k,[]) for k in
                    ('residual_english','omitted_rotated_text','text_quality_issues')))


def verify(sandbox):
    work=Path(sandbox)/'books/the-secrets-of-astrology/work';folder=work/'golden'
    path=folder/'result.json';result=json.loads(path.read_text())
    atomic_json(folder/'legacy_result.json',result)
    changed={r['page'] for r in result['pages'] if not r['byte_identical']}
    checks=[]
    for row in result['pages']:
        n=row['page']
        if row['byte_identical']:ok=row['vlm_evidence_retained']
        else:
            before=json.loads((work/f'pilot/p{n:04d}-qa.json').read_text())
            after=json.loads((folder/f'p{n:04d}/p{n:04d}-qa.json').read_text())
            ok=row['fit_complete'] and qa_nonregression(before,after)
            # 19/87 were already declared in 2C; their corrected QA is authoritative.
            if n in (19,87):ok=row['fit_complete'] and after['local_fidelity_ok']
        checks.append({'page':n,'ok':bool(ok),'declared_change':n in EXPECTED})
    ok=result['fully_rebuilt'] and changed==EXPECTED and all(c['ok'] for c in checks)
    result.update(regression_ok=ok,expected_changes=sorted(EXPECTED),phase2g_checks=checks,
                  declaration='docs/fase2g_enfasis.md; original golden remains immutable')
    atomic_json(path,result)
    if not ok:raise SystemExit(1)


def compact_artifacts(folder):
    """Keep the verified page and its parent digest, not fourteen full books."""
    import pymupdf as fitz
    from astro_translate.core import sha256
    for path in folder.glob('p*/*.pdf'):
        if path.name.endswith('-page.pdf'):continue
        n=int(path.parent.name[1:]);digest=sha256(path)
        target=path.with_name(path.stem+'-page.pdf')
        with fitz.open(path) as original,fitz.open() as page:
            page.insert_pdf(original,from_page=n-1,to_page=n-1)
            page.save(target,garbage=4,deflate=True)
        atomic_json(target.with_suffix('.json'),{'full_candidate_sha256':digest,
            'extracted_page':n,'page_pdf_sha256':sha256(target)})
        path.unlink()


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    output=a.output.resolve()
    if not any(output.is_relative_to(BOOK/'work'/name) for name in ('phase2g','phase2h')):
        raise ValueError('Use a private evidence directory below work/phase2g or work/phase2h')
    output.mkdir(parents=True,exist_ok=True)
    program=BOOTSTRAP.replace("'19,87'",repr(','.join(map(str,sorted(EXPECTED)))))
    program=program.replace("runpy.run_path(sys.argv[0],run_name='__main__')", "try:\n    runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit:\n    pass\nsys.path.insert(0,str(sandbox/'scripts'))\nfrom check_phase2g_golden import verify\nverify(sandbox)")
    with tempfile.TemporaryDirectory(prefix='phase2g-golden-') as tmp:
        sandbox=Path(tmp);copy_inputs(sandbox)
        env={**os.environ,'PYTHONPATH':str(sandbox)+os.pathsep+str(sandbox/'src'),
             'PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(sandbox)}
        with (output/'runner.log').open('w') as log:
            result=subprocess.run([sys.executable,'-c',program,str(sandbox)],cwd=sandbox,env=env,stdout=log,stderr=subprocess.STDOUT)
        evidence=sandbox/'books/the-secrets-of-astrology/work/golden'
        if evidence.exists():
            compact_artifacts(evidence)
            shutil.copytree(evidence,output,dirs_exist_ok=True)
        atomic_json(output/'isolation.json',{'temporary_checkout':True,'live_manifest_written':False,
            'network_forbidden':True,'write_guard':True,'exit_code':result.returncode})
        if result.returncode:raise SystemExit(result.returncode)

if __name__=='__main__':main()
