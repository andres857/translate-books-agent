"""Offline 2G contract and real failed provider recordings (no inference)."""
import copy
import json
from pathlib import Path
import pytest
import pymupdf as fitz
from astro_translate import llm
from astro_translate.style_spans import encode
from test_llm import setup, FakeBackend, use, response

ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/'books/the-secrets-of-astrology'
LIGHT={'font':'Soho-Light','bold':False,'weight':300,'color':0}
BOLD={'font':'Soho-Bold','bold':True,'weight':700,'color':123}

def astra_escalation(config):
    """Escalation is configuration now; these historical contracts pin the former astra/high pair."""
    config.write_text(config.read_text().replace('fallback="none"','fallback="none"\nescalation_model="gpt-6-astra"\nescalation_effort="high"'))

def request():
    source,pairs=encode([{'text':'Sun ','style':BOLD},{'text':'example 12','style':LIGHT}])
    return {'id':'a','source':source,'_style_pairs':pairs,'_emphasis_protocol':'flat-v1'}

def test_flat_wire_protects_values_and_preserves_base_style():
    from astro_translate.emphasis import reconstruct
    r=request();wire,maps=llm._prepare('translate_page',{'blocks':[r]},[],[])
    assert '{v' not in wire['blocks'][0]['source']
    assert '12' not in llm._prompt('translate_page',wire)
    answer={'translation':'Sol ejemplo {n1}','emphasis':[{'style':'bold','text':'Sol','occurrence':None}]}
    assert llm._validate('translate_page',{'blocks':[{'id':'a',**answer}]},wire,{'type':'object'})
    runs=reconstruct(answer,r)
    assert runs[0]['style']==BOLD and runs[1]['style']==LIGHT
    assert ''.join(x['text'] for x in runs)==answer['translation']

@pytest.mark.parametrize('annotations',[
    [], [{'style':'bold','text':''}], [{'style':'unknown','text':'Sol'}],
    [{'style':'bold','text':'missing'}], [{'style':'bold','text':'Sol'},{'style':'bold','text':'Sol ejemplo'}],
])
def test_invalid_annotations_rejected(annotations):
    from astro_translate.emphasis import reconstruct
    with pytest.raises(ValueError):reconstruct({'translation':'Sol ejemplo','emphasis':annotations},request())

def test_occurrence_disambiguates_and_order_is_required():
    from astro_translate.emphasis import reconstruct
    answer={'translation':'Sol y Sol','emphasis':[{'style':'bold','text':'Sol'}]}
    with pytest.raises(ValueError,match='unique'):reconstruct(answer,request())
    answer['emphasis'][0]['occurrence']=2
    assert reconstruct(answer,request())[-1]['text']=='Sol'

@pytest.mark.parametrize('path',sorted((ROOT/'tests/fixtures/phase2g').glob('*.json')))
def test_real_bad_recordings_are_rejected_with_new_contract(path):
    saved=json.loads(path.read_text());rows=[]
    for row in saved['wire']['blocks']:
        rows.append({**row,'_style_pairs':saved['wire']['_style_pairs'][row['id']], '_emphasis_protocol':'flat-v1'})
    wire,_=llm._prepare('translate_page',{'blocks':rows},[],[])
    with pytest.raises(llm.ValidationFailure,match='emphasis'):
        llm._validate('translate_page',saved['response'],wire,{'type':'object'})

def test_retry_escalates_and_logs_effective_model(setup,monkeypatch):
    book,config,payload=setup;payload['blocks']=[request()];astra_escalation(config)
    good=response(text='Sol ejemplo {n1}');good['blocks'][0]['emphasis']=[{'style':'bold','text':'Sol','occurrence':None}]
    fake=FakeBackend([response(text='{v1}{v2}')]*3+[good]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',payload)
    assert len(fake.calls)==4 and fake.calls[-1][3:]==('gpt-6-astra','high')
    assert result.data['blocks'][0]['translation']=='Sol ejemplo 12'
    assert result.model=='gpt-6-astra'
    log=[json.loads(x) for x in (book/'work/costs.jsonl').read_text().splitlines()]
    assert log[-1]['escalation']=='emphasis_validation' and log[-1]['model']=='gpt-6-astra'
    assert llm.run_task('translate_page',payload).cached

@pytest.mark.parametrize('n',[36,40,44,48,52,56,60])
def test_real_growth_obeys_editorial_margin(n):
    from astro_translate.fidelity import extract_fidelity_units,_candidate_boxes
    from astro_translate.editorial_margins import editorial_bounds
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[n-1];units=extract_fidelity_units(page);left,right=editorial_bounds(page)
        for u in units:
            if u['kind']=='paragraph':
                boxes=_candidate_boxes(page,u,units,[])
                assert all(b.x0>=min(left,u['bbox'][0])-.1 and b.x1<=max(right,u['bbox'][2])+.1 for b in boxes)

@pytest.mark.parametrize('text,missing',[
    ('SABES LO QUE QUIERES, Y LO QUIERES YA!',True),('¿Lo sabes? ¡Sí!',False),
    ('¡Sí! Lo sabes?',True),('{n1}!',False),('¡Claro! Otra frase!',True),
])
def test_spanish_opening_punctuation(text,missing):
    from astro_translate.text_quality import text_issues
    assert any(i['code']=='missing_opening_punctuation' for i in text_issues('',text,'u'))==missing

def test_clear_opening_correction_is_recorded_and_preserves_styles():
    from astro_translate.text_quality import repair_composition
    u={'id':'u','source':'YOU KNOW WHAT YOU WANT, AND YOU WANT IT NOW!',
       'target':'SABES LO QUE QUIERES, Y LO QUIERES YA!',
       'target_runs':[{'text':'SABES LO QUE QUIERES, ','style':LIGHT},{'text':'Y LO QUIERES YA!','style':BOLD}]}
    fixed=repair_composition(u)
    assert fixed['target']=='¡'+u['target']
    assert fixed['target_runs'][0]['style']==LIGHT and fixed['target_runs'][1]['style']==BOLD
    record=fixed['grammatical_corrections'][0]
    assert record['type']=='grammatical_correction' and record['source']==u['source'] and record['before']==u['target']
    assert repair_composition(fixed)==fixed


def test_real_three_recordings_drive_retries_before_astra(setup,monkeypatch):
    from astro_translate.emphasis import contract
    book,config,payload=setup;astra_escalation(config)
    # Recorded responses remain byte-for-byte fixtures. Echo is mechanical, not translation.
    recordings=[json.loads(p.read_text()) for p in sorted((ROOT/'tests/fixtures/phase2g').glob('*.json'))]
    monkeypatch.setattr(llm,'glossary_matches',lambda *a,**k:[])
    first=recordings[0]['wire']
    payload.pop('_page');payload['_pages']=[21,23,25,26,33]
    payload['blocks']=[{'id':r['id'],'source':r['source'],'_style_pairs':first['_style_pairs'][r['id']]} for r in first['blocks']]
    def echo(wire):
        return {'blocks':[{'id':r['id'],'translation':r['source'],'emphasis':[{**a,'occurrence':None} for a in r['emphasis_source']],
                          'glossary_terms_used':[]} for r in wire['blocks']]}
    fake=FakeBackend([r['response'] for r in recordings]+[echo]);use(monkeypatch,fake)
    result=llm.run_task('translate_page',payload)
    assert len(fake.calls)==4 and result.model=='gpt-6-astra'
    assert all('validation_error' in c[1] for c in fake.calls[1:])

@pytest.mark.parametrize('style',[{'bold':True,'color':0xff0000,'weight':700},{'italic':True,'weight':300,'color':0}])
def test_multiple_emphasis_style_keys_and_reordering(style):
    from astro_translate.emphasis import contract,reconstruct
    source,pairs=encode([{'text':'base ','style':LIGHT},{'text':'first ','style':BOLD},{'text':'second','style':style}])
    req={'source':source,'_style_pairs':pairs};c=contract(req)
    answer={'translation':'second base first','emphasis':[{'style':c['phrases'][1]['style'],'text':'second'}, {'style':c['phrases'][0]['style'],'text':'first'}]}
    runs=reconstruct(answer,req)
    assert runs[0]['style']==style and runs[-1]['style']==BOLD and runs[1]['style']==LIGHT


def test_flat_saved_answer_with_protected_emphasis_validates_locally():
    from astro_translate.retranslation import validate_answer
    source,pairs=encode([{'text':'base ','style':LIGHT},{'text':'12 values','style':BOLD}])
    req={'source':source,'_style_pairs':pairs,'id':'u'}
    validate_answer(req,{'translation':'base 12 values','emphasis':[{'style':'bold','text':'12 values','occurrence':None}]})


def test_margin_qa_rejects_overflow_even_if_fit_claims_success():
    from astro_translate.editorial_margins import margin_issues
    with fitz.open() as source,fitz.open() as dest:
        a=source.new_page(width=600,height=700);a.insert_text((60,100),'Original text',fontsize=12)
        b=dest.new_page(width=600,height=700);b.insert_text((60,100),'Very long replacement extending past the original editorial margin',fontsize=12)
        box=a.get_text('blocks')[0][:4]
        unit={'id':'u','bbox':box,'edit_bbox':[60,80,590,110],'target':'Very long replacement extending past the original editorial margin'}
        assert margin_issues(a,b,[unit])[0]['code']=='editorial_margin'


def test_plan_contains_historical_quotes_and_protocol_without_inference(tmp_path,monkeypatch):
    import importlib.util
    from astro_translate.production import Production
    spec=importlib.util.spec_from_file_location('prepare2g',ROOT/'scripts/prepare_phase2e_retranslations.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(llm,'run_task',lambda *a,**k:pytest.fail('offline preparation'))
    runner=Production(BOOK);runner.folder=tmp_path/'isolated-production';runner.folder.mkdir()
    plan=module.build_plan(runner,'15,17,21-40',tmp_path)
    assert plan['emphasis_protocol']=='flat-v1'
    assert {15,17,21,23,25,26,33}<={j['unit']['page'] for j in plan['jobs']}
    assert all(j['request']['_emphasis_protocol']=='flat-v1' for j in plan['jobs'])
    assert all(any(j['kind']=='quote' and j['unit']['page']==page for j in plan['jobs']) for page in (15,17))

def test_all_retries_exhausted_never_cache_invalid_emphasis(setup,monkeypatch):
    import sqlite3
    book,config,payload=setup;payload['blocks']=[request()];astra_escalation(config)
    fake=FakeBackend([response(text='Sol')]*6);use(monkeypatch,fake)
    with pytest.raises(llm.ValidationFailure):llm.run_task('translate_page',payload)
    assert len(fake.calls)==6
    assert [c[3] for c in fake.calls[3:]]==['gpt-6-astra']*3
    with sqlite3.connect(book/'work/llm_cache.sqlite') as db:
        assert db.execute('SELECT count(*) FROM blocks').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM responses').fetchone()[0]==0


def test_merge_split_allowance_and_multiple_style_order():
    from astro_translate.emphasis import reconstruct
    runs=[{'text':'a ','style':BOLD},{'text':'base ','style':LIGHT},
          {'text':'b ','style':BOLD},{'text':'tail ','style':LIGHT},{'text':'c','style':BOLD}]
    source,pairs=encode(runs);req={'source':source,'_style_pairs':pairs}
    ans={'translation':'a base b tail c','emphasis':[{'style':'bold','text':'a base b'},{'style':'bold','text':'c'}]}
    assert reconstruct(ans,req)
    ans['emphasis']=[{'style':'bold','text':'a base b tail c'}]
    with pytest.raises(ValueError,match='count'):reconstruct(ans,req)


def test_flat_glossary_validation_spans_whole_translation():
    source,pairs=encode([{'text':'Earth-','style':BOLD},{'text':'centred','style':LIGHT}])
    req={'id':'u','source':source,'_style_pairs':pairs,'_emphasis_protocol':'flat-v1'}
    wire,_=llm._prepare('translate_page',{'blocks':[req]},[],[{'source':'Earth-centred','target':'geocéntrico'}])
    ans={'blocks':[{'id':'u','translation':'geocéntrica','emphasis':[{'style':'bold','text':'geocéntrica'}]}]}
    assert llm._validate('translate_page',ans,wire,{'type':'object'})
    ans['blocks'][0].update(translation='otra',emphasis=[{'style':'bold','text':'otra'}])
    with pytest.raises(llm.ValidationFailure,match='Earth-centred => geocéntrico'):
        llm._validate('translate_page',ans,wire,{'type':'object'})


def test_new_contract_invalidates_only_paired_block_in_mixed_batch(tmp_path,monkeypatch):
    from astro_translate import emphasis
    from astro_translate.translation_context import block_keys
    for name in ('g','s','d','p'):(tmp_path/name).write_text(name)
    (tmp_path/'j').write_text('{}')
    cfg={'tasks':{'translate_page':{'model':'test','effort':'low','prompt':str(tmp_path/'p'),'schema':str(tmp_path/'j')}}}
    payload={'blocks':[request(),{'id':'plain','source':'base'}]}
    args=(cfg,tmp_path/'g',tmp_path/'s',tmp_path/'d',payload)
    before=block_keys(*args);monkeypatch.setattr(emphasis,'INSTRUCTION',emphasis.INSTRUCTION+' changed')
    after=block_keys(*args)
    assert before['plain']==after['plain'] and before['a']!=after['a']

@pytest.mark.parametrize('text',['MC!','12?','{n1}!','♈!'])
def test_protected_tokens_do_not_create_punctuation_sentences(text):
    from astro_translate.text_quality import opening_punctuation_issues
    assert not opening_punctuation_issues(text,'u')


def test_margin_clipping_preserves_centered_anchor():
    from astro_translate.editorial_margins import constrain
    with fitz.open() as doc:
        page=doc.new_page(width=600,height=700);page.insert_text((80,100),'Editorial text',fontsize=12)
        unit={'id':'u','bbox':[80,80,140,100],'alignment':'center'}
        boxes=constrain(page,unit,[fitz.Rect(unit['bbox']),fitz.Rect(50,80,170,100)])
        assert all(b.x0+b.x1==220 for b in boxes)


def test_golden_comparison_does_not_hide_new_failures():
    import importlib.util,sys
    sys.path.insert(0,str(ROOT/'scripts'))
    try:
        spec=importlib.util.spec_from_file_location('golden2g',ROOT/'scripts/check_phase2g_golden.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:sys.path.pop(0)
    good={**{k:True for k in module.PRESERVATION},'typography_audit':{'ok':True},
          'non_text_pixel_diff':0,'min_scale':.8,'residual_english':['old'],'text_quality_issues':[]}
    assert module.qa_nonregression(good,good)
    for key,value in [('residual_english',['new']),('text_quality_issues',[{'code':'new'}]),('numbers_equal',False),('non_text_pixel_diff',.01),('min_scale',.74)]:
        assert not module.qa_nonregression(good,{**good,key:value})

def test_single_style_empty_plain_translation_is_rejected():
    from astro_translate.emphasis import reconstruct
    source,pairs=encode([{'text':'whole unit','style':LIGHT}])
    with pytest.raises(ValueError,match='empty translation'):
        reconstruct({'translation':'','emphasis':[]},{'source':source,'_style_pairs':pairs})

def test_emphasis_cannot_split_a_protected_placeholder():
    from astro_translate.emphasis import reconstruct
    ans={'translation':'Sol {n1}','emphasis':[{'style':'bold','text':'n1'}]}
    with pytest.raises(ValueError,match='protected marker'):
        reconstruct(ans,request())


def test_flat_quote_saved_then_composed_with_local_styles(tmp_path):
    from astro_translate.composition_corrections import quote_job,translate_quote_jobs,apply_corrections
    from astro_translate.emphasis import contract
    from astro_translate.fidelity import extract_fidelity_units
    from astro_translate.core import sha256
    book=tmp_path/'book';book.mkdir()
    with fitz.open() as doc:
        page=doc.new_page(width=600,height=500)
        page.insert_text((40,80),'"FIRST PART',fontname='Times-Bold',fontsize=30)
        page.insert_text((40,115),'SECOND PART."',fontname='Times-Roman',fontsize=30)
        doc.save(book/'source.pdf')
    g=tmp_path/'g.csv';g.write_text('source,target\n')
    style=tmp_path/'style.md';style.write_text('test')
    protected=tmp_path/'protected.txt';protected.write_text('')
    def mechanical_echo(task,payload):
        rows=[]
        for r in payload['blocks']:
            c=contract(r);rows.append({'id':r['id'],'translation':c['source'],'emphasis':c['phrases']})
        return llm.LLMResult({'blocks':rows},'test','low',0,0,'fake',False,0)
    with fitz.open(book/'source.pdf') as doc:
        units=extract_fidelity_units(doc[0]);jobs=quote_job(doc[0],units,sha256(book/'source.pdf'))
        assert len(jobs)==1
        translate_quote_jobs(jobs,book,g,style,protected,tmp_path/'unused','production',mechanical_echo)
        output,proofs=apply_corrections(units,doc[0],book,g,style,protected)
        quote=next(u for u in output if u.get('correction_provenance'))
        assert proofs and {r['style']['bold'] for r in quote['target_runs']}=={False,True}
        assert ''.join(r['text'] for r in quote['target_runs'])==quote['source']

def test_source_sized_native_insertion_nudge_is_not_margin_growth():
    from astro_translate.editorial_margins import constrain
    with fitz.open() as doc:
        p=doc.new_page(width=600,height=700);p.insert_text((80,100),'Text',fontsize=12)
        u={'bbox':[80,80,140,100],'alignment':'left','kind':'label','source':'—','target':'—'}
        native=fitz.Rect(79,80,139,100)
        assert native in constrain(p,u,[fitz.Rect(u['bbox']),native])
        assert all(b.x0>=79 for b in constrain(p,u,[fitz.Rect(u['bbox']),fitz.Rect(60,80,140,100)]))

def test_restoration_resolves_occurrence_when_hidden_values_are_equal():
    source,pairs=encode([{'text':'12 signs','style':BOLD},{'text':' and 12 signs','style':LIGHT}])
    req={'id':'u','source':source,'_style_pairs':pairs,'_emphasis_protocol':'flat-v1'}
    payload={'blocks':[req]};wire,maps=llm._prepare('translate_page',payload,[],[])
    answer={'blocks':[{'id':'u','translation':'{n1} signos y {n2} signos',
                       'emphasis':[{'style':'bold','text':'{n1} signos','occurrence':None}]}]}
    llm._validate('translate_page',answer,wire,{'type':'object'})
    restored=llm._restore('translate_page',answer,maps,payload)['blocks'][0]
    assert restored['emphasis'][0]['occurrence']==1
    from astro_translate.emphasis import reconstruct
    assert reconstruct(restored,req)[0]=={'text':'12 signos','style':BOLD}
    from astro_translate.retranslation import validate_answer
    validate_answer(req,{'translation':restored['translation'],'emphasis':restored['emphasis']})
    second={**restored,'emphasis':[{'style':'bold','text':'12 signos','occurrence':2}]}
    validate_answer(req,{'translation':second['translation'],'emphasis':second['emphasis']})
