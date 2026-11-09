"""Orphan source punctuation, batch-21 audit (work/audit/batch-21.json).

A span that only holds punctuation matches the digit-like immutable class of
``fidelity.immutable_span``. Beside rewritten text it stayed painted:
p9 «MAP,» (',' SohoPro-LightCondensed 50 pt, x 144.0–152.7) read «MAP,A DEL TESORO»;
p21 «astrology,» (', ' SohoPro-LightCompressed 27 pt, x 429.4–436.4) doubled the
translated comma after «…trabajar con ella,». The immutable check accepted both.

Real pages composed with their saved Sonnet translation
(``work/production/p<NNNN>-translation.json``) in a private snapshot book. No LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf as fitz
import pytest

from test_composition_gaps import SECRETS, secrets

PRODUCTION = SECRETS/'work/production'
COMMAS = {9: (',', (144.0, 152.7)), 21: (', ', (429.4, 436.4))}


def _translation(page_number):
    return PRODUCTION/f'p{page_number:04d}-translation.json'


def saved(page_number):
    return pytest.mark.skipif(not _translation(page_number).is_file(), reason='Saved Sonnet translation not available')


def _audited_candidate(page_number):
    """Saved production candidate, only while it is still the one audited in batch-21."""
    from astro_translate.core import sha256
    fit_path = PRODUCTION/f'p{page_number:04d}-fit.json'
    audit_path = SECRETS/'work/audit/batch-21.json'
    if not (fit_path.is_file() and audit_path.is_file()):
        pytest.skip('Audited candidate not available')
    fit = json.loads(fit_path.read_text())
    audited = {p['page']: p.get('candidate_sha256') for p in json.loads(audit_path.read_text())['pages']}
    if not fit.get('output') or not (PRODUCTION/f'p{page_number:04d}-translated.pdf').is_file() \
            or sha256(fit['output']) != audited.get(page_number):
        pytest.skip('Production candidate recomposed after the audit')
    return fit


def _comma_spans(page, page_number):
    text, (x0, x1) = COMMAS[page_number]
    return [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']
            if s['text'] == text and abs(s['bbox'][0]-x0) < .1 and abs(s['bbox'][2]-x1) < .1]


@secrets
@pytest.mark.parametrize('page_number', [9, 21])
def test_qa_flags_the_audited_orphan_comma(page_number):
    from astro_translate.orphan_punctuation import orphan_punctuation_issues
    fit = _audited_candidate(page_number)
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 0)]
        assert _comma_spans(page, page_number), 'audited candidate no longer keeps the comma'
        issues = orphan_punctuation_issues(src[page_number-1], page)
    found = [i for i in issues if i['code'] == 'orphan_source_punctuation']
    assert len(found) == 1, issues
    assert found[0]['text'] == ',' and abs(found[0]['bbox'][0]-COMMAS[page_number][1][0]) < .1
    assert {9: 'MAP', 21: 'astrology'}[page_number] in found[0]['rewritten_neighbours']


@secrets
@pytest.mark.parametrize('page_number', [9, 21])
def test_composition_removes_the_comma_with_its_text(page_number, tmp_path):
    from test_batch16_composition import _saved_fit
    from astro_translate import fidelity
    from astro_translate.orphan_punctuation import orphan_punctuation_issues
    from astro_translate.production import Production
    if not _translation(page_number).is_file():
        pytest.skip('Saved Sonnet translation not available')
    fit = _saved_fit(page_number, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    owners = [b for b in fit['blocks'] if b.get('absorbed_punctuation')]
    assert [s['source'] for b in owners for s in b['absorbed_punctuation']] == [COMMAS[page_number][0]]
    # Source provenance of the saved translation is unchanged.
    saved_units = json.loads(_translation(page_number).read_text())['blocks']
    assert not any(s['source'].strip() == ',' for u in saved_units for s in u['spans'])
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 0)]
        assert _comma_spans(src[page_number-1], page_number)
        assert _comma_spans(page, page_number) == []
        assert orphan_punctuation_issues(src[page_number-1], page) == []
    runner = Production(SECRETS)
    snap = Path(fit['output']).parents[2]
    qa = fidelity.fidelity_qa(snap, fit, runner.glossary, runner.protected, artifact_dir=snap/'work/qa',
                              phase='production', update_manifest=False)
    assert qa['immutable_spans_equal'] is True
    assert not [i for i in qa['text_quality_issues'] if i['code'] == 'orphan_source_punctuation']


# ------------------------------------------------------------------ synthetic, no book needed

def _page_with(spans):
    """A one-line PDF page; ``spans`` = (text, fontname) drawn left to right."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=100)
    x = 20
    for text, font in spans:
        page.insert_text((x, 50), text, fontname=font, fontsize=12)
        x += fitz.get_text_length(text, fontname=font, fontsize=12)
    return doc


def _raw(page):
    return [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']
            if s['text'].strip()]


def _unit(spans, target):
    from astro_translate.fidelity import span_style
    items = [{'source': s['text'], 'bbox': list(s['bbox']), 'font': s['font'], 'size': s['size'],
              'color': s['color'], 'flags': s['flags'], 'rotate': 0, 'style': span_style(s)} for s in spans]
    rect = fitz.Rect(items[0]['bbox'])
    for s in items[1:]:
        rect |= fitz.Rect(s['bbox'])
    return {'id': 'u0000', 'spans': items, 'bbox': list(rect), 'target': target}


def test_mid_unit_comma_is_absorbed_and_redacted_with_its_unit():
    from astro_translate.orphan_punctuation import absorb_adjacent_punctuation, redaction_unit
    with _page_with([('MAP', 'hebo'), (',', 'tiro'), (' AND THE REAL', 'helv')]) as doc:
        page = doc[0]
        raw = _raw(page)
        assert [s['text'] for s in raw] == ['MAP', ',', ' AND THE REAL']
        unit = _unit([raw[0], raw[2]], 'MAPA Y LA REAL')
        [result] = absorb_adjacent_punctuation(page, [unit])
        assert [s['source'] for s in result['absorbed_punctuation']] == [',']
        assert 'absorbed_punctuation' not in unit and result['spans'] == unit['spans']
        assert [s['source'] for s in redaction_unit(result)['spans']] == ['MAP', ' AND THE REAL', ',']


def test_punctuation_after_preserved_text_stays():
    from astro_translate.orphan_punctuation import absorb_adjacent_punctuation
    with _page_with([('MAP', 'hebo'), (',', 'tiro'), (' AND THE REAL', 'helv')]) as doc:
        page = doc[0]
        raw = _raw(page)
        # «MAP» is not composed (e.g. a preserved published title): its comma stays.
        [result] = absorb_adjacent_punctuation(page, [_unit([raw[2]], 'Y LA REAL')])
        assert not result.get('absorbed_punctuation')


def test_terminal_mark_absent_from_the_translation_is_not_dropped():
    from astro_translate.orphan_punctuation import absorb_adjacent_punctuation
    with _page_with([('astrology', 'hebo'), (',', 'tiro')]) as doc:
        page = doc[0]
        raw = _raw(page)
        [kept] = absorb_adjacent_punctuation(page, [_unit([raw[0]], 'astrología')])
        assert not kept.get('absorbed_punctuation')
        [absorbed] = absorb_adjacent_punctuation(page, [_unit([raw[0]], 'astrología,')])
        assert [s['source'] for s in absorbed['absorbed_punctuation']] == [',']


def test_qa_ignores_punctuation_between_preserved_values():
    from astro_translate.orphan_punctuation import orphan_punctuation_issues
    with _page_with([('12', 'helv'), (',', 'tiro'), (' 15', 'helv')]) as a, \
            _page_with([('12', 'helv'), (',', 'tiro'), (' 15', 'helv')]) as b:
        assert orphan_punctuation_issues(a[0], b[0]) == []
