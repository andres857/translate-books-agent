"""Gap 9 (G-DETECT): italic constellation legends are figure legends.

Real cases: p47 'The Beehive was known to the ancient Greeks...' SohoPro-Italic
9 pt at (431,245,521,294) with a dashed leader curve ([0 1.5] dash, 0.75 pt)
ending at the legend. Because the 9 pt italic face dominates the page's small
text, column_flow chained the legends of p47/p73 into one 'column story'
(fit_failed on p47). Legends must stay separate callouts with caption growth.
"""
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate.chart_callouts import mark_figure_legends
from astro_translate.fidelity import extract_fidelity_units

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT/'books/the-secrets-of-astrology'
SKY = [35, 39, 43, 47, 51, 55, 61, 65, 69, 73, 77, 81]
pytestmark = pytest.mark.skipif(not (BOOK/'source.pdf').is_file(), reason='Secrets source PDF not available')


def _prepared(n):
    from astro_translate.survey import _prepared as prepared, _identity
    from astro_translate.profiles import load_profile
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[n-1]
        units = extract_fidelity_units(page)
        result, errors = prepared(page, _identity(units), load_profile(BOOK))
    assert not errors
    return units, result


@pytest.mark.parametrize('number', SKY)
def test_sky_panel_legends_are_marked(number):
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[number-1]
        units = extract_fidelity_units(page)
        marked = mark_figure_legends(page, units)
    legends = [u for u in marked if u.get('figure_legend')]
    italic = [u for u in units if u['size'] <= 10 and all(s['style'].get('italic') for s in u['spans'])]
    assert legends and len(legends) == len(italic), number
    assert all(u['legend_guides'] >= 1 for u in legends)
    # The panel body and title are never legends.
    assert not [u for u in marked if u.get('figure_legend') and u['size'] > 10]


@pytest.mark.parametrize('number', [47, 73])
def test_legends_are_not_swept_into_a_column_story(number):
    units, prepared = _prepared(number)
    assert not [u for u in prepared if u.get('story_frames')]
    legends = [u for u in prepared if u.get('figure_legend')]
    assert len(legends) >= 3
    assert all(u.get('caption_growth') for u in legends)


def test_body_text_without_guides_is_untouched():
    with fitz.open(BOOK/'source.pdf') as doc:
        for n in (12, 15):
            page = doc[n-1]
            units = extract_fidelity_units(page)
            assert not [u for u in mark_figure_legends(page, units) if u.get('figure_legend')], n
