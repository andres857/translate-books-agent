"""Run the real golden replay in a disposable checkout, never the live book.

An audit guard denies writes outside the temporary checkout and network access.
Protected inputs are copied byte-for-byte. No restoration of a live manifest is
needed, so concurrent production may continue safely.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from astro_translate.core import atomic_json

ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/'books/the-secrets-of-astrology'
BOOTSTRAP=r'''
import os,sys,runpy
from pathlib import Path
sandbox=Path(sys.argv.pop(1)).resolve()
def allow(path):
    if isinstance(path,(str,bytes,os.PathLike)) and not Path(os.fsdecode(path)).resolve().is_relative_to(sandbox):
        raise PermissionError('Golden sandbox write outside temporary checkout: '+str(path))
def audit(event,args):
    if event=='open':
        path,mode,flags=args
        if (mode and any(x in mode for x in 'wax+')) or (flags and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):allow(path)
    elif event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.utime'):allow(args[0])
    elif event in ('os.rename','os.replace'):allow(args[0]);allow(args[1])
    elif event.startswith('socket.'):raise PermissionError('Network forbidden in offline golden')
sys.addaudithook(audit)
import astro_translate.llm as llm
def deny(*a,**k):raise RuntimeError('LLM forbidden in golden')
llm.run_task=deny
sys.argv=[str(sandbox/'scripts/check_phase2c_golden.py'),'--expected-changes','19,87','--output',str(sandbox/'books/the-secrets-of-astrology/work/golden')]
runpy.run_path(sys.argv[0],run_name='__main__')
'''


def copy_inputs(destination):
    for name in ('src','scripts','config','glossary','prompts','schemas','fonts'):
        shutil.copytree(ROOT/name,destination/name,ignore=shutil.ignore_patterns('__pycache__'))
    target=destination/'tests/golden';target.mkdir(parents=True)
    shutil.copy2(ROOT/'tests/golden/secrets_pilot.json',target/'secrets_pilot.json')
    book=destination/'books/the-secrets-of-astrology';work=book/'work';work.mkdir(parents=True)
    shutil.copy2(BOOK/'source.pdf',book/'source.pdf')
    # Copy JSON/Python workflow evidence and exact gate inputs, not generated
    # page PDFs/renders. Cached font instances are required for byte replay
    # (their timestamps and internal names are part of the frozen PDF). Absolute old candidate paths are read-only.
    for folder in ('pilot','backend_smoke','glossary'):
        for p in (BOOK/'work'/folder).rglob('*'):
            if p.is_file() and p.suffix in ('.json','.py','.toml','.csv','.txt','.md','.ttf','.otf','.cff') and '__pycache__' not in p.parts:
                out=work/p.relative_to(BOOK/'work');out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,out)
    for p in (BOOK/'work').iterdir():
        if p.is_file() and p.suffix in ('.json','.py','.toml'):
            shutil.copy2(p,work/p.name)
    correction=BOOK/'work/production/composition_corrections.json'
    if correction.exists():
        (work/'production').mkdir();shutil.copy2(correction,work/'production'/correction.name)
    # The old runner starts its own subprocess. sitecustomize installs the
    # same write boundary there, while its no_llm context blocks inference.
    code=BOOTSTRAP.split('sys.addaudithook(audit)')[0]
    code=code.replace('sandbox=Path(sys.argv.pop(1)).resolve()',f'sandbox=Path({str(destination)!r}).resolve()')
    (destination/'sitecustomize.py').write_text(code+'sys.addaudithook(audit)\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    output=a.output.resolve()
    if not output.is_relative_to(BOOK/'work') or output in (BOOK/'work',BOOK/'work/production',BOOK/'work/pilot'):raise ValueError('Use separate evidence below book/work')
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='phase2f-golden-') as tmp:
        sandbox=Path(tmp);copy_inputs(sandbox)
        env={**os.environ,'PYTHONPATH':str(sandbox)+os.pathsep+str(sandbox/'src'),'PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(sandbox)}
        with (output/'runner.log').open('w') as log:
            result=subprocess.run([sys.executable,'-c',BOOTSTRAP,str(sandbox)],cwd=sandbox,env=env,stdout=log,stderr=subprocess.STDOUT)
        evidence=sandbox/'books/the-secrets-of-astrology/work/golden'
        if evidence.exists():shutil.copytree(evidence,output,dirs_exist_ok=True)
        atomic_json(output/'isolation.json',{'temporary_checkout':True,'live_manifest_written':False,
            'network_forbidden':True,'write_guard':True,'exit_code':result.returncode})
        if result.returncode:raise SystemExit(result.returncode)

if __name__=='__main__':main()
