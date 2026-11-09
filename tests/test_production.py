"""Production orchestration: offline tests, never real LLM integration."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT/'books/the-secrets-of-astrology'


def test_generic_patterns_enabled_beyond_pilot():
    from astro_translate.profiles import load_profile
    profile = load_profile(BOOK)
    for n in (36, 37, 102):
        policy = profile.page_policy(SimpleNamespace(number=n-1))
        assert all(policy.get(k) for k in ('run_in', 'callouts', 'protected_rows', 'vertical'))


def test_g5_excludes_only_justified_deferred():
    from astro_translate.production import batch_metrics
    rows = [{'page':n, 'status':'done_qa_ok'} for n in range(1, 19)]
    rows += [{'page':19, 'status':'review'}, {'page':20, 'status':'deferred_raster', 'deferred_regions':[{'bbox':[0,0,5,5], 'reason':'scan'}]}]
    result = batch_metrics(rows)
    assert result['qa_ok_ratio'] == 18/19 and result['g5_pass']
    rows[0]['status'] = 'failed'
    assert not batch_metrics(rows)['g5_pass']
    rows[-1]['deferred_regions'] = []
    assert batch_metrics(rows)['denominator'] == 20


def test_page_range_validation():
    from astro_translate.production import selected_pages
    assert selected_pages('36-37,102',192) == [36,37,102]
    for spec in ('0', '193', '37-36', 'a'):
        with pytest.raises(ValueError): selected_pages(spec,192)


@pytest.fixture
def offline_book(tmp_path, monkeypatch):
    """Real PDF/profile in a temporary book. Gate and run_task doubles ONLY here.

    No approval is written, no real book artifact is changed, no inference occurs.
    English identity output is intentionally NOT an accepted translation.
    """
    import shutil
    from astro_translate import production, core, adapters, fidelity, safe_vlm, llm
    from astro_translate.profiles import load_profile
    book=tmp_path/'book';work=book/'work';work.mkdir(parents=True)
    shutil.copyfile(BOOK/'source.pdf',book/'source.pdf')
    shutil.copyfile(BOOK/'work/profile.toml',work/'profile.toml')
    profile=load_profile(BOOK)
    def copy_dependencies(value):
        if isinstance(value,dict):
            if 'path' in value and 'sha256' in value:
                dest=book/value['path'];dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(profile.checked_file(value),dest)
            for v in value.values():copy_dependencies(v)
    copy_dependencies(profile.data)
    manifest=json.loads((BOOK/'work/manifest.json').read_text())
    for row in manifest['pages']:row['status']='pending'
    core.atomic_json(work/'manifest.json',manifest)
    # Only gate checks are simulated; extraction, memory, composition, QA,
    # masks, state transitions and assembly remain the real implementation.
    for module in (core, adapters, fidelity, safe_vlm, production):
        monkeypatch.setattr(module,'require_approval',lambda *a,**k: {},raising=False)
    monkeypatch.setattr(adapters,'require_backend_approval',lambda *a,**k: None)
    monkeypatch.setattr(production.Production,'gates',lambda self: {'pages':{}})
    monkeypatch.setattr(production.Production,'dependencies',lambda self: 'offline-test-dependency')
    calls=[]
    # The double reports the configured backend: adapters only commit sentence
    # memory for results from the approved backend (G5 backend provenance).
    import tomllib
    backend=tomllib.loads((BOOK/'work/pipeline.toml').read_text())['provider']['backend']
    def fake_run_task(task,payload):
        calls.append((task,payload))
        if task=='qa_judge':
            data={'verdict':'ok','overlaps':[],'residual_english':[],'notes':'OFFLINE MOCK, not model evidence'}
        else:
            key='labels' if task=='translate_labels' else 'blocks'
            assert sum(len(r['source']) for r in payload[key])<=6000
            data={key:[{'id':r['id'],'translation':r['source'],'glossary_terms_used':[]} for r in payload[key]]}
        return llm.LLMResult(data,'offline-mock','low',None,None,backend,False,0,cached_input_tokens=None)
    for module in (llm,adapters,safe_vlm):monkeypatch.setattr(module,'run_task',fake_run_task)
    return book,calls,BOOK/'work/pipeline.toml',fake_run_task


def test_real_three_pages_offline_artifacts_resume_and_quota(offline_book,monkeypatch):
    from astro_translate import production, safe_vlm, llm
    book,calls,config,fake=offline_book
    source_hash=production.sha256(book/'source.pdf')
    paused=False
    def pause_once(task,payload):
        nonlocal paused
        if task=='qa_judge' and not paused:
            paused=True
            raise llm.QuotaPaused('OFFLINE simulated quota')
        return fake(task,payload)
    monkeypatch.setattr(safe_vlm,'run_task',pause_once)
    with pytest.raises(llm.QuotaPaused):
        production.produce(book,pages='36-37,102',config_path=config)
    assert paused
    assert production.read(book/'work/quota_pause.json')['exit_code']==75
    state=production.read(book/'work/manifest.json')
    assert all(r['status']=='pending' for r in state['pages'] if r['page'] in (36,37,102))
    translations=len([t for t,p in calls if t=='translate_page'])
    assert translations>=1
    assert any(p.get('_pages')==[36,37,102] for t,p in calls if t=='translate_page')
    pause=production.read(book/'work/quota_pause.json');pause['resume_after_unix']=0
    production.atomic_json(book/'work/quota_pause.json',pause)
    result=production.produce(book,pages='36-37,102',resume=True,config_path=config)
    assert len([t for t,p in calls if t=='translate_page'])==translations
    assert result['status']=='g5_blocked'  # identity English must never pass QA; G5 blocks
    assert result['batches'][0]['id']==1 and not result['batches'][0]['metrics']['g5_pass']
    assert production.read(book/'work/production/g5_block.json')['batch']==1
    for n in (36,37,102):
        folder=book/'work/production'
        for suffix in ('translation.json','fonts.json','fit.json','qa.json','translated.pdf'):
            assert (folder/f'p{n:04d}-{suffix}').is_file(), (n,suffix)
        qa=production.read(folder/f'p{n:04d}-qa.json')
        assert qa['production_status']=='review'
        assert not qa['local_fidelity_ok']
        assert qa['candidate_sha256']==production.sha256(folder/f'p{n:04d}-translated.pdf')
    before=len(calls)
    again=production.produce(book,pages='36-37,102',resume=True,config_path=config)
    assert again['skipped_current']==[36,37,102] and len(calls)==before
    assert production.sha256(book/'source.pdf')==source_hash
    queue=production.read(book/'work/review_queue.json')
    assert all(Path(r['capture']).is_file() for r in queue if r.get('phase')=='production')


def test_three_repeated_errors_stop_batch(offline_book,monkeypatch):
    from astro_translate import production
    book,calls,config,_=offline_book
    def bad(*args):raise RuntimeError('OFFLINE identical exception')
    monkeypatch.setattr(production.Production,'inventory',bad)
    result=production.produce(book,pages='36-39',config_path=config)
    assert result['status']=='stopped_repeated_error'
    manifest=production.read(book/'work/manifest.json')
    assert [r['status'] for r in manifest['pages'][35:39]]==['failed','failed','failed','pending']
    assert result['batches'][0]['errors'][-1]['repetitions']==3
    assert not calls


def test_scan_deferred_without_llm_and_g5_continues(offline_book,monkeypatch):
    from astro_translate import production
    book,calls,config,_=offline_book
    # Actual raster pages: a route label alone must not hide native text.
    pages={2,8,10}
    result=production.produce(book,pages='2,8,10',batch_size=2,config_path=config)
    assert len(result['batches'])==2
    assert all(b['metrics']['denominator']==0 for b in result['batches'])
    assert not calls
    assert all(r['status']=='deferred_raster' and r['deferred_regions'] for r in production.read(book/'work/manifest.json')['pages'] if r['page'] in pages)


def test_checkpoint_and_review_candidate_binding(offline_book):
    from astro_translate import production
    book,_,config,_=offline_book
    runner=production.Production(book,config)
    p=runner.path(36,'translated.pdf');p.write_bytes(b'offline candidate')
    runner.checkpoint(36,'dep',stage='complete',status='done_qa_ok')
    assert runner.current(36,'dep')
    assert not runner.current(36,'changed')
    p.write_bytes(b'changed')
    assert not runner.current(36,'dep')
    with pytest.raises(production.Blocked,match='current complete candidate'):
        production.record_visual_review(book,36,'tester','wrong','offline note',config_path=config)


def test_partial_translation_quota_preserves_completed_memory(offline_book,monkeypatch):
    from astro_translate import adapters, llm
    book,calls,config,fake=offline_book
    count=0
    def pause_second(task,payload):
        nonlocal count
        count+=1
        if count==2:raise llm.QuotaPaused('OFFLINE pause between translation groups')
        return fake(task,payload)
    monkeypatch.setattr(adapters,'run_task',pause_second)
    kwargs=dict(config_path=config,reinsert_pages=False,phase='production')
    args=(book,BOOK/'work/glossary/astro_en_es.csv',BOOK/'work/glossary/style_guide.md',BOOK/'work/glossary/do_not_translate.txt','36-45')
    with pytest.raises(llm.QuotaPaused):adapters.fidelity_translate(*args,**kwargs)
    completed={r['id'] for task,payload in calls for r in payload.get('blocks',[])}
    assert completed
    calls.clear();monkeypatch.setattr(adapters,'run_task',fake)
    adapters.fidelity_translate(*args,**kwargs)
    resumed={r['id'] for task,payload in calls for r in payload.get('blocks',[])}
    assert resumed and not completed & resumed


def test_foreign_backend_result_is_not_committed_to_memory(offline_book,monkeypatch):
    """A result reported by a backend other than the configured one is never
    memorized, so a later run asks the approved backend again."""
    from astro_translate import adapters, llm
    book,calls,config,fake=offline_book
    def foreign(task,payload):
        result=fake(task,payload);result.backend='other_backend';return result
    monkeypatch.setattr(adapters,'run_task',foreign)
    kwargs=dict(config_path=config,reinsert_pages=False,phase='production')
    args=(book,BOOK/'work/glossary/astro_en_es.csv',BOOK/'work/glossary/style_guide.md',BOOK/'work/glossary/do_not_translate.txt','36')
    adapters.fidelity_translate(*args,**kwargs)
    first={r['id'] for task,payload in calls for r in payload.get('blocks',[])}
    assert first
    calls.clear();monkeypatch.setattr(adapters,'run_task',fake)
    adapters.fidelity_translate(*args,**kwargs)
    assert first <= {r['id'] for task,payload in calls for r in payload.get('blocks',[])}


def test_review_false_positive_persists_and_assembly_falls_back(offline_book,tmp_path):
    import pymupdf as fitz
    from astro_translate import production
    _,_,config,_=offline_book
    book=tmp_path/'small';(book/'work').mkdir(parents=True)
    with fitz.open() as doc:
        for i in range(3):
            page=doc.new_page(width=200,height=250);page.insert_text((20,40),'Original test')
            doc.xref_set_key(page.xref,'Group','<</S/Transparency/CS/DeviceRGB/I true>>')
        doc.save(book/'source.pdf')
    runner=production.Production(book,config)
    production.atomic_json(book/'work/manifest.json',{'source_sha256':runner.source_hash,'pages':[
        {'page':i,'status':'review','attempts':0} for i in range(1,4)]})
    for n in range(1,4):
        path=runner.path(n,'translated.pdf')
        production.atomic_copy(book/'source.pdf',path)
        fit={'page':n,'fit_complete':True,'source_sha256':runner.source_hash,
             'output':str(path),'output_sha256':production.sha256(path)}
        qa={'page':n,'source_sha256':runner.source_hash,'candidate_sha256':fit['output_sha256'],
            'local_fidelity_ok':n!=3,'vlm_complete':True,'min_scale':1,
            'vlm':{'native_vlm':{'verdict':'revisar','overlaps':['MOCK false positive']}},
            'production_status':'done_qa_ok' if n==1 else 'review'}
        production.atomic_json(runner.path(n,'fit.json'),fit);production.atomic_json(runner.path(n,'qa.json'),qa)
        runner.checkpoint(n,runner.dependencies(),stage='complete',status=qa['production_status'])
    for n in (2,3):
        h=production.sha256(runner.path(n,'translated.pdf'))
        production.record_visual_review(book,n,'offline-test-reviewer',h,'MOCK visual review; no real approval',config_path=config)
    assert production.read(runner.path(2,'qa.json'))['production_status']=='done_qa_ok'
    assert production.read(runner.path(3,'qa.json'))['production_status']=='review'  # local checks cannot be waived
    review=runner.review_for(2,production.sha256(runner.path(2,'translated.pdf')))
    assert review and not runner.review_for(2,'different-candidate')
    qa=production.read(runner.path(2,'qa.json'));qa.pop('vlm_local_review')
    runner.finish_qa(2,production.read(runner.path(2,'fit.json')),qa,[],runner.dependencies())
    assert production.read(runner.path(2,'qa.json'))['vlm_local_review']==review
    result=production.assemble_final(book,config_path=config)
    assert result['original_pages']==[3] and not result['complete_translation']
    assert all(p['assembled_render_equal'] and p['group_preserved'] for p in result['proofs'])
    with fitz.open(result['translated_pdf']) as final,fitz.open(result['comparison_pdf']) as comparison:
        assert len(final)==len(comparison)==3
        assert comparison[0].rect.width==final[0].rect.width*2
    # Stale QA bytes force original, never inherit a manual review blindly.
    runner.path(1,'qa.json').write_text('{}')
    result=production.assemble_final(book,config_path=config)
    assert result['original_pages']==[1,3]


def test_pilot_reused_as_single_page_with_pixel_identity_without_model(offline_book,monkeypatch):
    import pymupdf as fitz
    import shutil
    from astro_translate import production
    book,calls,config,_=offline_book
    n=5;base=book/'work/pilot';base.mkdir(exist_ok=True)
    for suffix in ('fit.json','qa.json','translation.json','fonts.json'):
        shutil.copyfile(BOOK/f'work/pilot/p{n:04d}-{suffix}',base/f'p{n:04d}-{suffix}')
    # Frozen G4 record that approved this candidate; the live pilot_approval.json
    # is mutable (2026-11-09: provisional, scope_pages only, no candidates).
    approval=json.loads((BOOK/'work/approval_history/pilot_approval.luna.2026-11-09.json').read_text())
    # An approval without a candidate entry for the page must never reuse it.
    runner=production.Production(book,config)
    assert runner.reuse_pilot(n,{'approved_by':'x','scope_pages':[n]},'dep') is False
    monkeypatch.setattr(production.Production,'gates',lambda self:approval)
    result=production.produce(book,pages=str(n),config_path=config)
    assert not calls and result['batches'][0]['metrics']['g5_pass']
    qa=production.read(book/f'work/production/p{n:04d}-qa.json')
    assert qa['reused_from_pilot'] and qa['production_status']=='done_qa_ok'
    with fitz.open(book/f'work/production/p{n:04d}-translated.pdf') as production_candidate:
        assert len(production_candidate)==1
        actual=production_candidate[0].get_pixmap(dpi=220,alpha=False)
    pilot_fit=production.read(base/f'p{n:04d}-fit.json')
    with fitz.open(pilot_fit['output']) as pilot_candidate:
        expected=pilot_candidate[pilot_fit.get('output_page',n-1)].get_pixmap(dpi=220,alpha=False)
    assert (actual.width,actual.height,actual.samples)==(expected.width,expected.height,expected.samples)


def test_unknown_cost_is_not_zero():
    from astro_translate.production import consumption
    result=consumption([{'backend':'codex_sdk','cost_usd':None,'input_tokens':None}], 'codex_sdk')
    assert result['cost_usd']['unknown_records']==1
    assert result['input_tokens']['unknown_records']==1


def test_legacy_codex_zero_cost_is_reported_unknown_but_cached_zero_remains_known():
    from astro_translate.production import consumption
    result=consumption([{'backend':'codex_sdk','cost_usd':0,'status':'ok'},
                        {'backend':'codex_sdk','cost_usd':0,'status':'cache_hit'}], 'codex_sdk')
    assert result['cost_usd']=={'known_sum':0,'unknown_records':1}


def test_generic_vertical_detection_and_masked_ordinal():
    import pymupdf as fitz
    from astro_translate.vertical_labels import extract_vertical
    from astro_translate.safe_vlm import _protected_rects
    with fitz.open(BOOK/'source.pdf') as doc:
        units=extract_vertical(doc[86])
        assert units and all(u['characters'][0]['direction']==[0.,-1.] for u in units)
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((20,30),'7TH')
        _,records,_,_=_protected_rects(page,(),(),set())
        assert all(r['protected'] for r in records if r['char'].strip())


def test_cli_quota_exit_code(monkeypatch,capsys):
    from astro_translate import production, llm
    from astro_translate.__main__ import main
    def pause(*args,**kwargs):raise llm.QuotaPaused('OFFLINE quota')
    monkeypatch.setattr(production,'produce',pause)
    assert main(['produce','--book',str(BOOK),'--pages','36-37','--resume'])==75
    assert '75' in capsys.readouterr().err


def test_group_translation_error_counts_toward_three(offline_book,monkeypatch):
    from astro_translate import production, adapters
    book,_,config,_=offline_book
    attempts=[]
    def error(*a,**k):
        attempts.append(a[-1]);raise RuntimeError('OFFLINE repeated translation error')
    monkeypatch.setattr(adapters,'fidelity_translate',error)
    result=production.produce(book,pages='36-40',config_path=config)
    assert result['status']=='stopped_repeated_error'
    assert len(attempts)==3


def test_resume_hard_interruption_reuses_batch(offline_book,monkeypatch):
    from astro_translate import production
    book,_,config,_=offline_book
    folder=book/'work/production';folder.mkdir(exist_ok=True)
    production.atomic_json(folder/'batch-01.json',{'id':1,'pages':[36], 'status':'running','cost_start':0,'errors':[]})
    def scan(self,row):return [{'bbox':[0,0,10,10], 'reason':'route_C_or_no_native_text'}]
    monkeypatch.setattr(production.Production,'inventory',scan)
    with pytest.raises(production.Blocked,match='resume'):
        production.produce(book,pages='36',config_path=config)
    result=production.produce(book,pages='36',config_path=config,resume=True)
    assert result['batches'][0]['id']==1
    assert not (folder/'batch-02.json').exists()


def test_failed_pages_can_be_retried_after_fix(offline_book,monkeypatch):
    from astro_translate import production
    book,_,config,_=offline_book
    def bad(self,row):raise RuntimeError('OFFLINE transient extraction failure')
    monkeypatch.setattr(production.Production,'inventory',bad)
    result=production.produce(book,pages='36',config_path=config)
    assert result['status']=='g5_blocked'
    monkeypatch.setattr(production.Production,'inventory',lambda self,row: [])
    production.produce(book,pages='36',config_path=config,resume=True)
    row=production.read(book/'work/manifest.json')['pages'][35]
    assert row['status']=='review'  # real offline identity candidate reaches QA


def test_vertical_numeric_prose_is_not_omitted():
    import pymupdf as fitz
    from astro_translate.vertical_labels import extract_vertical
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((50,150),'Label 12',rotate=90)
        units=extract_vertical(page)
        assert len(units)==1 and units[0]['source']=='Label 12'


def test_resume_closes_batch_after_last_checkpoint(offline_book):
    from astro_translate import production
    book,_,config,_=offline_book
    runner=production.Production(book,config)
    production.atomic_json(runner.path(36,'qa.json'),{'offline_test':True})
    runner.update(36,status='done_qa_ok')
    runner.checkpoint(36,runner.dependencies(),stage='complete',status='done_qa_ok')
    production.atomic_json(runner.folder/'batch-01.json',{'id':1,'pages':[36], 'status':'running','cost_start':0,'errors':[]})
    result=production.produce(book,pages='36',config_path=config,resume=True)
    assert len(result['batches'])==1 and result['batches'][0]['status']=='complete'
    assert production.read(runner.folder/'batch-01.json')['metrics']['g5_pass']


def test_explicit_recomposition_reuses_saved_native_translation(offline_book,monkeypatch):
    from astro_translate import production
    book,calls,config,_=offline_book
    production.produce(book,pages='36',config_path=config)
    count=sum(task!='qa_judge' for task,_ in calls)
    production.produce(book,pages='36',config_path=config,resume=True,recompose=True)
    assert sum(task!='qa_judge' for task,_ in calls)==count
    assert production.read(book/'work/production/p0036-fit.json')['fit_complete']


# --- G5 bloqueante (retrospectiva causa 5) -----------------------------------

def _fail_inventory(monkeypatch):
    from astro_translate import production
    def bad(self,row):raise RuntimeError('OFFLINE extraction failure')
    monkeypatch.setattr(production.Production,'inventory',bad)


def _done_compose(monkeypatch):
    """Offline stand-in for a page whose composition and QA pass."""
    from astro_translate import production
    def done(self,n,regions,dependency):
        production.atomic_json(self.path(n,'qa.json'),{'page':n,'offline_test':True})
        self.update(n,status='done_qa_ok')
        self.checkpoint(n,dependency,stage='complete',status='done_qa_ok')
    monkeypatch.setattr(production.Production,'compose_and_qa',done)


def test_g5_failure_writes_block_and_does_not_start_next_batch(offline_book,monkeypatch):
    from astro_translate import production
    book,_,config,_=offline_book
    _fail_inventory(monkeypatch)
    result=production.produce(book,pages='36-37',batch_size=1,config_path=config)
    assert result['status']=='g5_blocked'
    assert [b['id'] for b in result['batches']]==[1]
    assert result['not_started']==[37]
    folder=book/'work/production'
    assert not (folder/'batch-02.json').exists()
    block=production.read(folder/'g5_block.json')
    assert block['batch']==1 and block['pages']==[36]
    assert block['ratio']==0 and block['ok']==0 and block['in_scope']==1
    assert block['created_at']
    assert production.read(book/'work/manifest.json')['pages'][36]['status']=='pending'


def test_g5_block_refuses_pages_outside_batch_but_allows_same_batch(offline_book,monkeypatch):
    from astro_translate import production
    book,_,config,_=offline_book
    _fail_inventory(monkeypatch)
    production.produce(book,pages='36',config_path=config)
    with pytest.raises(production.G5Blocked,match='g5_decision-01'):
        production.produce(book,pages='37',config_path=config)
    with pytest.raises(production.G5Blocked):
        production.produce(book,pages='36-37',config_path=config)
    again=production.produce(book,pages='36',config_path=config,resume=True)
    assert again['status']=='g5_blocked' and again['g5_block']['batch']==1
    assert again['g5_block']['reevaluations'][-1]['ratio']==0


@pytest.mark.parametrize('decision',[
    {'decision':'skip','approved_by':'x','reason':'r','evidence':'e'},
    {'decision':'continue','approved_by':'','reason':'r','evidence':'e'},
    {'decision':'continue','approved_by':'x','reason':'r','evidence':[]},
    {'decision':'continue','approved_by':'x','reason':'r','evidence':'e','batch':2},
])
def test_g5_invalid_decision_keeps_block(offline_book,monkeypatch,decision):
    from astro_translate import production
    book,_,config,_=offline_book
    _fail_inventory(monkeypatch)
    production.produce(book,pages='36',config_path=config)
    production.atomic_json(book/'work/production/g5_decision-01.json',decision)
    with pytest.raises(production.G5Blocked,match='decision'):
        production.produce(book,pages='37',config_path=config)
    assert (book/'work/production/g5_block.json').exists()


def test_g5_valid_decision_archives_block_and_advances(offline_book,monkeypatch):
    from astro_translate import production
    book,_,config,_=offline_book
    _fail_inventory(monkeypatch)
    production.produce(book,pages='36',config_path=config)
    folder=book/'work/production'
    production.atomic_json(folder/'g5_decision-01.json',{'decision':'continue','approved_by':'offline-test',
        'reason':'OFFLINE test decision','evidence':['work/audit/batch-01.json']})
    def scan(self,row):return [{'bbox':[0,0,10,10],'reason':'route_C_or_no_native_text'}]
    monkeypatch.setattr(production.Production,'inventory',scan)
    result=production.produce(book,pages='37',config_path=config)
    assert result['status']!='g5_blocked'
    assert not (folder/'g5_block.json').exists()
    archived=[production.read(p) for p in folder.glob('g5_block-resolved-*.json')]
    assert len(archived)==1 and archived[0]['batch']==1
    assert archived[0]['resolution']=='decision' and archived[0]['decision']['decision']=='continue'


def test_g5_recompose_same_batch_resolves_block(offline_book,monkeypatch):
    from astro_translate import production
    book,_,config,_=offline_book
    _fail_inventory(monkeypatch)
    production.produce(book,pages='36',config_path=config)
    runner=production.Production(book,config)
    production.atomic_json(runner.path(36,'translation.json'),{'page':36,'source_sha256':runner.source_hash,
        'blocks':[{'id':'p0036_offline','source':'x','target':'y'}]})
    monkeypatch.setattr(production.Production,'inventory',lambda self,row: [])
    _done_compose(monkeypatch)
    result=production.produce(book,pages='36',config_path=config,resume=True,recompose=True)
    assert result['status']=='complete'
    folder=book/'work/production'
    assert not (folder/'g5_block.json').exists()
    archived=[production.read(p) for p in folder.glob('g5_block-resolved-*.json')]
    assert len(archived)==1 and archived[0]['resolution']=='recomposed'
    assert archived[0]['resolved_metrics']['qa_ok_ratio']==1
    assert production.read(folder/'batch-01.json')['status']=='complete'
    # Once resolved, the next pages are no longer blocked.
    def scan(self,row):return [{'bbox':[0,0,10,10],'reason':'route_C_or_no_native_text'}]
    monkeypatch.setattr(production.Production,'inventory',scan)
    production.produce(book,pages='37',config_path=config)


def test_g5_reevaluation_without_pending_pages_resolves(offline_book,monkeypatch):
    from astro_translate import production
    book,_,config,_=offline_book
    _fail_inventory(monkeypatch)
    production.produce(book,pages='36',config_path=config)
    runner=production.Production(book,config)
    production.atomic_json(runner.path(36,'qa.json'),{'offline_test':True})
    runner.update(36,status='done_qa_ok')
    runner.checkpoint(36,runner.dependencies(),stage='complete',status='done_qa_ok')
    result=production.produce(book,pages='36',config_path=config,resume=True)
    assert result['status']=='complete' and result['skipped_current']==[36]
    assert not (book/'work/production/g5_block.json').exists()


def test_g5_cli_exit_code(monkeypatch,capsys):
    from astro_translate import production
    from astro_translate.__main__ import main
    monkeypatch.setattr(production,'produce',lambda *a,**k: {'status':'g5_blocked','batches':[]})
    assert main(['produce','--book',str(BOOK),'--pages','36'])==76
    def refuse(*a,**k):raise production.G5Blocked('OFFLINE G5 block')
    monkeypatch.setattr(production,'produce',refuse)
    assert main(['produce','--book',str(BOOK),'--pages','37'])==76
    assert '76' in capsys.readouterr().err


# --- Escalada/modelo no disponible no aborta el lote (causa 3) ---------------

def _escalation_unavailable():
    from astro_translate import llm
    last=llm.ValidationFailure('emphasis: phrase missing for id p0037_x',{'p0037_x'})
    detail=('escalation unavailable (opus/high: ModelUnavailable: Claude model unavailable: opus; '
            'subtype=error_during_execution; model_not_found); last validation error: '+str(last))
    return llm.ValidationFailure(str(last)+'; '+detail,last.ids)


@pytest.mark.parametrize('error',[
    _escalation_unavailable,
    lambda: __import__('astro_translate.llm',fromlist=['x']).ModelUnavailable('Claude model unavailable: opus; model_not_found'),
    lambda: __import__('astro_translate.llm',fromlist=['x']).BackendError('Claude CLI failed: subtype=error_during_execution'),
],ids=['escalation_unavailable','model_unavailable','backend_error'])
def test_unavailable_model_sends_page_to_review_and_batch_continues(offline_book,monkeypatch,error):
    from astro_translate import production, adapters
    book,calls,config,fake=offline_book
    failing={37,38} if error is _escalation_unavailable else {37}
    def flaky(task,payload):
        if task=='translate_page' and failing & set(payload.get('_pages',[])):raise error()
        return fake(task,payload)
    monkeypatch.setattr(adapters,'run_task',flaky)
    pages='36-38' if len(failing)==2 else '36-37'
    result=production.produce(book,pages=pages,config_path=config)
    assert result['status']!='stopped_repeated_error'
    rows={r['page']:r for r in production.read(book/'work/manifest.json')['pages']}
    assert production.read(book/'work/production/p0036-qa.json')['production_status']=='review'  # composed + QA ran
    queue=production.read(book/'work/review_queue.json')
    for n in failing:
        assert rows[n]['status']=='review', rows[n]
        entry=next(r for r in queue if r.get('page')==n and r.get('phase')=='production')
        assert entry['status']=='review' and entry['reason']
        assert any(word in entry['reason'] for word in ('escalation unavailable','model unavailable','Claude CLI failed'))
    # A later run retries the LLM unit instead of treating review as final.
    monkeypatch.setattr(adapters,'run_task',fake)
    again=production.produce(book,pages=pages,config_path=config,resume=True)
    assert set(failing)<={n for b in again['batches'] for n in b['pages']}


def _assembly_book(tmp_path,config):
    """Synthetic 7-page book: one page per assembly/provenance case.

    1 done_qa_ok · 2 partial_deferred · 3 review with candidate · 4 deferred_raster
    · 5 failed with a leftover complete candidate · 6 review whose candidate bytes
    changed after fit · 7 review with candidate, never audited.
    """
    import os
    import pymupdf as fitz
    from astro_translate import production
    book=tmp_path/'synthetic';(book/'work').mkdir(parents=True)
    with fitz.open() as doc:
        for i in range(7):
            doc.new_page(width=200,height=250).insert_text((20,40),f'Original page {i+1}')
        doc.save(book/'source.pdf')
    runner=production.Production(book,config);dep=runner.dependencies()
    statuses={1:'done_qa_ok',2:'partial_deferred',3:'review',4:'deferred_raster',5:'failed',6:'review',7:'review'}
    region=[{'bbox':[0,0,50,50],'reason':'rotated_wheel_text','phase':4}]
    production.atomic_json(book/'work/manifest.json',{'source_sha256':runner.source_hash,'pages':[
        {'page':n,'status':s,'attempts':1,'deferred_regions':region if n in (2,4) else [],'native_qa_ok':n in (1,2)}
        for n,s in statuses.items()]})
    shas={}
    for n,status in statuses.items():
        if n!=4:
            path=runner.path(n,'translated.pdf')
            with fitz.open() as one:
                one.new_page(width=200,height=250).insert_text((20,40),f'Traduccion {n}');one.save(path)
            shas[n]=production.sha256(path)
            production.atomic_json(runner.path(n,'fit.json'),{'page':n,'fit_complete':True,'source_sha256':runner.source_hash,
                                   'output':str(path),'output_sha256':shas[n],'output_page':0})
        qa={'page':n,'source_sha256':runner.source_hash,'candidate_sha256':shas.get(n),'production_status':status,
            'local_fidelity_ok':n in (1,2),'native_local_fidelity_ok':n in (1,2),'deferred_regions':region if n in (2,4) else []}
        production.atomic_json(runner.path(n,'qa.json'),qa)
        runner.checkpoint(n,dep,stage='complete',status=status)
    with fitz.open() as one:  # p6: candidate bytes no longer match fit.json
        one.new_page(width=200,height=250).insert_text((20,40),'Changed');one.save(runner.path(6,'translated.pdf'))
    production.atomic_json(book/'work/review_queue.json',[
        {'page':3,'phase':'production','status':'review','reasons':['vlm_missing_or_findings','visual_graphics_review_required']},
        {'page':4,'phase':'production','status':'deferred_raster','reasons':['route_C_or_no_native_text']},
        {'page':5,'phase':'production','status':'failed','reasons':['BackendError']},
        {'page':7,'phase':'production','status':'review','reasons':['local_fidelity_or_text_quality_failed']},
        {'page':3,'phase':'pilot','reasons':['old pilot reason, not production']}])
    audit=book/'work/audit';audit.mkdir()
    old={'batch':'batch-01','date':'2026-11-08','pages':[
        {'page':1,'verdict':'ok','candidate_sha256':shas[1],'findings':[]},
        {'page':2,'verdict':'ok','candidate_sha256':shas[2],'findings':[]},
        {'page':3,'verdict':'defect','candidate_sha256':shas[3],'findings':[
            {'code':'MERGE','severity':'blocking','evidence':'Créditos fundidos: «Consultora Kim Farley Editoras»'},
            {'code':'ALIGN','severity':'minor','evidence':'interlineado distinto'}]}]}
    new={'batch':'batch-02','date':'2026-11-09','pages':[
        {'page':1,'verdict':'defect','candidate_sha256':shas[1],'findings':[
            {'code':'EN','severity':'blocking','evidence':'«THE RAM» en la rueda'}]},
        {'page':3,'verdict':'ok','candidate_sha256':'stale-candidate','findings':[]},
        {'page':4,'verdict':'deferred','candidate_sha256':None,'findings':[]}]}
    production.atomic_json(audit/'batch-01.json',old);production.atomic_json(audit/'batch-02.json',new)
    (audit/'batch-03.json').write_text('')  # being written: skipped, not fatal
    production.atomic_json(audit/'batch-04.json',{'batch':'batch-04','superseded_by':['batch-02']})
    os.utime(audit/'batch-01.json',(1,1))
    return book,runner,shas


def test_assemble_final_default_keeps_review_pages_original_and_reports_quality(offline_book,tmp_path):
    from astro_translate import production
    _,_,config,_=offline_book
    book,runner,shas=_assembly_book(tmp_path,config)
    result=production.assemble_final(book,config_path=config)
    assert result['original_pages']==[3,4,5,6,7] and not result['include_review']
    kinds={p['page']:p['provenance'] for p in result['pages']}
    assert kinds=={1:'translated_ok',2:'translated_partial_deferred',3:'original',4:'original',5:'original',6:'original',7:'original'}
    reasons={p['page']:p['reason'] for p in result['pages']}
    assert reasons[4]=='deferred_raster' and reasons[5]=='failed' and reasons[6]=='no_current_candidate'
    assert reasons[3]=='not_qa_ok_without_include_review'
    quality=result['quality']
    assert quality['translated']=={'pages':2,'total':7,'percent':2/7*100}
    assert quality['local_qa']['ok']==2 and quality['local_qa']['in_scope']==6
    auditor=quality['auditor']
    # p1: newest matching audit (defect) wins; p3: stale-sha ok ignored -> defect.
    assert auditor['verdicts']=={'1':'defect','2':'ok','3':'defect'}
    assert auditor['audited']==3 and auditor['ok']==1 and auditor['not_audited_pages']==[7]
    assert sorted(auditor['skipped_files'])==['batch-03.json','batch-04.json']
    assert [d['page'] for d in auditor['defects']]==[1,3]
    assert auditor['defects'][1]['findings']==[{'code':'MERGE','evidence':'Créditos fundidos: «Consultora Kim Farley Editoras»'}]
    text=(book/'out/synthetic_informe.md').read_text()
    header=text.split('\n\n')[1]
    assert '**Porcentaje de páginas traducidas:** 28,6 % (2/7)' in header
    assert '**Porcentaje de acierto según la QA local:** 33,3 % (2/6' in header
    assert '**Porcentaje de acierto según el book-auditor:** 33,3 % (1/3' in header
    assert '1 páginas con candidato sin auditar' in header
    assert 'p1 (batch-02): EN — «THE RAM» en la rueda' in text and 'p3 (batch-01): MERGE' in text
    assert not list((book/'out').glob('.*'))  # atomic writes leave no temporaries


def test_assemble_final_include_review_uses_current_candidates(offline_book,tmp_path):
    import pymupdf as fitz
    from astro_translate import production
    _,_,config,_=offline_book
    book,runner,shas=_assembly_book(tmp_path,config)
    result=production.assemble_final(book,config_path=config,include_review=True)
    assert result['include_review'] and result['original_pages']==[4,5,6]
    by={p['page']:p for p in result['pages']}
    assert by[3]['provenance']=='translated_unreviewed' and by[7]['provenance']=='translated_unreviewed'
    assert by[3]['review_reasons']==['vlm_missing_or_findings','visual_graphics_review_required']
    assert by[7]['review_reasons']==['local_fidelity_or_text_quality_failed']
    assert by[3]['candidate_sha256']==shas[3] and by[1]['provenance']=='translated_ok'
    assert by[5]['reason']=='failed' and by[5]['review_reasons']==['BackendError']
    assert by[4]['reason']=='deferred_raster' and by[6]['reason']=='no_current_candidate'
    assert result['quality']['translated']['pages']==4
    with fitz.open(result['translated_pdf']) as final:
        assert 'Traduccion 3' in final[2].get_text() and 'Original page 4' in final[3].get_text()
        assert 'Original page 6' in final[5].get_text() and 'Traduccion 7' in final[6].get_text()
    with fitz.open(result['comparison_pdf']) as comparison:
        assert 'Traduccion sin revisar' in comparison[2].get_text()
    assert '**Porcentaje de páginas traducidas:** 57,1 % (4/7)' in (book/'out/synthetic_informe.md').read_text()


def test_cli_assemble_final_include_review_flag(monkeypatch,capsys):
    from astro_translate import __main__ as cli, production
    seen=[]
    monkeypatch.setattr(production,'assemble_final',lambda book,**k: seen.append(k) or {'status':'ok'})
    assert cli.main(['assemble-final','--book','x'])==0
    assert cli.main(['assemble-final','--book','x','--include-review'])==0
    assert [k['include_review'] for k in seen]==[False,True]
