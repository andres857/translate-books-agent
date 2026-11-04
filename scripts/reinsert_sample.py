#!/usr/bin/env python3
"""Reproduce the authorized three-page reinsertion sample without model calls.

Consumes saved translation artifacts; grouping/formatting changes whitespace
only. Run from the project root with .venv/bin/python scripts/reinsert_sample.py.
This is the narrow Fase 1c sample, never a G4 pilot or production batch.
"""
from __future__ import annotations
import argparse
import json
import tomllib
from pathlib import Path
import pymupdf as fitz
from astro_translate.core import Blocked,atomic_json,read_extras,reinsert,sha256,visual_qa,_save_pdf
from astro_translate.layout import extract_layout_units
from astro_translate.adapters import require_backend_approval

PAGES=(7,15,121)

def regroup_cached(book,page,source_path,protected_path):
    """Match exact source-span provenance; do not infer translations or align text."""
    data=json.loads(source_path.read_text())
    with fitz.open(book/'source.pdf') as doc:
        units=extract_layout_units(doc[page-1],read_extras(protected_path))
    grouped=[];used=[]
    for unit in units:
        pieces=[b for b in data['blocks'] if b.get('spans') and all(span in unit['spans'] for span in b['spans'])]
        covered=[span for piece in pieces for span in piece['spans']]
        if len(covered)!=len(unit['spans']) or any(span not in covered for span in unit['spans']):
            raise Blocked(f'Cannot regroup cached page {page} by exact source spans')
        # Preserve source extraction order, never the provider's output ordering.
        pieces.sort(key=lambda piece:unit['spans'].index(piece['spans'][0]))
        used.extend(piece['id'] for piece in pieces)
        grouped.append({**unit,'target':' '.join(piece['target'].strip() for piece in pieces),
                        'translation_provenance':{'method':'join_cached_targets_by_source_spans','input':str(source_path),'input_sha256':sha256(source_path),'source_ids':[piece['id'] for piece in pieces]}})
    if len(used)!=len(data['blocks']) or len(set(used))!=len(used):
        raise Blocked(f'Cached page {page} contains unmatched or duplicated spans')
    return {**data,'blocks':grouped}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book',type=Path,default=Path('books/astrology-wisdom-stars'))
    parser.add_argument('--config',type=Path,default=Path('config/pipeline.toml'))
    args=parser.parse_args();book=args.book;config=tomllib.loads(args.config.read_text());paths=config['paths']
    require_backend_approval(book,args.config,'reinsertion')
    before={'source':sha256(book/'source.pdf'),'glossary':sha256(paths['glossary'])}
    forms={'path':Path(paths['short_glossary']),'approval':book/'work/shortforms_approval.json'}
    reports=[];fits=[];sample=fitz.open()
    try:
        for number in PAGES:
            cached=book/'work/reinsertion'/f'p{number:04d}-translation.json'
            payload=regroup_cached(book,number,cached,paths['do_not_translate'])
            atomic_json(book/'work/reinsertion'/f'p{number:04d}-grouped.json',payload)
            fit=reinsert(book,payload,paths['glossary'],paths['style_guide'],paths['do_not_translate'],font_path=paths['font_regular'],layout=config['layout'],shortforms=forms)
            fits.append({'page':number,'failed_blocks':sum(not block['fit'] for block in fit['blocks']),'min_scale':min([block['scale'] for block in fit['blocks']] or [1]),'output':fit.get('output'),'short_forms_used':[{'source':block['source'],'target':block['target']} for block in fit['blocks'] if block.get('shortform_used')]})
            if fits[-1]['failed_blocks'] or fits[-1]['min_scale']<.75:raise Blocked(f'Fit gate failed on page {number}')
            # Explicitly local: raster protections have not been audited, so the
            # full-page images must never be sent to a visual language model.
            qa=visual_qa(book,fit['output'],fit,paths['glossary'],do_not_translate=paths['do_not_translate'],selected_pages=[number])
            reports.extend(qa['pages'])
            with fitz.open(fit['output']) as candidate:sample.insert_pdf(candidate,from_page=number-1,to_page=number-1)
        if before!={'source':sha256(book/'source.pdf'),'glossary':sha256(paths['glossary'])}:raise Blocked('Immutable input hashes changed')
        path=book/'out/reinsercion_1c.pdf';_save_pdf(sample,path)
        report={'source_sha256':before['source'],'glossary_sha256':before['glossary'],'sample_sha256':sha256(path),'page_mapping':list(PAGES),'fits':fits,'pages':reports,'scope':'reinsertion_only_not_G4','new_model_calls':0,'vlm_reason':'Not executed: full-page raster artwork has no verified protected-content masks. Local QA and human review only.'}
        atomic_json(book/'work/reinsertion/qa_1c.json',report)
        print(json.dumps({'output':str(path),'fits':fits,'verdicts':[row['verdict'] for row in reports]},ensure_ascii=False,indent=2))
    finally:sample.close()

if __name__=='__main__':main()
