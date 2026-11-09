"""Offline contract for the Claude Code CLI subscription backend (no inference)."""
import base64
import json
import tomllib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import jsonschema
import pytest

from astro_translate import llm
from test_llm import setup, FakeBackend, use, response

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}


def result_line(**overrides):
    data = {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '', 'structured_output': {'ok': True},
            'total_cost_usd': 0.0123, 'duration_ms': 900,
            'usage': {'input_tokens': 5, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 40, 'output_tokens': 9},
            'modelUsage': {'claude-sonnet-5-5': {'inputTokens': 5}}}
    data.update(overrides)
    return data


def install(monkeypatch, tmp_path, stdout, returncode=0, stderr=''):
    calls = []
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secret-api')
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'secret-token')
    def fake(command, **kwargs):
        cwd = Path(kwargs['cwd'])
        calls.append({'command': command, **kwargs, 'cwd_was_empty': cwd.is_dir() and not list(cwd.iterdir())})
        out = stdout(command, kwargs) if callable(stdout) else stdout
        return SimpleNamespace(returncode=returncode, stdout=out, stderr=stderr)
    monkeypatch.setattr(llm.subprocess, 'run', fake)
    return calls


def backend(tmp_path, **provider):
    return llm.ClaudeCLIBackend(tmp_path/'book/work', {'timeout_seconds': 77, **provider})


def expected_command(binary, model, effort, schema, *, output='json', extra=()):
    return [binary, '-p', '--model', model, '--effort', effort, *extra, '--output-format', output,
            '--json-schema', llm._json(schema), '--system-prompt', llm.FIXED_INSTRUCTION,
            '--tools', '', '--setting-sources', '', '--strict-mcp-config', '--no-session-persistence',
            '--disable-slash-commands', '--settings', '{"autoMemoryEnabled":false}']


def test_registered_backend_name(tmp_path):
    assert isinstance(llm._make_backend('claude_cli', tmp_path/'work', {}), llm.ClaudeCLIBackend)
    assert {'codex_sdk', 'codex_exec', 'claude_cli'} <= llm.SUBSCRIPTION_BACKENDS
    assert 'openai_api' not in llm.SUBSCRIPTION_BACKENDS


def test_flags_isolation_env_cwd_and_usage_parsing(tmp_path, monkeypatch):
    calls = install(monkeypatch, tmp_path, json.dumps(result_line()))
    payload = {'_template': 'Do the task.', 'blocks': [{'id': 'a', 'source': 'Sun'}], '_book': 'local-only'}
    result = backend(tmp_path, claude_bin='/opt/claude').complete_json('translate_page', payload, SCHEMA, 'sonnet', 'low')
    call = calls[0]
    assert call['command'] == expected_command('/opt/claude', 'sonnet', 'low', SCHEMA)
    assert call['input'] == llm._prompt('translate_page', payload) and 'local-only' not in call['input']
    assert 'ANTHROPIC_API_KEY' not in call['env'] and 'ANTHROPIC_AUTH_TOKEN' not in call['env']
    assert call['timeout'] == 77 and call['text'] is True
    cwd = Path(call['cwd']).resolve()
    sandbox = (tmp_path/'book/work/.codex_llm_sandbox').resolve()
    assert cwd.is_relative_to(sandbox) and cwd != sandbox and call['cwd_was_empty']
    assert not cwd.is_relative_to(ROOT) or cwd.is_relative_to(ROOT/'books')
    assert cwd != ROOT and not cwd.exists()  # ephemeral per call
    assert result.data == {'ok': True} and isinstance(result.data, dict)
    assert result.backend == 'claude_cli' and result.model == 'claude-sonnet-5-5' and result.effort == 'low'
    assert result.input_tokens == 145 and result.cached_input_tokens == 40 and result.output_tokens == 9
    assert result.estimated_cost_usd == 0.0123 and not result.cached


def test_default_binary_and_requested_model_fallback(tmp_path, monkeypatch):
    calls = install(monkeypatch, tmp_path, json.dumps(result_line(modelUsage={})))
    result = backend(tmp_path).complete_json('review', {}, SCHEMA, 'opus', 'high')
    assert calls[0]['command'][0] == 'claude' and result.model == 'opus'


def test_cwd_never_repo_root(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, json.dumps(result_line()))
    with pytest.raises(llm.IsolationError):
        llm.ClaudeCLIBackend(ROOT/'work', {'sandbox_dir': str(ROOT)}).complete_json('review', {}, SCHEMA, 'sonnet', 'low')


def test_images_use_stream_json_without_tools(tmp_path, monkeypatch):
    first = tmp_path/'a.png'; first.write_bytes(b'\x89PNG-one'); second = tmp_path/'b.png'; second.write_bytes(b'\x89PNG-two')
    lines = [{'type': 'system', 'subtype': 'init', 'tools': [], 'mcp_servers': []},
             {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'thinking aloud'}]}},
             result_line(structured_output={'ok': False})]
    calls = install(monkeypatch, tmp_path, '\n'.join(json.dumps(x) for x in lines) + '\n')
    payload = {'_template': 'Judge.', 'note': 'x'}
    result = backend(tmp_path).complete_json('qa_judge', payload, SCHEMA, 'sonnet', 'medium', images=[first, second])
    call = calls[0]
    assert call['command'] == expected_command('claude', 'sonnet', 'medium', SCHEMA, output='stream-json',
                                               extra=('--input-format', 'stream-json', '--verbose'))
    sent = [json.loads(x) for x in call['input'].splitlines() if x.strip()]
    assert len(sent) == 1 and sent[0]['type'] == 'user' and sent[0]['message']['role'] == 'user'
    content = sent[0]['message']['content']
    assert [c['type'] for c in content] == ['image', 'image', 'text']
    assert content[0]['source'] == {'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(b'\x89PNG-one').decode()}
    assert content[1]['source']['data'] == base64.b64encode(b'\x89PNG-two').decode()
    assert content[2]['text'] == llm._prompt('qa_judge', payload)
    assert result.data == {'ok': False} and result.input_tokens == 145


@pytest.mark.parametrize('event', [
    {'type': 'system', 'subtype': 'init', 'tools': ['Bash'], 'mcp_servers': []},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Read', 'input': {}}]}},
])
def test_stream_tool_activity_is_isolation_error(tmp_path, monkeypatch, event):
    image = tmp_path/'a.png'; image.write_bytes(b'png')
    install(monkeypatch, tmp_path, '\n'.join(json.dumps(x) for x in [event, result_line()]))
    with pytest.raises(llm.IsolationError):
        backend(tmp_path).complete_json('qa_judge', {}, SCHEMA, 'sonnet', 'low', images=[image])


def test_permission_denials_are_isolation_error(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, json.dumps(result_line(permission_denials=[{'tool_name': 'Bash'}])))
    with pytest.raises(llm.IsolationError):
        backend(tmp_path).complete_json('review', {}, SCHEMA, 'sonnet', 'low')


@pytest.mark.parametrize('line,code', [
    (result_line(is_error=True, result='API Error: 500 overloaded'), 0),
    (result_line(subtype='error_max_turns', structured_output=None), 0),
    (result_line(subtype='error_during_execution', is_error=True), 1),
    (result_line(structured_output=None, result=''), 0),
])
def test_errors_are_backend_errors(tmp_path, monkeypatch, line, code):
    install(monkeypatch, tmp_path, json.dumps(line), returncode=code)
    with pytest.raises(llm.BackendError) as exc:
        backend(tmp_path).complete_json('review', {}, SCHEMA, 'sonnet', 'low')
    assert not isinstance(exc.value, llm.IsolationError)


def test_unparseable_stdout_and_timeout_are_backend_errors(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, 'not json', returncode=1, stderr='boom')
    with pytest.raises(llm.BackendError):backend(tmp_path).complete_json('review', {}, SCHEMA, 'sonnet', 'low')
    def slow(command, **kwargs):raise llm.subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(llm.subprocess, 'run', slow)
    with pytest.raises(llm.BackendError, match='timed out'):backend(tmp_path).complete_json('review', {}, SCHEMA, 'sonnet', 'low')


def test_answer_without_structured_output_goes_to_validation_retry(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, json.dumps(result_line(structured_output=None, result='Here you go: not json')))
    result = backend(tmp_path).complete_json('review', {}, SCHEMA, 'sonnet', 'low')
    assert result.data == 'Here you go: not json' and result.input_tokens == 145
    with pytest.raises(llm.ValidationFailure):llm._validate('review', result.data, {}, SCHEMA)


def test_model_unavailable_is_distinct_backend_error(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, json.dumps(result_line(is_error=True, result='API Error: 404 {"type":"not_found_error","message":"model: opus"}')))
    with pytest.raises(llm.ModelUnavailable):backend(tmp_path).complete_json('review', {}, SCHEMA, 'opus', 'high')


def test_invalid_effort_is_rejected_before_spawning(tmp_path, monkeypatch):
    calls = install(monkeypatch, tmp_path, json.dumps(result_line()))
    with pytest.raises(llm.BackendError):backend(tmp_path).complete_json('review', {}, SCHEMA, 'sonnet', 'minimal')
    assert not calls


# ---------------------------------------------------------------- quota

@pytest.mark.parametrize('text', [
    "You've hit your session limit · resets 3pm (America/Bogota)",
    "You've hit your weekly limit · resets Nov 12, 9am (Europe/Madrid)",
    "You've hit your Opus limit",
    "You've hit your Sonnet limit · resets 11:30pm",
    'Claude AI usage limit reached|1762650000',
])
def test_subscription_limit_texts_are_quota(text):
    assert llm._quota(text)


def test_model_scoped_limits():
    assert llm._model_scoped_limit("You've hit your Opus limit · resets 3pm")
    assert not llm._model_scoped_limit("You've hit your session limit · resets 3pm")


def test_reset_time_parsing():
    zone = ZoneInfo('America/Bogota')
    now = datetime(2026, 11, 8, 10, 0, tzinfo=zone).timestamp()
    assert llm._resume_after("hit your session limit · resets 3pm (America/Bogota)", now) == datetime(2026, 11, 8, 15, 0, tzinfo=zone).timestamp()
    # Already passed today -> tomorrow.
    assert llm._resume_after("resets 9:30am (America/Bogota)", now) == datetime(2026, 11, 9, 9, 30, tzinfo=zone).timestamp()
    madrid = ZoneInfo('Europe/Madrid')
    assert llm._resume_after("weekly limit · resets Nov 12, 9am (Europe/Madrid)", now) == datetime(2026, 11, 12, 9, 0, tzinfo=madrid).timestamp()
    assert llm._resume_after(f'Claude AI usage limit reached|{int(now)+3600}', now) == int(now)+3600
    assert llm._resume_after('Claude AI usage limit reached|1000000000', now) is None  # past
    assert llm._resume_after('resets in 2h 30m', now) == now + 9000
    assert llm._resume_after("hit your session limit", now) is None
    assert llm._resume_after("resets soon", now) is None
    assert llm._resume_after("resets 3pm (Not/AZone)", now) is not None  # unknown zone falls back to local time


def test_cli_limit_raises_quota_with_resume_after(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, json.dumps(result_line(is_error=True, structured_output=None,
                                                          result="You've hit your session limit · resets in 1h")))
    with pytest.raises(llm.QuotaExceeded) as exc:
        backend(tmp_path).complete_json('review', {}, SCHEMA, 'sonnet', 'low')
    assert exc.value.resume_after is not None and not exc.value.model_scoped


def claude_config(setup, extra=''):
    book, config, payload = setup
    config.write_text(config.read_text().replace('backend="codex_sdk"', 'backend="claude_cli"\ntimeout_seconds=30' + extra))
    return book, config, payload


def test_limit_message_pauses_with_exit_75_and_provider_reset(setup, tmp_path, monkeypatch):
    book, config, payload = claude_config(setup)
    calls = install(monkeypatch, tmp_path, json.dumps(result_line(is_error=True, structured_output=None,
                                                                  result="You've hit your session limit · resets in 2h")))
    before = llm.time.time()
    with pytest.raises(llm.QuotaPaused) as exc:llm.run_task('translate_page', payload)
    assert exc.value.exit_code == 75
    assert len(calls) == 1  # a parsed provider reset skips the short backoff loop
    pause = json.loads((book/'work/quota_pause.json').read_text())
    assert before + 7200 - 5 <= pause['resume_after_unix'] <= llm.time.time() + 7200 + 5
    assert pause['exit_code'] == 75 and pause['resume_after_source'] == 'provider_reset'
    assert json.loads((book/'work/manifest.json').read_text())['pages'][0]['status'] == 'pending'
    log = [json.loads(x) for x in (book/'work/costs.jsonl').read_text().splitlines()]
    assert log[-1]['status'] == 'quota' and log[-1]['backend'] == 'claude_cli'
    assert 'secret' not in (book/'work/costs.jsonl').read_text()


def test_unparsed_limit_uses_existing_backoff(setup, tmp_path, monkeypatch):
    book, config, payload = claude_config(setup)
    calls = install(monkeypatch, tmp_path, json.dumps(result_line(is_error=True, structured_output=None,
                                                                  result="You've hit your weekly limit")))
    with pytest.raises(llm.QuotaPaused):llm.run_task('translate_page', payload)
    assert len(calls) == 4
    assert json.loads((book/'work/quota_pause.json').read_text())['resume_after_source'] == 'backoff'


def test_cli_entrypoint_returns_75_on_claude_limit(setup, tmp_path, monkeypatch):
    from astro_translate import __main__
    book, config, payload = claude_config(setup)
    install(monkeypatch, tmp_path, json.dumps(result_line(is_error=True, structured_output=None,
                                                          result="You've hit your session limit · resets 3pm")))
    monkeypatch.setattr(__main__, 'native_translate', lambda *a, **k: llm.run_task('translate_page', payload))
    assert __main__.main(['translate-native-pages', '--book', str(book), '--pages', '1', '--phase', 'smoke']) == 75


def test_run_task_through_claude_cli_logs_estimated_subscription_cost(setup, tmp_path, monkeypatch):
    book, config, payload = claude_config(setup)
    install(monkeypatch, tmp_path, lambda command, kwargs: json.dumps(result_line(structured_output=response())))
    result = llm.run_task('translate_page', payload)
    assert result.data == response() and result.backend == 'claude_cli' and result.model == 'claude-sonnet-5-5'
    entry = [json.loads(x) for x in (book/'work/costs.jsonl').read_text().splitlines()][0]
    assert entry['backend'] == 'claude_cli' and entry['status'] == 'ok'
    assert entry['cost_usd'] is None and entry['cost_status'] == 'estimated_subscription'
    assert entry['estimated_cost_usd'] == 0.0123
    assert entry['input_tokens'] == 145 and entry['cached_input_tokens'] == 40
    assert llm.run_task('translate_page', payload).cached


# ---------------------------------------------------------------- escalation

def emphasis_request():
    from astro_translate.style_spans import encode
    light = {'font': 'Soho-Light', 'bold': False, 'weight': 300, 'color': 0}
    bold = {'font': 'Soho-Bold', 'bold': True, 'weight': 700, 'color': 123}
    source, pairs = encode([{'text': 'Sun ', 'style': bold}, {'text': 'example 12', 'style': light}])
    return {'id': 'a', 'source': source, '_style_pairs': pairs, '_emphasis_protocol': 'flat-v1'}


def with_escalation(config, model='opus', effort='high'):
    config.write_text(config.read_text().replace('fallback="none"', f'fallback="none"\nescalation_model="{model}"\nescalation_effort="{effort}"'))


def test_no_escalation_configured_stops_after_three_attempts(setup, monkeypatch):
    book, config, payload = setup; payload['blocks'] = [emphasis_request()]
    fake = FakeBackend([response(text='Sol')]*6); use(monkeypatch, fake)
    with pytest.raises(llm.ValidationFailure):llm.run_task('translate_page', payload)
    assert len(fake.calls) == 3 and {c[3] for c in fake.calls} == {'test-model'}


def test_configured_escalation_switches_model_and_effort(setup, monkeypatch):
    book, config, payload = setup; payload['blocks'] = [emphasis_request()]; with_escalation(config)
    good = response(text='Sol ejemplo {n1}'); good['blocks'][0]['emphasis'] = [{'style': 'bold', 'text': 'Sol', 'occurrence': None}]
    fake = FakeBackend([response(text='Sol')]*3 + [good]); use(monkeypatch, fake)
    result = llm.run_task('translate_page', payload)
    assert fake.calls[-1][3:] == ('opus', 'high') and result.model == 'opus'


@pytest.mark.parametrize('error', [
    llm.ModelUnavailable('model_not_found: opus'),
    llm.BackendError('Claude CLI failed: not supported'),
    llm.QuotaExceeded("You've hit your Opus limit", model_scoped=True),
])
def test_escalation_unavailable_becomes_review_not_abort(setup, monkeypatch, error):
    import sqlite3
    book, config, payload = setup; payload['blocks'] = [emphasis_request()]; with_escalation(config)
    fake = FakeBackend([response(text='Sol')]*3 + [error]); use(monkeypatch, fake)
    with pytest.raises(llm.ValidationFailure, match='escalation unavailable') as exc:
        llm.run_task('translate_page', payload)
    assert not isinstance(exc.value, llm.BackendError)
    assert len(fake.calls) == 4
    queue = json.loads((book/'work/review_queue.json').read_text())
    assert queue[-1]['reason'] == 'llm_validation' and 'escalation unavailable' in queue[-1]['detail']
    assert 'opus' in queue[-1]['detail']
    assert not (book/'work/quota_pause.json').exists()
    assert json.loads((book/'work/manifest.json').read_text())['pages'][0]['status'] == 'review'
    log = [json.loads(x) for x in (book/'work/costs.jsonl').read_text().splitlines()]
    assert log[-1]['escalation'] == 'unavailable' and log[-1]['model'] == 'opus'
    with sqlite3.connect(book/'work/llm_cache.sqlite') as db:
        assert db.execute('SELECT count(*) FROM responses').fetchone()[0] == 0


def test_escalation_isolation_error_still_aborts(setup, monkeypatch):
    book, config, payload = setup; payload['blocks'] = [emphasis_request()]; with_escalation(config)
    fake = FakeBackend([response(text='Sol')]*3 + [llm.IsolationError('tool attempted')]); use(monkeypatch, fake)
    with pytest.raises(llm.IsolationError):llm.run_task('translate_page', payload)


def test_escalation_session_quota_still_pauses(setup, monkeypatch):
    book, config, payload = setup; payload['blocks'] = [emphasis_request()]; with_escalation(config)
    fake = FakeBackend([response(text='Sol')]*3 + [llm.QuotaExceeded('session limit')]*4); use(monkeypatch, fake)
    with pytest.raises(llm.QuotaPaused):llm.run_task('translate_page', payload)


# ---------------------------------------------------------------- config, schemas and accounting

def test_repo_config_selects_claude_cli():
    cfg = tomllib.loads((ROOT/'config/pipeline.toml').read_text())
    provider = cfg['provider']
    assert provider['backend'] == 'claude_cli' and provider['fallback'] == 'none'
    assert provider['sandbox_dir'] == 'work/.codex_llm_sandbox'
    assert provider['escalation_model'] == 'opus' and provider['escalation_effort'] == 'high'
    assert provider.get('claude_bin', 'claude') == 'claude'
    for task in ('translate_page', 'translate_labels', 'shorten', 'classify_labels'):
        assert (cfg['tasks'][task]['model'], cfg['tasks'][task]['effort']) == ('sonnet', 'low')
    for task in ('qa_judge', 'glossary_candidates', 'review'):
        assert (cfg['tasks'][task]['model'], cfg['tasks'][task]['effort']) == ('sonnet', 'medium')
    assert all(t['effort'] in llm.CLAUDE_EFFORTS for t in cfg['tasks'].values())


def test_schemas_accept_claude_cli():
    approval = json.loads((ROOT/'schemas/backend_approval.json').read_text())
    assert 'claude_cli' in approval['properties']['backend']['enum']
    assert 'claude_cli' in approval['properties']['fallback']['enum']
    lote = json.loads((ROOT/'schemas/lote.json').read_text())
    jsonschema.validate({'pages_ok': [1], 'pages_review': [], 'tokens': 0, 'cost_usd': None, 'errors': [],
                         'backends_used': ['claude_cli'], 'calls': 1, 'cache_hits': 0, 'pages_paused': []}, lote)


def test_consumption_treats_every_subscription_zero_as_unknown():
    from astro_translate.production import consumption
    rows = [{'backend': 'claude_cli', 'cost_usd': None, 'estimated_cost_usd': 0.5, 'status': 'ok'},
            {'backend': 'claude_cli', 'cost_usd': 0, 'status': 'ok'},
            {'backend': 'codex_exec', 'cost_usd': 0, 'status': 'ok'},
            {'backend': 'claude_cli', 'cost_usd': 0, 'status': 'cache_hit'}]
    result = consumption(rows, 'claude_cli')
    assert result['cost_usd'] == {'known_sum': 0, 'unknown_records': 3}
    assert result['estimated_cost_usd']['known_sum'] == 0.5


def test_production_cost_records_normalize_subscription_zeros(tmp_path):
    from astro_translate.production import Production
    runner = Production.__new__(Production); runner.work = tmp_path
    (tmp_path/'costs.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {'backend': 'claude_cli', 'cost_usd': 0, 'status': 'ok'},
        {'backend': 'codex_sdk', 'cost_usd': 0, 'status': 'ok'},
        {'backend': 'openai_api', 'cost_usd': 0, 'status': 'ok'},
        {'backend': 'claude_cli', 'cost_usd': 0, 'status': 'block_cache_hit'}]) + '\n')
    rows = runner.cost_records()
    assert [r['cost_usd'] for r in rows] == [None, None, 0, 0]


def test_estimate_quota_generalizes_subscription_backends(tmp_path):
    import importlib.util
    import pymupdf
    spec = importlib.util.spec_from_file_location('estimate_quota', ROOT/'scripts/estimate_quota.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    book = tmp_path/'book'; (book/'work').mkdir(parents=True)
    with pymupdf.open() as doc:
        doc.new_page(); doc.new_page(); doc.save(book/'source.pdf')
    base = {'task': 'translate_page', 'phase': 'production', 'cached': False, 'status': 'ok', 'input_tokens': 10,
            'cached_input_tokens': 2, 'output_tokens': 3, 'latency_s': 1.0}
    (book/'work/costs.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {**base, 'page': 1, 'backend': 'claude_cli', 'cost_usd': None, 'cost_status': 'estimated_subscription', 'estimated_cost_usd': 0.25},
        {**base, 'page': 2, 'backend': 'claude_cli', 'cost_usd': 0}]) + '\n')
    result = module.estimate(book, phase='production')
    assert result['cost_usd_known'] == 0 and result['cost_usd_unknown_records'] == 2
    assert result['estimated_subscription_usd']['measured_total'] == 0.25
    assert result['estimated_subscription_usd']['unknown_records'] == 1


def test_cli_schema_strips_meta_schema_reference():
    # Real failure 2026-11-08: "--json-schema is not a valid JSON Schema: no schema with key or ref draft/2020-12".
    from astro_translate.llm import _cli_schema
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object',
            'properties':{'a':{'$schema':'x','type':'string'}},'required':['a']}
    out=_cli_schema(schema)
    assert '$schema' not in out and '$schema' not in out['properties']['a']
    assert out['required']==['a'] and schema['$schema']
