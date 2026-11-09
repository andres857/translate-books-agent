"""Offline D1–D3 evidence only; never rebuild, translate or mark pilot QA done."""
from __future__ import annotations
import argparse
from collections import defaultdict
import copy
import json
from pathlib import Path
import sys

import pymupdf as fitz
from PIL import Image
from astro_translate import fidelity, fonts
from astro_translate.core import sha256, atomic_json
from astro_translate.text_quality import page_issues, repair_composition, source_emphasis

ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/'books/the-secrets-of-astrology'
PILOT=BOOK/'work/pilot'
sys.path.insert(0,str(PILOT))
from local_layout import book_composition, composition_context, precise_redaction_rects


def compose_proof(number, folder):
    """New isolated single-page diagnostic; all words from saved translations."""
    with fitz.open(BOOK/'source.pdf') as source, composition_context():
        page=source[number-1]
        saved=json.loads((PILOT/f'p{number:04d}-translation.json').read_text())
        canonical={u['id']:u for u in fidelity.extract_fidelity_units(page)}
        units=[]
        for supplied in saved['blocks']:
            own=canonical[supplied['id'].removeprefix(f'p{number:04d}_')]
            assert own['source']==supplied['source'] and own['spans']==supplied['spans']
            units.append({**own,'target':supplied['target'],'target_runs':supplied.get('target_runs',
                          [dict(text=supplied['target'],style=own['style'])])})
        units=fidelity.finalize_editorial_composition(page,book_composition(page,units))
        # p128 proof owns the defective introduction; other page content stays
        # original. p5 owns the complete contents table.
        if number==128:
            units=[u for u in units if u['source'].startswith('POWER AND')]
        units=[repair_composition(u,emphasis=source_emphasis(page,u)) for u in units]
        font_root=folder/'fonts'
        mapping,audit=fonts.select_group_fonts(source,page,units,font_root)
        mask=fidelity._vector_mask(page)
        guards=fidelity.immutable_boxes(page,())
        groups=defaultdict(list)
        for u in units:groups[u['scale_group']].append(u)
        candidates={u['id']:fidelity._candidate_boxes(page,u,units,guards) for u in units}
        selections={}
        for group,members in groups.items():
            for percent in range(100,74,-1):
                attempt={};occupied=[fitz.Rect(info['box']) for info in selections.values()]
                for u in members:
                    for box in candidates[u['id']]:
                        if any(box.intersects(r) for r in occupied):continue
                        ok,info=fidelity._test_fit(page,u,box,percent/100,mapping,font_root,mask)
                        if ok:
                            attempt[u['id']]=info;occupied.append(box);break
                    if u['id'] not in attempt:break
                if len(attempt)==len(members):selections.update(attempt);break
            else:raise RuntimeError(f'No safe fit: {number} {group}')
        candidate=fitz.open();candidate.insert_pdf(source,from_page=number-1,to_page=number-1)
        out=candidate[0]
        for u in units:
            for rect in precise_redaction_rects(page,u):out.add_redact_annot(rect,fill=False)
        out.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        reports=[]
        for u in units:
            info=selections[u['id']]
            content,css,font_runs=fidelity._html_for(u,mapping,font_root,info['scale'])
            spare,_=out.insert_htmlbox(fitz.Rect(info['box']),content,css=css,archive=fitz.Archive(str(font_root)),scale_low=1)
            assert spare>=0
            reports.append({**u,'edit_bbox':info['box'],'scale':info['scale'],'font_runs':font_runs})
        # Inserted-page transparency dictionaries can change colored backgrounds.
        # Diagnostics below are cropped to text and not a replacement pilot PDF.
        candidate.save(folder/f'p{number:04d}-proof.pdf')
        clip=fitz.Rect(25,150,530,330) if number==5 else fitz.Rect(30,120,335,255)
        pix=out.get_pixmap(dpi=300,clip=clip);pix.save(folder/f'p{number:04d}-after-300dpi.png')
        with fitz.open(PILOT/f'p{number:04d}-translated.pdf') as old:
            old_page=old[number-1] if len(old)>1 else old[0]
            before=old_page.get_pixmap(dpi=300,clip=clip)
            a=Image.frombytes('RGB',(before.width,before.height),before.samples)
        b=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        pair=Image.new('RGB',(a.width+b.width,max(a.height,b.height)),'white');pair.paste(a,(0,0));pair.paste(b,(a.width,0));pair.save(folder/f'p{number:04d}-before-after-300dpi.png')
        immutable=not (fidelity._immutable_characters(page,())-fidelity._immutable_characters(out,(),False))
        # Dates are editable prose with locally protected values; standalone
        # folios are immutable operators and must retain pixel-identical ink.
        pixels_equal=all(page.get_pixmap(dpi=300,clip=g).samples==out.get_pixmap(dpi=300,clip=g).samples for g in guards)
        atomic_json(folder/f'p{number:04d}-proof.json',dict(source_sha256=sha256(BOOK/'source.pdf'),
                    diagnostic_only=True,immutable_characters_equal=immutable,protected_crops_equal_300dpi=pixels_equal,
                    blocks=reports,font_audit=audit))
        assert immutable and pixels_equal
        candidate.close()
        return reports


def audit_headers(folder):
    selection=json.loads((PILOT/'selection.json').read_text())['selected_pages']
    rows=[]
    with fitz.open(BOOK/'source.pdf') as source:
        for n in selection:
            path=PILOT/f'p{n:04d}-translation.json'
            saved=json.loads(path.read_text())['blocks'] if path.exists() else []
            headers=[u for u in saved if u['bbox'][3]<source[n-1].rect.height*.07]
            fit_path=PILOT/f'p{n:04d}-fit.json';pdf=PILOT/f'p{n:04d}-translated.pdf'
            issues=page_issues(headers)
            texts=[]
            if fit_path.exists() and pdf.exists():
                units=[u for u in json.loads(fit_path.read_text()).get('blocks',[]) if u['bbox'][3]<source[n-1].rect.height*.07]
                with fitz.open(pdf) as candidate:
                    page=candidate[n-1] if len(candidate)>1 else candidate[0]
                    issues+=page_issues(units,source_page=source[n-1],candidate_page=page)
                    for u in units:
                        rect=fitz.Rect(u.get('edit_bbox',u['bbox']))+(-1,-3,1,3)
                        texts.append(dict(id=u['id'],text=page.get_textbox(rect)))
            rows.append(dict(page=n,saved_headers=[dict(id=u['id'],target=u['target']) for u in headers],candidate_headers=texts,issues=issues))
    atomic_json(folder/'headers-20.json',dict(pages=rows,scope='All native running headers; no OCR',
                                           retranslate_units=sorted({i['unit_id'] for r in rows for i in r['issues'] if i['action']=='retranslate'})))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=PILOT/'d123-evidence');args=p.parse_args()
    folder=args.output.resolve()
    if not folder.is_relative_to((BOOK/'work').resolve()):p.error('Diagnostic output must be in book/work')
    folder.mkdir(parents=True,exist_ok=True)
    audit_headers(folder)
    for number in (5,128):
        reports=compose_proof(number,folder)
        print(number,{u['scale_group']:u['scale'] for u in reports})


if __name__=='__main__':main()
