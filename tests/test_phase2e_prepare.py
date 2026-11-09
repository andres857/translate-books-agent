"""Retranslation plan lists real source units without invoking inference."""
import importlib.util
from pathlib import Path
import pymupdf as fitz

ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/'books/the-secrets-of-astrology'


def test_prepare_batch2_is_offline_and_includes_complete_quotes(tmp_path,monkeypatch):
    from astro_translate import llm
    def forbidden(*a,**k):raise AssertionError('No inference while preparing')
    monkeypatch.setattr(llm,'run_task',forbidden)
    path=ROOT/'scripts/prepare_phase2e_retranslations.py'
    spec=importlib.util.spec_from_file_location('phase2e_prepare',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from astro_translate.production import Production
    runner=Production(BOOK);runner.folder=tmp_path/'isolated-production';runner.folder.mkdir()
    plan=module.build_plan(runner,'21-40',tmp_path)
    assert plan['llm_calls']==0
    jobs=plan['jobs']
    assert any(j['unit']['page']==21 and j['kind']=='missing_native' for j in jobs)
    for page in (23,33):
        quotes=[j for j in jobs if j['unit']['page']==page and j['kind']=='quote']
        assert len(quotes)==1 and len(quotes[0]['unit']['composed_ids'])>=2
    assert len({j['request']['id'] for j in jobs})==len(jobs)


def test_execute_installs_complete_source_topology_and_keeps_unrelated_records(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from astro_translate import llm
    from astro_translate.production import Production
    from astro_translate.core import atomic_json
    spec=importlib.util.spec_from_file_location('phase2e_prepare',ROOT/'scripts/prepare_phase2e_retranslations.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    real=Production(BOOK);real.folder=tmp_path/'isolated-production';real.folder.mkdir()
    plan=module.build_plan(real,'21',tmp_path)
    selected=plan['jobs'][0]['request']['id']
    grouped=module.build_plan(real,'21',tmp_path,[selected])
    assert [j['request']['id'] for j in grouped['jobs']]==[selected]
    # Mechanical orchestration fixture: source echoes, never a translation.
    def response(task,payload):
        return SimpleNamespace(data={'blocks':[{'id':r['id'],'translation':r['source']} for r in payload['blocks']]},
                               backend='test',model='test',input_tokens=0,output_tokens=0)
    monkeypatch.setattr(llm,'run_task',response);monkeypatch.setattr(llm,'_check_pause',lambda *a:None)
    runner=SimpleNamespace(book=tmp_path,config_path=real.config_path,source_hash=real.source_hash,gates=lambda:None,
                           path=lambda n,suffix:tmp_path/f'p{n:04d}-{suffix}')
    deps={k:v for k,v in plan.items() if k.endswith('_sha256') and k!='corrections_sha256'}
    out=tmp_path/'work/corrections.json';prior={'unit':{'page':9,'id':'unrelated'}}
    atomic_json(out,{**deps,'corrections':[prior]})
    module.execute(runner,plan,out)
    import json
    payload=json.loads(runner.path(21,'translation.json').read_text())
    with fitz.open(BOOK/'source.pdf') as doc:
        from astro_translate.fidelity import extract_fidelity_units
        ids={f'p0021_'+u['id'] for u in extract_fidelity_units(doc[20],real.extras)}
    assert {u['id'] for u in payload['blocks']}==ids
    assert any(u.get('translation_method')=='requires_joined_quote_correction' for u in payload['blocks'])
    assert prior in json.loads(out.read_text())['corrections']


def test_include_ids_adds_specific_retranslation_without_filtering_missing_units(tmp_path):
    from astro_translate.production import Production,read
    from astro_translate.core import atomic_json
    spec=importlib.util.spec_from_file_location('phase2e_prepare',ROOT/'scripts/prepare_phase2e_retranslations.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from astro_translate.fidelity import extract_fidelity_units
    runner=Production(BOOK);runner.folder=tmp_path/'isolated-production';runner.folder.mkdir()
    with fitz.open(BOOK/'source.pdf') as doc:
        unit=extract_fidelity_units(doc[20],runner.extras)[0]
    identifier='p0021_'+unit['id']
    unit['id']=identifier;unit['target']=unit['source']
    unit['target_runs']=[{'text':unit['source'],'style':unit['style']}]
    atomic_json(runner.path(21,'translation.json'),{'blocks':[unit]})
    base=module.build_plan(runner,'21',tmp_path)
    combined=module.build_plan(runner,'21',tmp_path,include_ids=[identifier])
    assert identifier not in {j['request']['id'] for j in base['jobs']}
    assert identifier in {j['request']['id'] for j in combined['jobs']}
    assert len(combined['jobs'])==len(base['jobs'])+1


def test_produce_dry_run_never_enters_writer_or_models(monkeypatch,capsys):
    from astro_translate.__main__ import main
    from astro_translate import production,llm
    def forbidden(*a,**k):raise AssertionError('Read-only dry-run entered production')
    monkeypatch.setattr(production,'produce',forbidden);monkeypatch.setattr(llm,'run_task',forbidden)
    assert main(['produce','--book',str(BOOK),'--pages','21-40','--resume','--recompose','--dry-run'])==0
    import json
    report=json.loads(capsys.readouterr().out)
    assert report['dry_run'] and report['llm_calls']==0 and len(report['pages'])==20
    assert set(report['missing_saved_translation'])<=set(range(21,41))


def test_chapter_title_is_one_retranslation_request():
    from astro_translate.composition_corrections import quote_job
    from astro_translate.fidelity import extract_fidelity_units
    with fitz.open(BOOK/'source.pdf') as source:
        jobs=quote_job(source[24],extract_fidelity_units(source[24]),'source-hash')
    assert len(jobs)==1 and jobs[0]['unit'].get('display_title')
    assert len(jobs[0]['unit']['composed_ids'])==2
