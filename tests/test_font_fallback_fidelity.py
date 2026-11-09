"""G-FONT 1-4 (the-secrets-of-astrology): fallback fonts must paint Spanish.

Real cases: p45 «JUGUETÓN Y DIVERTIDO» (SohoPro-HeavyCompressed 31 pt,
line-height 0.839, edit box 365.40,40.85-516.44,98.36) lost the Ó acute because
MuPDF Story clips ink to the placement rectangle and puts the first baseline
0.8 em below it, while accented capitals rise ~0.9 em. p3 LilitaOne/RobotoSlab
subsets have empty outlines; Avenir/Futura/Serifa were classified by PDF flags.
"""
import json
from pathlib import Path

import numpy as np
import pymupdf as fitz
import pytest
from fontTools.ttLib import TTFont

from astro_translate import fidelity, fonts
from astro_translate.fidelity import span_style

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT/'books/the-secrets-of-astrology'
SPANISH = 'áéíóúüñÁÉÍÓÚÜÑ¡¿ºª«»“”‘’—–'
# Production edit_bbox of unit u0002 (fit at scale 0.98), grown right from the source bbox.
P45_BOX = fitz.Rect(365.40118408203125, 40.85038757324219, 516.4432067871094, 98.35935974121094)
P45_STYLE = {'font': 'SohoPro-HeavyCompressed', 'size': 31.0, 'color': 1907995, 'weight': 900,
             'bold': True, 'italic': False, 'serif': True, 'width': 60}
needs_book = pytest.mark.skipif(not (BOOK/'source.pdf').exists(), reason='book-local source.pdf absent')


def _unit(style, target, line_height=1.05, group='g'):
    return {'id': 'u', 'scale_group': group, 'style': style, 'target': target,
            'target_runs': [{'text': target, 'style': style}], 'line_height': line_height,
            'alignment': 'left', 'first_line_indent': 0}


def _render_plain(unit, choice, box, factor=1.0, page_size=(540, 666)):
    """Compose one unit on a blank page through fidelity's plain insertion path."""
    root = Path(choice['path']).parent
    font_map = {(unit['scale_group'], tuple(sorted(unit['style'].items()))): choice}
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    content, css, _ = fidelity._html_for(unit, font_map, root, factor)
    spare, _scale = fidelity._insert_plain(page, unit, box, factor, content, css, root)
    return doc, page, spare


def _ink_above(page, char, cap_ratio, dpi=300):
    """Ink pixels above the cap line inside the column of the first ``char``."""
    chars = [c for b in page.get_text('rawdict')['blocks'] for line in b.get('lines', [])
             for s in line['spans'] for c in s['chars']]
    hit = next(c for c in chars if c['c'] == char)
    size = next(s['size'] for b in page.get_text('rawdict')['blocks'] for line in b.get('lines', [])
                for s in line['spans'] if hit in s['chars'])
    x0, x1 = hit['bbox'][0], hit['bbox'][2]
    cap_top = hit['origin'][1]-cap_ratio*size
    clip = fitz.Rect(x0+.15*(x1-x0), cap_top-.45*size, x1-.15*(x1-x0), cap_top-.04*size)
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csGRAY)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return int(np.count_nonzero(a < 128))


def _cap_ratio(choice):
    with TTFont(choice['path']) as font:
        return font['OS/2'].sCapHeight/font['head'].unitsPerEm


@needs_book
def test_p45_title_paints_uppercase_acute_with_original_geometry(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc:
        original = fonts.original_font(doc, doc[44], 'SohoPro-HeavyCompressed', tmp_path/'original')
    assert not fonts.covers(original['font'], 'Ó')
    choice = fonts.fallback_font(P45_STYLE, tmp_path/'fallback', original['font'], classify_slab=True)
    unit = _unit(P45_STYLE, 'JUGUETÓN Y DIVERTIDO', line_height=0.8389991021925404)
    out, page, spare = _render_plain(unit, choice, P45_BOX, factor=.98)
    assert spare >= 0
    assert 'JUGUETÓN' in page.get_text()
    assert _ink_above(page, 'Ó', _cap_ratio(choice)) > 40  # acute visible above the O
    assert _ink_above(page, 'G', _cap_ratio(choice)) == 0  # same line, no accent
    out.close()


def test_headroom_keeps_baseline_and_fit_identical(tmp_path):
    """The fix must not move text: same baselines and same spare height."""
    style = {**P45_STYLE, 'font': 'RobotoSlab-Bold', 'width': 100}
    choice = fonts.fallback_font(style, tmp_path, classify_slab=True)
    unit = _unit(style, 'ÍNDICE ÚNICO ÁRBOL', line_height=.84)
    box = fitz.Rect(40, 60, 300, 140)
    root = Path(choice['path']).parent
    font_map = {('g', tuple(sorted(style.items()))): choice}
    content, css, _ = fidelity._html_for(unit, font_map, root, 1)
    plain = fitz.open(); p0 = plain.new_page(width=400, height=300)
    spare0, _ = p0.insert_htmlbox(box, content, css=css, archive=fitz.Archive(str(root)), scale_low=1)
    fixed = fitz.open(); p1 = fixed.new_page(width=400, height=300)
    spare1, _ = fidelity._insert_plain(p1, unit, box, 1, content, css, root)
    origins = lambda p: [v for b in p.get_text('rawdict')['blocks'] for line in b.get('lines', [])
                         for s in line['spans'] for c in s['chars'] for v in c['origin']]
    assert spare1 == pytest.approx(spare0, abs=.01)
    assert origins(p1) == pytest.approx(origins(p0), abs=.01)
    assert _ink_above(p1, 'Í', _cap_ratio(choice)) > 20
    assert _ink_above(p0, 'Í', _cap_ratio(choice)) == 0  # the old clipping, reproduced
    plain.close(); fixed.close()


FAMILY_CASES = [
    # name, weight, italic, serif flag, width, expected base file
    ('SohoPro-Light', 300, False, True, 100, 'RobotoSlab[wght].ttf'),
    ('SohoPro-LightCondensed', 300, False, True, 75, 'RobotoSlab[wght].ttf'),
    ('SohoPro-BoldCompressed', 700, False, True, 60, 'RobotoSlab[wght].ttf'),
    ('SohoPro-Italic', 400, True, True, 100, 'Bitter-Italic[wght].ttf'),
    ('SohoPro-LightItalic', 300, True, True, 100, 'Bitter-Italic[wght].ttf'),
    ('SerifaStd-Light', 300, False, True, 100, 'RobotoSlab[wght].ttf'),
    ('AvenirNextLTPro-Bold', 700, False, True, 100, 'NotoSans[wdth,wght].ttf'),
    ('FuturaLTPro-MediumCond', 500, False, True, 75, 'NotoSans[wdth,wght].ttf'),
    ('FrutigerLTStd-Bold', 700, False, False, 100, 'NotoSans[wdth,wght].ttf'),
    ('LilitaOne', 900, False, False, 100, 'LilitaOne-Regular.ttf'),
    ('Times-Roman', 400, False, True, 100, 'RobotoSerif[GRAD,opsz,wdth,wght].ttf'),
]


@pytest.mark.parametrize('name,weight,italic,serif,width,base', FAMILY_CASES)
def test_every_fallback_family_paints_capital_i_acute_and_ordinal(tmp_path, name, weight, italic, serif, width, base):
    style = {'font': name, 'size': 24, 'color': 0, 'weight': weight, 'bold': weight >= 600,
             'italic': italic, 'serif': serif, 'width': width}
    choice = fonts.fallback_font(style, tmp_path, classify_slab=True)
    assert choice['base_sha256'] == fonts.sha256(ROOT/'fonts'/base)
    assert fonts.covers(choice['font'], SPANISH)
    assert fonts.glyph_ink_coverage(choice, SPANISH)
    unit = _unit(style, 'ÍO 1º 2ª', line_height=.9)
    out, page, spare = _render_plain(unit, choice, fitz.Rect(40, 80, 400, 200))
    assert spare >= 0
    text = page.get_text()
    assert 'ÍO' in text and 'º' in text and 'ª' in text
    ratio = _cap_ratio(choice)
    assert _ink_above(page, 'Í', ratio) > 10
    assert _ink_above(page, 'O', ratio) == 0
    out.close()


@pytest.mark.parametrize('name,serif,expected', [
    ('AvenirNextLTPro-Bold', True, 'sans'),
    ('AvenirNextLTPro-Medium', True, 'sans'),
    ('FuturaLTPro-MediumCond', True, 'sans'),
    ('SerifaStd-Bold', True, 'slab'),
    ('SerifaStd-Light', False, 'slab'),
    ('LilitaOne', False, 'display'),
])
def test_known_family_names_override_pdf_serif_flags(name, serif, expected):
    assert fonts.fallback_class({'font': name, 'serif': serif}) == expected


def test_known_names_are_opt_in_for_fallback_selection(tmp_path):
    """astrology-wisdom-stars (classify_slab=false) keeps its validated Futura choice."""
    style = {'font': 'FuturaCom-MediumCondensed', 'size': 9, 'color': 0, 'weight': 500, 'bold': False,
             'italic': False, 'serif': True, 'width': 75}
    families = {'sohogothic': 'sans', 'sohopro': 'serif', 'dinnext': 'sans', 'frutiger': 'sans'}
    legacy = fonts.fallback_font(style, tmp_path, families=families)
    assert legacy['base_sha256'] == fonts.sha256(ROOT/'fonts/RobotoSerif[GRAD,opsz,wdth,wght].ttf')
    current = fonts.fallback_font(style, tmp_path, families=families, classify_slab=True)
    assert current['base_sha256'] == fonts.sha256(ROOT/'fonts/NotoSans[wdth,wght].ttf')


def test_calibrated_files_carry_their_family_prefix(tmp_path):
    style = {'font': 'FrutigerLTStd-Bold', 'size': 9, 'color': 0, 'weight': 700, 'bold': True,
             'italic': False, 'serif': False, 'width': 100}
    choice = fonts.fallback_font(style, tmp_path, fitz.Font('helv'), classify_slab=True)
    assert Path(choice['path']).name.startswith('AstroSans-')
    assert choice['name'].startswith('AstroSans-')


@pytest.mark.parametrize('name', ['Bitter-Italic[wght].ttf', 'Bitter-OFL.txt',
                                  'LilitaOne-Regular.ttf', 'LilitaOne-OFL.txt'])
def test_new_fonts_have_verifiable_provenance(name):
    records = json.loads((ROOT/'fonts/provenance.json').read_text())
    record = next(r for r in records if r['file'] == name)
    path = ROOT/'fonts'/name
    assert record['url'].startswith('https://raw.githubusercontent.com/google/fonts/main/ofl/')
    assert record['sha256'] == fonts.sha256(path)
    assert record['bytes'] == path.stat().st_size
    assert record['license'] == 'SIL Open Font License 1.1'


def test_reserved_font_name_is_not_kept_in_derived_display_font(tmp_path):
    style = {'font': 'LilitaOne', 'size': 30, 'color': 0, 'weight': 900, 'bold': True,
             'italic': False, 'serif': False, 'width': 100}
    choice = fonts.fallback_font(style, tmp_path, classify_slab=True)
    with TTFont(choice['path']) as font:
        names = [r.toUnicode() for r in font['name'].names if r.nameID in (1, 3, 4, 6, 16, 17, 18, 21, 22, 25)]
    assert names and not any('lilita' in n.lower() for n in names)
    assert choice['name'].startswith('AstroDisplay-')


@needs_book
def test_p3_empty_outline_subsets_use_exact_libre_families(tmp_path):
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[2]
        spans = [s for b in page.get_text('dict')['blocks'] for line in b.get('lines', [])
                 for s in line['spans'] if s['text'].strip()]
        cases = {'LilitaOne': 'LilitaOne-Regular.ttf', 'RobotoSlab-Regular': 'RobotoSlab[wght].ttf'}
        for name, base in cases.items():
            span = next(s for s in spans if s['font'].endswith(name))
            style = {**span_style(span)}
            original = fonts.original_font(doc, page, span['font'], tmp_path/'original')
            target = 'ASTROLOGÍA ¿Sí? Ñ'
            assert fonts.glyph_ink_coverage(original, span['text'])  # source glyphs are inked
            assert not (fonts.covers(original['font'], target) and fonts.glyph_ink_coverage(original, target))
            units = [{'scale_group': name, 'style': style, 'target': target}]
            mapping, audit = fonts._select_group_fonts(doc, page, units, tmp_path, classify_slab=True)
            choice = next(iter(mapping.values()))
            assert choice['base_sha256'] == fonts.sha256(ROOT/'fonts'/base)
            assert audit[0]['group_fallback']
            assert audit[0]['fallback_verification']['ok'], audit[0]['fallback_verification']


@needs_book
@pytest.mark.parametrize('pdf_page,name', [(13, 'SohoPro-LightCompressed'), (13, 'SohoPro-LightCondensed'),
                                           (45, 'SohoPro-HeavyCompressed'), (19, 'SohoPro-Light')])
def test_weight_calibration_restores_stem_thickness_of_narrowed_slab(tmp_path, pdf_page, name):
    """Horizontal compression thins vertical stems; wght compensates to the source stem."""
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[pdf_page-1]
        span = next(s for b in page.get_text('dict')['blocks'] for line in b.get('lines', [])
                    for s in line['spans'] if s['font'].endswith(name))
        style = span_style(span)
        if 'heavy' in name.lower():
            style.update(weight=900, bold=True)
        original = fonts.original_font(doc, page, span['font'], tmp_path/'original')
    plain = fonts.fallback_font(style, tmp_path/'plain', original['font'], classify_slab=True)
    tuned = fonts.fallback_font(style, tmp_path/'tuned', original['font'], classify_slab=True, calibrate_weight=True)
    evidence = tuned['weight_calibration']
    assert evidence['characters']
    source_stem = evidence['source_stem_em']
    plain_stem = fonts.stem_width_em(plain['font'], evidence['characters'])
    tuned_stem = fonts.stem_width_em(tuned['font'], evidence['characters'])
    assert abs(tuned_stem-source_stem) <= abs(plain_stem-source_stem)+1e-6
    if not evidence['clamped']:
        assert tuned_stem == pytest.approx(source_stem, rel=.08)
    # Width calibration still holds after the weight change.
    cal = tuned['metric_calibration']
    assert cal['chosen_advance_em'] == pytest.approx(cal['source_advance_em'], rel=.003)
    with TTFont(tuned['path']) as static:
        assert static['OS/2'].usWeightClass == style['weight']  # declared style unchanged


def test_effective_weight_reads_family_weight_names():
    w = lambda name, weight=400: fonts.effective_weight({'font': name, 'weight': weight})
    assert w('SohoPro-ExtraLight', 300) == 200
    assert w('SohoPro-Heavy') == 900 and w('SohoPro-HeavyCompressed') == 900
    assert w('Foo-Black') == 900 and w('Foo-Thin') == 100 and w('Foo-SemiBold') == 600
    assert w('SohoPro-LightCondensed', 300) == 300 and w('FrutigerLTPro-BoldCn', 700) == 700
    assert w('SohoPro-Regular') == 400 and w('LilitaOne', 900) == 900


def test_selection_audit_verifies_style_equivalence(tmp_path):
    style = {'font': 'SohoPro-LightItalic', 'size': 10, 'color': 0, 'weight': 300, 'bold': False,
             'italic': True, 'serif': True, 'width': 100}
    choice = fonts.fallback_font(style, tmp_path, classify_slab=True)
    ok = fonts.verify_fallback(choice, style, 'Él vivía aquí, ¿sí?', classify_slab=True)
    assert ok['ok'] and ok['checks']['italic'] and ok['checks']['class'] == 'slab'
    wrong = fonts.fallback_font({**style, 'italic': False}, tmp_path, classify_slab=True)
    bad = fonts.verify_fallback(wrong, style, 'Él vivía aquí', classify_slab=True)
    assert not bad['ok'] and 'italic' in bad['problems']


def test_pseudo_report_separates_verified_fallbacks_from_defects(tmp_path):
    from astro_translate import pseudo
    folder = tmp_path/'compose'
    folder.mkdir()
    base = {'original_missing': ['á'], 'group_fallback': True, 'original_empty_glyph_outline': False,
            'original_text_roundtrip_failed': False, 'chosen': {'path': '/x/AstroSlab-1.ttf', 'source': 'fallback'}}
    audits = [{**base, 'scale_group': 'p0005_body', 'original_name': 'SohoPro-Light',
               'fallback_verification': {'ok': True, 'checks': {'base': 'RobotoSlab[wght].ttf'}, 'problems': []}},
              {**base, 'scale_group': 'p0005_caption', 'original_name': 'SohoPro-Italic',
               'fallback_verification': {'ok': False, 'checks': {}, 'problems': ['italic']}},
              {**base, 'scale_group': 'p0005_legacy', 'original_name': 'SohoPro-Bold'}]
    (folder/'p0005-fonts.json').write_text(json.dumps(audits))
    groups = {(g['code'], g['cause'].split(':')[0]): g for g in pseudo.mechanical_groups([{'page': 5, 'status': 'composed'}], folder)}
    assert groups[('font_fallback_applied', 'SohoPro-Light')]['action'] == 'none'
    assert groups[('font_fallback', 'SohoPro-Italic')]['details'][0]['problems'] == ['italic']
    assert ('font_fallback', 'SohoPro-Bold') in groups  # audits without evidence stay defects
    assert 'font_fallback_applied' in pseudo.INFORMATIVE_CODES


@needs_book
def test_space_calibration_matches_source_word_space(tmp_path):
    """SohoPro-Medium space is 0.196 em; the calibrated RobotoSlab space was 0.259 em."""
    with fitz.open(BOOK/'source.pdf') as doc:
        page = doc[11]
        span = next(s for b in page.get_text('dict')['blocks'] for line in b.get('lines', [])
                    for s in line['spans'] if s['font'].endswith('SohoPro-Medium'))
        style = span_style(span)
        original = fonts.original_font(doc, page, span['font'], tmp_path/'original')
    source_space = original['font'].text_length(' ', fontsize=1)
    plain = fonts.fallback_font(style, tmp_path/'plain', original['font'], classify_slab=True)
    tuned = fonts.fallback_font(style, tmp_path/'tuned', original['font'], classify_slab=True, calibrate_space=True)
    assert plain['font'].text_length(' ', fontsize=1) > source_space*1.2
    assert tuned['font'].text_length(' ', fontsize=1) == pytest.approx(source_space, abs=.001)
    assert tuned['font'].text_length('\u00a0', fontsize=1) == pytest.approx(source_space, abs=.001)
    assert tuned['metric_calibration']['source_space_em'] == pytest.approx(source_space, abs=1e-4)
    # Letters keep the horizontal calibration.
    for choice in (plain, tuned):
        cal = choice['metric_calibration']
        assert cal['chosen_advance_em'] == pytest.approx(cal['source_advance_em'], rel=.003)


def test_display_quote_left_aligned_keeps_capital_accents(tmp_path):
    """p9-type display quote (non-centered path of display_quotes.overlay)."""
    from astro_translate import display_quotes
    style = {**P45_STYLE, 'font': 'SohoPro-HeavyCompressed', 'size': 30}
    choice = fonts.fallback_font(style, tmp_path, classify_slab=True)
    unit = {**_unit(style, 'ÁRBOL ÚNICO', line_height=.84), 'display_quote': True, 'alignment': 'left'}
    font_map = {('g', tuple(sorted(style.items()))): choice}
    with fitz.open() as src:
        page = src.new_page(width=540, height=666)
        out, finished = display_quotes.overlay(page, unit, fitz.Rect(60, 120, 400, 220), 1, font_map)
    assert finished
    assert _ink_above(out[0], 'Á', _cap_ratio(choice)) > 10
    out.close()
