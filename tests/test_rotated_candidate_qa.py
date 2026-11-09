"""Rotated/vertical figure labels through the real compositor and its local QA.

Real cases of The Secrets of Astrology (pseudo-composition 61-192, 2026-11-09):

* p90 (no rotated text): the page carries a /Group transparency (ICC CS). The
  one-page candidate extraction dropped it, so every flat colour rendered one
  level off and ``non_text_pixel_diff`` reached 0.46 on 28 pages.
* p89 ribbons (FuturaLTPro-MediumCond 17.5 pt, separators '|' in
  FrutigerLTPro-Condensed): the rotated proofs stayed bound to the candidate
  hash before extraction, so QA ignored them -> 74 "omitted" spans and the
  isolated 'R' spans of HEART/PURPOSE counted as altered immutable values.
* p89/p99/p103: ribbon labels inside the artwork were judged against the
  measure of a small sidebar list (p89 right edge 237 pt): editorial_margin.
* p86: accents of 'AMPÁLBÁÉ' rose above the tight glyph box (y 390 < 393).
* p86: SohoPro-Condensed vertical labels fell back to Roboto Serif although
  the profile classifies Soho Pro as slab.
"""
import json
from pathlib import Path
import shutil

import pymupdf as fitz
import pytest

from astro_translate import llm, pseudo

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT/'books/the-secrets-of-astrology'
secrets = pytest.mark.skipif(not (SECRETS/'source.pdf').is_file(), reason='Secrets source PDF not available')


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('pseudo composition must never call run_task')
    from astro_translate import adapters, safe_vlm
    for module in (llm, adapters, safe_vlm):
        monkeypatch.setattr(module, 'run_task', fail, raising=False)
    monkeypatch.setattr(safe_vlm, 'judge_safe_pair', fail)


@pytest.fixture
def secrets_copy(tmp_path, monkeypatch):
    """Real PDF/profile/manifest in a temporary book; only the G3 gate is doubled."""
    from astro_translate import core, fidelity
    from astro_translate.profiles import load_profile
    book = tmp_path/'book'
    (book/'work').mkdir(parents=True)
    shutil.copyfile(SECRETS/'source.pdf', book/'source.pdf')
    for name in ('profile.toml', 'pipeline.toml', 'manifest.json'):
        shutil.copyfile(SECRETS/'work'/name, book/'work'/name)
    profile = load_profile(SECRETS)

    def copy(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                target = book/value['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(profile.checked_file(value), target)
            for child in value.values():
                copy(child)
    copy(profile.data)
    for module in (core, fidelity):
        monkeypatch.setattr(module, 'require_approval', lambda *a, **k: {}, raising=False)
    return book


def _qa(book, n):
    folder = book/'work/pseudo/input_snapshot/book/work/compose'
    return (json.loads((folder/f'p{n:04d}-qa.json').read_text()),
            json.loads((folder/f'p{n:04d}-fit.json').read_text()))


@secrets
def test_extracted_candidate_keeps_page_transparency_group_p90(tmp_path):
    from astro_translate.candidate_storage import extract_page
    single = extract_page(SECRETS/'source.pdf', 90, tmp_path/'p0090.pdf')
    with fitz.open(SECRETS/'source.pdf') as source, fitz.open(single) as candidate:
        assert source.xref_get_key(source[89].xref, 'Group')[0] == 'xref'
        assert candidate.xref_get_key(candidate[0].xref, 'Group')[0] == 'xref'
        x = source[89].get_pixmap(dpi=110, alpha=False); y = candidate[0].get_pixmap(dpi=110, alpha=False)
        assert (x.width, x.height, x.samples) == (y.width, y.height, y.samples)


def test_extract_candidate_rebinds_rotated_proofs(tmp_path):
    from astro_translate.candidate_storage import extract_candidate
    from astro_translate.core import sha256
    doc = fitz.open()
    for n in range(3):
        doc.new_page(width=200, height=100).insert_text((20, 50), f'Page {n+1}')
    path = tmp_path/'candidate.pdf'; doc.save(path); doc.close()
    old = sha256(path)
    fit = {'page': 2, 'output': str(path), 'output_sha256': old,
           'verified_rotated_spans': [{'text': 'A', 'candidate_sha256': old},
                                      {'text': 'B', 'candidate_sha256': 'stale-other-candidate'}]}
    extract_candidate(fit)
    with fitz.open(path) as one:
        assert len(one) == 1 and 'Page 2' in one[0].get_text()
    assert fit['output_page'] == 0 and fit['output_sha256'] == sha256(path) != old
    assert fit['verified_rotated_spans'][0]['candidate_sha256'] == fit['output_sha256']
    # A proof bound to another candidate is never silently re-bound.
    assert fit['verified_rotated_spans'][1]['candidate_sha256'] == 'stale-other-candidate'


@secrets
def test_p89_ribbons_keep_immutable_spans_margins_and_page_pixels(secrets_copy):
    report = pseudo.compose_offline(secrets_copy, pages='89')
    row = report['pages'][0]
    assert row['status'] != 'error', row.get('trace')
    qa, fit = _qa(secrets_copy, 89)
    rotated = [b for b in fit['blocks'] if '_rotated_' in b['id']]
    assert len(rotated) == 8 and all(b['scale'] >= .75 for b in rotated)
    assert qa['omitted_rotated_text'] == []
    assert qa['immutable_spans_equal'] is True
    assert qa['non_text_pixel_diff'] == 0
    assert not [i for i in qa['editorial_margin_issues'] if '_rotated_' in i['unit_id']]
    with fitz.open(secrets_copy/'source.pdf') as source, fitz.open(fit['output']) as candidate:
        bars = lambda page: sorted((round(c[2][0], 2), round(c[2][1], 2)) for t in page.get_texttrace()
                                   for c in t['chars'] if chr(c[0]) == '|')
        assert bars(candidate[0]) == bars(source[88]) and len(bars(source[88])) == 5


@secrets
def test_p86_accented_ribbon_ink_and_slab_vertical_labels(secrets_copy):
    report = pseudo.compose_offline(secrets_copy, pages='86')
    row = report['pages'][0]
    assert row['status'] != 'error', row.get('trace')
    qa, fit = _qa(secrets_copy, 86)
    assert qa['non_text_pixel_diff'] == 0
    assert not [i for i in qa['editorial_margin_issues'] if '_rotated_' in i['unit_id']]
    rotated = [b for b in fit['blocks'] if '_rotated_' in b['id']]
    for block in rotated:
        ink = fitz.Rect(block['ink_bbox'])
        assert fitz.Rect(block['edit_bbox']).contains(ink)
    vertical = [b for b in fit['blocks'] if '_vertical_' in b['id']]
    assert vertical and all(b['font'].startswith('SohoPro') for b in vertical)
    # Soho Pro is slab in the book profile: Roboto Slab, never Roboto Serif.
    assert all(run['engine_name'].startswith('AstroSlab-') for b in vertical for run in b['font_runs']), \
        [run['engine_name'] for b in vertical for run in b['font_runs']]
    for block in vertical:
        assert fitz.Rect(block['edit_bbox']).contains(fitz.Rect(block['ink_bbox']))


def test_vertical_redaction_tolerates_float32_origin_drift_p86():
    """p86 (pseudo-fit 2026-11-09): apply_redactions re-instanced the composed
    Form XObjects and the body glyph 'b' (AstroSlab Light 7.92 pt, bbox
    490.43,548.09,494.65,556.01) moved one float32 ulp, 490.43447876 ->
    490.43450928. Rounding to 1e-3 flipped (.434 -> .435) and the transaction
    was blocked as 'changed unrelated text'. A real move, a lost glyph or a
    leftover source glyph must still block."""
    from astro_translate.vertical_labels import redaction_preserved
    font = 'AstroSlab-7d026aea704a5abf-Ligh'
    body = {'c': 'b', 'font': font, 'origin': (490.4344787597656, 554.3817138671875)}
    drifted = {**body, 'origin': (490.43450927734375, 554.3817138671875)}
    sun = {'c': 'T', 'font': 'SohoPro-Condensed', 'origin': (31.1812, 39.53)}
    assert redaction_preserved([body], [sun], [drifted])
    assert not redaction_preserved([body], [sun], [{**body, 'origin': (490.45, 554.3817)}])
    assert not redaction_preserved([body], [sun], [])
    assert not redaction_preserved([body], [sun], [drifted, sun])
    # One survivor cannot account for two unrelated glyphs at the same place.
    assert not redaction_preserved([body, dict(body)], [sun], [drifted])
