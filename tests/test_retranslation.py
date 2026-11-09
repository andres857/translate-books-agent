"""Corrective translation regression: real I2 evidence, no provider access."""
import copy
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from astro_translate import llm
from astro_translate.consistency import build_plan, SentenceMemory
from astro_translate.core import read_extras
from astro_translate.retranslation import correct_translation, validate_units, rewrite_translation

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'books/the-secrets-of-astrology/work'
PILOT = WORK / 'pilot'
ARCHIVE = PILOT / 'history/i2-p0028-LYFs4pWP/p0028-translation.json'
UNIT = 'p0028_u0015'


@pytest.fixture
def case():
    import tomllib
    cfg = tomllib.loads((WORK / 'pipeline.toml').read_text())
    paths = cfg['paths']
    options = dict(extras=read_extras(ROOT / paths['do_not_translate']),
                   glossary=list(csv.DictReader((ROOT / paths['glossary']).open())),
                   schema=json.loads((ROOT / cfg['tasks']['translate_page']['schema']).read_text()))
    return json.loads(ARCHIVE.read_text()), json.loads((PILOT / 'p0028-translation.json').read_text()), options


def test_one_invalid_clause_only_reaches_run_task(case, monkeypatch):
    saved, donor, options = case
    before = copy.deepcopy(saved)
    good = next(u for u in donor['blocks'] if u['id'] == UNIT)
    first = build_plan([good])['requests'][0]
    calls = []

    def fake(task, payload):
        calls.append(payload)
        assert task == 'translate_page'
        assert payload['blocks'] == [first]
        return SimpleNamespace(data={'blocks': [{'id': first['id'], 'translation': good['target'].split('. ')[0]}]})

    monkeypatch.setattr(llm, 'run_task', fake)
    result, log = correct_translation(saved, payload_factory=lambda plan: {'blocks': plan['requests']}, **options)
    assert len(calls) == 1
    assert saved == before
    assert [u['id'] for u, v in zip(result['blocks'], saved['blocks']) if u != v] == [UNIT]
    for old, new in zip(saved['blocks'], result['blocks']):
        if old['id'] != UNIT:
            assert json.dumps(old, ensure_ascii=False).encode() == json.dumps(new, ensure_ascii=False).encode()
        else:
            assert old['target'].split('. ', 1)[1] == new['target'].split('. ', 1)[1]
    assert not validate_units(result, **options)
    assert log['units'][0]['id'] == UNIT
    assert 'numeric quantifier' in ' '.join(log['units'][0]['reasons'])


def test_reuse_validated_donor_archives_current_and_changes_only_one_unit(case, tmp_path, monkeypatch):
    saved, donor, options = case
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: pytest.fail('LLM forbidden'))
    book = tmp_path / 'book'
    folder = book / 'work/pilot'
    folder.mkdir(parents=True)
    path = folder / 'p0028-translation.json'
    current_bytes = json.dumps(donor, ensure_ascii=False).encode()
    path.write_bytes(current_bytes)
    source = folder / 'original.json'
    source.write_text(json.dumps(saved))
    log_path = rewrite_translation(book, path, from_archive=source, reuse_from=path,
                                   selected=[UNIT], no_llm=True, **options)
    result = json.loads(path.read_text())
    expected = copy.deepcopy(saved)
    expected['blocks'][-1] = donor['blocks'][-1]
    assert result == expected
    log = json.loads(log_path.read_text())
    assert Path(log['previous_artifact']).read_bytes() == current_bytes
    assert log['llm_calls'] == 0
    assert log['units'][0]['method'] == 'validated_existing_target'
    assert not validate_units(result, **options)


def test_no_llm_missing_valid_donor_leaves_file_intact(case, tmp_path, monkeypatch):
    saved, _, options = case
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: pytest.fail('LLM forbidden'))
    book = tmp_path / 'book'
    path = book / 'work/pilot/p0028-translation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='no-llm'):
        rewrite_translation(book, path, no_llm=True, **options)
    assert path.read_bytes() == before
    assert not list(path.parent.glob('history/*'))


def test_valid_memory_clause_is_reused_without_llm(case, tmp_path, monkeypatch):
    saved, donor, options = case
    good = next(u for u in donor['blocks'] if u['id'] == UNIT)
    plan = build_plan([good])
    entry = next(iter(plan['entries'].values()))
    memory = SentenceMemory(tmp_path, 'original-context')
    memory.put(entry, good['target'].split('. ')[0])
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: pytest.fail('LLM forbidden'))
    try:
        result, log = correct_translation(saved, memory=memory, no_llm=True, **options)
    finally:
        memory.close()
    assert not validate_units(result, **options)
    assert log['llm_calls'] == 0
    assert log['units'][0]['clauses'][0]['method'] == 'validated_sentence_memory'
    assert result['blocks'][:-1] == saved['blocks'][:-1]


def test_explicit_valid_unit_selection_only_requests_that_unit(case, monkeypatch):
    _, saved, options = case
    selected = saved['blocks'][0]
    calls = []
    def fake(task, payload):
        calls.append(payload)
        assert payload['blocks'] == build_plan([selected])['requests']
        return SimpleNamespace(data={'blocks': [{'id': payload['blocks'][0]['id'], 'translation': selected['target']}]})
    monkeypatch.setattr(llm, 'run_task', fake)
    result, log = correct_translation(saved, selected=[selected['id']],
                                      payload_factory=lambda plan: {'blocks': plan['requests']}, **options)
    assert len(calls) == 1
    assert result['blocks'][1:] == saved['blocks'][1:]
    assert log['units'][0]['reasons'] == ['explicit_selection']


@pytest.mark.parametrize('damage', ['number', 'glossary', 'style', 'runs'])
def test_invalid_donor_is_rejected_without_writes_or_calls(case, damage, monkeypatch):
    saved, donor, options = case
    donor = copy.deepcopy(donor)
    unit = donor['blocks'][-1]
    if damage == 'number':
        unit['target'] = unit['target'].replace('12', '13')
        unit['target_runs'][0]['text'] = unit['target']
    elif damage == 'glossary':
        unit['target'] = unit['target'].replace('cuatro elementos', 'xxx')
        unit['target_runs'][0]['text'] = unit['target']
    elif damage == 'style':
        unit['target_runs'][0]['style']['color'] = 99
    else:
        unit['target_runs'][0]['text'] += 'x'
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: pytest.fail('LLM forbidden'))
    with pytest.raises((ValueError, llm.ValidationFailure)):
        correct_translation(saved, donor=donor, no_llm=True, **options)


def test_cli_retranslate_routes_to_offline_mechanism(case, tmp_path, monkeypatch):
    import importlib
    import sys
    sys.path.insert(0, str(PILOT))
    native = importlib.import_module('native_runner')
    calls = []
    monkeypatch.setattr(native, 'retranslate', lambda pages, **kw: calls.append((pages, kw)))
    monkeypatch.setattr(sys, 'argv', ['native_runner.py', '--stage', 'retranslate', '--pages', '28',
                                    '--units', UNIT, '--from-archive', str(tmp_path),
                                    '--reuse-from', str(PILOT), '--no-llm'])
    native.main()
    assert calls == [([28], {'units': [UNIT], 'from_archive': tmp_path,
                            'reuse_from': PILOT, 'no_llm': True})]


def test_archived_defect_is_revalidated_but_history_is_not_a_live_candidate(case):
    saved, _, options = case
    issues = validate_units(saved, **options)
    assert list(issues) == [UNIT]
    assert 'numeric quantifier' in issues[UNIT][0]


def test_concurrent_change_is_not_overwritten(case, tmp_path, monkeypatch):
    saved, donor, options = case
    book = tmp_path / 'book'
    path = book / 'work/pilot/p0028-translation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved))
    good = donor['blocks'][-1]
    first = build_plan([good])['requests'][0]
    changed = b'{"concurrent": true}'
    def fake(task, payload):
        path.write_bytes(changed)
        return SimpleNamespace(data={'blocks': [{'id': first['id'], 'translation': good['target'].split('. ')[0]}]})
    monkeypatch.setattr(llm, 'run_task', fake)
    with pytest.raises(ValueError, match='concurrently'):
        rewrite_translation(book, path, payload_factory=lambda plan: {'blocks': plan['requests']}, **options)
    assert path.read_bytes() == changed
    assert not list(path.parent.glob('history/*'))
