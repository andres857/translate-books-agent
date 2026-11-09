"""`survey`: offline structural inventory and page archetypes; never inference."""
import json
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import llm, survey

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT/'books/the-secrets-of-astrology'
LOREM = ('Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor '
         'incididunt ut labore et dolore magna aliqua ut enim ad minim veniam quis nostrud ')*6


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('survey must not call run_task')
    monkeypatch.setattr(llm, 'run_task', fail)


def make_book(tmp_path, *builders):
    book = tmp_path/'book'
    (book/'work').mkdir(parents=True)
    doc = fitz.open()
    for build in builders:
        build(doc.new_page(width=612, height=792))
    doc.save(book/'source.pdf')
    return book


def two_columns(page):
    page.insert_textbox(fitz.Rect(72, 120, 290, 700), LOREM, fontname='helv', fontsize=10)
    page.insert_textbox(fitz.Rect(320, 120, 540, 700), LOREM, fontname='helv', fontsize=10)


def contents(page):
    page.insert_text((300, 60), 'Contents', fontname='helv', fontsize=20)
    for i, title in enumerate(['Beginnings', 'The signs', 'The planets', 'The houses', 'Aspects', 'Further reading']):
        y = 150 + i*24
        page.insert_text((300, y), title, fontname='helv', fontsize=12)
        folio = str(10 + i*17)
        page.insert_text((520 - fitz.get_text_length(folio, 'helv', 12), y), folio, fontname='helv', fontsize=12)


def coloured_panel(page):
    page.draw_rect(fitz.Rect(60, 100, 550, 260), color=None, fill=(0.05, 0.15, 0.45))
    page.insert_textbox(fitz.Rect(72, 120, 540, 250), 'White words on a dark blue panel need contrast.',
                        fontname='helv', fontsize=14, color=(1, 1, 1))


def vertical_label(page):
    page.insert_text((300, 500), 'Rotated margin label', fontname='helv', fontsize=11, rotate=90)
    page.insert_textbox(fitz.Rect(72, 100, 540, 200), 'A short paragraph of ordinary text.', fontname='helv', fontsize=11)


def scanned(page):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 80), False)
    pix.set_rect(pix.irect, (230, 225, 210))
    page.insert_image(page.rect, pixmap=pix)


def field_table(page):
    rows = [('Ruler', 'Mars'), ('Element', 'Fire'), ('Quality', 'Cardinal'), ('Symbol', 'Ram'),
            ('Colour', 'Red'), ('Body', 'Head')]
    html = '<br>'.join(f'{label} <b>{value}</b>' for label, value in rows)
    page.insert_htmlbox(fitz.Rect(72, 200, 300, 400), f'<p style="font-family:sans-serif;font-size:10pt">{html}</p>')


def tags(result, number):
    return set(next(p for p in result['pages'] if p['page'] == number)['tags'])


def test_detects_columns_toc_panels_rotation_rows_and_scans(tmp_path):
    book = make_book(tmp_path, two_columns, contents, coloured_panel, vertical_label, scanned, field_table)
    result = survey.survey_book(book)
    assert 'columns' in tags(result, 1)
    page1 = result['pages'][0]
    assert page1['columns']['count'] == 2 and len(page1['columns']['stories']) == 1
    assert 'toc' in tags(result, 2) and result['pages'][1]['toc']['rows'] >= 4
    assert 'colored_panel' in tags(result, 3)
    block = result['pages'][2]['colored_blocks'][0]
    assert block['background_source'] == 'vector_fill' and block['contrast_ratio'] > 7
    assert block['heuristic'] is True
    assert 'rotated_text' in tags(result, 4) and result['pages'][3]['rotated']['vertical'] >= 1
    assert tags(result, 5) == {'scanned'} and result['pages'][4]['route'] == 'C'
    assert result['pages'][4]['manifest'] is False
    assert 'field_rows' in tags(result, 6) and result['pages'][5]['field_rows']['rows'] >= 5
    assert result['llm_calls'] == 0


def test_writes_atomic_survey_and_groups_identical_pages(tmp_path):
    book = make_book(tmp_path, two_columns, contents, two_columns, scanned, two_columns)
    result = survey.survey_book(book)
    saved = json.loads((book/'work/structure/survey.json').read_text())
    assert saved['archetypes'] == result['archetypes']
    by_signature = {a['signature']: a for a in result['archetypes']}
    columns = next(a for a in result['archetypes'] if 1 in a['pages'])
    assert columns['pages'] == [1, 3, 5] and columns['representative_pages'][0] == 1
    assert set(columns['representative_pages']) <= {1, 3, 5} and len(columns['representative_pages']) <= 2
    assert by_signature['scanned']['pages'] == [4]
    # Deterministic ids and ordering: rerunning yields identical archetypes.
    assert survey.survey_book(book, write=False)['archetypes'] == result['archetypes']
    assert [a['id'] for a in result['archetypes']] == [f'A{i:02d}' for i in range(1, len(result['archetypes'])+1)]
    assert not list((book/'work/structure').glob('.*'))


def test_font_without_accents_is_a_fallback_risk(tmp_path):
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    font = instantiateVariableFont(TTFont(ROOT/'fonts/NotoSans[wdth,wght].ttf'), {'wdth': 100, 'wght': 400})
    subsetter = subset.Subsetter(subset.Options())
    subsetter.populate(text='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ ')
    subsetter.subset(font)
    path = tmp_path/'ascii.ttf'
    font.save(path)

    def ascii_page(page):
        page.insert_font(fontname='asc', fontfile=str(path))
        page.insert_text((72, 100), 'Plain English words only', fontname='asc', fontsize=12)
        page.insert_text((72, 140), 'Builtin face', fontname='helv', fontsize=12)

    result = survey.survey_book(make_book(tmp_path, ascii_page))
    risk = next(r for r in result['font_risks'] if r['font'] == 'NotoSans-Regular')
    assert risk['fallback_required'] is True and risk['pages'] == [1]
    assert set(risk['missing']) == set(survey.ACCENT_PROBE)
    assert risk['fallback_class'] == 'sans'
    entry = next(f for f in result['pages'][0]['fonts'] if f['name'] == 'NotoSans-Regular')
    assert entry['class'] == 'sans' and entry['coverage']['missing'] == risk['missing']


def test_cli_survey_subcommand(tmp_path, capsys):
    from astro_translate.__main__ import main
    book = make_book(tmp_path, two_columns)
    assert main(['survey', '--book', str(book)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['pages'] == 1 and summary['survey'].endswith('work/structure/survey.json')
    assert summary['llm_calls'] == 0


secrets = pytest.mark.skipif(not (SECRETS/'source.pdf').is_file(), reason='Secrets source PDF not available')


@secrets
def test_secrets_real_structure_patterns():
    pages = [5, 6, 7, 12, 13, 14, 15, 16, 17, 18, 19, 20, 26, 27, 28, 29, 30, 31, 38, 42, 46, 50, 54]
    result = survey.survey_book(SECRETS, pages=','.join(map(str, pages)), write=False)
    page = {p['page']: p for p in result['pages']}
    for n in (5, 6, 7):
        assert 'toc' in page[n]['tags'], n
    for n in range(12, 21):
        assert page[n]['columns']['stories'], n
    assert sum(page[n]['columns']['count'] >= 2 for n in range(12, 21)) >= 6
    for n in (38, 42, 46, 50, 54):
        assert 'field_rows' in page[n]['tags'], n
    assert len({page[n]['signature'] for n in (38, 42, 46, 50, 54)}) == 1
    for n in range(26, 32):
        assert 'curved_text' in page[n]['tags'], n
    assert result['llm_calls'] == 0


@secrets
def test_secrets_route_c_pages_are_scanned_archetype():
    # Gap 8: chapter openings (visible native titles over full-page art) are
    # mixed; only the 13 text-less rasters remain route C. The survey uses the
    # current classifier and flags manifest rows that still need re-triage.
    manifest = json.loads((SECRETS/'work/manifest.json').read_text())
    route_c = [r['page'] for r in manifest['pages'] if r['route'] == 'C']
    openings = [11, 25, 85, 137, 167]
    real = [1, 8, 10, 24, 32, 58, 84, 96, 110, 136, 152, 166, 184]
    assert set(route_c) in (set(real), set(real) | set(openings))
    result = survey.survey_book(SECRETS, pages=','.join(map(str, real+openings+[12])), write=False, ocr=False)
    scanned_pages = [p['page'] for p in result['pages'] if p['tags'] == ['scanned']]
    assert scanned_pages == real
    archetype = next(a for a in result['archetypes'] if a['signature'] == 'scanned')
    assert archetype['pages'] == real
    pages = {p['page']: p for p in result['pages']}
    for n in openings:
        assert pages[n]['route'] == 'A' and pages[n]['type'] == 'mixed'
        assert pages[n]['retriage_pending'] == (n in route_c)
    assert result['retriage_pending'] == sorted(set(openings) & set(route_c))


@secrets
def test_secrets_chart_callouts_and_leader_lines():
    result = survey.survey_book(SECRETS, pages='169', write=False)
    page = result['pages'][0]
    assert 'callouts' in page['tags'] and len(page['callouts']) >= 6
    assert sum(c['leader_lines'] > 0 for c in page['callouts']) >= 5
    assert all(c['leader_heuristic'] for c in page['callouts'])


@secrets
def test_secrets_gap9_detectors_in_survey():
    result = survey.survey_book(SECRETS, pages='13,47,73,89,179,180,182', write=False, ocr=False)
    page = {p['page']: p for p in result['pages']}
    assert 'display_quote' in page[13]['tags']
    assert page[13]['display_quotes'][0]['quote_marks'] == 'vector'
    for n in (47, 73):
        assert 'callouts' in page[n]['tags'] and 'columns' not in page[n]['tags'], n
        assert sum(c['figure_legend'] for c in page[n]['callouts']) >= 3
    assert 'curved_text' not in page[89]['tags'] and 'rotated_text' in page[89]['tags']
    assert page[89]['rotated']['straight'] == 8 and page[89]['rotated']['curved_fragments'] == 0
    for n in (179, 180, 182):
        assert 'table' in page[n]['tags'] and page[n]['tables'], n


@secrets
def test_secrets_textless_illustrations_are_not_raster_text():
    pytest.importorskip('paddleocr')
    result = survey.survey_book(SECRETS, pages='1,8', write=False)
    page = {p['page']: p for p in result['pages']}
    if page[8]['raster_text_probe']['contains_text'] is None:
        pytest.skip('local OCR unavailable')
    assert [i['contains_text'] for i in page[8]['raster']['images']] == [False]
    assert 'raster_text' not in page[8]['tags']
    assert page[1]['raster']['images'][0]['contains_text'] is True and 'raster_text' in page[1]['risk_tags']


@secrets
def test_secrets_back_matter_tags():
    result = survey.survey_book(SECRETS, pages='186,188,192', write=False, ocr=False)
    page = {p['page']: p for p in result['pages']}
    assert 'glossary' in page[186]['tags'] and 'index' in page[188]['tags']
    assert page[192]['back_matter'] is None and not {'index', 'glossary'} & set(page[192]['tags'])
