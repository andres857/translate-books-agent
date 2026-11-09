"""Phase 2E source-bound display quotation / glossary regressions, offline."""
import copy
import json
from pathlib import Path
import pymupdf as fitz
import pytest
from astro_translate import llm
from astro_translate.editorial_rules import quote_retranslation_units
from astro_translate.text_quality import text_issues, unit_issues

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT/'books/the-secrets-of-astrology'
FIXTURES = ROOT/'tests/fixtures/phase2e'


def saved(n):
    return json.loads((FIXTURES/f'p{n:04d}-translation.json').read_text())['blocks']


@pytest.mark.parametrize('n,count', [(23,2),(33,5)])
def test_complete_real_display_quote_without_requiring_quote_marks(n,count):
    with fitz.open(BOOK/'source.pdf') as doc:
        quotes = quote_retranslation_units(doc[n-1],saved(n))
    assert len(quotes)==1
    assert len(quotes[0]['composed_ids'])==count
    assert len(quotes[0]['spans'])==sum(len(u['spans']) for u in saved(n) if u['id'] in quotes[0]['composed_ids'])


def test_real_nonadjacent_content_repetition_is_rejected():
    units=saved(23)[1:3]
    issues=text_issues(' '.join(u['source'] for u in units),' '.join(u['target'] for u in units),'quote')
    assert any(i['code']=='repeated_ngram' and i['n']>=2 for i in issues)


@pytest.mark.parametrize('source,target',[
    ('one object and another','de la esfera y de la carta'),
    ('a creative gift and a creative gift','don creativo y otro don creativo'),
    ('a sentence','un don creativo. Otro don creativo'),
])
def test_nonadjacent_repeat_control(source,target):
    assert not any(i['code']=='repeated_ngram' for i in text_issues(source,target,'q'))


def test_real_display_case_and_line_geometry():
    from astro_translate.display_quotes import prepare_display_quotes
    with fitz.open(BOOK/'source.pdf') as doc:
        quotes=[u for u in prepare_display_quotes(doc[32],saved(33)) if u.get('display_quote')]
    assert len(quotes)==1
    q=quotes[0]
    assert q['target'].isupper()
    assert len(q['display_lines'])==6
    assert q['alignment']=='center'
    assert len({round(line['bbox'][2]-line['bbox'][0],1) for line in q['display_lines']})>3
    bad=copy.deepcopy(q);bad['target']=bad['target'].lower()
    bad['target_runs']=[{**r,'text':r['text'].lower()} for r in bad['target_runs']]
    assert any(i['code']=='source_case' for i in unit_issues(bad))


@pytest.mark.parametrize('target',['geocéntrica','geocéntricos','geocéntricas','GEOCÉNTRICA'])
def test_glossary_accepts_inflected_earth_centred(target):
    wire={'blocks':[{'id':'a','source':'Earth-centred','glossary':[{'source':'Earth-centred','target':'geocéntrico'}]}]}
    answer={'blocks':[{'id':'a','translation':target}]}
    assert llm._validate('translate_page',answer,wire,{'type':'object'})==answer


@pytest.mark.parametrize('target',['Planetas','planeta','PLANETAS'])
def test_glossary_accepts_number_and_case_across_paired_markers(target):
    from astro_translate.style_spans import encode
    source,pairs=encode([{'text':'planets','style':{}}])
    wire,_=llm._prepare('translate_page',{'blocks':[{'id':'a','source':source,'_style_pairs':pairs}]},[],[{'source':'planets','target':'planetas'}])
    answer={'blocks':[{'id':'a','translation':'{v1}'+target+'{v2}'}]}
    assert llm._validate('translate_page',answer,wire,{'type':'object'})==answer


def test_glossary_missing_remains_specific():
    wire={'blocks':[{'id':'a','source':'Earth-centred','glossary':[{'source':'Earth-centred','target':'geocéntrico'}]}]}
    with pytest.raises(llm.ValidationFailure,match='Earth-centred => geocéntrico') as error:
        llm._validate('translate_page',{'blocks':[{'id':'a','translation':'otro'}]},wire,{'type':'object'})
    assert error.value.ids=={'a'}


def test_punctuation_spacing_repairs_across_style_boundary():
    from astro_translate.text_quality import repair_composition
    unit={'id':'q','source':'A statement.','target':'UNA FRASE .','style':{},
          'target_runs':[{'text':'UNA FRASE ','style':{}},{'text':'.','style':{'bold':True}}]}
    assert any(i['code']=='punctuation_spacing' for i in unit_issues(unit))
    fixed=repair_composition(unit)
    assert fixed['target']=='UNA FRASE.'
    assert fixed['target_runs'][-1]['style']=={'bold':True}
    assert not any(i['code']=='punctuation_spacing' for i in unit_issues(fixed))


from test_llm import setup, FakeBackend, use, response


def test_missing_glossary_retry_carries_exact_term_feedback(setup,monkeypatch):
    _,_,payload=setup
    fake=FakeBackend([response(text='astro'),response(text='Sol')]);use(monkeypatch,fake)
    assert llm.run_task('translate_page',payload).data['blocks'][0]['translation']=='Sol'
    assert len(fake.calls)==2
    assert 'Sun => Sol' in fake.calls[1][1]['validation_error']


def test_adapter_sends_complete_quote_once_not_sentence_fragments(tmp_path,monkeypatch):
    from astro_translate import adapters
    book=tmp_path/'book';book.mkdir()
    doc=fitz.open();page=doc.new_page(width=600,height=500)
    page.insert_text((40,80),'"THE COMPLETE',fontname='Times-Bold',fontsize=30)
    page.insert_text((40,115),'QUOTE HAS TWO LINES."',fontname='Times-Roman',fontsize=30)
    doc.save(book/'source.pdf');doc.close()
    glossary=tmp_path/'g.csv';glossary.write_text('source,target\n')
    style=tmp_path/'style.md';style.write_text('test')
    protected=tmp_path/'p.txt';protected.write_text('')
    prompt=tmp_path/'prompt.md';prompt.write_text('test')
    schema=tmp_path/'schema.json';schema.write_text('{}')
    cfg=tmp_path/'settings.toml';cfg.write_text(f'[provider]\nbackend="codex_sdk"\n[tasks.translate_page]\nmodel="test"\neffort="low"\nprompt="{prompt}"\nschema="{schema}"\n')
    monkeypatch.setattr(adapters,'require_approval',lambda *args:None)
    monkeypatch.setattr(adapters,'require_backend_approval',lambda *args:None)
    calls=[]
    def fake(task,payload):
        calls.append(payload)
        return llm.LLMResult({'blocks':[{'id':r['id'],'translation':r['source']} for r in payload['blocks']]},'test','low',1,1,'codex_sdk',False,.01)
    monkeypatch.setattr(adapters,'run_task',fake)
    adapters.fidelity_translate(book,glossary,style,protected,'1',config_path=cfg,reinsert_pages=False,phase='production')
    assert len(calls)==1 and len(calls[0]['blocks'])==1
    request=calls[0]['blocks'][0]
    assert request.get('_style_pairs') and 'THE COMPLETE' in request['source'] and 'QUOTE HAS TWO LINES' in request['source']
    corrections=json.loads((book/'work/production/composition_corrections.json').read_text())['corrections']
    assert len(corrections)==1 and corrections[0]['provenance']['entry_point']=='astro_translate.llm.run_task'
    payload=json.loads((book/'work/production/p0001-translation.json').read_text())
    assert all(u['translation_method']=='requires_joined_quote_correction' for u in payload['blocks'])
    calls.clear()
    adapters.fidelity_translate(book,glossary,style,protected,'1',config_path=cfg,reinsert_pages=False,phase='production')
    assert not calls


def test_real_display_quote_recomposition_bounds_and_case(tmp_path,monkeypatch):
    from astro_translate import fidelity
    from astro_translate.production import Production
    r=Production(BOOK)
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    payload=json.loads((FIXTURES/'p0033-translation.json').read_text())
    fit=fidelity.reinsert_fidelity(BOOK,payload,r.glossary,r.style,r.protected,config=r.config,
                                  artifact_dir=tmp_path,phase='production',corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete'],fit.get('failures')
    qa=fidelity.fidelity_qa(BOOK,fit,r.glossary,r.protected,artifact_dir=tmp_path,update_manifest=False)
    assert qa['non_text_pixel_diff']==0
    assert qa['typography_audit']['ok']
    assert qa['numbers_equal'] and qa['vectors_equal'] and qa['images_equal']
    assert {issue['code'] for issue in qa['text_quality_issues']}=={'fragmented_display_quote'}
    quote=next(u for u in fit['blocks'] if u.get('display_quote'))
    assert quote['target'].isupper() and quote['scale']>=.75
    with fitz.open(fit['output']) as doc:
        actual=[];centers=[]
        for b in doc[32].get_text('dict')['blocks']:
            for line in b.get('lines',[]):
                if not line['bbox'][1]<quote['bbox'][3]:continue
                text=''.join(s['text'] for s in line['spans']).strip()
                if text:
                    actual.append(text);centers.append((line['bbox'][0]+line['bbox'][2])/2)
        expected=[(line['bbox'][0]+line['bbox'][2])/2 for line in quote['display_lines']]
        assert all(abs(a-b)<1 for a,b in zip(centers,expected))
        assert len(actual)==6 and all(text.isupper() for text in actual)


def test_repeated_source_word_can_be_a_multiword_term_in_real_credits():
    units=json.loads((ROOT/'tests/fixtures/phase2c/p0004-translation.json').read_text())['blocks']
    selected=[u for u in units if 23<=int(u['id'].rsplit('u',1)[1])<=27]
    assert selected
    assert not any(i['code']=='repeated_ngram' for i in text_issues(
        ' '.join(u['source'] for u in selected),' '.join(u['target'] for u in selected),'credit'))


def test_identical_style_runs_keep_layout_boundaries_during_normalization():
    from astro_translate.text_quality import normalize_local_typography
    u={'id':'u','source':'two native lines','target':'one two','target_runs':[
        {'text':'one ','style':{}},{'text':'two','style':{}}]}
    assert normalize_local_typography(u)==u
    u['source']='TWO NATIVE LINES'
    fixed=normalize_local_typography(u)
    assert [r['text'] for r in fixed['target_runs']]==['ONE ','TWO']


def test_real_traits_table_is_not_an_unquoted_pullquote():
    from astro_translate.fidelity import extract_fidelity_units
    with fitz.open(BOOK/'source.pdf') as doc:
        assert not quote_retranslation_units(doc[59],extract_fidelity_units(doc[59]))


@pytest.mark.parametrize('n',[15,21])
def test_real_pullquote_allows_short_final_line(n):
    from astro_translate.fidelity import extract_fidelity_units
    with fitz.open(BOOK/'source.pdf') as doc:
        quotes=quote_retranslation_units(doc[n-1],extract_fidelity_units(doc[n-1]))
    assert len(quotes)==1


def test_real_table_glyph_boundary_measures_only_its_native_row():
    from astro_translate.text_quality import glyph_boundaries, candidate_boundary_matches
    units=saved(38)
    with fitz.open(BOOK/'source.pdf') as source:
        boundary=glyph_boundaries(source[37],units)[0]
    owner=next(u for u in units if u['id']==boundary['unit_id'])
    label=owner['target'].split()[0]
    assert len(owner['target'].split())>1
    with fitz.open() as candidate:
        page=candidate.new_page(width=600,height=700)
        box=fitz.Rect(boundary['label_bbox'])
        page.insert_text((box.x0,box.y1-3),label,fontsize=10)
        page.insert_text((box.x0,box.y1+23),'Different row',fontsize=10)
        matches=candidate_boundary_matches(page,owner,boundary)
    assert len(matches)==1 and matches[0]['text']==label
    assert matches[0]['ink_bbox'][2]<boundary['glyph_bbox'][0]-boundary['minimum_gap']


def test_typography_never_hides_invalid_saved_target_runs():
    from astro_translate.text_quality import repair_composition
    unit={'id':'bad','source':'A sentence','target':'a different saved target',
          'target_runs':[{'text':'one run','style':{}}]}
    assert repair_composition(unit)==unit
