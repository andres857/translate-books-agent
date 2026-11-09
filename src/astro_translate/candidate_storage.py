"""Compact, page-local storage for production candidates."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

import pymupdf as fitz

from .core import Blocked, atomic_json, save_deterministic, sha256


def _save_compact(doc, path):
    # Deterministic: content-derived /ID, no dates (candidate hashes bind reviews).
    save_deterministic(doc,path,garbage=4,deflate=True,clean=True)


def extract_page(source, page_number, destination, *, source_index=None):
    """Write one source page (1-based) with embedded page resources retained."""
    source=Path(source);destination=Path(destination)
    if source_index is None:source_index=page_number-1
    with fitz.open(source) as full, fitz.open() as one:
        if not 0 <= source_index < len(full):raise Blocked('Candidate source page index out of bounds')
        one.insert_pdf(full,from_page=source_index,to_page=source_index,links=True,annots=True)
        _copy_page_group(full,source_index,one,0)
        _save_compact(one,destination)
    return destination


def _copy_page_group(source,index,target,target_index):
    """insert_pdf omits the page /Group: graft the transparency group too.

    Without it the blending colour space changes and every flat colour of a
    page with an ICC transparency group renders a level off (p90: 46 % of the
    non-text pixels), which QA rightly reports as ink outside the edit boxes.
    """
    kind,_=source.xref_get_key(source[index].xref,'Group')
    if kind=='null':return
    m=fitz.mupdf
    group=m.pdf_dict_get(m.pdf_load_object(fitz._as_pdf_document(source),source[index].xref),m.pdf_new_name('Group'))
    destination=fitz._as_pdf_document(target)
    m.pdf_dict_put(m.pdf_load_object(destination,target[target_index].xref),m.pdf_new_name('Group'),
                   m.pdf_graft_object(destination,group))


def extract_candidate(fit):
    """Store a fitted candidate as its single page and re-bind its own proofs.

    Rotated/vertical proofs are bound to the candidate hash; extraction keeps
    the page content but changes the file hash. Only proofs bound to this very
    candidate (pre-extraction hash) are moved to the new hash.
    """
    old=sha256(fit['output'])
    number=fit['page']
    extract_page(fit['output'],number,fit['output'],source_index=fit.get('output_page',number-1))
    new=sha256(fit['output'])
    for proof in fit.get('verified_rotated_spans',[]):
        if proof.get('candidate_sha256')==old:proof['candidate_sha256']=new
    fit.update(output_page=0,output_sha256=new)
    return fit


def _pixels(path,index,dpi=220):
    with fitz.open(path) as doc:
        page=doc[index];pix=page.get_pixmap(dpi=dpi,alpha=False)
        return pix.width,pix.height,pix.samples


def migrate_candidates(book, *, skip_mismatch=False):
    """Idempotently compact full-book production PDFs and carry hash bindings."""
    book=Path(book);folder=book/'work/production';source=book/'source.pdf'
    if not source.is_file():raise FileNotFoundError(source)
    dependency=None
    if (book/'work/pipeline.toml').exists():
        from .production import Production
        dependency=Production(book).dependencies()
    with fitz.open(source) as source_doc:count=len(source_doc)
    converted=[];skipped=[];mapping=folder/'candidate_hash_migrations.jsonl'
    folder.mkdir(parents=True,exist_ok=True)
    for path in sorted(folder.glob('p[0-9][0-9][0-9][0-9]-translated.pdf')):
        n=int(path.name[1:5]);
        with fitz.open(path) as candidate:
            pages=len(candidate)
        if pages==1:continue
        if pages!=count:raise Blocked(f'{path.name}: expected one or {count} pages, found {pages}')
        old=sha256(path);before=_pixels(path,n-1)
        fd,tmp=tempfile.mkstemp(dir=folder,prefix='.migration-',suffix='.pdf');os.close(fd);os.unlink(tmp)
        try:
            extract_page(path,n,tmp,source_index=n-1)
            if _pixels(tmp,0)!=before:
                if not skip_mismatch:raise Blocked(f'Pixel render changed during migration: {path.name}')
                # Keep the legacy full-book candidate untouched; it is regenerated on recomposition.
                skipped.append({'page':n,'candidate':path.name,'reason':'pixel_render_changed'});continue
            new=sha256(tmp);os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        _migrate_bindings(folder,n,old,new,path,dependency)
        row={'page':n,'candidate':path.name,'old_sha256':old,'new_sha256':new,
             'render_dpi':220,'pixel_identical':True,'at':datetime.now(timezone.utc).isoformat()}
        with mapping.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
        converted.append(row)
    return {'converted':len(converted),'pages':[r['page'] for r in converted],'skipped':skipped,'mapping':str(mapping)}


def _migrate_bindings(folder,n,old,new,path,dependency=None):
    stem=f'p{n:04d}-'
    fit_path=folder/(stem+'fit.json');qa_path=folder/(stem+'qa.json')
    fit=json.loads(fit_path.read_text()) if fit_path.exists() else {}
    if fit:
        fit.update(output=str(path.resolve()),output_sha256=new,output_page=0)
        atomic_json(fit_path,fit)
    qa=json.loads(qa_path.read_text()) if qa_path.exists() else {}
    if qa.get('candidate_sha256')==old:qa['candidate_sha256']=new;atomic_json(qa_path,qa)
    reviews=folder/'visual_reviews.json'
    if reviews.exists():
        data=json.loads(reviews.read_text());page=data.get(str(n),{})
        if old in page:
            record=page.pop(old);record['candidate_sha256']=new;page[new]=record;atomic_json(reviews,data)
    state_path=folder/(stem+'state.json')
    if state_path.exists():
        state=json.loads(state_path.read_text())
        if dependency:state['dependency']=dependency
        state['artifacts']={p.name:sha256(p) for p in folder.glob(stem+'*') if p.is_file() and p!=state_path}
        atomic_json(state_path,state)

