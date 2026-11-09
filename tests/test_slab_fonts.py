"""I1: real Soho subsets, font selection only; never compose a book page."""
import copy
import importlib.util
import json
from pathlib import Path

import pymupdf as fitz
import pytest
from fontTools.ttLib import TTFont

from astro_translate import fonts
from astro_translate.fidelity import span_style

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / 'books/the-secrets-of-astrology'
SLAB_SHA256 = '786ae192477447d33c6672c3055fba7cbfe45184c9a79e77a14f15716ca05b16'
spec = importlib.util.spec_from_file_location(
    'slab_local_layout', BOOK / 'work/pilot/local_layout.py')
local_layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_layout)


@pytest.mark.parametrize('name,weight,width', [
    ('SohoPro-Light', 300, 100),
    ('SohoPro-BoldCompressed', 700, 60),
])
def test_real_soho_missing_accents_selects_calibrated_slab(tmp_path, name, weight, width):
    with fitz.open(BOOK / 'source.pdf') as doc:
        page = doc[18]
        span = next(s for b in page.get_text('dict')['blocks']
                    for line in b.get('lines', []) for s in line['spans']
                    if s['font'] == name)
        style = span_style(span)
        assert (style['weight'], style['width']) == (weight, width)
        original = fonts.original_font(doc, page, name, tmp_path / 'original')
        assert not fonts.covers(original['font'], 'áéíóú')
        # A covered sibling must use the same fallback as its accented neighbor.
        units = [{'scale_group': 'siblings', 'style': style, 'target': target}
                 for target in ('áéíóú Íñ¿¡', 'Aries')]
        before = copy.deepcopy(units)
        selector = fonts.select_group_fonts
        with local_layout.composition_context():
            mapping, audit = fonts.select_group_fonts(doc, page, units, tmp_path)
        assert fonts.select_group_fonts is selector
        assert units == before  # Saved translations/override styles stay valid.
        choice = next(iter(mapping.values()))
        assert choice['base_sha256'] == SLAB_SHA256
        assert choice['name'].startswith('AstroSlab-')
        assert set(choice['axes']) == {'wght'}  # RobotoSlab has no wdth axis.
        # Profile calibrate_weight: wght tuned to the source stems (G-FONT-1).
        assert choice['weight_calibration']['nominal'] == weight
        assert choice['axes']['wght'] == choice['weight_calibration']['chosen']
        assert abs(choice['axes']['wght']-weight) <= 400
        assert audit[0]['group_fallback'] and audit[0]['original_missing']
        assert fonts.covers(choice['font'], 'áéíóú Íñ¿¡Aries')
        calibration = choice['metric_calibration']
        assert calibration['chosen_advance_em'] == pytest.approx(
            calibration['source_advance_em'], rel=.002)
        if width == 60:
            assert calibration['horizontal_scale'] < 1
        with TTFont(choice['path']) as static:
            assert 'fvar' not in static and 'GSUB' not in static
            assert static['OS/2'].usWeightClass == weight
            assert static['OS/2'].usWidthClass == (2 if width == 60 else 5)


@pytest.mark.parametrize('name,serif,expected', [
    ('ABCDEF+SohoPro-Light', False, 'slab'),
    ('Soho Pro Medium Condensed', False, 'slab'),
    ('Rockwell-Bold', False, 'slab'),
    ('Museo Slab 300', False, 'slab'),
    ('RobotoSlab-Regular', False, 'slab'),
    ('SohoGothicPro-Light', True, 'sans'),
    ('FrutigerLTPro-Light', False, 'sans'),
    ('RobotoSerif-Regular', True, 'serif'),
    ('Times-Roman', True, 'serif'),
])
def test_fallback_class_known_families(name, serif, expected):
    assert fonts.fallback_class({'font': name, 'serif': serif}) == expected


@pytest.mark.parametrize('name,italic,serif,base', [
    ('FrutigerLTPro-Light', False, False, 'NotoSans[wdth,wght].ttf'),
])
def test_sans_and_slab_italic_keep_identical_fallback_bytes(tmp_path, name, italic, serif, base):
    style = {'font': name, 'size': 12, 'color': 0, 'weight': 300,
             'bold': False, 'italic': italic, 'serif': serif, 'width': 75}
    original = fitz.Font('helv')
    previous = fonts.fallback_font(style, tmp_path, original)
    current = fonts.fallback_font(style, tmp_path, original, classify_slab=True)
    assert current['base_sha256'] == fonts.sha256(ROOT / 'fonts' / base)
    assert current['sha256'] == previous['sha256']
    assert current['metric_calibration'] == previous['metric_calibration']
    assert current['axes'] == previous['axes']


def test_legacy_soho_policy_for_wisdom_stars_remains_serif(tmp_path):
    style = span_style({'font': 'SohoPro-Light', 'size': 12, 'color': 0})
    choice = fonts.fallback_font(style, tmp_path)
    assert choice['base_sha256'] == '351ced75f3851806aa6d846b669361521eb1925cfc530396df9c1a1b77061ddb'
    assert choice['name'].startswith('AstroSerif-')


def test_slab_provenance_matches_local_font_and_license():
    records = json.loads((ROOT / 'fonts/provenance.json').read_text())
    for name in ('RobotoSlab[wght].ttf', 'RobotoSlab-LICENSE.txt'):
        record = next(r for r in records if r['file'] == name)
        path = ROOT / 'fonts' / name
        assert record['sha256'] == fonts.sha256(path)
        assert record['bytes'] == path.stat().st_size
        assert record['license'] == 'Apache-2.0'


def test_slab_italic_uses_bitter_italic_only_when_slab_is_opted_in(tmp_path):
    """G-FONT-3: slab italics were RobotoSerif-Italic; legacy flag path is unchanged."""
    style = {'font': 'SohoPro-LightItalic', 'size': 12, 'color': 0, 'weight': 300,
             'bold': False, 'italic': True, 'serif': True, 'width': 75}
    original = fitz.Font('helv')
    legacy = fonts.fallback_font(style, tmp_path, original)
    assert legacy['base_sha256'] == fonts.sha256(ROOT/'fonts/RobotoSerif-Italic[GRAD,opsz,wdth,wght].ttf')
    slab = fonts.fallback_font(style, tmp_path, original, classify_slab=True)
    assert slab['base_sha256'] == fonts.sha256(ROOT/'fonts/Bitter-Italic[wght].ttf')
    assert slab['name'].startswith('AstroSlab-') and slab['axes'] == {'wght': 300}
    with TTFont(slab['path']) as static:
        assert static['OS/2'].fsSelection & 1 and static['head'].macStyle & 2
