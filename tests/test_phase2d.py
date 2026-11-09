"""Offline real-source regressions. Saved answers are frozen, never generated."""
import json
from pathlib import Path

import pymupdf as fitz
import pytest
from astro_translate import fidelity
from astro_translate.layout import safe_redaction_rects
from astro_translate.profiles import load_profile
from astro_translate.production import Production

BOOK = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology'
FIXTURES = Path(__file__).parent / 'fixtures/phase2c'


def test_real_chapter_recomposition(tmp_path, monkeypatch):
    runner = Production(BOOK)
    monkeypatch.setattr(fidelity, '_artifact_folder', lambda *a, **k: tmp_path)
    payload = json.loads((FIXTURES/'p0011-translation.json').read_text())
    fit = fidelity.reinsert_fidelity(BOOK, payload, runner.glossary, runner.style,
        runner.protected, artifact_dir=tmp_path, phase='production',corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete'], fit.get('failures')
    qa = fidelity.fidelity_qa(BOOK, fit, runner.glossary, runner.protected,
                              artifact_dir=tmp_path, update_manifest=False)
    assert qa['local_fidelity_ok'], qa


def test_real_credits_copyright_exception(tmp_path, monkeypatch):
    runner = Production(BOOK)
    monkeypatch.setattr(fidelity, '_artifact_folder', lambda *a, **k: tmp_path)
    payload = json.loads((FIXTURES/'p0004-translation.json').read_text())
    fit = fidelity.reinsert_fidelity(BOOK, payload, runner.glossary, runner.style,
        runner.protected, artifact_dir=tmp_path, phase='production',corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete'], fit.get('failures')
    qa = fidelity.fidelity_qa(BOOK, fit, runner.glossary, runner.protected,
                              artifact_dir=tmp_path, update_manifest=False)
    assert qa['local_fidelity_ok'], qa['residual_english']
    assert any(e['code'] == 'legal_copyright' for e in qa['language_exceptions'])


@pytest.mark.parametrize('n', [11,25,85,137,167])
def test_native_chapter_redaction_dry_run_preserves_number_and_art(n):
    from astro_translate.native_redaction import redact_source
    from astro_translate.core import read_extras
    import numpy as np
    profile=load_profile(BOOK)
    with fitz.open(BOOK/'source.pdf') as doc, fitz.open() as working:
        page=doc[n-1]
        units=fidelity.extract_fidelity_units(page,read_extras(BOOK/'work/glossary/do_not_translate.txt'))
        assert len(units)>=2 and any(u['source']=='CHAPTER' for u in units)
        working.insert_pdf(doc,from_page=n-1,to_page=n-1)
        target=working[0]
        proofs=redact_source(page,target,units,profile,safe_redaction_rects)
        assert proofs and proofs[0]['method']=='native_text_filter'
        assert target.get_text().strip()==str([11,25,85,137,167].index(n)+1)
        assert fidelity._immutable_characters(page,[])==fidelity._immutable_characters(target,[])
        drawings=lambda p:[{k:v for k,v in d.items() if k!='seqno'} for d in p.get_drawings()]
        assert drawings(page)==drawings(target)
        assert len(page.get_images())==len(target.get_images())
        before=page.get_pixmap(dpi=220);after=target.get_pixmap(dpi=220)
        a=np.frombuffer(before.samples,dtype=np.uint8).reshape(before.height,before.width,3)
        b=np.frombuffer(after.samples,dtype=np.uint8).reshape(after.height,after.width,3)
        mask=np.ones(a.shape[:2],dtype=bool)
        for u in units:
            for s in u['spans']:
                r=(fitz.Rect(s['bbox'])*fitz.Matrix(220/72,220/72)).irect+(-2,-2,2,2)
                mask[max(0,r.y0):r.y1,max(0,r.x0):r.x1]=False
        assert np.array_equal(a[mask],b[mask])


def test_chapter_preceding_leaves_have_no_native_text():
    with fitz.open(BOOK/'source.pdf') as doc:
        assert all(not doc[n-1].get_text().strip() for n in (24,84,136,166))


def test_copyright_exception_is_exact_book_page_and_credit_scoped():
    from astro_translate.profiles import BookProfile
    from astro_translate.language import residual_english
    profile=load_profile(BOOK)
    with fitz.open(BOOK/'source.pdf') as doc, fitz.open() as candidate:
        candidate.new_page().insert_text((20,50),'Copyright ©')
        assert profile.language_exceptions(doc[3],candidate[0])
        assert not BookProfile().language_exceptions(doc[3],candidate[0])
        assert not profile.language_exceptions(doc[10],candidate[0])
        other=candidate.new_page();other.insert_text((20,50),'Copyright policy')
        assert not profile.language_exceptions(doc[3],other)
        # The exception is a phrase, never a blanket whitelist of Copyright.
        assert residual_english(['Copyright policy'],['Copyright ©'])


def test_frozen_unrepaired_quote_still_fails_qa(tmp_path,monkeypatch):
    runner=Production(BOOK)
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    payload=json.loads((FIXTURES/'p0009-translation.json').read_text())
    fit=fidelity.reinsert_fidelity(BOOK,payload,runner.glossary,runner.style,runner.protected,
        artifact_dir=tmp_path,phase='production',corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete']
    qa=fidelity.fidelity_qa(BOOK,fit,runner.glossary,runner.protected,
                           artifact_dir=tmp_path,update_manifest=False)
    assert not qa['local_fidelity_ok']
    assert any(i['code']=='fragmented_display_quote' for i in qa['text_quality_issues'])


def test_native_filter_rejects_ambiguous_origins_and_changed_source():
    from astro_translate.native_redaction import remove_source_spans
    from astro_translate.core import Blocked
    from copy import deepcopy
    with fitz.open(BOOK/'source.pdf') as source, fitz.open() as target:
        target.insert_pdf(source,from_page=10,to_page=10)
        page=target[0]
        unit=next(u for u in fidelity.extract_fidelity_units(source[10]) if u['source']=='CHAPTER')
        changed=deepcopy(unit);changed['spans'][0]['source']='STALE'
        with pytest.raises(Blocked,match='identify every source span'):
            remove_source_spans(page,[changed])
        origin=next(s['origin'] for b in page.get_text('dict')['blocks']
                    for l in b.get('lines',[]) for s in l['spans'] if s['text']=='CHAPTER')
        page.insert_text(origin,'X',fontsize=10)
        with pytest.raises(Blocked,match='Ambiguous glyph origin'):
            remove_source_spans(page,[unit])


def test_frozen_fixture_hashes():
    from astro_translate.core import sha256
    hashes=json.loads((FIXTURES/'sha256.json').read_text())
    assert hashes and all(sha256(FIXTURES/name)==digest for name,digest in hashes.items())
