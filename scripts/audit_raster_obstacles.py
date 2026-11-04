#!/usr/bin/env python3
"""Conservative local candidate audit; actual fit trials remain authoritative."""
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf as fitz

from astro_translate.raster_obstacles import check_overlay, native_ink_alpha


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--book', type=Path, required=True)
    args = parser.parse_args()
    book = args.book.resolve()
    source_path = book / 'source.pdf'
    results = []
    with fitz.open(source_path) as source:
        for report_path in sorted((book / 'work/pilot').glob('p*-fit.json')):
            report = json.loads(report_path.read_text())
            page_number = int(report['page'])
            candidate_path = book / 'work/pilot' / f'p{page_number:04d}-translated.pdf'
            if page_number == 1 or not candidate_path.exists():
                continue
            with fitz.open(candidate_path) as candidate:
                before = source[page_number - 1]
                after = candidate[page_number - 1 if len(candidate) > 1 else 0]
                ink = native_ink_alpha(after)
                rows = []
                for unit in report.get('blocks', []):
                    if not unit.get('fit'):
                        continue
                    box = unit.get('edit_bbox', unit['bbox'])
                    result = check_overlay(before, None, box, ink_alpha=ink)
                    rows.append({'id': unit['id'], 'bbox': box, **result})
                results.append({'page': page_number, 'candidate': str(candidate_path),
                                'candidate_sha256': sha256(candidate_path), 'blocks': rows})
    output = book / 'work/pilot/raster_guard_regression/all_native_audit.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {'source_sha256': sha256(source_path),
            'scope': 'Local full native ink; includes preserved native text. Actual new-ink fit trial is authoritative; no automatic overlap exemption.',
            'pages': results}
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(output)
    print(json.dumps({'output': str(output), 'pages': len(results), 'regions_flagged': sum(
        not row['ok'] for page in results for row in page['blocks'])}))


if __name__ == '__main__':
    main()
