"""Extended doctor: offline only. Every subprocess/provider is a double."""
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
CONFIG = ROOT/'config/pipeline.toml'
SECRET = 'sk-ant-oat01-OFFLINE-SECRET-TOKEN'


def _pdf(path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page(width=100, height=100)
        doc.save(path)


@pytest.fixture
def book(tmp_path):
    book = tmp_path/'book'
    (book/'work/production').mkdir(parents=True)
    _pdf(book/'source.pdf', 3)
    return book


@pytest.fixture
def env(monkeypatch):
    """Healthy defaults: 100 GiB free, claude present and logged in by OAuth."""
    from astro_translate import doctor
    state = SimpleNamespace(free=100*GIB, which='/usr/bin/claude', calls=[],
                            auth={'loggedIn':True, 'authMethod':'claude.ai', 'apiProvider':'firstParty',
                                  'subscriptionType':'pro', 'email':'someone@example.com',
                                  'orgId':'org-123', 'oauthToken':SECRET}, auth_code=0)
    monkeypatch.setattr(doctor, '_disk_usage', lambda path: SimpleNamespace(total=500*GIB, used=0, free=state.free))
    monkeypatch.setattr(doctor, '_which', lambda name: state.which)
    def run(command, **kwargs):
        state.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, state.auth_code, json.dumps(state.auth), '')
    monkeypatch.setattr(doctor, '_run', run)
    monkeypatch.setattr(doctor, '_legacy', lambda: {'versions':{'pymupdf':'x'}, 'babeldoc_options':{}})
    return state


def check(report, name):
    return next(c for c in report['checks'] if c['name'] == name)


def test_legacy_output_preserved_and_healthy_book_has_no_errors(book, env):
    from astro_translate.doctor import run_doctor
    report = run_doctor(book, config_path=CONFIG)
    assert report['versions'] == {'pymupdf':'x'} and 'babeldoc_options' in report
    assert report['ok'] and report['errors'] == 0
    assert check(report, 'disk_free')['level'] == 'ok'
    assert check(report, 'claude_auth')['level'] == 'ok'
    assert check(report, 'quota_pause')['level'] == 'ok'
    assert check(report, 'g5_block')['level'] == 'ok'
    assert check(report, 'probe_models')['level'] == 'skipped'


@pytest.mark.parametrize('free,level', [(3, 'error'), (10, 'warning'), (20, 'ok')])
def test_disk_thresholds(book, env, free, level):
    from astro_translate.doctor import run_doctor
    env.free = free*GIB
    assert check(run_doctor(book, config_path=CONFIG), 'disk_free')['level'] == level


def test_disk_thresholds_configurable(book, env):
    from astro_translate.doctor import run_doctor
    env.free = 3*GIB
    report = run_doctor(book, config_path=CONFIG, min_free_gib=1, warn_free_gib=2)
    assert check(report, 'disk_free')['level'] == 'ok'


def test_production_size_and_unmigrated_full_book_candidates(book, env):
    from astro_translate.doctor import run_doctor
    _pdf(book/'work/production/p0002-translated.pdf', 3)
    _pdf(book/'work/production/p0003-translated.pdf', 1)
    report = run_doctor(book, config_path=CONFIG)
    assert check(report, 'production_size')['bytes'] > 0
    candidates = check(report, 'single_page_candidates')
    assert candidates['level'] == 'warning' and candidates['pages'] == [2]
    assert 'migrate_single_page_candidates.py' in candidates['detail']


def test_auth_never_prints_secrets_or_identity(book, env, monkeypatch, capsys):
    from astro_translate.doctor import run_doctor, render
    monkeypatch.setenv('ANTHROPIC_API_KEY', SECRET)
    report = run_doctor(book, config_path=CONFIG)
    text = json.dumps(report) + render(report)
    assert SECRET not in text and 'someone@example.com' not in text and 'org-123' not in text
    command, kwargs = env.calls[0]
    assert command[1:3] == ['auth', 'status'] and 'ANTHROPIC_API_KEY' not in kwargs['env']
    assert check(report, 'claude_auth')['auth'] == {'loggedIn':True, 'authMethod':'claude.ai',
                                                      'apiProvider':'firstParty', 'subscriptionType':'pro'}
    assert check(report, 'paid_api_env')['level'] == 'warning'


def test_auth_logged_out_is_error_and_missing_binary_is_error(book, env):
    from astro_translate.doctor import run_doctor
    env.auth = {'loggedIn':False}
    assert check(run_doctor(book, config_path=CONFIG), 'claude_auth')['level'] == 'error'
    env.which = None
    report = run_doctor(book, config_path=CONFIG)
    assert check(report, 'claude_binary')['level'] == 'error'
    assert check(report, 'claude_auth')['level'] == 'skipped'
    assert not report['ok']


def test_auth_status_unsupported_is_skipped_with_explanation(book, env):
    from astro_translate.doctor import run_doctor
    env.auth_code = 1;env.auth = 'error: unknown command auth'
    result = check(run_doctor(book, config_path=CONFIG), 'claude_auth')
    assert result['level'] == 'skipped' and 'auth status' in result['detail']


def test_active_quota_pause_and_g5_block(book, env):
    from astro_translate.doctor import run_doctor
    from astro_translate.core import atomic_json
    atomic_json(book/'work/quota_pause.json', {'resume_after_unix':time.time()+3600, 'reason':'limit'})
    atomic_json(book/'work/production/g5_block.json', {'batch':3, 'pages':[41, 42], 'ratio':.1, 'ok':1, 'in_scope':10})
    report = run_doctor(book, config_path=CONFIG)
    assert check(report, 'quota_pause')['level'] == 'error'
    g5 = check(report, 'g5_block')
    assert g5['level'] == 'error' and 'g5_decision-03.json' in g5['detail']
    assert not report['ok']
    atomic_json(book/'work/quota_pause.json', {'resume_after_unix':time.time()-10})
    atomic_json(book/'work/production/g5_decision-03.json', {'decision':'continue', 'approved_by':'x',
                                                               'reason':'r', 'evidence':'e'})
    report = run_doctor(book, config_path=CONFIG)
    assert check(report, 'quota_pause')['level'] == 'warning'
    assert check(report, 'g5_block')['level'] == 'warning'


def test_probe_models_helper_classifies_and_logs_costs(book, monkeypatch):
    from astro_translate import llm
    seen = []
    class Fake:
        def complete_json(self, task, payload, schema, model, effort, images=None):
            seen.append((task, model, effort, llm._prompt(task, payload)))
            if model == 'opus':
                raise llm.ModelUnavailable('Claude model unavailable: opus; model_not_found')
            if model == 'haiku':
                raise llm.QuotaExceeded('hit your haiku limit', model_scoped=True)
            return llm.LLMResult({'ok':True}, 'claude-sonnet-5-5', effort, 10, 2, 'claude_cli', False, .1)
    monkeypatch.setattr(llm, '_make_backend', lambda name, work, provider: Fake())
    config = book/'work/pipeline.toml'
    config.write_text(CONFIG.read_text().replace('[tasks.review]\nmodel = "sonnet"', '[tasks.review]\nmodel = "haiku"'))
    results = {r['model']:r for r in llm.probe_models(book, config)}
    assert results['sonnet']['status'] == 'available' and results['sonnet']['effort'] == 'low'
    assert 'translate_page' in results['sonnet']['roles']
    assert results['opus']['status'] == 'unavailable' and results['opus']['roles'] == ['escalation']
    assert results['haiku']['status'] == 'limited'
    assert len(seen) == 3 and all(task == 'doctor_probe' for task, *_ in seen)
    costs = [json.loads(line) for line in (book/'work/costs.jsonl').read_text().splitlines()]
    assert len(costs) == 3 and all(c['task'] == 'doctor_probe' and c['backend'] == 'claude_cli' for c in costs)
    assert all(c['cost_usd'] is None for c in costs)
    assert not (book/'work/quota_pause.json').exists()


def test_doctor_probe_levels(book, env, monkeypatch):
    from astro_translate import doctor
    probes = [{'model':'sonnet', 'effort':'low', 'roles':['translate_page'], 'status':'available', 'detail':''},
              {'model':'opus', 'effort':'high', 'roles':['escalation'], 'status':'unavailable', 'detail':'x'}]
    monkeypatch.setattr(doctor.llm, 'probe_models', lambda book, config: probes)
    report = doctor.run_doctor(book, config_path=CONFIG, probe_models=True)
    assert check(report, 'probe_models')['level'] == 'warning' and report['ok']
    probes[0]['status'] = 'unavailable'
    report = doctor.run_doctor(book, config_path=CONFIG, probe_models=True)
    assert check(report, 'probe_models')['level'] == 'error' and not report['ok']
    report = doctor.run_doctor(None, config_path=CONFIG, probe_models=True)
    assert check(report, 'probe_models')['level'] == 'error' and '--book' in check(report, 'probe_models')['detail']


def test_cli_json_and_exit_codes(book, env, capsys):
    from astro_translate.__main__ import main
    assert main(['doctor', '--book', str(book), '--config', str(CONFIG), '--json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['ok'] and 'versions' in data and data['checks']
    env.free = 1*GIB
    assert main(['doctor', '--book', str(book), '--config', str(CONFIG)]) == 1
    out = capsys.readouterr().out
    assert 'ERROR' in out and 'disk_free' in out


def test_book_config_backend_mismatch_is_warned(book, env):
    from astro_translate.doctor import run_doctor
    (book/'work/pipeline.toml').write_text(CONFIG.read_text().replace('backend = "claude_cli"', 'backend = "openai_api"'))
    result = check(run_doctor(book), 'config')
    assert result['level'] == 'warning' and 'openai_api' in result['detail'] and 'claude_cli' in result['detail']
    assert check(run_doctor(book, config_path=CONFIG), 'config')['level'] == 'ok'


@pytest.mark.parametrize('name', ['codex_sdk', 'codex_exec'])
def test_retired_codex_backend_in_config_is_an_error(book, env, name):
    from astro_translate.doctor import run_doctor
    (book/'work/pipeline.toml').write_text(CONFIG.read_text().replace('backend = "claude_cli"', f'backend = "{name}"'))
    result = check(run_doctor(book), 'config')
    assert result['level'] == 'error' and 'retired 2026-11-08' in result['detail'] and name in result['detail']
