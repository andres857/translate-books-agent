#!/usr/bin/env python3
"""Re-bind ``units_sha256`` (and its ``policy_sha256``) of profile page overrides.

Reproducible, offline, no LLM. For every requested page with ``units_sha256``:
1. extract the units of ``source.pdf`` with the current extractor;
2. if ``work/production/pNNNN-translation.json`` exists, require that its units have
   exactly the same ids, sources and spans (only order may differ); otherwise refuse:
   a content change means the reviewed override is stale and needs review;
3. print the canonical ``units_digest`` and the recomputed ``policy_sha256``;
4. with ``--write``, replace both values inside that page's TOML table only.

Usage: .venv/bin/python scripts/rebind_profile_units.py --book books/<slug> --pages 34,60 [--write]
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from astro_translate.core import read_extras  # noqa: E402
from astro_translate.fidelity import extract_fidelity_units  # noqa: E402
from astro_translate.production import Production  # noqa: E402
from astro_translate.profiles import units_digest  # noqa: E402


def content(units):
    return {u['id'].split('_', 1)[1] if re.match(r'p\d{4}_', u['id']) else u['id']:
            json.dumps({'source': u['source'], 'spans': u['spans']}, sort_keys=True, ensure_ascii=False)
            for u in units}


def rebind(book, pages, write=False):
    book = Path(book).resolve()
    path = book/'work/profile.toml'
    text = path.read_text()
    data = tomllib.loads(text)
    extras = read_extras(Production(book).protected)
    results = []
    with fitz.open(book/'source.pdf') as doc:
        for number in pages:
            policy = dict(data['pages'][str(number)])
            if 'units_sha256' not in policy:
                raise SystemExit(f'p{number}: no units_sha256 to re-bind')
            units = extract_fidelity_units(doc[number-1], extras)
            saved = book/f'work/production/p{number:04d}-translation.json'
            if saved.is_file() and content(json.loads(saved.read_text())['blocks']) != content(units):
                raise SystemExit(f'p{number}: unit content changed against the saved translation; review required')
            policy['units_sha256'] = units_digest(units)
            body = {k: v for k, v in policy.items() if k != 'policy_sha256'}
            policy['policy_sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            results.append({'page': number, 'units_sha256': policy['units_sha256'], 'policy_sha256': policy['policy_sha256']})
            if write:
                header = f'[pages."{number}"]'
                start = text.index(header)
                end = text.find('\n[', start+len(header))
                end = len(text) if end < 0 else end
                section = text[start:end]
                for key in ('units_sha256', 'policy_sha256'):
                    section, count = re.subn(rf'(?m)^{key} = "[0-9a-f]{{64}}"$', f'{key} = "{policy[key]}"', section)
                    if count != 1:
                        raise SystemExit(f'p{number}: {key} not found exactly once')
                text = text[:start]+section+text[end:]
    if write:
        tmp = path.with_suffix('.toml.tmp')
        tmp.write_text(text)
        tmp.replace(path)
        check = tomllib.loads(path.read_text())
        for r in results:
            assert check['pages'][str(r['page'])]['units_sha256'] == r['units_sha256']
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--book', type=Path, required=True)
    parser.add_argument('--pages', required=True)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    pages = [int(x) for x in args.pages.split(',') if x.strip()]
    print(json.dumps(rebind(args.book, pages, args.write), indent=2))


if __name__ == '__main__':
    main()
