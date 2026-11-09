import json

import pymupdf as fitz
from PIL import Image, ImageChops

from astro_translate.candidate_storage import extract_page, migrate_candidates
from astro_translate.core import atomic_json, sha256


def _book(path):
    path.mkdir()
    work=path/'work'/'production';work.mkdir(parents=True)
    doc=fitz.open()
    for n in range(3):
        page=doc.new_page(width=300,height=200)
        page.insert_text((30,50),f'Page {n+1}')
    doc.save(path/'source.pdf');doc.close()
    return work


def test_extract_page_is_one_page_and_pixel_identical(tmp_path):
    source=tmp_path/'source.pdf';doc=fitz.open()
    for n in range(3):
        p=doc.new_page(width=300,height=200);p.insert_text((30,50),f'Page {n+1}')
    doc.save(source);doc.close()
    dest=tmp_path/'candidate.pdf';extract_page(source,2,dest)
    with fitz.open(source) as a,fitz.open(dest) as b:
        assert len(b)==1
        x=a[1].get_pixmap(dpi=110,alpha=False);y=b[0].get_pixmap(dpi=110,alpha=False)
        assert (x.width,x.height,x.samples)==(y.width,y.height,y.samples)


def test_migration_is_idempotent_and_records_old_new_hashes(tmp_path):
    book=tmp_path/'book';work=_book(book);candidate=work/'p0002-translated.pdf'
    with fitz.open(book/'source.pdf') as d:d.save(candidate)
    old_sha=sha256(candidate)
    atomic_json(work/'p0002-fit.json',{'fit_complete':True,'output':str(candidate),'output_sha256':old_sha})
    atomic_json(work/'p0002-qa.json',{'candidate_sha256':old_sha,'local_fidelity_ok':True})
    atomic_json(work/'visual_reviews.json',{'2':{old_sha:{'candidate_sha256':old_sha,'source_sha256':'source'}}})
    atomic_json(work/'p0002-state.json',{'dependency':'old','artifacts':{candidate.name:old_sha}})
    report=migrate_candidates(book)
    assert report['converted']==1
    with fitz.open(candidate) as compacted:assert len(compacted)==1
    mappings=[json.loads(line) for line in (book/'work/production/candidate_hash_migrations.jsonl').read_text().splitlines()]
    assert mappings[0]['old_sha256'] and mappings[0]['new_sha256']
    new_sha=mappings[0]['new_sha256']
    assert json.loads((work/'p0002-fit.json').read_text())['output_sha256']==new_sha
    assert json.loads((work/'p0002-qa.json').read_text())['candidate_sha256']==new_sha
    assert new_sha in json.loads((work/'visual_reviews.json').read_text())['2']
    assert json.loads((work/'p0002-state.json').read_text())['dependency']=='old'
    assert migrate_candidates(book)['converted']==0
    assert len((book/'work/production/candidate_hash_migrations.jsonl').read_text().splitlines())==1



def test_pixel_mismatch_blocks_by_default_and_skip_leaves_candidate_untouched(tmp_path,monkeypatch):
    # Real case 2026-11-08: p0089 (rotated ribbons) differs by anti-aliasing after extraction.
    import pytest
    from astro_translate import candidate_storage
    from astro_translate.core import Blocked
    book=tmp_path/'book';work=_book(book);candidate=work/'p0002-translated.pdf'
    with fitz.open(book/'source.pdf') as d:d.save(candidate)
    old_sha=sha256(candidate);real=candidate_storage._pixels
    monkeypatch.setattr(candidate_storage,'_pixels',lambda path,index,dpi=220:real(path,index,dpi) if str(path)==str(candidate) else (0,0,b''))
    with pytest.raises(Blocked):migrate_candidates(book)
    report=migrate_candidates(book,skip_mismatch=True)
    assert report['converted']==0 and report['skipped'][0]['page']==2
    assert sha256(candidate)==old_sha and not list(work.glob('.migration-*'))
