"""Hash-bound separate supplements. Fixtures are mechanical, never approval of a book."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from astro_translate.core import Blocked,atomic_json


def proposal():
    return {'request_sha256':'scope','page':1,'unit_id':'label','source':'Original',
            'full_target':'Existing long target','short_target':'Short',
            'max_chars':9,'provenance':{'entry_point':'astro_translate.llm.run_task','backend':'fake'}}


def test_unsigned_or_modified_supplement_never_composes(tmp_path):
    from astro_translate.short_forms import approve,load_approved,digest
    data={'dependencies':{'source':'hash'},'proposals':[proposal()]}
    review={'approved_by':'test reviewer','proposals_sha256':digest(data),'request_sha256s':['scope']}
    file=approve(tmp_path,data,review)
    assert file.name=='approved-v1.json'
    assert load_approved(tmp_path,data['dependencies'])['scope']['short_target']=='Short'
    signed=json.loads(file.read_text());signed['forms'][0]['short_target']='Forged';atomic_json(file,signed)
    with pytest.raises(Blocked,match='hash'):load_approved(tmp_path,data['dependencies'])


def test_proposals_do_not_implicitly_approve_and_review_is_content_bound(tmp_path):
    from astro_translate.short_forms import approve,load_approved,digest
    data={'dependencies':{'source':'hash'},'proposals':[proposal()]}
    atomic_json(tmp_path/'proposals.json',data)
    assert load_approved(tmp_path,data['dependencies'])=={}
    with pytest.raises(Blocked):approve(tmp_path,data,{'approved_by':'test','proposals_sha256':'old','request_sha256s':['scope']})
    file=approve(tmp_path,data,{'approved_by':'test','proposals_sha256':digest(data),'request_sha256s':['scope']})
    with pytest.raises(Blocked):load_approved(tmp_path,{'source':'changed'})
    second=approve(tmp_path,data,{'approved_by':'test','proposals_sha256':digest(data),'request_sha256s':['scope']})
    assert second.name=='approved-v2.json' and file.exists()


def test_short_request_real_display_budget_uses_geometry_not_source_ratio(tmp_path):
    import pymupdf as fitz
    from astro_translate import fidelity
    from astro_translate.fonts import select_group_fonts
    from astro_translate.short_forms import make_request
    root=Path(__file__).resolve().parents[1];book=root/'books/the-secrets-of-astrology'
    data=json.loads((root/'tests/fixtures/phase2f/p0049-translation.json').read_text())
    with fitz.open(book/'source.pdf') as doc:
        units=[{**u,'id':u['id'].removeprefix('p0049_')} for u in data['blocks']]
        units=fidelity.finalize_editorial_composition(doc[48],fidelity.apply_editorial_composition(doc[48],units))
        u=next(u for u in units if u['source']=='CARING');fonts,_=select_group_fonts(doc,doc[48],units,tmp_path)
        boxes=fidelity._candidate_boxes(doc[48],u,units,fidelity.immutable_boxes(doc[48],()))
        request=make_request(49,u,boxes,fonts)
        assert request['min_scale']==.75
        assert 0<request['max_chars']<len(u['target'])
        assert request['max_width_pt']>0
        assert request['full_target']==u['target']
        assert request['request_sha256']


def test_real_display_full_fit_precedes_reviewed_short_retry(tmp_path,monkeypatch):
    from astro_translate import fidelity,short_forms,llm
    from astro_translate.production import Production
    root=Path(__file__).resolve().parents[1];book=root/'books/the-secrets-of-astrology'
    runner=Production(book);payload=json.loads((root/'tests/fixtures/phase2f/p0049-translation.json').read_text())
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    monkeypatch.setattr(llm,'run_task',lambda *a,**k:pytest.fail('Composition called a model'))
    original=fidelity._test_fit;attempts=[];requests=[]
    full=next(u['target'] for u in payload['blocks'] if u['source']=='CARING')
    def observed(page,unit,box,factor,*args):
        if unit['source']=='CARING':attempts.append((unit['target'],factor))
        return original(page,unit,box,factor,*args)
    def make(*args):
        result=real_make(*args)
        if result:requests.append(result)
        return result
    real_make=short_forms.make_request
    def approved(*args):
        # Test-only mechanical fixture from existing saved text, not a book
        # proposal or an approval artifact. The signer is deliberately a fake.
        assert any(t==full and scale==.75 for t,scale in attempts)
        req=requests[-1]
        return {req['request_sha256']:{**req,'short_target':full.split()[0],
             'supplement_sha256':'test-only','supplement':'temporary-test-fixture'}}
    monkeypatch.setattr(fidelity,'_test_fit',observed)
    monkeypatch.setattr(short_forms,'make_request',make)
    monkeypatch.setattr(short_forms,'load_approved',approved)
    fit=fidelity.reinsert_fidelity(book,payload,runner.glossary,runner.style,runner.protected,
        artifact_dir=tmp_path,phase='production',corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete'],fit.get('failures')
    unit=next(u for u in fit['blocks'] if u['source']=='CARING')
    assert unit['short_form']['full_target']==full
    assert unit['short_form']['supplement_sha256']=='test-only'
    assert min(u['scale'] for u in fit['blocks'])>=.75
    assert fit['group_scales']['p0049_display_31.0']==unit['scale']


def test_request_hash_does_not_depend_on_font_cache_timestamp():
    import pymupdf as fitz
    from astro_translate.short_forms import make_request
    style={'font':'helv','size':12,'color':0,'weight':400,'italic':False}
    u={'id':'u1','source':'LABEL','target':'LONG LABEL','target_runs':[{'text':'LONG LABEL','style':style}],
       'kind':'label','spans':[],'bbox':[0,0,60,20],'scale_group':'g'}
    font=fitz.Font('helv');key=('g',tuple(sorted(style.items())))
    choice={'source':'fallback','sha256':'timestamp-a','base_sha256':'stable-font',
            'axes':{'wght':400},'metric_calibration':{'horizontal_scale':1},'font':font}
    a=make_request(1,u,[fitz.Rect(0,0,60,20)],{key:choice})
    b=make_request(1,u,[fitz.Rect(0,0,60,20)],{key:{**choice,'sha256':'timestamp-b'}})
    assert a['request_sha256']==b['request_sha256']


def test_proposal_command_uses_run_task_and_skips_reviewed_requests(tmp_path,monkeypatch):
    import importlib.util
    from types import SimpleNamespace
    from astro_translate import llm
    from astro_translate.core import sha256
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('phase2f_short_cli',root/'scripts/short_forms.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    cfg=tmp_path/'settings.toml';cfg.write_text('fixture')
    r=SimpleNamespace(book=tmp_path,work=tmp_path,config_path=cfg,gates=lambda:None,
        glossary='unused',style='unused',protected='unused')
    deps={'source':'fixture'};monkeypatch.setattr(cli,'dependencies',lambda *a:deps)
    monkeypatch.setattr(llm,'_check_pause',lambda *a:None)
    req={'request_sha256':'request','page':1,'unit_id':'test','source':'LABEL',
         'full_target':'LONG LABEL','max_chars':6,'max_width_pt':60,'font_size':12}
    plan={'dependencies':deps,'config_sha256':sha256(cfg),'requests':[req],'artifacts':{}}
    calls=[]
    def fake(task,payload):
        calls.append((task,payload));assert task=='shorten'
        assert payload['blocks']==[{'id':'p0001_test','source':'LONG LABEL','max_chars':6}]
        assert payload['geometry_constraints'][0]['max_width_pt']==60
        return SimpleNamespace(backend='fake',model='test',data={'blocks':[{'id':'p0001_test','translation':'LABEL'}]})
    monkeypatch.setattr(llm,'run_task',fake)
    proposals=cli.propose(r,plan,tmp_path/'proposals.json')
    assert len(calls)==1 and proposals['proposals'][0]['provenance']['backend']=='fake'
    cli.propose(r,plan,tmp_path/'proposals.json');assert len(calls)==1
    monkeypatch.setattr(cli,'load_approved',lambda *a:{'request':{'short_target':'LABEL'}})
    cli.propose(r,plan,tmp_path/'different-proposals.json');assert len(calls)==1


def test_shortening_context_masks_book_numbers_before_backend():
    from astro_translate import llm
    wire,maps=llm._prepare('shorten',{'blocks':[{'id':'label','source':'34 light-years','max_chars':20}],
        'meaning_context':[{'id':'label','source':'A star 34 light-years away'}],
        'geometry_constraints':[{'id':'label','max_width_pt':60}]},(),())
    assert '34' not in json.dumps(wire)
    assert maps['label']
