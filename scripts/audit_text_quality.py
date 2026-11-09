"""Read-only pilot I6 audit; only the requested work report is written.

No imports of the runners, no LLM, no manifest/QA/translation/cache mutations.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sqlite3
import tomllib

import pymupdf as fitz

from astro_translate.core import atomic_json, book_lock, sha256
from astro_translate.text_quality import page_issues


def audit(book):
    book = Path(book).resolve()
    folder = book/'work/pilot'
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((book/'work/pipeline.toml').read_text())
    resolve = lambda s: Path(s) if Path(s).is_absolute() else root/s
    glossary_path = resolve(cfg['paths']['glossary'])
    style_path = resolve(cfg['paths']['style_guide'])
    with glossary_path.open() as stream:
        glossary = list(csv.DictReader(stream))
    style = style_path.read_text()
    selection = json.loads((folder/'selection.json').read_text())
    pages = selection.get('pages', selection.get('selected_pages'))
    rows = []
    inputs = {str(p.relative_to(book)) if p.is_relative_to(book) else str(p): sha256(p)
              for p in [book/'source.pdf', glossary_path, style_path, folder/'selection.json']}
    with fitz.open(book/'source.pdf') as original:
        for n in pages:
            translation = folder/f'p{n:04d}-translation.json'
            units = json.loads(translation.read_text())['blocks'] if translation.exists() else []
            saved = page_issues(units, source_page=original[n-1], glossary=glossary, style_guide=style)
            fit = folder/f'p{n:04d}-fit.json'
            candidate = folder/f'p{n:04d}-translated.pdf'
            composed = []
            coverage = 'saved_units_only; no composed candidate'
            if fit.exists() and candidate.exists():
                data = json.loads(fit.read_text())
                with fitz.open(candidate) as document:
                    page = document[n-1] if len(document) > 1 else document[0]
                    composed = page_issues(data['blocks'], source_page=original[n-1],
                                          candidate_page=page, glossary=glossary, style_guide=style)
                coverage = 'saved_units + candidate_runs + PDF protected-glyph gaps + source text matrices'
            retr = sorted({i['unit_id'] for i in saved+composed if i['action'] == 'retranslate'})
            local = sorted({i['unit_id'] for i in saved+composed if i['action'] == 'local'})
            for path in (translation, fit, candidate):
                if path.exists():
                    inputs[str(path.relative_to(book))] = sha256(path)
            command = (f'.venv/bin/python books/{book.name}/work/pilot/native_runner.py '
                       f'--stage retranslate --pages {n} --units '+','.join(retr)) if retr else None
            rows.append(dict(page=n, coverage=coverage, editable_units=len(units),
                             saved_issues=saved, candidate_issues=composed,
                             retranslate_units=retr, local_units=local,
                             retranslate_command=command, recompose=bool(retr or local)))
    provenance = []
    cache = book/'work/llm_cache.sqlite'
    if cache.exists():
        # Evidence only: SQLite URI read-only, no SentenceMemory construction.
        with sqlite3.connect(cache.as_uri()+'?mode=ro', uri=True) as db:
            for table in ('blocks', 'responses'):
                for key, raw in db.execute(f'SELECT key,result FROM {table}'):
                    data = json.loads(raw)
                    entries = [data] if table == 'blocks' else data.get('data', {}).get('blocks', [])
                    for entry in entries:
                        if entry.get('translation') in ('es la asterismo de la Tetera', 'de la Tetera'):
                            provenance.append(dict(table=table, cache_key=key, id=entry.get('id'),
                                                   translation=entry['translation']))
    return dict(incident='I6', scope='20-page pilot; deterministic local checks only',
                input_sha256=inputs, pages=rows,
                recompose_pages=[r['page'] for r in rows if r['recompose']],
                p69_model_cache_evidence=provenance,
                limitations=['No OCR/raster text coverage; pages without composed candidates remain pending.',
                             'Translated inline emphasis uses ordered run signatures; semantic alignment still needs human review.',
                             'A source repetition of the same n-gram length is conservatively exempted.',
                             'No candidate was recomposed and no issue was approved or marked resolved.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = audit(args.book)
    if args.report:
        if not args.report.resolve().is_relative_to((args.book/'work').resolve()):
            raise ValueError('Report must be inside book/work')
        with book_lock(args.book):
            atomic_json(args.report, report)
    print(json.dumps([dict(page=r['page'], retranslate=r['retranslate_units'], local=r['local_units'])
                      for r in report['pages']], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
