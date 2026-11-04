import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from astro_translate.adapters import doctor, masked_visual_judge, require_backend_approval
from astro_translate.llm import OpenAIAPIBackend, BackendError


def test_paid_api_is_explicit_even_when_key_present(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','not-real')
    with pytest.raises(BackendError,match='explicitly'):OpenAIAPIBackend(tmp_path,{'backend':'codex_sdk'},Mock())


def test_responses_schema_effort_and_usage(tmp_path):
    client=Mock();client.responses.create.return_value=SimpleNamespace(output_text='{"ok":true}',usage=SimpleNamespace(input_tokens=10,output_tokens=2,input_tokens_details=SimpleNamespace(cached_tokens=6)))
    api=OpenAIAPIBackend(tmp_path,{'backend':'openai_api'},client)
    result=api.complete_json('test',{}, {'type':'object'},'test-model','low')
    assert result.data=={'ok':True} and result.input_tokens==10 and result.cached_input_tokens==6
    sent=client.responses.create.call_args.kwargs
    assert sent['reasoning']=={'effort':'low'} and sent['text']['format']['type']=='json_schema'


def test_api_errors_do_not_disclose_secrets(tmp_path):
    client=Mock();client.responses.create.side_effect=RuntimeError('secret-value')
    api=OpenAIAPIBackend(tmp_path,{'backend':'openai_api'},client)
    with pytest.raises(BackendError) as e:api.complete_json('test',{}, {},'test-model','low')
    assert 'secret-value' not in str(e.value)


def test_doctor_options():
    d=doctor();assert all(d['babeldoc_options'].values());assert not d['babeldoc_protection_verified']


def test_backend_gate_cannot_be_skipped(tmp_path):
    config=tmp_path/'config.toml';config.write_text('[provider]\nbackend="codex_sdk"')
    with pytest.raises(Exception,match='G1b'):require_backend_approval(tmp_path,config,'pilot')


def test_masked_visual_adapter_uses_boundary(tmp_path,monkeypatch):
    from PIL import Image
    def fake(task,payload):
        assert task=='qa_judge' and payload['_images_verified']
        assert all(__import__('pathlib').Path(p).exists() for p in payload['_images'])
        return SimpleNamespace(data={'verdict':'ok','residual_english':[],'overlaps':[],'notes':'mock'})
    monkeypatch.setattr('astro_translate.adapters.run_task',fake)
    assert masked_visual_judge(tmp_path,Image.new('RGB',(8,8)),Image.new('RGB',(8,8)))['ok']


def test_partial_backend_approval_allows_reinsertion_but_not_pilot(tmp_path):
    from astro_translate.adapters import backend_config_digest
    from astro_translate.core import atomic_json
    config=tmp_path/'config.toml';config.write_text('[provider]\nbackend="codex_sdk"\n[tasks.translate_page]\nmodel="mock"\neffort="low"\n[layout]\nmin_scale=0.75\n')
    import tomllib
    digest=backend_config_digest(tomllib.loads(config.read_text()))
    atomic_json(tmp_path/'work/backend_approval.json',{'approved':True,'approved_by':'TEST ONLY','backend':'codex_sdk','route':'pymupdf','backend_config_sha256':digest,'authorized_phases':['reinsertion']})
    require_backend_approval(tmp_path,config,'reinsertion')
    config.write_text(config.read_text().replace('min_scale=0.75','min_scale=0.80'))
    require_backend_approval(tmp_path,config,'reinsertion')
    with pytest.raises(Exception,match='G1b'):require_backend_approval(tmp_path,config,'pilot')
    config.write_text(config.read_text().replace('model="mock"','model="different"'))
    with pytest.raises(Exception,match='G1b'):require_backend_approval(tmp_path,config,'reinsertion')


def test_native_groups_small_pages_and_reports_usage_once(tmp_path,monkeypatch):
    import pymupdf
    from astro_translate import adapters
    from astro_translate.core import approve_glossary
    from astro_translate.llm import LLMResult
    book=tmp_path/'book';book.mkdir();doc=pymupdf.open()
    for text in ('Sun','Moon','Planet'):doc.new_page().insert_text((72,72),text)
    doc.save(book/'source.pdf');doc.close()
    glossary=tmp_path/'g.csv';glossary.write_text('source,target,tgt_lng\nSun,Sol,es\n');style=tmp_path/'style.md';style.write_text('Neutral.');protected=tmp_path/'protected.txt';protected.write_text('MC\n')
    approve_glossary(book,glossary,style,'TEST ONLY',protected)
    config=tmp_path/'config.toml';config.write_text('[paths]\nfont_regular="fonts/NotoSans-Regular.ttf"\n[batching]\nmax_chars_per_call=6000\n')
    calls=[]
    def fake(task,payload):
        calls.append(payload)
        return LLMResult({'blocks':[{'id':r['id'],'translation':r['source'],'glossary_terms_used':[]} for r in payload['blocks']]},'mock','low',120,10,'codex_sdk',False,1,cached_input_tokens=100)
    monkeypatch.setattr(adapters,'run_task',fake)
    result=adapters.native_translate(book,glossary,style,protected,'1,2,3',config_path=config,phase='smoke',reinsert_pages=False)
    assert len(calls)==1 and calls[0]['_pages']==[1,2,3]
    assert all(row['id'].startswith(f'p{number:04d}_') for row,number in zip(calls[0]['blocks'],[1,2,3]))
    assert result['batches'][0]['input_tokens']==120 and result['batches'][0]['cached_input_tokens']==100
    assert all(page['input_tokens'] is None for page in result['pages'])


def test_quota_projection_does_not_multiply_shared_usage(tmp_path):
    import importlib.util
    from pathlib import Path
    import pymupdf
    spec=importlib.util.spec_from_file_location('quota_estimator',Path('scripts/estimate_quota.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    book=tmp_path/'book';(book/'work').mkdir(parents=True);doc=pymupdf.open()
    for _ in range(3):doc.new_page()
    doc.save(book/'source.pdf');doc.close()
    entry={'task':'translate_page','phase':'reinsertion','page':None,'pages':[1,2,3],'backend':'codex_sdk','status':'ok','cached':False,'input_tokens':120,'cached_input_tokens':90,'output_tokens':12,'latency_s':1.5,'cost_usd':0}
    (book/'work/costs.jsonl').write_text(json.dumps(entry)+'\n')
    result=module.estimate(book,{1,2,3},'reinsertion')
    assert result['calls']==1 and result['input_tokens']['measured_total']==120
    assert result['input_tokens']['mean_per_page']==40 and result['cached_input_tokens']['measured_total']==90
    assert result['uncached_input_tokens_measured']==30
