"""I2: real pilot evidence, read-only; no provider calls or artifact edits."""
import json
from pathlib import Path

import pytest

from astro_translate import llm
from astro_translate.consistency import SentenceMemory, build_plan, resolve_plan
from astro_translate.numeric_order import numeric_quantifier_issues

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'books/the-secrets-of-astrology/work'
SENTENCE_ID = 'p0028_s218a0ca09b472946031e'


def real_case():
    path = WORK / 'llm_validation/33a1d8edfb8625ad8b7f941ed547fbdf6b09d709769169ec3498bb474b7b6b98-attempt-3.json'
    evidence = json.loads(path.read_text())
    source = next(r for r in evidence['wire']['blocks'] if r['id'] == SENTENCE_ID)
    answer = next(r for r in evidence['response']['blocks'] if r['id'] == SENTENCE_ID)
    return source, answer


def test_real_p28_model_response_rejects_displaced_numeric_quantifier():
    source, answer = real_case()
    with pytest.raises(llm.ValidationFailure, match='numeric quantifier') as exc:
        llm._validate('translate_page', {'blocks': [answer]}, {'blocks': [source]}, {'type': 'object'})
    assert exc.value.ids == {SENTENCE_ID}


@pytest.mark.parametrize(('source', 'target'), [
    ('The {n0} signs', 'Los signos {n0}'),
    ('The 12 signs', 'Los signos 12'),
    ('{v1}The {v2}{n8}{v3} planets{v4}', '{v1}Los {v2}planetas {v3}{n8}{v4}'),
    ('{n2} houses', 'CASAS {n2}'),
    ('{n1} years', 'años {n1}'),
])
def test_inversions_are_detected_without_exposing_values(source, target):
    issues = numeric_quantifier_issues(source, target)
    assert len(issues) == 1
    assert not any(c.isdigit() for c in json.dumps(issues, ensure_ascii=False))


@pytest.mark.parametrize(('source', 'target'), [
    ('The {n0} signs', 'Los {n0} signos'),
    ('House {n0}', 'Casa {n0}'),
    ('{n0}th house', 'Casa {n0}'),
    ('{n0} March', 'Marzo {n0}'),
    ('{n0} signs and {n1} houses', '{n1} casas y {n0} signos'),
    ('{n0} signs', 'signos; {n0}'),
    ('{n0} signs; signs {n1}', 'signos {n0}; signos {n1}'),
    ('12 signs and 12 houses', 'signos 12 y casas 12'),  # ambiguous restored value
    ('{n0} unknown', 'desconocidos {n0}'),
    ('1.5 years', 'años 1.5'),  # fractional values outside this narrow rule
])
def test_conservative_abstentions_and_valid_order(source, target):
    assert not numeric_quantifier_issues(source, target)


def test_local_restore_and_style_resolution_preserve_model_order():
    saved = json.loads((WORK / 'pilot/p0028-translation.json').read_text())
    unit = next(b for b in saved['blocks'] if b['id'] == 'p0028_u0015')
    assert len(unit['style_runs']) == len(unit['target_runs']) == 1
    plan = build_plan([unit])
    first, second = plan['requests']
    assert first['id'] == SENTENCE_ID and '_style_markers' not in first
    source, answer = real_case()
    wire, maps = llm._prepare('translate_page', {'blocks': [first]}, [], [])
    assert wire['blocks'][0]['source'] == source['source']
    restored = llm._restore('translate_page', {'blocks': [answer]}, maps, {'blocks': [first]})
    assert restored['blocks'][0]['translation'] == answer['translation'].replace('{n1}', '12')
    good = {**answer, 'translation': answer['translation'].replace('signos {n1}', '{n1} signos')}
    text = llm._restore('translate_page', {'blocks': [good]}, maps, {'blocks': [first]})['blocks'][0]['translation']
    answers = {first['id']: text, second['id']: unit['target'].split('. ')[1].rstrip('.')}
    result = resolve_plan(plan, answers)['units'][0]
    assert result['target'].startswith('Los 12 signos ')
    assert ''.join(r['text'] for r in result['target_runs']) == result['target']
    assert result['target_runs'][0]['style'] == unit['target_runs'][0]['style']


def test_sentence_memory_rejects_old_bad_entry_and_allows_valid_retranslation(tmp_path):
    plan = json.loads(next((WORK / 'pilot').glob('plan-28-*.json')).read_text())
    entry = next(e for e in plan['entries'].values() if e['id'] == SENTENCE_ID)
    _, answer = real_case()
    bad = answer['translation'].replace('{n1}', '12')
    good = bad.replace('signos 12', '12 signos')  # test fixture only
    memory = SentenceMemory(tmp_path, 'test')
    try:
        saved = {'plain': bad, 'translation': bad, 'marked_source': entry['marked_source']}
        memory.db.execute('INSERT INTO sentence_memory VALUES (?,?,?)', ('test', entry['source'], json.dumps(saved)))
        memory.db.commit()
        assert memory.get(entry) is None
        with pytest.raises(ValueError, match='numeric quantifier'):
            memory.put(entry, bad)
        memory.put(entry, good)
        assert memory.get(entry) == good
    finally:
        memory.close()


def test_all_saved_pilot_and_wisdom_translations_have_no_other_flags():
    roots = [WORK / 'pilot', ROOT / 'books/astrology-wisdom-stars/work']
    flagged = []
    for root in roots:
        # Historical inputs deliberately preserve defects for audit/regression.
        paths = sorted(p for p in root.rglob('p*-translation.json')
                       if 'history' not in p.relative_to(root).parts)
        assert paths
        for path in paths:
            data = json.loads(path.read_text())
            for block in data.get('blocks', []):
                if numeric_quantifier_issues(block.get('source', ''), block.get('target', '')):
                    flagged.append((path.relative_to(ROOT).as_posix(), block['id']))
    assert not flagged
