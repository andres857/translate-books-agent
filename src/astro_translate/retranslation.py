"""Revalidate saved native translations and repair only selected/invalid clauses.

Saved valid units are authoritative, regardless of a new cache context. No
translation is authored here. Ambiguous sentence/style alignment fails closed.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from . import llm
from .consistency import _annotate, _ranges, build_plan, resolve_plan
from .core import atomic_json, book_lock, sha256
from .dates import translate_date, looks_like_date
from .text_quality import repair_composition, continuation_pairs, boundary_issues


def _target_runs(unit):
    runs = unit.get('target_runs')
    if runs is None:
        runs = [{'text': unit.get('target', ''), 'style': unit.get('style', {})}]
    if ''.join(r['text'] for r in runs) != unit.get('target'):
        raise ValueError('target_runs disagree with target')
    return runs


def _marked_pair(unit):
    source, markers, styles = _annotate(unit, 0, len(unit['source']))
    runs = _target_runs(unit)
    # Adjacent equivalent runs are one style segment, as in _annotate.
    merged = []
    for run in runs:
        if not run['text']:
            continue
        if merged and merged[-1]['style'] == run['style']:
            merged[-1]['text'] += run['text']
        else:
            merged.append(deepcopy(run))
    expected = styles or [unit.get('style', {})]
    if [r['style'] for r in merged] != expected:
        raise ValueError('Saved inline styles differ from source')
    target = unit['target']
    request = {'id': unit['id'], 'source': source}
    if markers:
        request['_style_markers'] = markers
        target = ''.join(m + r['text'] for m, r in zip(markers, merged)) + markers[-1]
    if 'max_chars' in unit:
        request['max_chars'] = unit['max_chars']
    return request, target


def validate_answer(request, target, *, extras=(), glossary=(), schema=None):
    """Run the current wire validators on restored text, without inference.

    Mask both sides locally, then align markers by their original values (not
    their position). Thus a legitimate value reorder is preserved and tested.
    """
    flat_answer = target if isinstance(target,dict) else None
    if flat_answer is not None:
        from .emphasis import reconstruct
        reconstruct(flat_answer,request)
        request={**request,'_emphasis_protocol':'flat-v1'}
        target=flat_answer['translation']
    payload = {'blocks': [request]}
    wire, maps = llm._prepare('translate_page', payload, extras, glossary)
    target_map = {}
    masked = llm._mask_text(target, extras, target_map)
    available = defaultdict(deque)
    for marker, value in maps[request['id']].items():
        available[value].append(marker)
    replacements = {}
    for marker, value in target_map.items():
        if not available[value]:
            raise ValueError('Saved protected values changed')
        replacements[marker] = available[value].popleft()
    annotations=[]
    if flat_answer is not None:
        from .emphasis import intervals,occurrence_at
        for (start,end,key),annotation in zip(intervals(target,flat_answer['emphasis']),flat_answer['emphasis']):
            left=len(llm._mask_text(target[:start],extras,{}))
            right=len(llm._mask_text(target[:end],extras,{}))
            phrase=llm.PLACEHOLDER.sub(lambda m:replacements.get(m.group(),m.group()),masked[left:right])
            converted=llm.PLACEHOLDER.sub(lambda m:replacements.get(m.group(),m.group()),masked)
            converted_start=len(llm.PLACEHOLDER.sub(lambda m:replacements.get(m.group(),m.group()),masked[:left]))
            annotations.append({**annotation,'text':phrase,'occurrence':occurrence_at(converted,phrase,converted_start)})
    masked = llm.PLACEHOLDER.sub(lambda m: replacements.get(m.group(), m.group()), masked)
    # Response-only annotations are not retained in page artifacts. Empty
    # adapters are used solely for schema validation, never as audit evidence.
    answer = {'blocks': [{'id': request['id'], 'translation': masked,
                          'glossary_terms_used': [], 'grammatical_corrections': []}]}
    if flat_answer is not None:answer['blocks'][0]['emphasis']=annotations
    llm._validate('translate_page', answer, wire, schema or {'type': 'object'})
    llm._restore('translate_page', answer, maps, payload)


def _validate_unit(unit, **options):
    if unit.get('translation_method') == 'deterministic_date':
        if translate_date(unit['source']) != unit.get('target'):
            raise ValueError('Invalid deterministic date')
        return
    # Local spacing/exact-text style repairs belong to composition; they must
    # not force an otherwise valid saved unit through a model.
    request, target = _marked_pair(repair_composition(unit))
    validate_answer(request, target, **options)


def validate_units(saved, **options):
    ids = [u['id'] for u in saved['blocks']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate saved unit ids')
    issues = {}
    for unit in saved['blocks']:
        try:
            _validate_unit(unit, **options)
        except (ValueError, llm.ValidationFailure) as exc:
            issues[unit['id']] = [str(exc)]
    for left,right in continuation_pairs(saved['blocks']):
        for issue in boundary_issues(left,right):
            issues.setdefault(issue['unit_id'],[]).append(issue['code']+' across caption continuation')
    return issues


def _slice_runs(runs, start, end):
    result = []
    offset = 0
    for run in runs:
        a, b = max(start, offset), min(end, offset + len(run['text']))
        if a < b:
            result.append({**deepcopy(run), 'text': run['text'][a-offset:b-offset]})
        offset += len(run['text'])
    return result


def _clauses(unit):
    sources, targets = list(_ranges(unit['source'])), list(_ranges(unit['target']))
    if len(sources) != len(targets):
        raise ValueError('Ambiguous saved sentence alignment; use a validated donor unit')
    runs = _target_runs(unit)
    for (a, b), (c, d) in zip(sources, targets):
        styles = []
        for run in unit.get('style_runs', []):
            left, right = max(a, run['start']), min(b, run['end'])
            if left < right:
                styles.append({**run, 'start': left-a, 'end': right-a})
        clause = {**unit, 'source': unit['source'][a:b], 'style_runs': styles,
                  'target': unit['target'][c:d], 'target_runs': _slice_runs(runs, c, d)}
        if styles:
            clause['style'] = styles[0]['style']
        yield clause, c, d


def correct_translation(saved, *, selected=(), donor=None, payload_factory=None,
                        memory=None, no_llm=False, **options):
    """Return a copy and audit; valid unselected units stay byte-for-byte equal.

    An explicit unit selection forces its clauses through run_task (I6). An
    automatic repair sends only failing clauses. A validated donor, or memory
    for an automatic repair, can satisfy a repair with no provider call.
    """
    result = deepcopy(saved)
    issues = validate_units(saved, **options)
    ids = {u['id'] for u in saved['blocks']}
    selected = set(selected)
    if selected - ids:
        raise ValueError('Unknown selected units: ' + ', '.join(sorted(selected - ids)))
    donor_units = {}
    if donor is not None:
        if (donor.get('source_sha256'), donor.get('page')) != (saved.get('source_sha256'), saved.get('page')):
            raise ValueError('Donor source/page mismatch')
        donor_units = {u['id']: u for u in donor['blocks']}
        if len(donor_units) != len(donor['blocks']):
            raise ValueError('Duplicate donor unit ids')
    audit = {'units': [], 'llm_calls': 0, 'run_task_calls': 0, 'validated_units': len(ids)}
    pending = {}
    repairs = []
    for index, unit in enumerate(saved['blocks']):
        identifier = unit['id']
        if identifier not in issues and identifier not in selected:
            continue
        record = {'id': identifier, 'reasons': issues.get(identifier, []) + (['explicit_selection'] if identifier in selected else []), 'clauses': []}
        audit['units'].append(record)
        candidate = donor_units.get(identifier)
        if candidate is not None:
            # Only translated fields may be imported. Geometry and source are
            # tied to the base artifact; reject stale extraction/layout donors.
            fields = {'target', 'target_runs', 'editorial_punctuation'}
            if {k:v for k,v in candidate.items() if k not in fields} != {k:v for k,v in unit.items() if k not in fields}:
                raise ValueError('Donor unit source/style/geometry mismatch: ' + identifier)
            _validate_unit(candidate, **options)
            result['blocks'][index] = deepcopy(candidate)
            record['method'] = 'validated_existing_target'
            continue
        if llm._symbol_provenance(unit):
            raise ValueError('Symbol-font units cannot enter corrective requests')
        if looks_like_date(unit['source']):
            raise ValueError('Date corrections require deterministic handling')
        patches = []
        for clause, start, end in _clauses(unit):
            try:
                _validate_unit(clause, **options)
                invalid = False
            except (ValueError, llm.ValidationFailure):
                invalid = True
            boundary_failed=any('caption continuation' in reason for reason in issues.get(identifier, []))
            if boundary_failed:
                invalid = True
            if not invalid and identifier not in selected:
                continue
            plan = build_plan([clause])
            if len(plan['requests']) != 1:
                raise ValueError('Ambiguous clause plan; use a validated donor unit')
            request = plan['requests'][0]
            entry = next(iter(plan['entries'].values()))
            answer = None
            if memory is not None and identifier not in selected and not boundary_failed:
                answer = memory.get(entry)
                if answer is not None:
                    try:
                        validate_answer(request, answer, **options)
                    except (ValueError, llm.ValidationFailure):
                        answer = None
            method = 'validated_sentence_memory' if answer is not None else 'run_task'
            record['clauses'].append({'id': request['id'], 'method': method})
            if answer is None:
                pending[request['id']] = request
            patches.append((start, end, plan, answer))
        if not patches:
            raise ValueError('Unit failure cannot be isolated to clauses: ' + identifier)
        record['method'] = 'clause_repair'
        repairs.append((index, unit, patches))
    answers = {}
    if pending:
        if no_llm:
            raise ValueError('no-llm: selected/invalid clauses lack a validated existing target')
        if payload_factory is None:
            raise ValueError('A gated payload_factory is required for run_task')
        # Validate neighbouring caption fragments jointly, while retaining the
        # original unit ids and each request's protected-value/style contract.
        requested={record['id']:[c['id'] for c in record['clauses'] if c['id'] in pending]
                   for record in audit['units']}
        current={u['id']:u for u in result['blocks']}
        for left,right in continuation_pairs(saved['blocks']):
            for unit,neighbor,side in ((left,right,'after'),(right,left,'before')):
                requests=requested.get(unit['id'],[])
                if not requests:continue
                request=pending[requests[-1] if side=='after' else requests[0]]
                other=requested.get(neighbor['id'],[])
                context={k:current[neighbor['id']][k] for k in ('id','source','target')}
                context['side']=side
                if other:context['request_id']=other[0] if side=='after' else other[-1]
                request.setdefault('quality_neighbors',[]).append(context)
        plan = {'requests': list(pending.values()), 'entries': {
            key: {'id': key, 'pages': [saved['page']]} for key in pending}}
        response = llm.run_task('translate_page', payload_factory(plan))
        audit['run_task_calls'] = 1
        # run_task may batch, retry or hit cache; provider counts live in costs.jsonl.
        audit['llm_calls'] = 0 if getattr(response, 'cached', False) else None
        rows = response.data['blocks']
        answers = {r['id']: r['translation'] for r in rows}
        if len(answers) != len(rows) or set(answers) != set(pending):
            raise ValueError('Corrective response ids differ')
        for identifier, request in pending.items():
            # Standalone validation sees the new neighbouring answers, not the
            # defective saved fragment that triggered this correction.
            request=deepcopy(request)
            for neighbor in request.get('quality_neighbors',[]):
                if neighbor.get('request_id') in answers:
                    neighbor['target']=answers[neighbor['request_id']]
            validate_answer(request, answers[identifier], **options)
    for index, unit, patches in repairs:
        runs = []
        cursor = 0
        original_runs = _target_runs(unit)
        for start, end, plan, cached in patches:
            runs.extend(_slice_runs(original_runs, cursor, start))
            identifier = plan['requests'][0]['id']
            resolved = resolve_plan(plan, {identifier: cached if cached is not None else answers[identifier]})
            runs.extend(resolved['units'][0]['target_runs'])
            cursor = end
        runs.extend(_slice_runs(original_runs, cursor, len(unit['target'])))
        result['blocks'][index]['target_runs'] = runs
        result['blocks'][index]['target'] = ''.join(r['text'] for r in runs)
    remaining = validate_units(result, **options)
    if remaining:
        raise ValueError('Corrective result failed validation: ' + json.dumps(remaining))
    return result, audit


def rewrite_translation(book, destination, *, from_archive=None, reuse_from=None,
                        validation_dependency=None, **options):
    """Optimistic locking, exact original backup, atomic output and audit.

    A prepared audit precedes replacement so a process interruption is traceable.
    This operation does not approve or regenerate page QA/layout artifacts.
    """
    destination = Path(destination).resolve()
    base = Path(from_archive).resolve() if from_archive else destination
    donor_path = Path(reuse_from).resolve() if reuse_from else None
    with book_lock(book):
        current = destination.read_bytes() if destination.exists() else None
        base_bytes = base.read_bytes()
        donor_bytes = donor_path.read_bytes() if donor_path else None
    result, audit = correct_translation(json.loads(base_bytes), donor=json.loads(donor_bytes) if donor_bytes else None, **options)
    digest = lambda content: hashlib.sha256(content).hexdigest()
    with book_lock(book):
        if ((destination.read_bytes() if destination.exists() else None) != current
                or base.read_bytes() != base_bytes
                or (donor_path and donor_path.read_bytes() != donor_bytes)):
            raise ValueError('Translation changed concurrently; refusing overwrite')
        history = destination.parent / 'history'
        history.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='retranslate-' + destination.stem + '-', dir=history))
        previous = folder / destination.name
        if current is not None:
            previous.write_bytes(current)
        (folder / 'base-translation.json').write_bytes(base_bytes)
        if donor_bytes is not None:
            (folder / 'donor-translation.json').write_bytes(donor_bytes)
        audit.update({'status': 'prepared', 'validation_dependency_sha256': validation_dependency,
                      'base_artifact': str(base), 'base_sha256': digest(base_bytes),
                      'previous_artifact': str(previous) if current is not None else None,
                      'previous_sha256': digest(current) if current is not None else None,
                      'donor_artifact': str(donor_path) if donor_path else None,
                      'donor_sha256': digest(donor_bytes) if donor_bytes is not None else None,
                      'output_artifact': str(destination), 'qa_status': 'not_run_for_this_correction'})
        record = folder / 'retranslation.json'
        atomic_json(record, audit)
        atomic_json(destination, result)
        audit.update(status='complete', output_sha256=sha256(destination))
        atomic_json(record, audit)
    return record
