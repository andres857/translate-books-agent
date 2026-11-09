"""Orchestrator report 2026-11-09: p23 «Imum Coeli» residual English; p138/p139 «ª» raised twice.

Real pages of ``books/the-secrets-of-astrology/source.pdf`` composed with their saved Sonnet
translation in a private snapshot book (no LLM: translations are read, never produced).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf as fitz
import pytest

from test_composition_gaps import SECRETS, secrets

PRODUCTION = SECRETS/'work/production'


def saved(page_number):
    return pytest.mark.skipif(not (PRODUCTION/f'p{page_number:04d}-translation.json').is_file(),
                              reason='Saved Sonnet translation not available')


# ------------------------------------------------------------------ 2. Latin terms (p23)
# p23 u0011 (SohoPro-Light 11 pt, 201.0,495.6–345.8,590.6): «… IC is “Imum Coeli” (“bottom of the
# heavens”).» → «… IC es el “Fondo del Cielo” (“Imum Coeli”, “parte inferior de los cielos”).»
# The Latin name is cited unchanged from the source; Lingua (EN/ES) gives the leftover
# «“ ” (“Imum Coeli”,» to English (≥0.70) once «Fondo del Cielo» is masked.
P23_SOURCE = ('The Descendant lies opposite and shows our “other half.” The second axis is the MC–IC. MC stands '
              'for “Medium Coeli” (Latin for “middle of the heavens”). IC is “Imum Coeli” (“bottom of the heavens”).')
P23_TARGET = ('El Descendente se encuentra en el lado opuesto y muestra nuestra “otra mitad”. El segundo eje es el '
              'MC–IC. MC significa “Medio Cielo” (“Medium Coeli” en latín, “mitad de los cielos”). IC es el “Fondo '
              'del Cielo” (“Imum Coeli”, “parte inferior de los cielos”).')


def test_source_identical_latin_term_is_not_residual_english():
    from astro_translate.language import residual_english, source_identical_latin_terms
    found = source_identical_latin_terms([{'id': 'u0011', 'source': P23_SOURCE, 'target': P23_TARGET}])
    assert [(f['unit_id'], f['text']) for f in found] == [('u0011', 'Medium Coeli'), ('u0011', 'Imum Coeli')]
    assert all(f['code'] == 'source_identical_latin_term' and f['source_identical'] for f in found)
    leftover = '“ ” (“Imum Coeli”,'
    assert residual_english([leftover])  # the reported false positive
    # fidelity_qa masks every allowed term in the candidate text before detection.
    masked = leftover
    for term in (f['text'] for f in found):
        masked = re.sub(r'(?<!\w)'+re.escape(term)+r'(?!\w)', ' ', masked)
    assert residual_english([masked]) == []


def test_latin_exception_does_not_admit_english():
    from astro_translate.language import residual_english, source_identical_latin_terms
    # Not in the source unit: the model may not introduce it.
    assert source_identical_latin_terms([{'id': 'u1', 'source': 'IC is the bottom of the heavens.',
                                          'target': 'IC es el “Imum Coeli”.'}]) == []
    # Translated, not copied: nothing to exempt.
    assert source_identical_latin_terms([{'id': 'u1', 'source': 'IC is “Imum Coeli”.',
                                          'target': 'IC es el “Fondo del Cielo”.'}]) == []
    # English copied identically from the source is still English.
    english = [{'id': 'u1', 'source': 'IC is “Imum Coeli” (“bottom of the heavens”).',
                'target': 'IC es “Imum Coeli” (“bottom of the heavens”).'}]
    allowed = [f['text'] for f in source_identical_latin_terms(english)]
    assert allowed == ['Imum Coeli']
    assert residual_english(['“bottom of the heavens”).'], allowed)
    # Single English-looking words of a Latin name never qualify on their own.
    assert source_identical_latin_terms([{'id': 'u1', 'source': 'a medium orb', 'target': 'a medium orb'}]) == []


@secrets
@saved(23)
def test_p23_qa_has_no_residual_english_for_imum_coeli(tmp_path):
    from test_batch16_composition import _saved_fit
    from astro_translate import fidelity
    from astro_translate.production import Production
    runner = Production(SECRETS)
    fit = _saved_fit(23, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    unit = next(b for b in fit['blocks'] if b['id'] == 'u0011')
    assert '“Imum Coeli”' in unit['source'] and '“Imum Coeli”' in unit['target']
    snap = Path(fit['output']).parents[2]
    qa = fidelity.fidelity_qa(snap, fit, runner.glossary, runner.protected, artifact_dir=snap/'work/qa',
                              phase='production', update_manifest=False)
    assert not any('Coeli' in text for text in qa['residual_english']), qa['residual_english']
    assert not any('Coeli' in text for text in qa.get('native_residual_english', []))


# ------------------------------------------------------------------ 3. «ª» raised once (p138/p139)
# House wheel: «1» SohoPro-Bold 25.8 pt (baseline 293.85, ink top 274.61) + raised «st» SohoPro-Bold
# 15.04 pt (origin y 283.26, ink 274.36–283.47). «.ª» was drawn with «ª» at the raised suffix origin:
# the glyph is already a superscript, so its ink top reached 272.42, 2.2 pt above the digits («11. ª»).
# One mechanism: «.ª» on the numeral's baseline, at the numeral's size (ink of «ª» = digit top band).
def _wheel_ink(page, block):
    from astro_translate.safe_vlm import _glyph_ink_bounds
    data = block['native_ordinal']
    box = fitz.Rect(block['edit_bbox']) | fitz.Rect(data['digit']['bbox'])
    bounds, _, failed = _glyph_ink_bounds(page)
    assert not failed
    ink = lambda ch: [r for (u, x, y), rs in bounds.items() if chr(u) == ch and box.contains(fitz.Point(x, y))
                      for r in rs]
    return ink(data['digit']['text'][0]), ink('ª'), ink('.')


def _source_ink(page, data, char):
    from astro_translate.safe_vlm import _glyph_ink_bounds
    box = fitz.Rect(data['suffix']['bbox'])+(-.5, -.5, .5, .5)
    bounds, _, _ = _glyph_ink_bounds(page)
    return [r for (u, x, y), rs in bounds.items() if chr(u) == char and box.contains(fitz.Point(x, y)) for r in rs]


@secrets
@pytest.mark.parametrize('page_number,expected', [(138, ['1.ª', '11.ª', '12.ª', '2.ª']),
                                                  (139, ['10.ª', '3.ª', '4.ª', '5.ª', '6.ª', '7.ª', '8.ª', '9.ª'])])
def test_house_ordinal_indicator_is_raised_once(page_number, expected, tmp_path):
    from test_batch16_composition import _saved_fit
    from astro_translate import fidelity
    if not (PRODUCTION/f'p{page_number:04d}-translation.json').is_file():
        pytest.skip('Saved Sonnet translation not available')
    fit = _saved_fit(page_number, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    wheel = [b for b in fit['blocks'] if b.get('native_ordinal', {}).get('kind') == 'house_diagram']
    assert sorted(b['target'] for b in wheel) == expected
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', page_number-1)]
        for block in wheel:
            data = block['native_ordinal']; digit = data['digit']
            chars = [(c['c'], c['origin'], s['size']) for b in page.get_text('rawdict', clip=fitz.Rect(block['edit_bbox'])
                     + (-.5, -.5, .5, .5))['blocks'] for l in b.get('lines', []) for s in l['spans'] for c in s['chars']]
            marks = {c: (origin, size) for c, origin, size in chars if c in '.ª'}
            # Single mechanism: both marks on the numeral's baseline, numeral size.
            for mark in '.ª':
                assert marks[mark][0][1] == pytest.approx(digit['origin'][1], abs=.01), (block['target'], mark)
                assert marks[mark][1] == pytest.approx(digit['size'], abs=.01)
            assert marks['.'][0][0] == pytest.approx(data['suffix']['origin'][0], abs=.01)
            digits, letter, _ = _wheel_ink(page, block)
            top = min(r.y0 for r in digits)
            # Never above the figures (was ≈2.2 pt); still a superior letter, in the band of
            # the original raised suffix (ink top within 1.5 pt of it).
            assert letter[0].y0 >= top-.3, (block['target'], letter[0].y0, top)
            assert letter[0].y1 < digit['origin'][1]-.4*(digit['origin'][1]-top)
            suffix_top = min(r.y0 for c in data['suffix']['text'].strip() for r in _source_ink(src[page_number-1], data, c))
            assert abs(letter[0].y0-suffix_top) <= 1.5, (block['target'], letter[0].y0, suffix_top)
        issues = fidelity.composition_line_issues(src[page_number-1], page, fit['blocks'])
        assert [i for i in issues if i['code'].startswith('ordinal_')] == []
        from astro_translate.native_ordinals import qa_adjustments
        _, proofs = qa_adjustments(src[page_number-1], page, fit['blocks'], {})
        assert len(proofs) == len(wheel)


def test_qa_flags_indicator_raised_twice():
    """The batch-17 form (point on the baseline, «ª» at the raised suffix origin) is reported."""
    from astro_translate.native_ordinals import point_issues
    from astro_translate.fonts import ROOT
    doc = fitz.open(); page = doc.new_page(width=200, height=200)
    font = ROOT/'fonts'/'RobotoSlab[wght].ttf'
    page.insert_font(fontname='F', fontfile=str(font))
    page.insert_text((20, 100), '1', fontname='F', fontsize=25.8)
    page.insert_text((36, 100), '.', fontname='F', fontsize=15.04)
    page.insert_text((40, 89.4), 'ª', fontname='F', fontsize=15.04)
    block = {'id': 'native_house_1', 'edit_bbox': [19, 70, 60, 105],
             'native_ordinal': {'kind': 'house_diagram',
                                'digit': {'text': '1', 'origin': [20, 100], 'size': 25.8, 'bbox': [20, 78.5, 36, 104.3]},
                                'suffix': {'text': 'st', 'origin': [36, 89.4], 'size': 15.04, 'bbox': [36, 78, 48, 93]}}}
    codes = [i['code'] for i in point_issues(page, [block])]
    assert codes == ['ordinal_indicator_raised_twice']
    doc.close()
