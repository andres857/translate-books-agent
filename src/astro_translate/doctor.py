"""Extended environment doctor (retrospective cause 7): disk, storage, Claude
login, model availability, quota pause and G5 block.

Read-only except for `--probe-models`, which makes one minimal call per model
through llm.probe_models (llm.py remains the only provider boundary) and
journals it in costs.jsonl. Secrets and account identity are never reported.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib

from . import llm

GIB = 1024**3
DEFAULT_MIN_FREE_GIB = 5.0
DEFAULT_WARN_FREE_GIB = 15.0
# Only these `claude auth status` fields are reported; email/org/tokens never are.
AUTH_FIELDS = ('loggedIn', 'authMethod', 'apiProvider', 'subscriptionType')
SUBSCRIPTION_AUTH = ('claude.ai',)

# Indirections so tests can replace system access without touching globals.
_disk_usage = shutil.disk_usage
_which = shutil.which
_run = subprocess.run


def _legacy():
    from .adapters import doctor
    return doctor()


def _check(name, level, detail='', **extra):
    return {'name':name, 'level':level, 'detail':detail, **extra}


def resolve_config(book=None, config_path=None):
    if config_path:
        return Path(config_path).resolve()
    if book and (Path(book)/'work/pipeline.toml').exists():
        return (Path(book)/'work/pipeline.toml').resolve()
    return llm.ROOT/'config/pipeline.toml'


def _folder_size(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(root)/name).stat().st_size
            except OSError:
                pass
    return total


def _disk(book, minimum, warning):
    target = Path(book) if book else llm.ROOT
    free = _disk_usage(target).free
    level = 'error' if free < minimum*GIB else 'warning' if free < warning*GIB else 'ok'
    return _check('disk_free', level, f'{free/GIB:.1f} GiB free at {target} (error < {minimum:g}, warning < {warning:g} GiB)',
                  free_bytes=free, min_free_gib=minimum, warn_free_gib=warning)


def _candidates(book):
    import pymupdf as fitz
    folder = Path(book)/'work/production'; full = []; unreadable = []
    for path in sorted(folder.glob('p[0-9][0-9][0-9][0-9]-translated.pdf')):
        try:
            with fitz.open(path) as doc:
                if len(doc) > 1:
                    full.append(int(path.name[1:5]))
        except Exception:
            unreadable.append(path.name)
    if unreadable:
        return _check('single_page_candidates', 'error', 'Unreadable candidates: '+', '.join(unreadable[:10]),
                      pages=full, unreadable=unreadable)
    if full:
        return _check('single_page_candidates', 'warning',
                      f'{len(full)} full-book candidates not migrated; run '
                      f'.venv/bin/python scripts/migrate_single_page_candidates.py --book {book}', pages=full)
    return _check('single_page_candidates', 'ok', 'All production candidates are single-page', pages=[])


def _claude(provider):
    required = provider.get('backend') == 'claude_cli'
    binary = provider.get('claude_bin') or 'claude'
    found = _which(binary)
    checks = [_check('claude_binary', 'ok' if found else 'error' if required else 'warning',
                     f'{binary} -> {found}' if found else f'{binary} not found in PATH', path=found)]
    if not found:
        checks.append(_check('claude_auth', 'skipped', 'claude binary missing'))
        return checks
    env = os.environ.copy()
    for key in llm.CLAUDE_PAID_ENV:  # same environment as llm.ClaudeCLIBackend calls
        env.pop(key, None)
    try:
        completed = _run([found, 'auth', 'status', '--json'], capture_output=True, text=True, env=env, timeout=20)
        data = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        data = None
    if not isinstance(data, dict):
        checks.append(_check('claude_auth', 'skipped',
                             '`claude auth status --json` unavailable in this CLI; login not verified '
                             '(use --probe-models for an end-to-end check)'))
        return checks
    auth = {key:data[key] for key in AUTH_FIELDS if key in data}
    if not data.get('loggedIn'):
        level, detail = 'error' if required else 'warning', 'claude is not logged in; run `claude auth login`'
    elif data.get('authMethod') not in SUBSCRIPTION_AUTH:
        level, detail = 'warning', f"authMethod={data.get('authMethod')!r}: layer B expects subscription OAuth"
    else:
        level, detail = 'ok', f"logged in via {data.get('authMethod')} ({data.get('subscriptionType', 'unknown')} plan)"
    expires = data.get('loginExpiresAt')
    if level == 'ok' and isinstance(expires, str):
        try:
            if datetime.fromisoformat(expires.replace('Z', '+00:00')) < datetime.now(timezone.utc):
                level, detail = 'warning', detail+'; stored login expiry is in the past'
        except ValueError:
            pass
    checks.append(_check('claude_auth', level, detail, auth=auth))
    return checks


def _paid_env():
    present = [key for key in llm.CLAUDE_PAID_ENV if os.environ.get(key)]
    if present:
        return _check('paid_api_env', 'warning', ', '.join(present)+' set in the environment (value not shown); '
                      'llm.py removes it from every claude call', variables=present)
    return _check('paid_api_env', 'ok', 'No paid API credentials in the environment', variables=[])


def _quota(book):
    path = Path(book)/'work/quota_pause.json'
    if not path.exists():
        return _check('quota_pause', 'ok', 'No quota pause')
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return _check('quota_pause', 'error', f'{path} is not valid JSON')
    after = data.get('resume_after_unix', 0)
    when = datetime.fromtimestamp(after, timezone.utc).isoformat() if isinstance(after, (int, float)) and after else None
    if isinstance(after, (int, float)) and after > time.time():
        return _check('quota_pause', 'error', f'Active quota pause until {when}; then produce --resume',
                      resume_after=when, pages=data.get('pages'))
    return _check('quota_pause', 'warning', f'Expired quota pause (since {when}); produce --resume may proceed',
                  resume_after=when, pages=data.get('pages'))


def _g5(book):
    from .production import G5Blocked, validate_g5_decision
    folder = Path(book)/'work/production'; path = folder/'g5_block.json'
    if not path.exists():
        return _check('g5_block', 'ok', 'No G5 block')
    try:
        block = json.loads(path.read_text()); batch = int(block['batch'])
    except (ValueError, KeyError, TypeError):
        return _check('g5_block', 'error', f'{path} is unreadable')
    decision_name = f'g5_decision-{batch:02d}.json'
    for candidate in (folder/decision_name, folder/f'g5_decision-{batch}.json'):
        if candidate.exists():
            try:
                decision = validate_g5_decision(json.loads(candidate.read_text()), batch)
            except (ValueError, G5Blocked) as exc:
                return _check('g5_block', 'error', f'Batch {batch} blocked; invalid decision {candidate.name}: {exc}', block=block)
            return _check('g5_block', 'warning', f"Batch {batch} blocked; decision '{decision['decision']}' recorded, "
                          'the next produce outside the batch archives the block', block=block)
    return _check('g5_block', 'error',
                  f"G5 failed for batch {batch} (ratio {block.get('ratio')}, {block.get('ok')}/{block.get('in_scope')}); "
                  f"recompose pages {block.get('pages')} or record work/production/{decision_name}", block=block)


def _probe(book, config_path, config):
    if not book:
        return _check('probe_models', 'error', '--probe-models requires --book (sandbox and costs.jsonl are per book)')
    results = llm.probe_models(book, config_path)
    level = 'ok'; problems = []
    for item in results:
        if item['status'] == 'available':
            continue
        escalation_only = item['roles'] == ['escalation']
        severe = item['status'] in ('unavailable', 'error') and not escalation_only
        level = 'error' if severe else ('warning' if level == 'ok' else level)
        note = ' (escalation only: units go to review, the batch continues)' if escalation_only else ''
        problems.append(f"{item['model']}/{item['effort']} {item['status']}{note}")
    detail = '; '.join(problems) if problems else f'{len(results)} model(s) available'
    return _check('probe_models', level, detail, backend=config.get('provider', {}).get('backend'), models=results)


def run_doctor(book=None, *, config_path=None, probe_models=False, min_free_gib=None, warn_free_gib=None):
    book = Path(book).resolve() if book else None
    report = {}
    checks = []
    try:
        report.update(_legacy())
    except Exception as exc:
        checks.append(_check('environment', 'error', f'Legacy environment probe failed: {type(exc).__name__}: {exc}'))
    config_path = resolve_config(book, config_path)
    try:
        config = tomllib.loads(config_path.read_text())
        provider = config.get('provider', {})
        level, note = 'ok', ''
        default = llm.ROOT/'config/pipeline.toml'
        if config_path != default and default.exists():
            reference = tomllib.loads(default.read_text()).get('provider', {}).get('backend')
            if reference != provider.get('backend'):
                level, note = 'warning', f'; differs from {default.relative_to(llm.ROOT)} backend={reference} (produce uses this file)'
        if provider.get('backend') in llm.RETIRED_BACKENDS:
            level, note = 'error', note+'; Codex backend retired 2026-11-08, use backend="claude_cli"'
        checks.append(_check('config', level, f"backend={provider.get('backend')}; config={config_path}{note}",
                             backend=provider.get('backend'), path=str(config_path)))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        config, provider = {}, {}
        checks.append(_check('config', 'error', f'Cannot read {config_path}: {type(exc).__name__}'))
    settings = config.get('doctor', {})
    minimum = float(min_free_gib if min_free_gib is not None else settings.get('min_free_gib', DEFAULT_MIN_FREE_GIB))
    warning = float(warn_free_gib if warn_free_gib is not None else settings.get('warn_free_gib', DEFAULT_WARN_FREE_GIB))
    checks.append(_disk(book, minimum, warning))
    if book:
        size = _folder_size(book/'work/production')
        checks.append(_check('production_size', 'ok', f'work/production uses {size/GIB:.2f} GiB', bytes=size))
        checks.append(_candidates(book))
    checks.extend(_claude(provider))
    checks.append(_paid_env())
    if book:
        checks.append(_quota(book))
        checks.append(_g5(book))
    if probe_models:
        checks.append(_probe(book, config_path, config))
    else:
        checks.append(_check('probe_models', 'skipped', 'Use --probe-models for one minimal call per configured model'))
    errors = sum(c['level'] == 'error' for c in checks)
    report.update(book=str(book) if book else None, checks=checks, errors=errors,
                  warnings=sum(c['level'] == 'warning' for c in checks), ok=errors == 0)
    return report


def render(report):
    lines = [f"astro_translate doctor - book: {report.get('book') or '(none)'}"]
    for item in report['checks']:
        lines.append(f"[{item['level'].upper():7}] {item['name']}: {item['detail']}")
    versions = report.get('versions') or {}
    if versions:
        lines.append('versions: '+', '.join(f'{k}={v}' for k, v in versions.items()))
    for key in ('babeldoc_options', 'babeldoc_protection_verified', 'babeldoc_reason', 'openai_key_present',
                'ocr_weights_checked', 'batch_api_integration_verified'):
        if key in report:
            lines.append(f'{key}: {report[key]}')
    lines.append(f"result: {'OK' if report['ok'] else 'ERRORS'} ({report['errors']} errors, {report['warnings']} warnings)")
    return '\n'.join(lines)


def cli(args):
    report = run_doctor(args.book, config_path=args.config, probe_models=args.probe_models,
                        min_free_gib=args.min_free_gib, warn_free_gib=args.warn_free_gib)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0 if report['ok'] else 1
