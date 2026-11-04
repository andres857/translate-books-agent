import copy,importlib.util
from pathlib import Path
from astro_translate.fidelity import _make_unit

spec=importlib.util.spec_from_file_location('composition_script',Path(__file__).parents[1]/'scripts/retranslate_composition.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def span(text,x,y,bold=False):
    style={'font':'Times-Bold' if bold else 'Times-Roman','size':10,'color':0,'weight':700 if bold else 400,'bold':bold,'italic':False,'serif':True,'width':100}
    return {'source':text,'bbox':[x,y,x+len(text)*5,y+10],'font':style['font'],'size':10,'color':0,'flags':16 if bold else 0,'rotate':0,'style':style}


def test_whole_paragraph_plan_preserves_inline_styles_and_two_paragraphs():
    first=_make_unit([span('Alpha',10,10,True),span('Beta.',40,10)],1,False,[]);first['id']='u0001'
    whole=_make_unit(first['spans']+[span('Gamma.',10,30)],1,False,[]);whole['id']='u0001';whole['paragraphs']=[{'indent':0},{'indent':8.5}]
    source_snapshot=copy.deepcopy(whole)
    plan=module.make_plan([whole],[first],146)
    assert len(plan['requests'])==2 and all(r['id'].startswith('p0146_') for r in plan['requests'])
    blocks,occurrences=module.resolve_composition([whole],plan,{r['id']:r['source'] for r in plan['requests']})
    block=blocks[0]
    assert ''.join(r['text'] for r in block['target_runs'])==block['target']=='Alpha Beta. Gamma.'
    assert [p['indent'] for p in block['paragraphs']]==[0,8.5]
    assert [r for p in block['paragraphs'] for r in p['target_runs']]==block['target_runs']
    assert block['target_runs'][0]['style']['bold']
    assert not block['target_runs'][1]['style']['bold']
    assert whole==source_snapshot and len(occurrences)==2


def test_relocated_original_punctuation_is_ordered_without_changing_bound_source():
    first=span('Alpha MC',10,10);second=span('Next clause',10,30);dot=span('.',first['bbox'][2],10)
    unit=_make_unit([first,second,dot],1,False,[]);unit['id']='u0001';unit['relocated_punctuation']=[dot]
    original=copy.deepcopy(unit)
    plan=module.make_plan([unit],[unit],146)
    assert plan['pieces']['u0001'][0]['source']=='Alpha MC. Next clause'
    blocks,_=module.resolve_composition([unit],plan,{r['id']:r['source'] for r in plan['requests']})
    assert blocks[0]['source']==original['source'] and blocks[0]['spans']==original['spans']
    assert blocks[0]['translation_source']=='Alpha MC. Next clause'
    assert unit==original


def test_unique_approved_term_recovers_source_emphasis_without_text_changes():
    light=span('x',0,0)['style'];bold=span('x',0,0,True)['style']
    source=[{'text':'Alpha ','style':light},{'text':'Beta','style':bold}]
    target=[{'text':'Gamma Delta','style':bold}]
    runs,records=module.align_unique_term_style(source,target,{'source':'Alpha','target':'Gamma'})
    assert ''.join(r['text'] for r in runs)=='Gamma Delta'
    assert runs[0]['style']==light and runs[-1]['style']==bold
    assert records[0]['text_changed'] is False
    unchanged,records=module.align_unique_term_style(source,target,{'source':'Alpha','target':'Missing'})
    assert unchanged==target and not records


def test_validated_continuation_reuse_preserves_other_targets_and_rejects_semantic_addition():
    import pytest
    from astro_translate.core import Blocked
    spec=importlib.util.spec_from_file_location('repair_script',Path(__file__).parents[1]/'scripts/repair_composition_continuation.py');repair=importlib.util.module_from_spec(spec);spec.loader.exec_module(repair)
    owner=_make_unit([span('Alpha',10,10)],1,False,[]);owner.update(id='u0021',target='First',target_runs=[{'text':'First','style':owner['style']}])
    tail=_make_unit([span('Beta',10,30)],1,False,[]);tail.update(id='u0022',target='Bad extra',target_runs=[{'text':'Bad extra','style':tail['style']}])
    previous={**tail,'target':'Valid','target_runs':[{'text':'Valid','style':tail['style']}]}
    punctuation=span('.',30,30);merged={**owner,'source':'Alpha Beta.','spans':owner['spans']+tail['spans']+[punctuation]}
    other={**owner,'id':'u0099'};old=[owner,tail,other];snapshot=copy.deepcopy(old)
    cached={'marked_source':'Beta','plain':'Valid'}
    result=repair.merge_validated_continuation(old,[merged,other],previous,cached)
    assert result[0]['target']=='First Valid.' and result[1]==other
    assert ''.join(r['text'] for r in result[0]['target_runs'])==result[0]['target']
    assert old==snapshot
    with pytest.raises(Blocked,match='memory'):repair.merge_validated_continuation(old,[merged,other],previous,{'marked_source':'Beta','plain':'Bad extra'})
