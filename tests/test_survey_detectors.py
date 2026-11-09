"""Gap 9 (G-DETECT): table, single-unit quote and raster-text detectors.

Real cases (The Secrets of Astrology):
- p179 'Root 1  Symbol ☌  Angle 0°  Orb 8°' rows (SohoPro-Medium 7 pt labels,
  protected values) form a key/value table; p180/p182 rows of PRH17 glyphs
  captioned 'The Sun', 'The Moon'... (6 pt) form glyph tables.
- p13 pull quote 'Your birth chart is an image of you...' (27 pt, one unit,
  vector quote marks at (62,149) and (198,237)) was not detected.
- p8/p10/p24/p184 illustrations were flagged contains_text=True only for
  being route C; p1 (cover) really carries raster text.
"""
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import survey_detectors as sd
from astro_translate.fidelity import extract_fidelity_units

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT/'books/the-secrets-of-astrology'
secrets = pytest.mark.skipif(not (BOOK/'source.pdf').is_file(), reason='Secrets source PDF not available')


@secrets
def test_p179_key_value_table():
    with fitz.open(BOOK/'source.pdf') as doc:
        tables = sd.detect_tables(doc[178])
    kv = [t for t in tables if t['kind'] == 'key_value_rows']
    assert len(kv) == 1
    assert kv[0]['columns'] == ['root', 'symbol', 'angle', 'orb'] and kv[0]['rows'] >= 8


@secrets
@pytest.mark.parametrize('number,cells', [(180, [7]), (182, [2, 3, 2, 4, 2])])
def test_glyph_label_tables(number, cells):
    with fitz.open(BOOK/'source.pdf') as doc:
        tables = sd.detect_tables(doc[number-1])
    rows = [t for t in tables if t['kind'] == 'glyph_label_row']
    assert sorted(t['columns'] for t in rows) == sorted(cells)
    if number == 180:
        assert rows[0]['labels'] == ['The Sun', 'The Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']


@secrets
@pytest.mark.parametrize('number', [12, 13, 15, 34, 89, 141, 186, 188])
def test_no_table_on_prose_fields_or_lists(number):
    with fitz.open(BOOK/'source.pdf') as doc:
        assert sd.detect_tables(doc[number-1]) == [], number


@secrets
def test_p13_single_unit_pull_quote():
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[12]
        units = extract_fidelity_units(page)
        quotes = sd.single_unit_quotes(page, units)
    assert len(quotes) == 1
    assert quotes[0]['quote_marks'] == 'vector' and quotes[0]['unit_ids'] == ['u0001']


@secrets
@pytest.mark.parametrize('number', [3, 9, 11, 12, 15, 23, 25, 180])
def test_titles_body_and_known_quotes_are_not_new_quotes(number):
    from astro_translate.editorial_rules import quote_retranslation_units
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[number-1]
        units = extract_fidelity_units(page)
        known = {i for q in quote_retranslation_units(page, units) for i in q['composed_ids']}
        assert sd.single_unit_quotes(page, units, known) == [], number


class FakeResult:
    def __init__(self, texts, scores, boxes):
        self.json = {'res': {'rec_texts': texts, 'rec_scores': scores, 'rec_boxes': boxes}}


class FakeEngine:
    def __init__(self, *results):
        self.results = results

    def predict(self, path):
        assert Path(path).is_file()
        return [FakeResult(*r) for r in self.results]


def test_raster_probe_thresholds_and_native_exclusion():
    doc = fitz.open(); page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), 'Native words', fontsize=12)
    k = 110/72
    stars = FakeEngine((['**', 'Ey', 'TITLE TEXT', 'Native words'], [.99, .46, .97, .99],
                        [[10, 10, 30, 30], [40, 40, 60, 60], [20*k, 100*k, 120*k, 120*k], [20*k, 30*k, 100*k, 42*k]]))
    result = sd.probe_raster_text(page, engine=stars)
    assert result['contains_text'] is True
    assert [l['text'] for l in result['lines']] == ['TITLE TEXT']
    blank = FakeEngine((['**', 'n'], [.99, .3], [[0, 0, 5, 5], [6, 6, 9, 9]]))
    assert sd.probe_raster_text(page, engine=blank)['contains_text'] is False


def test_raster_probe_unavailable_is_unknown(monkeypatch):
    def broken():
        raise ImportError('no paddle')
    monkeypatch.setattr(sd, '_engine', broken)
    doc = fitz.open(); page = doc.new_page()
    result = sd.probe_raster_text(page)
    assert result['contains_text'] is None and result['method'].startswith('unavailable')


@secrets
def test_real_local_ocr_cover_vs_illustrations():
    pytest.importorskip('paddleocr')
    with fitz.open(BOOK/'source.pdf') as doc:
        cover = sd.probe_raster_text(doc[0])
        if cover['contains_text'] is None:
            pytest.skip('local OCR unavailable: '+cover['method'])
        assert cover['contains_text'] is True
        assert any('ASTROLOGY' in l['text'] for l in cover['lines'])
        for n in (8, 184):
            assert sd.probe_raster_text(doc[n-1])['contains_text'] is False, n
