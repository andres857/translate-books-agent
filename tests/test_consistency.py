import pytest
from astro_translate.consistency import build_plan,resolve_plan,check_consistency,normalize_source

REG={'font':'Body','bold':False,'italic':False,'size':10,'color':0}
BOLD={**REG,'bold':True,'font':'Bold'}
def unit(identifier,source,runs=None):return {'id':identifier,'page':15,'source':source,'style':REG,'style_runs':runs or [{'start':0,'end':len(source),'style':REG}]}


def test_repeated_quote_and_longer_body_clause_share_source_and_target():
    source='The map is a picture of you. Its lines describe your personal history and experience.'
    body=source[:-1]+', from the physical to the psychological.'
    quote=unit('p0015_quote',source,[{'start':0,'end':4,'style':REG},{'start':4,'end':7,'style':BOLD},{'start':7,'end':len(source),'style':REG}])
    plan=build_plan([quote,unit('p0015_body',body)])
    assert len(plan['requests'])==3
    assert sum(r['source'].count('The ') for r in plan['requests'])==1
    answers={r['id']:r['source'] for r in plan['requests']}
    result=resolve_plan(plan,answers)
    byid={r['id']:r for r in result['units']}
    assert byid['p0015_body']['target'].startswith(byid['p0015_quote']['target'][:-1]+',')
    assert ''.join(r['text'] for r in byid['p0015_quote']['target_runs'])==byid['p0015_quote']['target']
    assert any(r['style']['bold'] for r in byid['p0015_quote']['target_runs'])
    assert not check_consistency(result['occurrences'])


def test_consistency_detects_different_translations_for_same_source():
    rows=[{'source':'One sentence .','target':'Primera frase','page':7,'id':'a'},{'source':'One sentence.','target':'Otra frase','page':15,'id':'b'}]
    assert len(check_consistency(rows))==1


def test_normalization_does_not_join_numbers_around_punctuation():
    assert normalize_source('you .')=='you.'
    assert normalize_source('15 . 23')=='15 . 23'


def test_reordered_style_markers_fail_before_render():
    source='The map is here.'
    plan=build_plan([unit('p0015_q',source,[{'start':0,'end':4,'style':REG},{'start':4,'end':len(source),'style':BOLD}])])
    req=plan['requests'][0]
    answer=req['source'].replace('{v1}','{swap}').replace('{v2}','{v1}').replace('{swap}','{v2}')
    with pytest.raises(ValueError):resolve_plan(plan,{req['id']:answer})


def test_exact_memory_reuses_plain_and_refuses_changed_translation(tmp_path):
    from astro_translate.consistency import SentenceMemory
    plan=build_plan([unit('p0015_a','One complete sentence.')]);entry=next(iter(plan['entries'].values()))
    memory=SentenceMemory(tmp_path,'test-context')
    try:
        memory.put(entry,'Una frase completa')
        assert memory.get(entry)=='Una frase completa'
        with pytest.raises(ValueError,match='conflict'):memory.put(entry,'Una frase diferente')
    finally:memory.close()


def test_rendered_consistency_checks_both_copies_not_only_planned_values():
    from astro_translate.consistency import check_rendered_consistency
    rows=[{'source':'One source sentence','target':'Una misma frase','page':15,'id':'quote'},{'source':'One source sentence','target':'Una misma frase','page':15,'id':'body'}]
    assert not check_rendered_consistency(rows,{15:'Una misma frase.\n\nUna misma frase, con continuación.'})
    issues=check_rendered_consistency(rows,{15:'Una misma frase. Una frase diferente.'})
    assert issues[0]['observed_occurrences']==1 and issues[0]['expected_occurrences']==2


def test_complete_question_adds_opening_mark_without_changing_memory_core():
    from astro_translate.consistency import normalize_complete_question
    original={**unit('p0015_heading','What is this?'),'target':'Qué es esto?','target_runs':[{'text':'Qué es esto?','style':REG}]}
    updated=normalize_complete_question(original)
    assert updated['target']=='¿Qué es esto?' and ''.join(r['text'] for r in updated['target_runs'])==updated['target']
    assert original['target']=='Qué es esto?'
    assert normalize_complete_question(updated)==updated
    for kind in ('quote','fragment'):
        assert normalize_complete_question({**original,'kind':kind})['target']=='Qué es esto?'
    assert normalize_complete_question({**original,'source':'What is this'})['target']=='Qué es esto?'


def test_rendered_fields_adjacent_to_digits_are_not_words_inside_other_words():
    from astro_translate.consistency import check_rendered_consistency
    rows=[{'source':'Capricorn','target':'Capricornio','page':7,'id':'a'}]
    assert check_rendered_consistency(rows,{7:'Capricornio22 de diciembre'})
    assert not check_rendered_consistency(rows,{7:'Capricornio 22 de diciembre'})
    rows=[{'source':'Sun','target':'Sol','page':7,'id':'a'}]
    assert check_rendered_consistency(rows,{7:'solar'})
