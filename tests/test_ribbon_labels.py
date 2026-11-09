"""Gap 9 (G-DETECT): A13 ribbons are near-straight rotated labels, not curves.

Real case p89: 'SHINING LIGHT | VITALITY | CONFIDENCE' etc. in
FuturaLTPro-MediumCond 17.5 pt; every glyph carries its own operator angle
(+2.8 deg .. -0.6 deg along the ribbon) and rawdict emits one span per glyph,
so the strict per-span rule rejected them as 'curved_or_fragmented_native_text'.
Per label the baseline deviates <0.6 pt from its chord: composable as straight.
Wheels (26-31, 177) and the p175 path text must stay curved.
"""
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate.native_rotated import deferred_inventory, extract_straight_labels

BOOK = Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
A13 = [89, 93, 99, 103, 107, 113, 117, 121, 125, 129, 133]
pytestmark = pytest.mark.skipif(not (BOOK/'source.pdf').is_file(), reason='Secrets source PDF not available')


def test_p89_ribbons_are_straight_labels_with_fitted_angle():
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[88]
        units = extract_straight_labels(page)
        sources = [u['source'] for u in units]
        assert sorted(sources) == sorted(['PURPOSE', 'CREATIVE POWER', 'PRIDE', 'HEART OF OUR BEING', 'GOALS',
                                          'SHINING LIGHT', 'VITALITY', 'CONFIDENCE'])
        shining = next(u for u in units if u['source'] == 'SHINING LIGHT')
        assert shining['font'].endswith('FuturaLTPro-MediumCond') and shining['size'] == pytest.approx(17.5, abs=.01)
        assert 1.0 < shining['angle_spread_deg'] < 1.2 and shining['baseline_residual'] < .3
        assert shining['geometry'] == 'near_straight_chord'
        assert 0 < shining['direction'][1] < .06  # gentle clockwise tilt, as printed
        # Separators '|' (FrutigerLTPro-Condensed) are not label text.
        assert all('|' not in s for s in sources)
        regions = deferred_inventory(page)
        assert not [r for r in regions if r['reason'] == 'curved_or_fragmented_native_text']
        assert {r['reason'] for r in regions} == {'straight_rotated_translation_pending'}
        assert all('|' not in s['text'] for r in regions for s in r['source_spans'])


@pytest.mark.parametrize('number', A13)
def test_a13_ribbons_leave_no_curved_inventory(number):
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[number-1]
        units = extract_straight_labels(page)
        assert len(units) >= 6, number
        assert not [r for r in deferred_inventory(page) if r['reason'] == 'curved_or_fragmented_native_text'], number


@pytest.mark.parametrize('number', [26, 27, 28, 29, 30, 31, 175, 177])
def test_wheels_and_paths_stay_curved(number):
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[number-1]
        assert not [u for u in extract_straight_labels(page) if u.get('geometry') == 'near_straight_chord'], number
        assert [r for r in deferred_inventory(page) if r['reason'] == 'curved_or_fragmented_native_text'], number


def test_ribbon_label_removal_identifies_every_glyph(tmp_path):
    from astro_translate.native_redaction import remove_source_spans
    with fitz.open(BOOK/'source.pdf') as doc:
        units = extract_straight_labels(doc[88])
        one = fitz.open(); one.insert_pdf(doc, from_page=88, to_page=88)
    target = [u for u in units if u['source'] == 'VITALITY']
    report = remove_source_spans(one[0], target)
    assert report['removed_characters'] == len('VITALITY')
    assert 'VITALITY' not in one[0].get_text().replace('\n', '')
    assert 'CONFIDENCE' in ''.join(c for c in one[0].get_text() if c.isalpha())


def test_every_p89_ribbon_label_is_removable_and_composable(tmp_path):
    from astro_translate.native_redaction import remove_source_spans
    from astro_translate.native_rotated import compose_labels
    from astro_translate.pseudo import pseudo_text
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[88]
        units = extract_straight_labels(page)
        for unit in units:
            one = fitz.open(); one.insert_pdf(doc, from_page=88, to_page=88)
            assert remove_source_spans(one[0], [unit])['removed_characters'] == len(unit['source'].replace(' ', ''))
        # Same-length pseudo targets: mechanics only, no translation.
        answers = {u['id']: pseudo_text(u["source"], expansion=1.0) for u in units}
        candidate = fitz.open(); candidate.insert_pdf(doc, from_page=88, to_page=88)
        rows, audit = compose_labels(page, candidate[0], units, answers, tmp_path)
    assert len(rows) == len(units) and all(r['scale'] >= .75 for r in rows)
    engines = {r['font_runs'][0]['engine_name'] for r in rows}
    tilts = [t['dir'][1] for t in candidate[0].get_texttrace() if t['font'] in engines]
    # One straight baseline per label, exactly at its fitted chord angle.
    chords = [u['direction'][1] for u in units]
    assert tilts and all(min(abs(t-c) for c in chords) < 1e-4 for t in tilts)
    assert all(abs(c) < .11 for c in chords)
