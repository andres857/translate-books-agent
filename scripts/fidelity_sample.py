#!/usr/bin/env python3
"""Rebuild the authorized Fase 1d three-page sample from cached translations.

No inference or network calls. The original source, main glossary and approved
style guide remain unchanged. This script cannot start a G4 pilot or production.
"""
from __future__ import annotations
import argparse
import json
import tomllib
from pathlib import Path
import pymupdf as fitz
from astro_translate.adapters import require_backend_approval
from astro_translate.core import Blocked,sha256,atomic_json,_save_pdf,book_lock
from astro_translate.fidelity import reinsert_fidelity,fidelity_qa
from astro_translate.consistency import check_rendered_consistency

PAGES=(7,15,121)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book',type=Path,default=Path('books/astrology-wisdom-stars'))
    parser.add_argument('--config',type=Path,default=Path('config/pipeline.toml'))
    args=parser.parse_args();book=args.book;config=tomllib.loads(args.config.read_text());paths=config['paths'];folder=book/'work/fidelity'
    require_backend_approval(book,args.config,'fidelity')
    before={'source':sha256(book/'source.pdf'),'glossary':sha256(paths['glossary']),'style':sha256(paths['style_guide'])}
    sample=fitz.open();comparison=fitz.open();fits=[];qa=[];texts={}
    try:
        with fitz.open(book/'source.pdf') as original:
            for number in PAGES:
                payload_path=folder/f'p{number:04d}-translation.json';payload=json.loads(payload_path.read_text())
                if payload['source_sha256']!=before['source']:raise Blocked('Cached translations refer to a different source')
                result=reinsert_fidelity(book,payload,paths['glossary'],paths['style_guide'],paths['do_not_translate'],config)
                if not result['fit_complete']:raise Blocked(f'Fidelity fit failed on page {number}: see saved diagnostics')
                report=fidelity_qa(book,result,paths['glossary'],paths['do_not_translate']);qa.append(report)
                fits.append({'page':number,'failed_blocks':0,'min_scale':report['min_scale'],'group_scales':result['group_scales'],'source_units':len(result['blocks']),'payload_sha256':sha256(payload_path),'fit_report':str(folder/f'p{number:04d}-fit.json')})
                with fitz.open(result['output']) as candidate:
                    page=candidate[number-1];texts[number]=page.get_text();sample.insert_pdf(candidate,from_page=number-1,to_page=number-1)
                    width=page.rect.width;height=page.rect.height;paired=comparison.new_page(width=width*2,height=height+24)
                    paired.insert_text((12,16),f'Original - pagina {number}',fontsize=10)
                    paired.insert_text((width+12,16),'Espanol - fidelidad 1d',fontsize=10)
                    paired.show_pdf_page(fitz.Rect(0,24,width,height+24),original,number-1)
                    paired.show_pdf_page(fitz.Rect(width,24,width*2,height+24),candidate,number-1)
                    if number==7:
                        title=next(b for b in result['blocks'] if b['source']=='THE ZODIAC SIGNS')
                        rect=fitz.Rect(title['edit_bbox'])+(-4,-4,4,4)
                        page.get_pixmap(dpi=220,clip=rect).save(folder/'renders/p0007-zodiaco-detail.png')
        memory=json.loads((folder/'consistency.json').read_text())
        consistency=check_rendered_consistency(memory['occurrences'],texts)
        if before!={'source':sha256(book/'source.pdf'),'glossary':sha256(paths['glossary']),'style':sha256(paths['style_guide'])}:raise Blocked('Immutable source/editorial input hashes changed')
        out=book/'out/fidelidad_1d.pdf';paired_out=book/'out/fidelidad_1d_comparativa.pdf'
        _save_pdf(sample,out);_save_pdf(comparison,paired_out)
        report={'source_sha256':before['source'],'glossary_sha256':before['glossary'],'style_sha256':before['style'],'sample_sha256':sha256(out),'comparison_sha256':sha256(paired_out),'page_mapping':list(PAGES),'fits':fits,'pages':qa,'rendered_consistency_issues':consistency,'new_model_calls':0,'scope':'fidelity_sample_only_not_G4','local_fidelity_ok':all(x['local_fidelity_ok'] for x in qa) and not consistency,'art_exception':'The decorative raster word PLANETS on page 7 is intentionally preserved with explicit human approval.'}
        atomic_json(folder/'qa_1d.json',report)
        with book_lock(book):
            qp=book/'work/review_queue.json';queue=json.loads(qp.read_text()) if qp.exists() else [];queue=[x for x in queue if x['page'] not in PAGES]
            for item in qa:queue.append({'page':item['page'],'reason':'human_visual_review_fidelity' if item['local_fidelity_ok'] else 'technical_fidelity_checks_failed','artifacts':[str(folder/f'p{item["page"]:04d}-qa.json'),str(folder/'renders'/f'p{item["page"]:04d}-comparison.png'),str(out)],'suggested_action':'Review final typography and comparison; no G4 approval is implied.'})
            atomic_json(qp,queue)
        print(json.dumps({'output':str(out),'comparison':str(paired_out),'local_fidelity_ok':report['local_fidelity_ok'],'fits':fits,'consistency_issues':consistency},ensure_ascii=False,indent=2))
    finally:sample.close();comparison.close()

if __name__=='__main__':main()
