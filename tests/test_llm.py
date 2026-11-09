import ast
import json
from pathlib import Path
from dataclasses import replace
import pytest
from astro_translate import llm

class FakeBackend:
    def __init__(self, responses): self.responses=list(responses); self.calls=[]
    def complete_json(self, task, payload, schema, model, effort, images=None):
        self.calls.append((task,payload,schema,model,effort))
        answer=self.responses.pop(0)
        if isinstance(answer,Exception): raise answer
        if callable(answer): answer=answer(payload)
        return llm.LLMResult(answer,model,effort,12,7,'codex_sdk',False,.01)

@pytest.fixture
def setup(tmp_path,monkeypatch):
    from astro_translate.core import approve_glossary,atomic_json
    import pymupdf
    book=tmp_path/'book'; book.mkdir(); d=pymupdf.open();d.new_page();d.save(book/'source.pdf');d.close()
    g=tmp_path/'g.csv';g.write_text('source,target,tgt_lng\nSun,Sol,es\nSun sign,signo solar,es\n')
    s=tmp_path/'style.md';s.write_text('Español neutro.');p=tmp_path/'protected.txt';p.write_text('MC\n')
    approve_glossary(book,g,s,'TEST ONLY',p)
    atomic_json(book/'work/manifest.json',{'pages':[{'page':1,'status':'translated'}]})
    schema={'type':'object','properties':{'blocks':{'type':'array','items':{'type':'object','properties':{'id':{'type':'string'},'translation':{'type':'string'},'glossary_terms_used':{'type':'array','items':{'type':'string'}}},'required':['id','translation','glossary_terms_used'],'additionalProperties':False}}},'required':['blocks'],'additionalProperties':False}
    sp=tmp_path/'schema.json';sp.write_text(json.dumps(schema));pr=tmp_path/'prompt.md';pr.write_text('Translate each block.')
    config=tmp_path/'pipeline.toml';config.write_text(f'''[provider]\nbackend="codex_sdk"\nfallback="none"\nmax_parallel=2\n[quota]\nbackoff_seconds=[0,0,0]\npause_exit_code=75\n[batching]\nmax_chars_per_call=6000\n[paths]\nglossary="{g}"\nstyle_guide="{s}"\ndo_not_translate="{p}"\n[tasks.translate_page]\nmodel="test-model"\neffort="low"\nprompt="{pr}"\nschema="{sp}"\n''')
    payload={'_book':str(book),'_config':str(config),'_page':1,'blocks':[{'id':'a','source':'Sun'}]}
    return book,config,payload

def response(id='a',text='Sol'):return {'blocks':[{'id':id,'translation':text,'glossary_terms_used':['Sol']}]}

def use(monkeypatch,fake):monkeypatch.setattr(llm,'_make_backend',lambda *a,**k:fake)


@pytest.mark.parametrize(('source','bad','good'), [
    ('EDUCATION', 'EDUCACION', 'EDUCACIÓN'),
    ('COMMUNICATION', 'COMUNICACION', 'COMUNICACIÓN'),
    ('The Zodiac', 'El ZODIACO', 'El ZODÍACO'),
    ('the Teapot', 'la Tetera la Tetera', 'la Tetera'),
])
def test_i6_text_retry_and_both_old_cache_layers(setup, monkeypatch, source, bad, good):
    import sqlite3
    book, _, payload = setup
    payload['blocks'] = [{'id': 'a', 'source': source}]
    fake = FakeBackend([response(text=bad), response(text=good), response(text=good)])
    use(monkeypatch, fake)
    assert llm.run_task('translate_page', payload).data['blocks'][0]['translation'] == good
    assert len(fake.calls) == 2 and 'validation_error' in fake.calls[1][1]
    with sqlite3.connect(book/'work/llm_cache.sqlite') as db:
        for table in ('blocks', 'responses'):
            for key, raw in db.execute(f'SELECT key,result FROM {table}').fetchall():
                db.execute(f'UPDATE {table} SET result=? WHERE key=?', (raw.replace(good, bad), key))
    assert not llm.run_task('translate_page', payload).cached
    assert len(fake.calls) == 3


def test_i6_persistent_failure_never_cached(setup, monkeypatch):
    import sqlite3
    book, _, payload = setup
    payload['blocks'] = [{'id': 'a', 'source': 'EDUCATION'}]
    use(monkeypatch, FakeBackend([response(text='EDUCACION')]*3))
    with pytest.raises(llm.ValidationFailure, match='uppercase_accent'):
        llm.run_task('translate_page', payload)
    with sqlite3.connect(book/'work/llm_cache.sqlite') as db:
        assert db.execute('SELECT count(*) FROM blocks').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM responses').fetchone()[0] == 0


def test_i6_caption_boundary_retry_masks_context_values(setup, monkeypatch):
    _, _, payload = setup
    payload['blocks'] = [
        {'id': 'a', 'source': 'the Teapot', 'quality_neighbors': [
            {'id': 'next', 'request_id': 'b', 'side': 'after', 'source': 'asterism 153 ♈', 'target': 'la Tetera 153 ♈'}]},
        {'id': 'b', 'source': 'asterism 153 ♈'},
    ]
    bad = {'blocks': [response('a', 'la Tetera')['blocks'][0],
                      response('b', 'la Tetera {n1} {n2}')['blocks'][0]]}
    fake = FakeBackend([bad]*3)
    use(monkeypatch, fake)
    with pytest.raises(llm.ValidationFailure, match='repeated_ngram'):
        llm.run_task('translate_page', payload)
    assert len(fake.calls) == 3
    wire = json.dumps([call[1] for call in fake.calls], ensure_ascii=False)
    assert '153' not in wire and '♈' not in wire


def test_numeric_quantifier_retries_and_preserves_hidden_values(setup,monkeypatch):
    book,c,p=setup
    p['blocks']=[{'id':'a','source':'The 12 signs'}]
    fake=FakeBackend([response(text='Los signos {n1}'),response(text='Los {n1} signos')]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',p)
    assert result.data['blocks'][0]['translation']=='Los 12 signos'
    assert len(fake.calls)==2
    assert 'numeric quantifier' in fake.calls[1][1]['validation_error']
    assert '12' not in json.dumps([call[1] for call in fake.calls])
    assert llm.run_task('translate_page',p).cached


def test_numeric_quantifier_persistent_failure_is_not_cached(setup,monkeypatch):
    import sqlite3
    book,c,p=setup;p['blocks']=[{'id':'a','source':'The 12 signs'}]
    fake=FakeBackend([response(text='Los signos {n1}')]*3);use(monkeypatch,fake)
    with pytest.raises(llm.ValidationFailure,match='numeric quantifier'):
        llm.run_task('translate_page',p)
    with sqlite3.connect(book/'work/llm_cache.sqlite') as db:
        assert db.execute('SELECT count(*) FROM blocks').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM responses').fetchone()[0]==0
    assert json.loads((book/'work/review_queue.json').read_text())[0]['reason']=='llm_validation'


def test_numeric_quantifier_rechecks_both_legacy_cache_layers(setup,monkeypatch):
    import sqlite3
    book,c,p=setup;p['blocks']=[{'id':'a','source':'The 12 signs'}]
    fake=FakeBackend([response(text='Los {n1} signos')]*2);use(monkeypatch,fake)
    llm.run_task('translate_page',p)
    # Simulate pre-I2 cached output, only in the isolated temporary book.
    with sqlite3.connect(book/'work/llm_cache.sqlite') as db:
        for table in ('blocks','responses'):
            for key,raw in db.execute(f'SELECT key,result FROM {table}').fetchall():
                db.execute(f'UPDATE {table} SET result=? WHERE key=?',(raw.replace('12 signos','signos 12'),key))
    result=llm.run_task('translate_page',p)
    assert not result.cached and len(fake.calls)==2
    assert result.data['blocks'][0]['translation']=='Los 12 signos'

def test_cache_identical_call_does_not_spend(setup,monkeypatch):
    book,c,p=setup;fake=FakeBackend([response()]);use(monkeypatch,fake)
    assert not llm.run_task('translate_page',p).cached
    assert llm.run_task('translate_page',p).cached
    assert len(fake.calls)==1

def test_ids_schema_glossary_retry(setup,monkeypatch):
    book,c,p=setup;fake=FakeBackend([response('wrong'),response()]);use(monkeypatch,fake)
    assert llm.run_task('translate_page',p).data==response()
    assert 'validation_error' in fake.calls[1][1]

def test_protected_values_and_placeholders_never_sent(setup,monkeypatch):
    book,c,p=setup;p['blocks'][0]['source']='Sun 15° ☉ {v9}'
    def answer(payload):return response(text=payload['blocks'][0]['source'].replace('Sun','Sol'))
    fake=FakeBackend([answer]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',p)
    assert result.data['blocks'][0]['translation']=='Sol 15° ☉ {v9}'
    wire=json.dumps(fake.calls[0][1],ensure_ascii=False)
    assert '15' not in wire and '☉' not in wire and '°' not in wire

def test_duplicate_ids_rejected_and_review_queue(setup,monkeypatch):
    book,c,p=setup;bad={'blocks':[response()['blocks'][0]]*2};fake=FakeBackend([bad,bad,bad]);use(monkeypatch,fake)
    with pytest.raises(llm.ValidationFailure):llm.run_task('translate_page',p)
    assert json.loads((book/'work/review_queue.json').read_text())[0]['reason']=='llm_validation'

def test_subset_retry_only_problem_ids(setup,monkeypatch):
    book,c,p=setup;p['blocks'].append({'id':'b','source':'Moon'})
    bad={'blocks':[response()['blocks'][0],{'id':'b','translation':'Moon {v99}','glossary_terms_used':[]}]}
    good={'blocks':[{'id':'b','translation':'Luna','glossary_terms_used':[]}]}
    fake=FakeBackend([bad,bad,good]);use(monkeypatch,fake)
    out=llm.run_task('translate_page',p)
    assert [b['id'] for b in fake.calls[-1][1]['blocks']]==['b']
    assert len(out.data['blocks'])==2

def test_quota_pause_and_exit_code(setup,monkeypatch):
    book,c,p=setup;fake=FakeBackend([llm.QuotaExceeded('usageLimitExceeded')]*4);use(monkeypatch,fake)
    with pytest.raises(llm.QuotaPaused) as e:llm.run_task('translate_page',p)
    assert e.value.exit_code==75
    assert json.loads((book/'work/manifest.json').read_text())['pages'][0]['status']=='pending'
    assert (book/'work/quota_pause.json').exists()

def test_missing_g3_prevents_backend(setup,monkeypatch):
    book,c,p=setup;(book/'work/glossary_approval.json').unlink();fake=FakeBackend([]);use(monkeypatch,fake)
    with pytest.raises(Exception,match='G3'):llm.run_task('translate_page',p)
    assert not fake.calls

def test_invalid_json_retries(setup,monkeypatch):
    book,c,p=setup;fake=FakeBackend(['not json',response()]);use(monkeypatch,fake)
    assert llm.run_task('translate_page',p).data==response()

def test_prompt_change_invalidates_cache(setup,monkeypatch):
    book,c,p=setup;fake=FakeBackend([response(),response()]);use(monkeypatch,fake)
    llm.run_task('translate_page',p);(c.parent/'prompt.md').write_text('Translate carefully.')
    assert not llm.run_task('translate_page',p).cached and len(fake.calls)==2

def test_cache_partition_by_backend(setup,monkeypatch):
    book,c,p=setup;fake=FakeBackend([response(),response()]);use(monkeypatch,fake)
    llm.run_task('translate_page',p);c.write_text(c.read_text().replace('backend="codex_sdk"','backend="claude_cli"'))
    assert not llm.run_task('translate_page',p).cached and len(fake.calls)==2

def test_import_boundary():
    violations=[]
    for path in [*Path('src').rglob('*.py'),*Path('scripts').rglob('*.py')]:
        if path.name=='llm.py':continue
        for node in ast.walk(ast.parse(path.read_text())):
            names=[n.name for n in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            if any(n.split('.')[0] in ('openai','openai_codex') for n in names):violations.append(str(path))
    assert not violations

def test_api_fallback_not_implicitly_enabled(setup,monkeypatch):
    book,c,p=setup;monkeypatch.setenv('OPENAI_API_KEY','not-a-real-key')
    fake=FakeBackend([llm.QuotaExceeded('usageLimitExceeded')]*4);use(monkeypatch,fake)
    with pytest.raises(llm.QuotaPaused):llm.run_task('translate_page',p)
    assert len(fake.calls)==4


def test_cross_page_block_cache_ignores_ids_and_geometry(setup,monkeypatch):
    book,c,p=setup;fake=FakeBackend([response()]);use(monkeypatch,fake)
    llm.run_task('translate_page',p)
    p['_page']=2;p['blocks'][0].update(id='new',bbox=[1,2,3,4])
    result=llm.run_task('translate_page',p)
    assert result.cached and result.input_tokens==0 and result.data['blocks'][0]['id']=='new'
    assert len(fake.calls)==1


def test_context_protected_values_never_wire(setup,monkeypatch):
    book,c,p=setup;p['context']={'title':'15° ♈','nested':['MC']}
    fake=FakeBackend([response()]);use(monkeypatch,fake);llm.run_task('translate_page',p)
    wire=json.dumps(fake.calls[0][1],ensure_ascii=False)
    assert '15' not in wire and '♈' not in wire and 'MC' not in wire


def test_duplicate_present_id_is_also_invalid():
    schema={'type':'object'};wire={'blocks':[{'id':'a'},{'id':'b'}]}
    with pytest.raises(llm.ValidationFailure) as e:llm._validate('translate_page',{'blocks':[{'id':'a'},{'id':'a'}]},wire,schema)
    assert e.value.ids=={'a','b'}


def test_chunking_keeps_page_batches_under_limit(setup,monkeypatch):
    book,c,p=setup;c.write_text(c.read_text().replace('max_chars_per_call=6000','max_chars_per_call=5'))
    p['blocks'].append({'id':'b','source':'Moon'})
    fake=FakeBackend([response(),{'blocks':[{'id':'b','translation':'Luna','glossary_terms_used':[]}]}]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',p)
    assert len(result.data['blocks'])==2 and len(fake.calls)==2
    assert all(sum(len(r['source']) for r in call[1]['blocks'])<=5 for call in fake.calls)


@pytest.mark.parametrize('name',['codex_sdk','codex_exec'])
def test_codex_backends_retired_with_clear_error(tmp_path,name):
    # Codex backends were retired on 2026-11-08 (docs/decisiones.md, Limpieza del sistema Codex).
    with pytest.raises(llm.BackendError,match='Codex backend retired 2026-11-08'):
        llm._make_backend(name,tmp_path/'work',{'backend':name})
    assert name in llm.SUBSCRIPTION_BACKENDS  # historical costs.jsonl rows stay readable
    assert not hasattr(llm,'CodexSDKBackend') and not hasattr(llm,'CodexExecBackend')
    assert not hasattr(llm,'_profile_overrides') and not hasattr(llm,'_safe_overrides') and not hasattr(llm,'_account_chatgpt')


def test_llm_module_has_no_codex_imports():
    tree=ast.parse(Path(llm.__file__).read_text())
    names=[a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
    names+=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(x.split('.')[0]=='openai_codex' for x in names)


def test_retired_backend_run_task_blocks_without_spending(setup):
    book,c,p=setup
    with pytest.raises(llm.BackendError,match='retired'):llm.run_task('translate_page',p)
    assert not (book/'work/costs.jsonl').exists() or 'ok' not in (book/'work/costs.jsonl').read_text()


def test_sandbox_cannot_use_repo_root(tmp_path):
    with pytest.raises(llm.IsolationError):llm._sandbox_folder(tmp_path/'work',{'sandbox_dir':str(tmp_path)})




def test_impossible_shortening_glossary_is_local_preflight(setup,monkeypatch):
    book,c,p=setup;c.write_text(c.read_text()+f'\n[tasks.shorten]\nmodel="test-model"\neffort="low"\nprompt="{c.parent / "prompt.md"}"\nschema="{c.parent / "schema.json"}"\n')
    p['blocks']=[{'id':'a','source':'signo solar','max_chars':8}]
    fake=FakeBackend([]);use(monkeypatch,fake)
    with pytest.raises(llm.ValidationFailure,match='Impossible'):llm.run_task('shorten',p)
    assert not fake.calls and (book/'work/review_queue.json').exists()




def test_quota_cli_returns_75(tmp_path,monkeypatch):
    from astro_translate import __main__
    def paused(*args,**kwargs):raise llm.QuotaPaused('test quota')
    monkeypatch.setattr(__main__,'native_translate',paused)
    assert __main__.main(['translate-native-pages','--book',str(tmp_path),'--pages','1','--phase','smoke'])==75


def test_oversized_single_span_is_split_and_rejoined(setup,monkeypatch):
    book,c,p=setup;c.write_text(c.read_text().replace('max_chars_per_call=6000','max_chars_per_call=8'))
    p['blocks'][0]['source']='Moon Moon Moon'
    def answer(payload):return {'blocks':[{'id':r['id'],'translation':r['source'].replace('Moon','Luna'),'glossary_terms_used':[]} for r in payload['blocks']]}
    fake=FakeBackend([answer,answer,answer]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',p)
    assert result.data['blocks'][0]['id']=='a' and result.data['blocks'][0]['translation']=='Luna Luna Luna'
    assert all(sum(len(x['source']) for x in call[1]['blocks'])<=8 for call in fake.calls)


def test_native_does_not_swallow_quota_from_fitting(setup,monkeypatch):
    from astro_translate import adapters
    import tomllib
    book,c,p=setup;config=tomllib.loads(c.read_text());paths=config['paths']
    monkeypatch.setattr(adapters,'run_task',lambda *a,**kw:llm.LLMResult({'blocks':[]},'mock','low',0,0,'codex_sdk',False,0))
    def paused(*args,**kwargs):raise llm.QuotaPaused('quota during grouped shorten')
    monkeypatch.setattr(adapters,'reinsert',paused)
    with pytest.raises(llm.QuotaPaused):adapters.native_translate(book,paths['glossary'],paths['style_guide'],paths['do_not_translate'],'1',config_path=c,phase='smoke')




def test_cached_input_tokens_are_logged_and_cache_is_zero(setup,monkeypatch):
    book,c,p=setup
    class Tokens(FakeBackend):
        def complete_json(self,*args,**kwargs):
            result=super().complete_json(*args,**kwargs);result.cached_input_tokens=9;return result
    fake=Tokens([response()]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',p)
    assert result.cached_input_tokens==9
    assert json.loads((book/'work/costs.jsonl').read_text().splitlines()[0])['cached_input_tokens']==9
    assert llm.run_task('translate_page',p).cached_input_tokens==0


def test_multi_page_quota_updates_every_affected_page(setup,monkeypatch):
    book,c,p=setup
    (book/'work/manifest.json').write_text(json.dumps({'pages':[{'page':7,'status':'translated'},{'page':15,'status':'translated'},{'page':121,'status':'translated'}]}))
    p.pop('_page');p['_pages']=[7,15];p['blocks']=[{'id':'p0007_a','source':'Sun'},{'id':'p0015_b','source':'Moon'}]
    fake=FakeBackend([llm.QuotaExceeded('usageLimitExceeded')]*4);use(monkeypatch,fake)
    with pytest.raises(llm.QuotaPaused):llm.run_task('translate_page',p)
    states={r['page']:r['status'] for r in json.loads((book/'work/manifest.json').read_text())['pages']}
    assert states=={7:'pending',15:'pending',121:'translated'}
    assert json.loads((book/'work/quota_pause.json').read_text())['pages']==[7,15]


def test_multi_page_invalid_response_reviews_only_affected_pages(setup,monkeypatch):
    book,c,p=setup;p.pop('_page');p['_pages']=[7,15];p['blocks']=[{'id':'p0007_a','source':'Sun'},{'id':'p0015_b','source':'Moon'}]
    fake=FakeBackend(['bad json']*3);use(monkeypatch,fake)
    with pytest.raises(llm.ValidationFailure):llm.run_task('translate_page',p)
    assert {r['page'] for r in json.loads((book/'work/review_queue.json').read_text())}=={7,15}


def test_three_pages_share_one_call_and_cache_ids_are_irrelevant(setup,monkeypatch):
    book,c,p=setup;p.pop('_page');p['_pages']=[7,15,121];p['blocks']=[{'id':'p0007_a','source':'Sun'},{'id':'p0015_b','source':'Moon'},{'id':'p0121_c','source':'Planet'}]
    def answer(wire):return {'blocks':[{'id':r['id'],'translation':r['source'].replace('Sun','Sol'),'glossary_terms_used':['Sol'] if r['source']=='Sun' else []} for r in wire['blocks']]}
    fake=FakeBackend([answer]);use(monkeypatch,fake);llm.run_task('translate_page',p)
    assert len(fake.calls)==1
    p['_pages']=[8,16,122]
    for row,pageno in zip(p['blocks'],p['_pages']):row['id']=f'p{pageno:04d}_new'
    result=llm.run_task('translate_page',p)
    assert result.cached and len(fake.calls)==1 and [r['id'] for r in result.data['blocks']]==[r['id'] for r in p['blocks']]


def test_quota_marks_pages_depending_on_deduplicated_block(setup,monkeypatch):
    book,c,p=setup;p.pop('_page');p['_pages']=[7,15];p['blocks']=[{'id':'p0007_a','source':'Sun'},{'id':'p0015_a','source':'Sun'}]
    (book/'work/manifest.json').write_text(json.dumps({'pages':[{'page':7,'status':'translated'},{'page':15,'status':'translated'}]}))
    fake=FakeBackend([llm.QuotaExceeded('usageLimitExceeded')]*4);use(monkeypatch,fake)
    with pytest.raises(llm.QuotaPaused):llm.run_task('translate_page',p)
    assert all(r['status']=='pending' for r in json.loads((book/'work/manifest.json').read_text())['pages'])


def test_oversized_fragments_keep_page_prefix_and_quota_state(setup,monkeypatch):
    book,c,p=setup;c.write_text(c.read_text().replace('max_chars_per_call=6000','max_chars_per_call=8'))
    p.pop('_page');p['_pages']=[7];p['blocks']=[{'id':'p0007_unit','source':'Moon Moon Moon'}]
    (book/'work/manifest.json').write_text(json.dumps({'pages':[{'page':7,'status':'translated'}]}))
    fake=FakeBackend([llm.QuotaExceeded('usageLimitExceeded')]*4);use(monkeypatch,fake)
    with pytest.raises(llm.QuotaPaused):llm.run_task('translate_page',p)
    assert all(row['id'].startswith('p0007_') for call in fake.calls for row in call[1]['blocks'])
    assert json.loads((book/'work/quota_pause.json').read_text())['pages']==[7]
    assert json.loads((book/'work/manifest.json').read_text())['pages'][0]['status']=='pending'


def test_canonical_n_markers_restore_numbers_degrees_and_glyphs(setup,monkeypatch):
    book,c,p=setup;p['blocks'][0]['source']='Sun 12 15°23′ ☉ {v9} Mc'
    def echo(wire):
        text=wire['blocks'][0]['source'];assert '{n1}' in text and '{v9}' in text
        assert all(s not in text for s in ('12','15','23','°','′','☉','Mc'))
        return response(text=text.replace('Sun','Sol'))
    fake=FakeBackend([echo]);use(monkeypatch,fake)
    assert llm.run_task('translate_page',p).data['blocks'][0]['translation']=='Sol 12 15°23′ ☉ {v9} Mc'


def test_style_markers_must_keep_order(setup,monkeypatch):
    book,c,p=setup;p['blocks'][0]={'id':'a','source':'{v1}Sun{v2}','_style_markers':['{v1}','{v2}']}
    bad=response(text='{v2}Sol{v1}');fake=FakeBackend([bad,bad,bad]);use(monkeypatch,fake)
    with pytest.raises(llm.ValidationFailure):llm.run_task('translate_page',p)


def test_glossary_spanning_style_markers_is_required(setup,monkeypatch):
    book,c,p=setup;p['blocks'][0]={'id':'a','source':'{v1}Sun {v2}sign{v3}','_style_markers':['{v1}','{v2}','{v3}']}
    good=response(text='{v1}signo {v2}solar{v3}');good['blocks'][0]['glossary_terms_used']=['signo solar']
    fake=FakeBackend([good]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',p)
    assert fake.calls[0][1]['blocks'][0]['glossary']==[{'source':'Sun sign','target':'signo solar'}]
    assert result.data['blocks'][0]['translation']=='{v1}signo {v2}solar{v3}'


def test_fidelity_routes_dates_locally_and_masks_numeric_prose(setup,monkeypatch):
    from astro_translate import adapters,fidelity
    from astro_translate.core import approve_glossary,atomic_json
    import pymupdf,tomllib
    book,c,p=setup;cfg=tomllib.loads(c.read_text());paths=cfg['paths']
    doc=pymupdf.open()
    for _ in range(7):doc.new_page()
    doc.save(book/'source.pdf');doc.close();approve_glossary(book,paths['glossary'],paths['style_guide'],'TEST ONLY',paths['do_not_translate'])
    atomic_json(book/'work/backend_approval.json',{'approved':True,'approved_by':'TEST ONLY','backend':'codex_sdk','route':'pymupdf','backend_config_sha256':adapters.backend_config_digest(cfg),'authorized_phases':['fidelity']})
    style={'font':'mock','size':10,'color':0,'bold':False}
    def extract(*args):
        sources=['March 21–April 20','Sun has 12 planets.']
        return [{'id':f'u{i}','source':source,'bbox':[0,0,100,10],'spans':[],'style':style,'style_runs':[{'start':0,'end':len(source),'style':style}]} for i,source in enumerate(sources)]
    monkeypatch.setattr(fidelity,'extract_fidelity_units',extract)
    def answer(wire):
        assert len(wire['blocks'])==1 and 'March' not in json.dumps(wire) and '12' not in wire['blocks'][0]['source']
        return {'blocks':[{'id':r['id'],'translation':r['source'].replace('Sun','Sol'),'glossary_terms_used':['Sol']} for r in wire['blocks']]}
    fake=FakeBackend([answer]);use(monkeypatch,fake)
    result=adapters.fidelity_translate(book,paths['glossary'],paths['style_guide'],paths['do_not_translate'],'7',config_path=c,reinsert_pages=False)
    assert result['batches'][0]['deterministic_dates']==1 and len(fake.calls)==1
    data=json.loads((book/'work/fidelity/p0007-translation.json').read_text())
    assert data['blocks'][0]['target']=='21 de marzo–20 de abril'
    assert '12' in data['blocks'][1]['target']
    # Same source and dependencies reuse exact sentence memory, without a call.
    adapters.fidelity_translate(book,paths['glossary'],paths['style_guide'],paths['do_not_translate'],'7',config_path=c,reinsert_pages=False)
    assert len(fake.calls)==1


def test_symbol_font_nested_span_is_never_sent(setup,monkeypatch):
    book,c,p=setup;p['blocks'][0]['spans']=[{'source':'A','font':'AstroGadget'}]
    fake=FakeBackend([]);use(monkeypatch,fake)
    with pytest.raises(llm.Blocked,match='Symbol-font'):llm.run_task('translate_page',p)
    assert not fake.calls


def test_task_structured_schemas_require_every_declared_property():
    import json
    from pathlib import Path
    def visit(node):
        if isinstance(node,dict):
            if node.get("type")=="object":
                assert set(node.get("required",[]))==set(node.get("properties",{}))
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(json.loads(Path("schemas/translate_page.json").read_text()))


def test_hyphenated_approved_phrase_precedes_component():
    from astro_translate.language import glossary_matches
    terms=[{'source':'Sun','target':'Sol'},{'source':'Sun sign','target':'signo solar'}]
    for text in ('Sun sign','Sun-sign','Sun‑sign','Sun  sign'):
        assert glossary_matches(text,terms)==[terms[1]]
    assert glossary_matches('Sun-sign and the Sun',terms)==[terms[1],terms[0]]
