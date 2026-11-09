"""Offline orchestration tests: never compose a real book page or invoke an LLM."""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from astro_translate.core import Blocked, atomic_json, sha256

PILOT = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology/work/pilot'
sys.path.insert(0, str(PILOT))


def rebuild():
    return importlib.import_module('rebuild_pilot')


def test_scope_rejects_every_page_outside_pilot():
    r = rebuild()
    expected = (1, 5, 19, 28, 34, 35, 60, 69, 86, 87, 100, 101, 128, 141, 154, 169, 173, 175, 179, 180)
    assert r.parse_pages(','.join(map(str, expected))) == expected
    for value in ('', '7', '1,121', '0', '192', '5,bad', '5,5'):
        with pytest.raises(Blocked):
            r.parse_pages(value)


def test_recipe_order_matches_documented_table_and_historical_dependencies():
    r = rebuild()
    special = {
        1: ('preserve_original',),
        28: ('native_saved', 'layout28'),
        34: ('native_saved', 'table_repair_saved'),
        60: ('native_saved', 'table_repair_saved'),
        86: ('native_saved', 'vertical_saved'),
        87: ('native_saved', 'vertical_saved'),
        141: ('native_saved', 'layout141'),
        169: ('shorten169_failed_saved', 'native_saved'),
        173: ('native_saved', 'layout173'),
        175: ('native_saved', 'quote_repair_saved', 'rotated_saved'),
        180: ('native_saved', 'heading_repair_saved', 'recompose_saved'),
    }
    for page in r.PAGES:
        steps = special.get(page, ('native_saved',))
        assert r.RECIPES[page] == steps
        assert f'{page} | {" -> ".join(steps)}' in r.__doc__
    assert r.QA_REPAIRS[87] == ('qa87_morphology',)
    assert (PILOT / 'quote_repair.log').stat().st_mtime < (PILOT / 'rotated_after_quote.log').stat().st_mtime
    assert (PILOT / 'heading_style_repair.log').stat().st_mtime < (PILOT / 'recompose180_paragraphs.log').stat().st_mtime
    assert 'ValidationFailure: character limit exceeded' in (PILOT / 'shorten169.log').read_text()
    from astro_translate.resegmentation import verified_migration_history
    before, current = PILOT/'p0169-translation-before-shortening.json', PILOT/'p0169-translation.json'
    assert before.read_bytes() == current.read_bytes() or verified_migration_history(before, current, preserve_ids=('p0169_u0003',))


def test_offline_guard_covers_direct_calls_and_preimported_aliases(monkeypatch):
    r = rebuild()
    from astro_translate import llm
    native = importlib.import_module('native_runner')
    calls = []
    def forbidden(*a, **kw):
        calls.append(a)
        raise AssertionError('A real LLM boundary was reached')
    monkeypatch.setattr(llm, 'run_task', forbidden)
    monkeypatch.setattr(native, 'run_task', forbidden)
    with r.no_llm('p0005'):
        for module in (llm, native):
            with pytest.raises(Blocked, match='p0005.*LLM'):
                module.run_task('translate_page', {})
    assert not calls
    assert llm.run_task is forbidden and native.run_task is forbidden


def test_compose_enters_guard_before_any_compositor(monkeypatch):
    r = rebuild()
    from astro_translate import llm
    calls = []
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(r, 'preflight', lambda pages: None)
    monkeypatch.setattr(r, 'archive_evidence', lambda n: None)
    monkeypatch.setattr(r, 'compose_saved', lambda n: llm.run_task('shorten', {}))
    with pytest.raises(Blocked, match='p0005.*LLM'):
        r.compose_page(5)
    assert calls == []


def test_missing_saved_translation_names_unit_and_never_calls_llm(tmp_path, monkeypatch):
    r = rebuild()
    from astro_translate import llm
    monkeypatch.setattr(r, 'FOLDER', tmp_path)
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: pytest.fail('LLM called'))
    with pytest.raises(Blocked, match='p0175.*p0175-translation.json'):
        r.preflight([175])
    with pytest.raises(Blocked, match='p0175_curve_1'):
        r.validate_targets([{'id': 'p0175_curve_1'}], [{'id': 'p0175_curve_0', 'translation': 'synthetic'}], 'translation')


def test_dispatch_keeps_all_local_repairs_in_order(monkeypatch):
    r = rebuild()
    calls = []
    monkeypatch.setattr(r, 'preflight', lambda pages: None)
    monkeypatch.setattr(r, 'archive_evidence', lambda n: None)
    monkeypatch.setattr(r, 'compose_saved', lambda n: calls.append(('base', n)) or {'fit_complete': True})
    monkeypatch.setattr(r, 'supplement', lambda n, step: calls.append((step, n)))
    monkeypatch.setattr(r, 'qa_local_page', lambda n: calls.append(('qa', n)))
    r.compose_page(175)
    assert calls == [('base', 175), ('rotated_saved', 175), ('qa', 175)]
    calls.clear()
    r.compose_page(87)
    assert calls == [('base', 87), ('vertical_saved', 87), ('qa', 87)]


def test_cli_quota_pause_is_75_and_stops(monkeypatch):
    r = rebuild()
    from astro_translate.llm import QuotaPaused
    def paused(*a):
        raise QuotaPaused('quota')
    monkeypatch.setattr(r, 'run', paused)
    assert r.main(['--pages', '5', '--stage', 'vlm']) == 75


def test_assemble_always_requires_all_twenty_even_for_subset():
    r = rebuild()
    assert r.stage_pages('compose', (5,)) == (5,)
    assert r.stage_pages('assemble', (5,)) == r.PAGES


def test_qa_rejects_stale_candidate_without_inference(tmp_path, monkeypatch):
    r = rebuild()
    monkeypatch.setattr(r, 'FOLDER', tmp_path)
    output = tmp_path / 'candidate.pdf'
    output.write_bytes(b'changed')
    atomic_json(tmp_path / 'p0005-fit.json', {'page': 5, 'fit_complete': True, 'output': str(output), 'output_sha256': 'old'})
    with pytest.raises(Blocked, match='p0005.*candidate'):
        r.current_fit(5)


@pytest.mark.parametrize('number,callback,override', [
    (5, 'book_composition', False), (28, 'composition28', False),
    (34, 'table_composition', True), (60, 'table_composition', True),
    (141, '<lambda>', False), (173, 'composition173', False),
    (175, 'quote_composition', True), (180, 'book_composition', True),
])
def test_saved_composition_selects_final_transform_before_insertion(number, callback, override, tmp_path, monkeypatch):
    """Exercise source-bound profile wiring without replacing compositor functions."""
    from contextlib import nullcontext
    import pymupdf
    import astro_translate.fidelity as fidelity
    r = rebuild()
    monkeypatch.setattr(r, 'FOLDER', tmp_path)
    payload = {'page': number, 'blocks': []}
    atomic_json(tmp_path / f'p{number:04d}-translation.json', payload)
    atomic_json(tmp_path / 'p0141-clitic-scope.json', {})
    monkeypatch.setattr(r, 'local_dependencies', lambda: nullcontext(({}, 'g', 's', None, 'dep')))
    monkeypatch.setattr(pymupdf, 'open', lambda *a, **kw: nullcontext({number - 1: object()}))
    def extract(*a, merge_captions=False):
        assert merge_captions
        return []
    monkeypatch.setattr(fidelity, 'extract_fidelity_units', extract)
    captured = []
    def insert(book, saved, *a, **kw):
        from astro_translate.profiles import load_profile
        profile=load_profile(book)
        policy=profile.data.get('pages',{}).get(str(number),{})
        hook=policy.get('override','book_composition')
        hook={'clitic':'<lambda>','legend':'composition173'}.get(hook,hook)
        captured.append((hook, kw.get('composition_overrides')))
        assert fidelity.apply_editorial_composition is previous
        assert saved == payload
        return {'fit_complete': True}
    monkeypatch.setattr(fidelity, 'reinsert_fidelity', insert)
    previous = fidelity.apply_editorial_composition
    with r.no_llm(f'p{number:04d}'):
        r.compose_saved(number)
    assert captured == [(callback, tmp_path / f'p{number:04d}-composition-translations.json' if override else None)]
    assert fidelity.apply_editorial_composition is previous


def test_real_saved_inputs_are_complete_without_composing():
    # Read-only validation of artifacts; no PDF generation, gates or LLM.
    r = rebuild()
    with r.no_llm('read-only saved artifacts'):
        r.preflight(r.PAGES)


def test_local_qa_also_guards_llm(monkeypatch):
    from contextlib import nullcontext
    from astro_translate import llm
    r = rebuild()
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: pytest.fail('LLM reached'))
    monkeypatch.setattr(r, 'local_dependencies', lambda: nullcontext(({}, 'g', 's', None, 'dep')))
    monkeypatch.setattr(r, 'current_fit', lambda n: llm.run_task('qa_judge', {}))
    with pytest.raises(Blocked, match='p0005.*LLM'):
        r.qa_local_page(5)


def test_rebuild_log_is_incremental_and_hashes_inputs_outputs(tmp_path, monkeypatch):
    r = rebuild()
    monkeypatch.setattr(r, 'FOLDER', tmp_path)
    book = tmp_path / 'book'
    monkeypatch.setattr(r, 'BOOK', book)
    inp = tmp_path / 'saved.json'
    inp.write_text('{}')
    out = tmp_path / 'out.json'
    out.write_text('{}')
    monkeypatch.setattr(r, 'common_inputs', lambda: {'code.py': 'hash'})
    monkeypatch.setattr(r, 'preflight', lambda pages: None)
    monkeypatch.setattr(r, 'saved_inputs', lambda n: [inp])
    monkeypatch.setattr(r, 'outputs', lambda n: r.hashes([out]))
    monkeypatch.setattr(r, 'compose_page', lambda n: out.write_text('{"changed": true}'))
    r.run((5,), 'compose')
    r.run((19,), 'compose')
    log = json.loads((tmp_path / 'rebuild-log.json').read_text())
    assert len(log['runs']) == 2
    entry = log['runs'][0]['pages']['5']
    assert entry['inputs'][str(inp)] == sha256(inp)
    assert entry['before'] != entry['outputs']
    assert all(run['status'] == 'complete' for run in log['runs'])
    assert all(run['qa_approved'] is False for run in log['runs'])


def test_all_stops_on_quota_before_assembly(tmp_path, monkeypatch):
    from astro_translate.llm import QuotaPaused
    import native_runner
    import run_visual_qa
    r = rebuild()
    monkeypatch.setattr(r, 'FOLDER', tmp_path)
    monkeypatch.setattr(r, 'BOOK', tmp_path / 'book')
    monkeypatch.setattr(r, 'preflight', lambda pages: None)
    monkeypatch.setattr(r, 'common_inputs', lambda: {})
    monkeypatch.setattr(r, 'outputs', lambda n: {})
    monkeypatch.setattr(r, 'saved_inputs', lambda n: [])
    monkeypatch.setattr(r, 'current_fit', lambda n: {'fit_complete': True})
    monkeypatch.setattr(native_runner, 'deps', lambda: None)
    calls = []
    monkeypatch.setattr(r, 'compose_page', lambda n: calls.append(('compose', n)))
    def pause(argv):
        calls.append(('vlm', argv))
        raise QuotaPaused('quota')
    monkeypatch.setattr(run_visual_qa, 'main', pause)
    assert r.main(['--pages', '5,19', '--stage', 'all']) == 75
    assert [c[0] for c in calls] == ['compose', 'compose', 'vlm']
    log = json.loads((tmp_path / 'rebuild-log.json').read_text())
    assert log['runs'][-1]['status'] == 'quota_paused'


def test_vertical_font_name_is_stable_not_python_hash():
    import vertical_local
    names = []
    page = SimpleNamespace(insert_font=lambda **kw: names.append(kw['fontname']), insert_text=lambda *a, **kw: None)
    unit = {'characters': [{'origin': [1, 2]}], 'color': 0}
    # Fixed real font bytes; only the drawing interface is synthetic.
    font = Path(__file__).resolve().parents[1] / 'fonts/RobotoSlab[wght].ttf'
    vertical_local.draw_vertical(page, unit, 'synthetic', str(font), 9)
    assert names == ['Vertical' + sha256(font)[:16]]


def test_finalize_rebuild_uses_current_evidence_and_never_approves(tmp_path, monkeypatch):
    import finalize_review_package as finalizer
    r = rebuild()
    book = tmp_path / 'repo/books/synthetic'
    work = book / 'work'
    folder = work / 'pilot'
    out = book / 'out'
    folder.mkdir(parents=True)
    out.mkdir()
    (book / 'source.pdf').write_bytes(b'synthetic original')
    (out / 'piloto.pdf').write_bytes(b'synthetic assembled')
    (out / 'piloto_comparativa.pdf').write_bytes(b'synthetic comparison')
    source_hash = sha256(book / 'source.pdf')
    rows = []
    for n in r.PAGES:
        rows.append({'page': n, 'original_retained': n in (1, 169),
                     'candidate_included': n not in (1, 169), 'partial_native_coverage': False,
                     'scale_warning_below_080': False, 'local_fidelity_ok': n not in (1, 169),
                     'selection_reason': [], 'min_scale': 1, 'vlm_complete': False, 'vlm_verdict': None})
        atomic_json(folder / f'p{n:04d}-qa.json', {'local_fidelity_ok': n not in (1, 169)})
    atomic_json(folder / 'pilot-report.json', {
        'source_sha256': source_hash, 'output_sha256': sha256(out / 'piloto.pdf'),
        'comparison_sha256': sha256(out / 'piloto_comparativa.pdf'), 'production_authorized': False,
        'qa_approved_pages': 0, 'candidates_included': 18, 'originals_retained': [1, 169], 'pages': rows})
    atomic_json(folder / 'assembly-proof.json', [{'assembled_render_equal': True} for _ in r.PAGES])
    atomic_json(work / 'reference_baseline.json', {'files': {}})
    atomic_json(work / 'pilot_shared_code_baseline.json', {'files': {'changed.py': 'historical'}})
    atomic_json(work / 'manifest.json', {'pages': [{'page': n, 'status': 'pending'} for n in range(1, 193)]})
    atomic_json(work / 'review_queue.json', [{'page': 192, 'reason': 'unrelated'}])
    atomic_json(work / 'gates.json', {'G3': {'approved': True}, 'G4': {'status': 'awaiting_human_visual_review'}})
    approval = work / 'glossary_approval.json'
    atomic_json(approval, {'approved': True, 'source': 'human'})
    approval_hash = sha256(approval)
    (work / 'costs.jsonl').write_text('')
    monkeypatch.setattr(finalizer, 'BOOK', book)
    monkeypatch.setattr(finalizer, 'WORK', work)
    monkeypatch.setattr(finalizer, 'FOLDER', folder)
    monkeypatch.setattr(finalizer, 'require_approval', lambda *a: None)
    monkeypatch.setattr(finalizer, 'require_backend_approval', lambda *a: None)
    proof = finalizer.main(rebuild=True)
    assert proof['historical_baseline_is_current'] is False
    assert proof['pytest_run_by_rebuild'] is False
    assert not (folder / 'pytest_close.log').exists()
    gates = json.loads((work / 'gates.json').read_text())
    assert gates['G3'] == {'approved': True}
    assert gates['G4']['status'] == 'awaiting_human_visual_review'
    assert gates['G4']['human_pilot_approval'] is False
    assert sha256(approval) == approval_hash
    queue = json.loads((work / 'review_queue.json').read_text())
    assert {'page': 192, 'reason': 'unrelated'} in queue
    assert all(p['status'] == 'review' for p in json.loads((work / 'manifest.json').read_text())['pages'] if p['page'] in r.PAGES)
