"""Verified historical inputs for tests of the pre-resegmentation pipeline.

Live saved translations are mutable workflow artifacts. Historical regression
cases must exercise the archived input, without rolling back the working book.
"""
import hashlib
import json
from pathlib import Path

PILOT = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology/work/pilot'


def pre_resegment(number):
    name = f'p{number:04d}-translation.json'
    for record in sorted((PILOT/'history').glob(f'resegment-p{number:04d}-translation-*/resegmentation.json')):
        audit = json.loads(record.read_text())
        raw = (record.parent/name).read_bytes()
        assert audit['status'] == 'complete'
        assert hashlib.sha256(raw).hexdigest() == audit['previous_sha256']
        if not any('_join_' in u['id'] for u in json.loads(raw)['blocks']):
            return raw
    raw = (PILOT/name).read_bytes()
    assert not any('_join_' in u['id'] for u in json.loads(raw)['blocks']), 'Missing verified pre-migration fixture'
    return raw
