"""Selective caption migration, with literal preservation of untouched JSON units.

No automatic correction of unrelated invalid units. All new targets go through
llm.run_task and the existing validators. Source/style drift fails closed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import llm
from .consistency import build_plan, resolve_plan
from .core import atomic_json, book_lock, sha256
from .retranslation import validate_answer, _validate_unit


def plan_resegmentation(saved, units):
    old = {u['id']: u for u in saved['blocks']}
    if len(old) != len(saved['blocks']) or len({u['id'] for u in units}) != len(units):
        raise ValueError('Duplicate resegmentation ids')
    prefix = f'p{saved["page"]:04d}_'
    replacements, joined, consumed = [], [], set()
    for unit in units:
        if unit.get('page') != saved['page']:
            raise ValueError('Resegmentation source page mismatch')
        if unit['id'] in old:
            previous = old[unit['id']]
            if any(previous[k] != unit[k] for k in ('source', 'spans', 'style_runs')):
                raise ValueError('Resegmentation source provenance changed: '+unit['id'])
            consumed.add(unit['id'])
            continue
        ids = [prefix+i for i in unit.get('merged_from', [])]
        if len(ids) < 2 or not set(ids) <= old.keys() or consumed.intersection(ids):
            raise ValueError('New unit lacks exact source fragments: '+unit['id'])
        members = [old[i] for i in ids]
        from .fidelity import _make_unit
        expected = _make_unit([s for u in members for s in u['spans']], unit['source_block'], False, [])
        if any(expected[k] != unit[k] for k in ('source', 'spans', 'style_runs')):
            raise ValueError('Joined source/style provenance changed: '+unit['id'])
        positions = [next(i for i,u in enumerate(saved['blocks']) if u['id'] == identifier) for identifier in ids]
        if positions != list(range(positions[0], positions[0]+len(ids))):
            raise ValueError('Joined source fragments must be consecutive')
        joined.append(deepcopy(unit))
        replacements.append(dict(old_ids=ids, new_id=unit['id']))
        consumed.update(ids)
    if consumed != old.keys():
        raise ValueError('Resegmentation would discard unrelated source units')
    return dict(page=saved['page'], replacements=replacements, joined_units=joined)


def _fields(raw):
    """Top-level JSON value byte ranges, using the JSON parser (no regex)."""
    text = raw.decode('utf-8')
    decoder = json.JSONDecoder()
    def space(i):
        while i < len(text) and text[i].isspace():
            i += 1
        return i
    index = space(0)
    if text[index] != '{':
        raise ValueError('Expected page JSON object')
    result = {}
    index = space(index+1)
    while text[index] != '}':
        key, end = decoder.raw_decode(text, index)
        index = space(end)
        if text[index] != ':':
            raise ValueError('Invalid JSON field')
        start = space(index+1)
        _, end = decoder.raw_decode(text, start)
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = (len(text[:start].encode()), len(text[:end].encode()))
        index = space(end)
        if text[index] == ',':
            index = space(index+1)
        elif text[index] != '}':
            raise ValueError('Invalid JSON object')
    return result


def block_slices(raw):
    start, end = _fields(raw)['blocks']
    text = raw[start:end].decode('utf-8')
    decoder = json.JSONDecoder()
    index = 1
    result = {}
    while True:
        while text[index].isspace() or text[index] == ',':
            index += 1
        if text[index] == ']':
            return result
        unit, stop = decoder.raw_decode(text, index)
        if unit['id'] in result:
            raise ValueError('Duplicate JSON unit')
        result[unit['id']] = (start+len(text[:index].encode()), start+len(text[:stop].encode()))
        index = stop


def resegment_bytes(raw, units, *, payload_factory=None, no_llm=False, **options):
    saved = json.loads(raw)
    migration = plan_resegmentation(saved, units)
    audit = {k:v for k,v in migration.items() if k != 'joined_units'}
    audit.update(run_task_calls=0, qa_status='not_run_for_this_resegmentation')
    if not migration['joined_units']:
        return raw, audit
    if no_llm:
        raise ValueError('no-llm: new caption units require run_task')
    if payload_factory is None:
        raise ValueError('A gated payload_factory is required for run_task')
    plan = build_plan(migration['joined_units'])
    response = llm.run_task('translate_page', payload_factory(plan))
    audit['run_task_calls'] = 1
    rows = response.data['blocks']
    answers = {r['id']: r['translation'] for r in rows}
    if len(rows) != len(answers) or set(answers) != {r['id'] for r in plan['requests']}:
        raise ValueError('Resegmentation response ids differ')
    for request in plan['requests']:
        validate_answer(request, answers[request['id']], **options)
    resolved = resolve_plan(plan, answers)
    if resolved['issues']:
        raise ValueError('Resegmentation consistency failed')
    for unit in resolved['units']:
        _validate_unit(unit, **options)
    new = {u['id']: u for u in resolved['units']}
    first = {r['old_ids'][0]: r['new_id'] for r in migration['replacements']}
    removed = {i for r in migration['replacements'] for i in r['old_ids']}
    slices = block_slices(raw)
    parts = []
    for identifier, (a,b) in slices.items():
        if identifier in first:
            parts.append(json.dumps(new[first[identifier]], ensure_ascii=False, indent=2).encode())
        elif identifier not in removed:
            parts.append(raw[a:b])
    a,b = _fields(raw)['blocks']
    output = raw[:a]+b'[\n'+b',\n'.join(parts)+b'\n]'+raw[b:]
    fields = _fields(output)
    if 'qa_verdict' in fields:
        a,b = fields['qa_verdict']
        output = output[:a]+b'"not_run"'+output[b:]
    # Structural and byte-level postconditions, including all target_runs.
    plan_resegmentation(json.loads(output), units)
    after = block_slices(output)
    if any(raw[a:b] != output[after[i][0]:after[i][1]] for i,(a,b) in slices.items() if i not in removed):
        raise ValueError('Untouched translation bytes changed')
    audit['preserved_unit_ids'] = [i for i in slices if i not in removed]
    return output, audit


def _atomic_bytes(path, raw):
    fd, name = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def rewrite_resegmentation(book, destination, units, *, validation_dependency=None, **options):
    destination = Path(destination)
    with book_lock(book):
        before = destination.read_bytes()
    if json.loads(before)['source_sha256'] != sha256(Path(book)/'source.pdf'):
        raise ValueError('Resegmentation source hash changed')
    after, audit = resegment_bytes(before, units, **options)
    if before == after:
        return None
    with book_lock(book):
        if destination.read_bytes() != before:
            raise ValueError('Translation changed concurrently; refusing overwrite')
        history = destination.parent/'history'
        history.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='resegment-'+destination.stem+'-', dir=history))
        backup = folder/destination.name
        backup.write_bytes(before)
        audit.update(status='prepared', previous_artifact=str(backup),
                     previous_sha256=hashlib.sha256(before).hexdigest(),
                     output_artifact=str(destination), output_sha256=hashlib.sha256(after).hexdigest(),
                     validation_dependency_sha256=validation_dependency)
        record = folder/'resegmentation.json'
        atomic_json(record, audit)
        _atomic_bytes(destination, after)
        audit['status'] = 'complete'
        atomic_json(record, audit)
    return record


def verified_migration_history(before, destination, *, preserve_ids=()):
    """Prove a caption-only rewrite of a frozen legacy input (p169 guard)."""
    destination, before = Path(destination), Path(before)
    old, current = before.read_bytes(), destination.read_bytes()
    old_parts, new_parts = block_slices(old), block_slices(current)
    for identifier in preserve_ids:
        if identifier not in old_parts or identifier not in new_parts:
            return False
        a,b = old_parts[identifier]
        c,d = new_parts[identifier]
        if old[a:b] != current[c:d]:
            return False
    for record in destination.parent.glob('history/resegment-'+destination.stem+'-*/resegmentation.json'):
        audit = json.loads(record.read_text())
        if (audit.get('status') != 'complete' or audit.get('previous_sha256') != sha256(before)
                or audit.get('output_sha256') != sha256(destination)):
            continue
        ids = {i for r in audit['replacements'] for i in r['old_ids']}
        old_parts, new_parts = block_slices(old), block_slices(current)
        return all(i in new_parts and old[a:b] == current[new_parts[i][0]:new_parts[i][1]]
                   for i,(a,b) in old_parts.items() if i not in ids)
    return False
