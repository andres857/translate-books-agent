"""Offline inventory of caption joins and the I6 follow-up evidence.

Only writes the specified work report; never calls a provider or a runner.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import tomllib

import pymupdf as fitz
from astro_translate.core import atomic_json, book_lock, read_extras, sha256
from astro_translate.fidelity import extract_fidelity_units
from astro_translate.resegmentation import plan_resegmentation
from astro_translate.text_quality import page_issues, quoted_foreign_terms, glyph_boundaries, candidate_text_matches


def audit(book):
    book = Path(book).resolve()
    folder = book/'work/pilot'
    cfg = tomllib.loads((book/'work/pipeline.toml').read_text())
    root = Path(__file__).resolve().parents[1]
    extras_path = Path(cfg['paths']['do_not_translate'])
    if not extras_path.is_absolute():
        extras_path = root/extras_path
    extras = read_extras(extras_path)
    selection = json.loads((folder/'selection.json').read_text())['selected_pages']
    report = dict(scope='I6 follow-up; read-only native pilot inventory', pages=[],
                  input_sha256={str(book/'source.pdf'):sha256(book/'source.pdf')},
                  llm_calls=0, composition_executed=False, qa_approved=False)
    with fitz.open(book/'source.pdf') as source:
        for n in selection:
            units = [{**u,'id':f'p{n:04d}_'+u['id'],'page':n}
                     for u in extract_fidelity_units(source[n-1],extras,merge_captions=True)]
            path = folder/f'p{n:04d}-translation.json'
            row = dict(page=n, replacements=[], native_units=len(units))
            if units:
                report['input_sha256'][str(path)] = sha256(path)
                saved = json.loads(path.read_text())
                plan = plan_resegmentation(saved,units)
                row['replacements'] = plan['replacements']
                row['joined_geometry'] = [{k:u[k] for k in ('id','bbox','alignment','line_height','merged_from')}
                                          for u in plan['joined_units']]
                row['preserved_units'] = len(saved['blocks'])-sum(len(r['old_ids']) for r in plan['replacements'])
            else:
                row['coverage'] = 'no native caption units; raster/preserved source outside this migration'
            if n in (34,60,128,179):
                fit_path = folder/f'p{n:04d}-fit.json'
                candidate = folder/f'p{n:04d}-translated.pdf'
                data = json.loads(fit_path.read_text())
                report['input_sha256'].update({str(p):sha256(p) for p in (fit_path,candidate)})
                with fitz.open(candidate) as target:
                    page = target[n-1] if len(target)>1 else target[0]
                    row['candidate_text_quality_issues'] = page_issues(data['blocks'],source_page=source[n-1],candidate_page=page)
                    row['language_exceptions'] = quoted_foreign_terms(source[n-1],page,data['blocks'])
                    row['glyph_gaps'] = []
                    for boundary in glyph_boundaries(source[n-1],data['blocks']):
                        owner = next(u for u in data['blocks'] if u['id']==boundary['unit_id'])
                        matches = candidate_text_matches(page,owner,owner['target'].strip())
                        if len(matches)==1:
                            row['glyph_gaps'].append(dict(unit_id=owner['id'],
                                actual_gap=boundary['glyph_bbox'][0]-matches[0]['ink_bbox'][2],
                                minimum_gap=boundary['minimum_gap'],label_ink_bbox=matches[0]['ink_bbox'],
                                glyph_bbox=boundary['glyph_bbox']))
            report['pages'].append(row)
    changed = [r['page'] for r in report['pages'] if r['replacements']]
    report['resegment_pages'] = changed
    report['joined_units'] = sum(len(r['replacements']) for r in report['pages'])
    report['replaced_fragments'] = sum(len(u['old_ids']) for r in report['pages'] for u in r['replacements'])
    prefix = '.venv/bin/python '+str(folder.relative_to(root))+'/'
    report['commands_not_executed'] = [
        prefix+'native_runner.py --stage resegment --pages '+','.join(map(str,changed)),
        prefix+'rebuild_pilot.py --stage compose --pages '+','.join(map(str,sorted(set(changed)|{128,179}))),
    ]
    report['limits'] = ['No resegmentation, retranslation or rebuild was executed.',
                        'Existing p169 fit failure remains subject to its unchanged I7 recipe.',
                        'Current QA JSONs are not overwritten; future fidelity_qa records language_exceptions.',
                        'No OCR coverage or new visual approval.']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    args = parser.parse_args()
    if not args.report.resolve().is_relative_to((args.book/'work').resolve()):
        parser.error('Report must reside under book/work')
    report = audit(args.book)
    with book_lock(args.book):
        atomic_json(args.report,report)
    print(json.dumps({k:report[k] for k in ('resegment_pages','joined_units','replaced_fragments','llm_calls')}))


if __name__ == '__main__':
    main()
