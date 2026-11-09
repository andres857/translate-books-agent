"""Defects of the Sonnet pilot, batch 17 audit (work/audit/batch-17.json).

Real pages of ``books/the-secrets-of-astrology/source.pdf`` composed with their saved Sonnet
translation (``work/production/p<NNNN>-translation.json``) in a private snapshot book, or the saved
production QA of the audited candidate. No LLM call: translations are read, never produced.
"""
from __future__ import annotations

import copy
import json

import pymupdf as fitz
import pytest

from test_composition_gaps import SECRETS, secrets

PRODUCTION = SECRETS/'work/production'


def _saved_qa(page_number):
    qa_path, fit_path = (PRODUCTION/f'p{page_number:04d}-{name}.json' for name in ('qa', 'fit'))
    if not (qa_path.is_file() and fit_path.is_file()):
        pytest.skip('Saved production QA not available')
    return json.loads(qa_path.read_text()), json.loads(fit_path.read_text())


def _finish(tmp_path, page_number, qa, fit, regions):
    from types import SimpleNamespace
    from astro_translate.production import Production
    saved = {}
    runner = SimpleNamespace(book=SECRETS, review_for=lambda *a: None, path=lambda n, s: tmp_path/s,
                             update=lambda n, **v: saved.update(v), queue=lambda *a: None,
                             checkpoint=lambda *a, **k: None)
    return Production.finish_qa(runner, page_number, fit, qa, regions, 'dep'), qa


# 1. p89/p93: keyword ribbons «PURPOSE | CREATIVE POWER | PRIDE» (FuturaLTPro-MediumCond, slightly rotated,
# direction ≈ (0.999, 0.048)) are deferred to Phase 4 per word. The «|» separators between them
# (FrutigerLTPro-Condensed, p89 x≈336.8 and 356.0; p93 x≈344) fall between the registered word regions.
# They carry no letters: they are not untranslated text and must not keep the page out of partial_deferred.
@secrets
@pytest.mark.parametrize('page_number', [89, 93])
def test_separator_bars_between_deferred_ribbons_are_not_untranslated_text(page_number, tmp_path):
    from astro_translate.production import uncovered_deferred_text
    qa, fit = _saved_qa(page_number)
    regions = copy.deepcopy(qa['deferred_regions'])
    boxes = [fitz.Rect(r['bbox'])+(-.5, -.5, .5, .5) for r in regions]
    bars = [i for i in qa['omitted_rotated_text'] if i['text'].strip() == '|'
            and not any(b.contains(fitz.Rect(i['bbox'])) for b in boxes)]
    assert bars, 'saved QA no longer lists separator spans outside the deferred regions'
    assert uncovered_deferred_text(qa, regions) == []
    status, report = _finish(tmp_path, page_number, copy.deepcopy(qa), fit, regions)
    assert status == 'partial_deferred', report['production_reasons']
    assert 'untranslated_text_outside_deferred_regions' not in report['production_reasons']
    # A real word outside every region still blocks partial_deferred.
    word = {**bars[0], 'text': 'PRIDE'}
    qa = {**copy.deepcopy(qa), 'omitted_rotated_text': qa['omitted_rotated_text']+[word]}
    status, report = _finish(tmp_path, page_number, qa, fit, copy.deepcopy(regions))
    assert status == 'review'
    assert [i['text'] for i in report['untranslated_outside_deferred']] == ['PRIDE']


# ------------------------------------------------------------------ helpers: real composition

def _translation(page_number):
    return PRODUCTION/f'p{page_number:04d}-translation.json'


def saved(page_number):
    return pytest.mark.skipif(not _translation(page_number).is_file(), reason='Saved Sonnet translation not available')


def _saved_fit(page_number, tmp_path):
    """Real page, saved Sonnet payload, real reinsertion in a private snapshot book."""
    from test_batch16_composition import _saved_fit as compose
    return compose(page_number, tmp_path)


def _audited_candidate(page_number):
    """Saved production candidate, only while it is still the one audited in batch-17."""
    from astro_translate.core import sha256
    fit_path = PRODUCTION/f'p{page_number:04d}-fit.json'
    audit_path = SECRETS/'work/audit/batch-17.json'
    if not (fit_path.is_file() and audit_path.is_file()):
        pytest.skip('Audited candidate not available')
    fit = json.loads(fit_path.read_text())
    audited = {p['page']: p['candidate_sha256'] for p in json.loads(audit_path.read_text())['pages']}
    if not fit.get('output') or not (PRODUCTION/f'p{page_number:04d}-translated.pdf').is_file() \
            or sha256(fit['output']) != audited.get(page_number):
        pytest.skip('Production candidate recomposed after the audit')
    return fit


def _clearance(page, upper_clip, lower_clip):
    """Smallest real glyph-ink gap from glyphs in ``lower_clip`` up to glyphs in ``upper_clip``."""
    from astro_translate.line_clearance import glyph_records, smallest_gap
    records = glyph_records(page)
    inside = lambda clip: [r for r in records if fitz.Rect(clip).contains(fitz.Point(*r['origin']))]
    found = smallest_gap(inside(lower_clip), inside(upper_clip))
    assert found is not None
    return found[0]


# 2. p37 title u0003 «BRAVE & / FEARLESS» (SohoPro-HeavyCompressed 31 pt, 365.6,41.6–478.3,98.6; source leading
# 26 pt = 0.839 em, unaccented capitals, ink gap ≥2.17 pt). Spanish «VALIENTE E / INTRÉPIDO» in AstroSlab Bold at
# 30.4 pt kept that leading: the tilde of «É» (ink 417.5,60.2) rises over the base of «E»/«N» of line 1
# (baseline 62.0): −1.7 pt. p46: «CÁNCER» (SohoPro-Bold 50 pt → 48 pt, u0003) under «EL 4.º SIGNO DEL ZODÍACO»
# (baseline 95.7): the tilde of «Á» ends 0.26 pt below the base of «SIGNO» (glyph bounds; 0.48 pt in pixels).
@secrets
@pytest.mark.parametrize('page_number,unit_id', [(37, 'u0003'), (46, 'u0003')])
def test_local_qa_flags_accent_touching_the_line_above(page_number, unit_id):
    from astro_translate import fidelity
    fit = _audited_candidate(page_number)
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        issues = fidelity.composition_line_issues(src[page_number-1], doc[fit.get('output_page', 0)], fit['blocks'])
    found = [i for i in issues if i['code'] == 'line_ink_collision']
    assert [i['unit_id'] for i in found] == [unit_id], issues
    assert found[0]['lower'] in 'ÉÁ'


@secrets
@saved(37)
def test_accented_display_title_clears_the_line_above_p37(tmp_path):
    from astro_translate import fidelity
    fit = _saved_fit(37, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    title = next(b for b in fit['blocks'] if b['id'] == 'u0003')
    assert title['target'] == 'VALIENTE E INTRÉPIDO'
    assert title['line_height'] > title['accent_leading']['source_line_height']
    assert title['scale'] >= .8
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 36)]
        gap = _clearance(page, (360, 30, 500, 70), (360, 70, 500, 100))
        assert gap >= 1.0, gap
        # Still two lines, inside the title area, above the body text.
        lines = [l for b in page.get_text('dict', clip=fitz.Rect(360, 30, 520, 99))['blocks'] for l in b.get('lines', [])
                 if any(s['size'] > 20 for s in l['spans'])]
        assert [''.join(s['text'] for s in l['spans']).strip() for l in lines] == ['VALIENTE E', 'INTRÉPIDO']
        assert fidelity.composition_line_issues(src[36], page, fit['blocks']) == []


@secrets
@saved(46)
def test_accented_display_word_clears_the_heading_above_p46(tmp_path):
    from astro_translate import fidelity
    fit = _saved_fit(46, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    word = next(b for b in fit['blocks'] if b['id'] == 'u0003')
    assert word['target'] == 'CÁNCER' and word['scale'] >= .8
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 45)]
        gap = _clearance(page, (30, 80, 260, 99), (30, 99, 260, 150))
        assert gap >= .04*50*word['scale']-.1, gap  # 0.04 em; was 0.26 pt
        assert [i for i in fidelity.composition_line_issues(src[45], page, fit['blocks'])
                if i['code'] == 'line_ink_collision'] == []


# 3. p176 title u0006 «THE WHEEL OF / RULERSHIP» (SohoPro-Light/Bold 48 pt, ink 37.9–367.3). The page is a
# full-page raster: the blue rule under the title (y≈187–189.5) ends at x≈372.5 and a light-blue side panel
# starts at x≈388.5. «LA RUEDA DE LAS / REGENCIAS» grew through a +24 pt box (size ≥30 growth of
# shifted_candidates) to ink x=386.4, 13 pt past the rule and 2 pt from the panel; margins QA said OK.
@secrets
def test_rule_and_panel_limits_found_in_the_raster_p176():
    from astro_translate.rule_limits import horizontal_limits
    fit = json.loads((PRODUCTION/'p0176-fit.json').read_text()) if (PRODUCTION/'p0176-fit.json').is_file() else None
    if fit is None:
        pytest.skip('Saved p176 composition not available')
    unit = next(b for b in fit['blocks'] if b['source'] == 'THE WHEEL OF RULERSHIP')
    with fitz.open(SECRETS/'source.pdf') as src:
        left, right, ink, evidence = horizontal_limits(src[175], unit)
    assert ink.x1 == pytest.approx(367.3, abs=.2)
    assert right == pytest.approx(372.5, abs=.5)
    assert {e['kind'] for e in evidence} == {'rule', 'panel'}
    assert any(e.get('x0') == pytest.approx(388.5, abs=.5) for e in evidence)


@secrets
def test_local_qa_flags_title_past_the_rule_p176():
    from astro_translate import fidelity
    fit = _audited_candidate(176)
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        issues = fidelity.composition_line_issues(src[175], doc[fit.get('output_page', 0)], fit['blocks'])
    found = [i for i in issues if i['code'] == 'rule_or_panel_overflow']
    assert [i['unit_id'] for i in found] == ['u0006']
    assert found[0]['ink_x'][1] > 386


@secrets
@saved(176)
def test_display_title_growth_stops_at_the_rule_p176(tmp_path):
    from astro_translate import fidelity
    fit = _saved_fit(176, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    title = next(b for b in fit['blocks'] if b['source'] == 'THE WHEEL OF RULERSHIP')
    assert title['target'] == 'LA RUEDA DE LAS REGENCIAS'
    assert fitz.Rect(title['edit_bbox']).x1 <= 373
    assert title['scale'] >= .8
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 175)]
        glyphs = [r for r in __import__('astro_translate.line_clearance', fromlist=['x']).glyph_records(page)
                  if r['size'] > 30 and r['origin'][1] < 200]
        assert glyphs and max(r['ink'].x1 for r in glyphs) <= 372.5+.5
        assert [i for i in fidelity.composition_line_issues(src[175], page, fit['blocks'])
                if i['code'] in ('rule_or_panel_overflow', 'line_ink_collision')] == []


# 4a. p138/p139 house wheel: «1» + raised «st» (SohoPro-Bold 25.8 pt, baseline 293.85 at x=383.5; suffix
# 15.04 pt, origin 399.14,283.26). «.ª» was drawn whole at the raised suffix origin: the point floated at
# mid-height of the numeral («1·ª»). The point belongs on the numeral's baseline; only «ª» is raised.
def _ordinal_glyphs(page, block):
    data = block['native_ordinal']
    clip = fitz.Rect(block['edit_bbox'])+(-.5, -.5, .5, .5)
    chars = [(c['c'], c['origin']) for b in page.get_text('rawdict', clip=clip)['blocks'] for l in b.get('lines', [])
             for s in l['spans'] for c in s['chars']]
    return data, dict(chars)


@secrets
@pytest.mark.parametrize('page_number', [138, 139])
def test_local_qa_flags_raised_ordinal_point(page_number):
    from astro_translate import fidelity
    fit = _audited_candidate(page_number)
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        issues = fidelity.composition_line_issues(src[page_number-1], doc[fit.get('output_page', 0)], fit['blocks'])
    raised = {i['unit_id'] for i in issues if i['code'] == 'ordinal_point_off_baseline'}
    wheel = {b['id'] for b in fit['blocks'] if b.get('native_ordinal', {}).get('kind') == 'house_diagram'}
    assert wheel and raised == wheel


@secrets
@saved(138)
def test_ordinal_point_sits_on_the_numeral_baseline_p138(tmp_path):
    from astro_translate import fidelity
    fit = _saved_fit(138, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    wheel = [b for b in fit['blocks'] if b.get('native_ordinal', {}).get('kind') == 'house_diagram']
    assert sorted(b['target'] for b in wheel) == ['1.ª', '11.ª', '12.ª', '2.ª']
    with fitz.open(SECRETS/'source.pdf') as src, fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 137)]
        for block in wheel:
            data, chars = _ordinal_glyphs(page, block)
            assert chars['.'][1] == pytest.approx(data['digit']['origin'][1], abs=.01)
            # «ª» is itself superior: on the numeral's baseline too (raised once, 2026-11-09).
            assert chars['ª'][1] == pytest.approx(data['digit']['origin'][1], abs=.01)
            assert chars['.'][0] == pytest.approx(data['suffix']['origin'][0], abs=.01)
            assert chars['ª'][0] > chars['.'][0]
        issues = fidelity.composition_line_issues(src[137], page, fit['blocks'])
        assert [i for i in issues if i['code'] == 'ordinal_point_off_baseline'] == []
        qa_counts, proofs = __import__('astro_translate.native_ordinals', fromlist=['x']).qa_adjustments(
            src[137], page, fit['blocks'], {})
        assert len(proofs) == len(wheel)


# 4c. p139 u0006 (SohoPro-Light 11 pt, 44.5,524.4–262.3,605.4): «…with Mercury in the 1st house, … make your way
# in the world (1st), …» → saved target «…en la casa 1, … en el mundo (1), …». The bare parenthesized
# ordinal agrees with «1st house» of the same sentence: feminine, «(1.ª)». «(9th house)» → «(casa 9)» stays.
def test_parenthesized_house_ordinal_takes_the_feminine_suffix():
    from astro_translate.native_ordinals import parenthesized_ordinals
    style = {'font': 'SohoPro-Light', 'size': 11.0}
    unit = {'id': 'u0006', 'source': 'For example, with Mercury in the 1st house, for you to make your way in the world (1st), you',
            'target': 'Por ejemplo, con Mercurio en la casa 1, para abrirte camino en el mundo (1), necesitarás',
            'target_runs': [{'text': 'Por ejemplo, con Mercurio en la casa 1, para abrirte camino en el mundo (1), necesitarás',
                             'style': style}]}
    [fixed] = parenthesized_ordinals([unit])
    assert fixed['target'].endswith('en el mundo (1.ª), necesitarás')
    assert ''.join(r['text'] for r in fixed['target_runs']) == fixed['target']
    assert fixed['editorial_ordinals'] == [{'ordinal': '1st', 'target': '(1.ª)', 'agreement': 'house'}]
    # No agreement evidence (no «1st house» in the unit): left as it is, flagged by the QA.
    alone = {**unit, 'source': 'to make your way in the world (1st), you'}
    assert parenthesized_ordinals([alone])[0]['target'] == unit['target']
    from astro_translate.text_quality import ordinal_suffix_issues
    assert [i['code'] for i in ordinal_suffix_issues(alone)] == ['ordinal_suffix_dropped']
    assert ordinal_suffix_issues(fixed) == []
    # «(9th house)» → «(casa 9)» already names the house.
    house = {'id': 'u0028', 'source': 'might be through adventure (9th house).', 'target': 'podría ser a través de la aventura (casa 9).'}
    assert parenthesized_ordinals([house])[0]['target'] == house['target'] and ordinal_suffix_issues(house) == []


@secrets
@saved(139)
def test_parenthesized_ordinal_composed_p139(tmp_path):
    fit = _saved_fit(139, tmp_path)
    assert fit['fit_complete'], fit.get('failures')
    world = next(b for b in fit['blocks'] if 'make your way in the world (1st)' in b['source'])
    adventure = next(b for b in fit['blocks'] if 'adventure (9th house)' in b['source'])
    assert 'en el mundo (1.ª)' in world['target']
    assert '(casa 9)' in adventure['target']
    with fitz.open(fit['output']) as doc:
        text = doc[fit.get('output_page', 138)].get_text()
    assert '(1.ª)' in text and '(1)' not in text


# 4b. «middle of the heavens» = «mitad de los cielos» (p23 u0011, inside a quotation) but «Mitad del cielo»
# (p139 u0023); «bottom of the heavens» = «parte inferior de los cielos» / «Parte inferior del cielo» (u0024).
# G3 fixes Midheaven/Medium Coeli = Medio Cielo and Imum Coeli = Fondo del Cielo (validator), not these phrases:
# the cross-page consistency QA must report them.
def _saved_blocks(page_number):
    path = _translation(page_number)
    if not path.is_file():
        pytest.skip('Saved Sonnet translation not available')
    data = json.loads(path.read_text())
    return [{**b, 'page': data['page']} for b in data['blocks']]


def _glossary():
    import csv
    with open(SECRETS/'work/glossary/astro_en_es.csv') as stream:
        return list(csv.DictReader(stream))


@secrets
def test_cross_page_phrase_inconsistency_is_reported_p23_p139():
    from astro_translate.consistency import phrase_consistency_issues
    issues = phrase_consistency_issues(_saved_blocks(139), _saved_blocks(23), _glossary())
    found = {(i['unit_id'], i['source_phrase'], i['target'].casefold(), i['others'][0]['target'].casefold()) for i in issues}
    assert found == {('p0139_u0023', 'middle of the heavens', 'mitad del cielo', 'mitad de los cielos'),
                     ('p0139_u0024', 'bottom of the heavens', 'parte inferior del cielo', 'parte inferior de los cielos')}
    assert all(i['code'] == 'phrase_inconsistency' and i['others'][0]['page'] == 23 for i in issues)
    # Symmetric: QA of p23 with p139 as evidence names the same phrases.
    back = phrase_consistency_issues(_saved_blocks(23), _saved_blocks(139), _glossary())
    assert {i['source_phrase'] for i in back} == {'middle of the heavens', 'bottom of the heavens'}


@secrets
def test_phrase_consistency_has_no_false_positive_on_consistent_or_glossary_phrases():
    from astro_translate.consistency import phrase_consistency_issues
    # p186 «MC (Midheaven)» = «MC (Medio Cielo)» like p172: glossary term, consistent.
    assert phrase_consistency_issues(_saved_blocks(186), _saved_blocks(172), _glossary()) == []
    # Same phrase, same translation: nothing to report.
    unit = {'page': 1, 'id': 'p0001_u0001', 'source': 'Look at the middle of the heavens tonight.',
            'target': 'Mira la mitad del cielo esta noche.'}
    other = {**unit, 'page': 2, 'id': 'p0002_u0001'}
    assert phrase_consistency_issues([unit], [other]) == []


# 5. p15/p23 display quotes (SohoPro-LightCompressed / SohoPro-BoldCompressed 27 pt, colour 8882055 / 7303023).
# p15 u0001_quote (98.8,156.9–316.3,244.4): «It seems a natural part of human experience to **seek meaning in the
# sky**.» → «**Buscar significado en el cielo** parece una parte natural de la experiencia humana.»; Spanish fronts
# the bold infinitive. p23 u0001_quote (150.3,70.1–433.8,127.5): «**The Sun** represents a person’s individual
# **creative gift** and **sense of identity**.» → «**El Sol** representa el **don creativo** y el **sentido de
# identidad** individuales de una persona.»; the adjective moves after the bold phrases. The bold falls on the
# same phrases in both (auditor, batch-17): only the relative position changed. The local QA compared the
# ordered light/bold sequence and asked for a retranslation. Emphasis lost or moved to another phrase must
# still be reported.
_QUOTES = {15: ((98.79, 156.94, 316.33, 244.37), ['Buscar significado en el cielo']),
           23: ((150.28, 70.09, 433.8, 127.53), ['El Sol', 'don creativo', 'sentido de identidad'])}


@pytest.fixture(scope='module')
def composed_quotes(tmp_path_factory):
    from astro_translate.text_quality import _signature
    found = {}
    for page_number, (bbox, bold) in _QUOTES.items():
        if not _translation(page_number).is_file():
            pytest.skip('Saved Sonnet translation not available')
        fit = _saved_fit(page_number, tmp_path_factory.mktemp(f'p{page_number}'))
        quote = next(b for b in fit['blocks'] if b['id'] == 'u0001_quote')
        assert quote['bbox'] == pytest.approx(bbox, abs=.01)
        assert {s['style']['font'] for s in quote['spans']} == {'SohoPro-LightCompressed', 'SohoPro-BoldCompressed'}
        assert [r['text'] for r in quote['target_runs'] if _signature(r['style'])[1]] == bold
        found[page_number] = (fit, quote)
    return found


def _restyle(unit, bold):
    """Same composed target, bold only on ``bold`` phrases (target text unchanged)."""
    light, heavy = (next(r['style'] for r in unit['target_runs'] if r['style']['bold'] is b) for b in (False, True))
    runs, cursor = [], 0
    for phrase in bold:
        start = unit['target'].index(phrase, cursor)
        runs += [{'text': unit['target'][cursor:start], 'style': light}, {'text': phrase, 'style': heavy}]
        cursor = start+len(phrase)
    runs.append({'text': unit['target'][cursor:], 'style': light})
    return {**copy.deepcopy(unit), 'target_runs': [r for r in runs if r['text']]}


@secrets
@pytest.mark.parametrize('page_number', [15, 23])
def test_fronted_display_quote_emphasis_is_not_an_inline_style_issue(composed_quotes, page_number):
    from astro_translate.text_quality import page_issues, unit_issues
    fit, quote = composed_quotes[page_number]
    with fitz.open(SECRETS/'source.pdf') as src:
        issues = page_issues(fit['blocks'], source_page=src[page_number-1], glossary=_glossary())
    assert [i for i in issues if i['code'] == 'inline_style'] == []
    assert 'inline_style' not in {i['code'] for i in unit_issues(quote, glossary=_glossary())}


@secrets
@pytest.mark.parametrize('page_number,bold', [
    (15, []),                                    # emphasis lost
    (15, ['parece una parte natural']),          # moved to the light clause (natural/parte)
    (15, ['Buscar']),                            # shrunk to one word of the phrase
    (15, ['Buscar significado en el cielo', 'experiencia humana']),  # extra phrase
    (23, ['don creativo', 'sentido de identidad']),                  # «The Sun» lost
    (23, ['El Sol', 'representa el', 'sentido de identidad']),       # moved from «creative gift»
    (23, ['El Sol', 'don creativo', 'individuales de una persona']),  # moved from «sense of identity»
])
def test_lost_or_displaced_display_quote_emphasis_is_still_reported(composed_quotes, page_number, bold):
    from astro_translate.text_quality import page_issues
    fit, quote = composed_quotes[page_number]
    damaged = _restyle(quote, bold)
    blocks = [damaged if b['id'] == 'u0001_quote' else b for b in fit['blocks']]
    with fitz.open(SECRETS/'source.pdf') as src:
        issues = page_issues(blocks, source_page=src[page_number-1], glossary=_glossary())
    assert [i['unit_id'] for i in issues if i['code'] == 'inline_style'] == [f'p{page_number:04d}_u0001_quote']


# Same order, other phrase: p17 u0001 (87,211–330,269), «Older **forms** of astrology have been renewed for
# **today’s audience**.» was saved as «Las formas **más antiguas** de la astrología se han renovado para el
# **público actual**.» The light/bold sequence matches, but the bold moved from «forms» to «older». Frozen
# copy of the saved spans and runs: the check must not depend on a later retranslation of the page.
def test_emphasis_moved_to_another_phrase_in_the_same_order_is_reported_p17():
    from astro_translate.text_quality import unit_issues
    style = lambda bold: {'font': 'SohoPro-%sCompressed' % ('Bold' if bold else 'Light'), 'size': 27.0,
                          'color': 7303023 if bold else 8882055, 'weight': 700 if bold else 300,
                          'bold': bold, 'italic': False, 'serif': True, 'width': 60}
    spans = [('Older ', False), ('forms', True), (' of astrology have been ', False), ('renewed for ', False),
             ('today’s audience', True), ('. ', False)]
    runs = [('Las formas ', False), ('más antiguas ', True), ('de la astrología se han ', False),
            ('renovado para el ', False), ('público actual', True), ('.', False)]
    unit = {'id': 'u0001', 'source': ''.join(t for t, _ in spans).strip(), 'style': style(False),
            'spans': [{'source': t, 'style': style(b)} for t, b in spans],
            'target': ''.join(t for t, _ in runs), 'target_runs': [{'text': t, 'style': style(b)} for t, b in runs]}
    assert 'inline_style' in {i['code'] for i in unit_issues(unit)}
    fixed = copy.deepcopy(unit)
    fixed['target_runs'] = [{'text': t, 'style': style(b)} for t, b in [
        ('Las ', False), ('formas', True), (' más antiguas de la astrología se han renovado para el ', False),
        ('público actual', True), ('.', False)]]
    assert 'inline_style' not in {i['code'] for i in unit_issues(fixed)}
