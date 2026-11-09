"""Auditor batch-16 defects (book-auditor, 2026-11-09) on real pages; offline, no LLM.

p138/p139: house-wheel ordinals spanning a spread stayed English (11TH, 12TH,
1ST, 2ND, 3RD); p139 «• Romance» was a false residual-English positive; p176:
stacked title split across units bypassed the glossary; p47: a figure caption
named the wrong object (Beehive → «Pesebre» while the body says «Colmena»).
"""
import copy
import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import fidelity
from astro_translate.fonts import select_group_fonts

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / 'books/the-secrets-of-astrology'
sys.path.insert(0, str(BOOK / 'work/pilot'))
pytestmark = pytest.mark.skipif(not (BOOK / 'source.pdf').exists(), reason='book source not available')


def _diagram_units(page):
    from astro_translate.native_ordinals import adapt_headings
    return [u for u in adapt_headings(page, fidelity.extract_fidelity_units(page)) if u.get('native_ordinal')]


# ---------------------------------------------------------------- defect 1

@pytest.mark.parametrize('number,expected', [
    (138, ['1.ª', '2.ª', '11.ª', '12.ª']),
    (139, ['3.ª', '4.ª', '5.ª', '6.ª', '7.ª', '8.ª', '9.ª', '10.ª']),
    (171, ['3.ª', '4.ª', '5.ª', '6.ª', '7.ª', '8.ª', '9.ª', '10.ª']),
    (172, ['1.ª', '2.ª', '11.ª', '12.ª']),
    (173, ['3.ª', '4.ª', '5.ª', '6.ª', '7.ª', '8.ª', '9.ª', '10.ª']),
])
def test_house_wheel_ordinals_across_a_spread_are_feminine(number, expected):
    """A wheel split over facing pages: the house context and cohort are the spread's."""
    from astro_translate.native_ordinals import inventory
    with fitz.open(BOOK / 'source.pdf') as doc:
        page = doc[number - 1]
        units = _diagram_units(page)
        assert sorted(u['target'] for u in units) == sorted(expected)
        assert all(u['native_ordinal']['rule'] == 'I3_house_feminine_delegated_2026-11-07' for u in units)
        diagram = [r for r in inventory(page) if r['treatment'] == 'local_house_diagram']
        assert len(diagram) == len(expected)


def test_wheel_blocked_by_layout_stays_protected_and_reviewed_p170():
    """p170: «11th» touches the metric box of the 48-pt title; the wheel is all or nothing.

    The composition guard compares unit boxes (fidelity, owned by composition);
    until it compares ink, the labels stay protected and the QA flags English."""
    from astro_translate.native_ordinals import diagram_layout_conflicts, _headings
    from astro_translate.secrets_ordinal_policy import house_diagram_lines
    from astro_translate.text_quality import english_ordinal_issues
    with fitz.open(BOOK / 'source.pdf') as doc:
        page = doc[169]
        assert len(house_diagram_lines(page)) == 4  # the spread cohort is recognised
        conflicts = diagram_layout_conflicts(list(_headings(page)), fidelity.extract_fidelity_units(page))
        assert [(c['ordinal'], c['unit_id']) for c in conflicts] == [('11th', 'u0005')]
        assert _diagram_units(page) == []
        assert sorted(i['ordinal'] for i in english_ordinal_issues(page)) == ['11th', '12th', '1st', '2nd']


def test_ordinal_suffix_span_with_trailing_space_p139():
    with fitz.open(BOOK / 'source.pdf') as doc:
        three = next(u for u in _diagram_units(doc[138]) if u['native_ordinal']['digit']['text'] == '3')
        assert three['native_ordinal']['suffix']['text'] == 'rd '
        assert [r['text'] for r in three['target_runs']] == ['3', '.ª']


def test_ordinal_followed_by_label_on_same_line_p138():
    """«11th» + «Friendships» share one PDF line: only the digit/suffix pair is local."""
    with fitz.open(BOOK / 'source.pdf') as doc:
        page = doc[137]
        eleven = next(u for u in _diagram_units(page) if u['native_ordinal']['digit']['text'] == '11')
        assert [s['source'] for s in eleven['spans']] == ['11', 'th']
        units = fidelity.extract_fidelity_units(page)
        assert any(u['source'].strip() == 'Friendships' for u in units)


def test_facing_page_without_wheel_does_not_borrow_context():
    """A page alone keeps the per-page rule; a synthetic single page is not a spread."""
    from astro_translate.secrets_ordinal_policy import house_diagram_lines
    with fitz.open(BOOK / 'source.pdf') as doc, fitz.open() as alone, fitz.open() as spread:
        alone.insert_pdf(doc, from_page=137, to_page=137)
        assert house_diagram_lines(alone[0]) == []
        # Index 1 faces index 2. A facing copy repeats the same houses: not one wheel.
        alone.new_page(0)
        alone.insert_pdf(doc, from_page=137, to_page=137)
        assert house_diagram_lines(alone[1]) == []
        # The real spread works at any page index (left page odd index).
        spread.new_page()
        spread.insert_pdf(doc, from_page=137, to_page=138)
        assert len(house_diagram_lines(spread[1])) == 4
        assert len(house_diagram_lines(spread[2])) == 8


@pytest.mark.parametrize('number', [138, 139])
def test_spread_house_ordinals_compose_and_pass_qa(number, tmp_path):
    from collections import Counter
    from astro_translate.native_ordinals import prepare_headings, heading_overlay, select_heading_fonts, qa_adjustments
    from astro_translate.text_quality import english_ordinal_issues
    from local_layout import precise_redaction_rects
    with fitz.open(BOOK / 'source.pdf') as doc, fitz.open() as candidate:
        page = doc[number - 1]
        units = _diagram_units(page)
        mapping, _ = select_heading_fonts(select_group_fonts, doc, page, units, tmp_path, classify_slab=True)
        prepare_headings(page, units, mapping)
        candidate.insert_pdf(doc, from_page=number - 1, to_page=number - 1)
        cp = candidate[0]
        def in_diagram(issues):
            boxes = [fitz.Rect(u['native_ordinal']['edit_bbox']) for u in units]
            return [i for i in issues if any(fitz.Rect(i['candidate_bbox']).intersects(b) for b in boxes)]
        # The untouched source keeps every diagram suffix in English.
        assert len(in_diagram(english_ordinal_issues(cp))) == len(units)
        for u in units:
            for rect in precise_redaction_rects(page, u):
                cp.add_redact_annot(rect, fill=False)
        cp.apply_redactions(images=0, graphics=0)
        reports = []
        for u in units:
            overlay, _ = heading_overlay(page, u, 1, mapping)
            cp.show_pdf_page(cp.rect, overlay, 0); overlay.close()
            _, _, used = fidelity._html_for(u, mapping, tmp_path, 1)
            reports.append({**u, 'edit_bbox': u['native_ordinal']['edit_bbox'], 'font_runs': used, 'scale': 1})
        immutable = fidelity._immutable_characters(page, ())
        added, proof = qa_adjustments(page, cp, json.loads(json.dumps(reports)), immutable)
        assert len(proof) == len(units) and all(p['numeric_origin_preserved'] for p in proof)
        assert not (immutable - fidelity._immutable_characters(cp, (), False))
        assert Counter(fidelity.NUMBER_PATTERN.findall(page.get_text())) == Counter(fidelity.NUMBER_PATTERN.findall(cp.get_text()))
        # Prose ordinals of this offline copy are untranslated on purpose.
        assert in_diagram(english_ordinal_issues(cp)) == []
        for u in units:
            clip = fitz.Rect(u['native_ordinal']['digit']['bbox'])
            assert page.get_pixmap(clip=clip, dpi=220).samples == cp.get_pixmap(clip=clip, dpi=220).samples


def test_residual_english_qa_reviews_protected_ordinal_suffixes():
    """Protected digits with an English suffix are residual English in the candidate."""
    from astro_translate.text_quality import english_ordinal_issues, page_issues
    with fitz.open(BOOK / 'source.pdf') as doc:
        page = doc[137]
        found = english_ordinal_issues(page)
        # Raised diagram suffixes beside protected digits, and inline prose «3rd house».
        assert sorted(i['ordinal'] for i in found) == ['11th', '12th', '1st', '2nd', '3rd']
        assert {i['code'] for i in found} == {'english_ordinal_suffix'}
        assert {i['scope'] for i in found} == {'candidate_pdf'}
        codes = [i['code'] for i in page_issues([], source_page=page, candidate_page=page)]
        assert codes.count('english_ordinal_suffix') == 5
    # Spanish morphology and protected digits alone are not English.
    doc = fitz.open(); clean = doc.new_page()
    clean.insert_text((40, 40), 'La 3.ª casa y el 1.er signo; 12 casas.')
    assert english_ordinal_issues(clean) == []
    doc.close()


# ---------------------------------------------------------------- defect 5

def test_spanish_homograph_is_not_residual_english():
    from astro_translate.language import residual_english
    assert residual_english(['• Romance']) == []
    assert residual_english(['• Self-sacrifice']) == ['• Self-sacrifice']
    assert residual_english(['• Romance', '• Money']) == ['• Money']


# ---------------------------------------------------------------- defect 2

def _p176_saved():
    folder = BOOK / 'work/production'
    qa, fit = (json.loads((folder / f'p0176-{name}.json').read_text()) for name in ('qa', 'fit'))
    if qa.get('source_sha256') != fit.get('source_sha256'):
        pytest.skip('saved p176 artifacts are not source-bound to each other')
    return qa, fit


def _finish(tmp_path, qa, fit, regions):
    from types import SimpleNamespace
    from astro_translate.production import Production
    saved = {}
    runner = SimpleNamespace(book=BOOK, review_for=lambda *a: None, path=lambda n, s: tmp_path / s,
                             update=lambda n, **v: saved.update(v), queue=lambda *a: None,
                             checkpoint=lambda *a, **k: None)
    return Production.finish_qa(runner, 176, fit, qa, regions, 'dep'), qa


def test_p176_deferred_wheel_letters_alone_make_partial_deferred(tmp_path):
    qa, fit = _p176_saved()
    regions = copy.deepcopy(qa['deferred_regions'])
    assert regions and all(r['bbox'] and r['reason'] for r in regions)
    # Audited state (batch-16): the stacked title broke the approved glossary form. The saved
    # page has been retranslated since, so the audited failure is injected on the saved QA.
    audited = {**copy.deepcopy(qa), 'glossary_missing': ['Wheel of Rulership'], 'native_local_fidelity_ok': False}
    status, report = _finish(tmp_path, audited, fit, copy.deepcopy(regions))
    assert status == 'review'
    assert report['native_failure_causes'] == ['glossary_missing']
    assert report['untranslated_outside_deferred'] == []
    # Once the title carries «rueda de las regencias», only PISCES/ARIES remain: Phase 4.
    fixed = {**copy.deepcopy(qa), 'glossary_missing': [], 'native_local_fidelity_ok': True}
    status, report = _finish(tmp_path, fixed, fit, copy.deepcopy(regions))
    assert status == 'partial_deferred'
    assert 'deferred_raster_phase4' in report['production_reasons']
    # Untranslated letters outside every registered region never pass as deferred.
    fixed = {**copy.deepcopy(qa), 'glossary_missing': [], 'native_local_fidelity_ok': True}
    status, report = _finish(tmp_path, fixed, fit, copy.deepcopy(regions[1:]))
    assert status == 'review'
    assert 'untranslated_text_outside_deferred_regions' in report['production_reasons']


# ------------------------------------------------- defects 3 and 4 (translation side, mocked LLM)

class FakeBackend:
    def __init__(self, answer):
        self.answer = answer; self.calls = []

    def complete_json(self, task, payload, schema, model, effort, images=None):
        from astro_translate import llm
        self.calls.append(json.loads(json.dumps({k: v for k, v in payload.items() if k != '_template'})))
        return llm.LLMResult(self.answer(payload, len(self.calls)), model, effort, 12, 7, 'claude_cli', False, .01)


@pytest.fixture
def offline_book(tmp_path, monkeypatch):
    """Temporary approved book; real p47/p176 sources and geometry, no LLM."""
    from astro_translate import adapters, llm
    from astro_translate.core import approve_glossary, atomic_json
    import tomllib
    book = tmp_path / 'book'; book.mkdir()
    doc = fitz.open()
    for _ in range(176):
        doc.new_page(width=552.77, height=660.49)
    doc.save(book / 'source.pdf'); doc.close()
    g = tmp_path / 'g.csv'
    g.write_text('source,target,tgt_lng\nWheel of Rulership,rueda de las regencias,es\nrulership,regencia,es\nSun,Sol,es\n')
    s = tmp_path / 'style.md'; s.write_text('Español neutro.')
    p = tmp_path / 'protected.txt'; p.write_text('MC\n')
    schema = {'type': 'object', 'properties': {'blocks': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'translation': {'type': 'string'}, 'glossary_terms_used': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['id', 'translation', 'glossary_terms_used'], 'additionalProperties': False}}},
        'required': ['blocks'], 'additionalProperties': False}
    sp = tmp_path / 'schema.json'; sp.write_text(json.dumps(schema))
    pr = tmp_path / 'prompt.md'; pr.write_text('Translate each block.')
    config = tmp_path / 'pipeline.toml'
    config.write_text(f'[provider]\nbackend="claude_cli"\nfallback="none"\nmax_parallel=2\n[quota]\nbackoff_seconds=[0,0,0]\n'
                      f'pause_exit_code=75\n[batching]\nmax_chars_per_call=6000\n[paths]\nglossary="{g}"\nstyle_guide="{s}"\n'
                      f'do_not_translate="{p}"\n[tasks.translate_page]\nmodel="sonnet"\neffort="low"\nprompt="{pr}"\nschema="{sp}"\n')
    approve_glossary(book, g, s, 'TEST ONLY', p)
    atomic_json(book / 'work/manifest.json', {'pages': [{'page': n, 'status': 'pending'} for n in range(1, 177)]})
    cfg = tomllib.loads(config.read_text())
    atomic_json(book / 'work/backend_approval.json', {'approved': True, 'approved_by': 'TEST ONLY', 'backend': 'claude_cli',
        'route': 'pymupdf', 'backend_config_sha256': adapters.backend_config_digest(cfg), 'authorized_phases': ['pilot']})
    return {'book': book, 'config': config, 'glossary': g, 'style': s, 'protected': p}


def _unit(identifier, source, bbox, size, kind='paragraph', context='running_text', block=0):
    style = {'font': 'SohoPro-Light', 'size': size, 'color': 0, 'weight': 300, 'bold': False, 'italic': kind == 'caption',
             'serif': True, 'width': 100}
    return {'id': identifier, 'source': source, 'bbox': bbox, 'size': size, 'kind': kind, 'context': context,
            'source_block': block, 'spans': [], 'style': style, 'style_runs': [{'start': 0, 'end': len(source), 'style': style}]}


P176_TITLE = [_unit('u0006', 'THE WHEEL OF', [36.34, 79.58, 368.98, 127.58], 48.0, block=5),
              _unit('u0007', 'RULERSHIP', [36.34, 127.45, 341.18, 175.45], 48.0, block=6)]
P47_BODY = ('Cancer is the faintest of the 12 zodiac constellations. Its major star group, Praesepe, is also called the '
            'Beehive or Manger. It was named the Manger because the stars Gamma and Delta Cancri look like two donkeys '
            'feeding at a manger.')
P47_CAPTION = 'The Beehive was known to the ancient Greeks, who could see it as a misty spot in the sky'
P47 = [_unit('caption_7b2e3b9610b0', P47_CAPTION, [430.56, 244.67, 521.48, 293.67], 9.0, 'caption', 'figure_label', 4),
       _unit('u0009', P47_BODY, [289.13, 84.83, 518.14, 233.77], 14.0, block=9)]
SPANISH = {
    'Cancer is the faintest of the {n1} zodiac constellations': 'Cáncer es la más tenue de las {n1} constelaciones del zodíaco',
    'Its major star group, Praesepe, is also called the Beehive or Manger':
        'Su principal grupo de estrellas, Praesepe, también se llama la Colmena o el Pesebre',
    'It was named the Manger because the stars Gamma and Delta Cancri look like two donkeys feeding at a manger':
        'Recibió el nombre de Pesebre porque las estrellas Gamma y Delta Cancri parecen dos burros comiendo en un pesebre',
}
WRONG_CAPTION = 'El Pesebre era conocido por los antiguos griegos, que podían verlo como una mancha brumosa en el cielo'
RIGHT_CAPTION = 'La Colmena era conocida por los antiguos griegos, que podían verla como una mancha brumosa en el cielo'


def _translate(env, monkeypatch, units_by_page, answer, pages):
    from astro_translate import adapters, fidelity, llm
    monkeypatch.setattr(fidelity, 'extract_fidelity_units',
                        lambda page, *a, **k: copy.deepcopy(units_by_page.get(page.number + 1, [])))
    fake = FakeBackend(answer)
    monkeypatch.setattr(llm, '_make_backend', lambda *a, **k: fake)
    adapters.fidelity_translate(env['book'], env['glossary'], env['style'], env['protected'], pages,
                                config_path=env['config'], reinsert_pages=False, phase='pilot')
    return fake


def _saved(env, number):
    data = json.loads((env['book'] / f'work/pilot/p{number:04d}-translation.json').read_text())
    return {u['id'].split('_', 1)[1]: u['target'] for u in data['blocks']}


def _row(r, text):
    return {'id': r['id'], 'translation': text, 'glossary_terms_used': []}


def test_stacked_title_lines_are_validated_against_the_glossary_jointly(offline_book, monkeypatch):
    """p176: «LA RUEDA DE» + «LA REGENCIA» passes line by line; jointly it must be «rueda de las regencias»."""
    def answer(wire, call):
        lines = {'THE WHEEL OF': 'LA RUEDA DE', 'RULERSHIP': 'LA REGENCIA' if call == 1 else 'LAS REGENCIAS'}
        return {'blocks': [_row(r, lines[r['source']]) for r in wire['blocks']]}
    fake = _translate(offline_book, monkeypatch, {176: P176_TITLE}, answer, '176')
    first = {r['source']: r for r in fake.calls[0]['blocks']}
    assert first['THE WHEEL OF']['line_continuation'][0]['glossary'] == {'source': 'Wheel of Rulership', 'target': 'rueda de las regencias'}
    assert first['RULERSHIP']['line_continuation'][0]['side'] == 'before'
    assert 'stacked lines' in fake.calls[1]['validation_error']
    targets = _saved(offline_book, 176)
    assert targets['u0006'] + ' ' + targets['u0007'] == 'LA RUEDA DE LAS REGENCIAS'


def test_saved_split_title_memory_is_rejected_and_replaced(offline_book, monkeypatch):
    from astro_translate.consistency import SentenceMemory, build_plan
    from astro_translate.translation_context import translation_context
    import tomllib
    context = translation_context(tomllib.loads(offline_book['config'].read_text()), offline_book['glossary'],
                                  offline_book['style'], offline_book['protected'])
    plan = build_plan([{**u, 'id': 'p0176_'+u['id'], 'page': 176} for u in P176_TITLE])
    memory = SentenceMemory(offline_book['book'] / 'work', context)
    old = {'THE WHEEL OF': 'LA RUEDA DE', 'RULERSHIP': 'LA REGENCIA'}
    for entry in plan['entries'].values():
        memory.put(entry, old[entry['source']])
    memory.close()
    def answer(wire, call):
        lines = {'THE WHEEL OF': 'LA RUEDA DE', 'RULERSHIP': 'LAS REGENCIAS'}
        return {'blocks': [_row(r, lines[r['source']]) for r in wire['blocks']]}
    fake = _translate(offline_book, monkeypatch, {176: P176_TITLE}, answer, '176')
    assert len(fake.calls) == 1
    assert _saved(offline_book, 176)['u0007'] == 'LAS REGENCIAS'
    # The replacement is now the exact memory: a rerun makes no call.
    fake = _translate(offline_book, monkeypatch, {176: P176_TITLE}, answer, '176')
    assert fake.calls == []


def test_caption_is_translated_after_its_body_with_term_context(offline_book, monkeypatch):
    """p47: the caption sees «Beehive → Colmena»; «El Pesebre…» fails validation and is retried."""
    def answer(wire, call):
        rows = []
        for r in wire['blocks']:
            if r['source'] in SPANISH:
                rows.append(_row(r, SPANISH[r['source']]))
            else:
                rows.append(_row(r, WRONG_CAPTION if call == 2 else RIGHT_CAPTION))
        return {'blocks': rows}
    fake = _translate(offline_book, monkeypatch, {47: P47}, answer, '47')
    assert [r['source'] for r in fake.calls[0]['blocks']] == list(SPANISH)  # body first, page whole
    caption = fake.calls[1]['blocks'][0]
    assert caption['source'] == P47_CAPTION
    assert caption['term_context'] == [{'source': 'Its major star group, Praesepe, is also called the Beehive or Manger',
                                        'target': SPANISH['Its major star group, Praesepe, is also called the Beehive or Manger']}]
    assert 'term_context_instructions' in caption
    assert 'term inconsistent' in fake.calls[2]['validation_error']
    assert _saved(offline_book, 47)['caption_7b2e3b9610b0'] == RIGHT_CAPTION


def test_inconsistent_caption_memory_is_retranslated_with_context(offline_book, monkeypatch):
    def first(wire, call):
        return {'blocks': [_row(r, SPANISH.get(r['source'], WRONG_CAPTION)) for r in wire['blocks']]}
    # Simulate the legacy flow: caption alone, translated without its body.
    _translate(offline_book, monkeypatch, {47: P47[:1]}, first, '47')
    assert _saved(offline_book, 47)['caption_7b2e3b9610b0'] == WRONG_CAPTION
    def second(wire, call):
        return {'blocks': [_row(r, SPANISH.get(r['source'], RIGHT_CAPTION)) for r in wire['blocks']]}
    fake = _translate(offline_book, monkeypatch, {47: P47}, second, '47')
    assert [r['source'] for r in fake.calls[-1]['blocks']] == [P47_CAPTION]
    assert fake.calls[-1]['blocks'][0]['term_context']
    assert _saved(offline_book, 47)['caption_7b2e3b9610b0'] == RIGHT_CAPTION


def test_page_groups_never_split_a_page_that_fits():
    from astro_translate.page_context import page_groups
    rows = [{'id': f'p{p:04d}_s{i}', 'source': 'x'*size} for p, i, size in
            [(1, 0, 2500), (1, 1, 2500), (2, 0, 1500), (2, 1, 1500), (3, 0, 7000)]]
    groups = [[r['id'] for r in g] for g in page_groups(rows, 6000)]
    assert groups == [['p0001_s0', 'p0001_s1'], ['p0002_s0', 'p0002_s1'], ['p0003_s0']]


# ------------------------------------------------- defects 3 and 4 (QA side, real saved artifacts)

def test_p47_saved_caption_names_the_wrong_object():
    from astro_translate.text_quality import term_consistency_issues
    import csv
    data = json.loads((BOOK / 'work/production/p0047-translation.json').read_text())
    with open(BOOK / 'work/glossary/astro_en_es.csv') as stream:
        glossary = list(csv.DictReader(stream))
    # The saved caption has been retranslated (RIGHT_CAPTION) and is consistent now.
    assert term_consistency_issues(data['blocks'], (), glossary) == []
    # The audited caption (batch-16) on the same saved page is still flagged.
    for block in data['blocks']:
        if block['id'] == 'p0047_caption_7b2e3b9610b0':
            assert block['target'] == RIGHT_CAPTION
            block['target'] = WRONG_CAPTION
    issues = term_consistency_issues(data['blocks'], (), glossary)
    assert [(i['unit_id'], i['source_term'], i['expected']) for i in issues] == [
        ('p0047_caption_7b2e3b9610b0', 'beehive', 'colmena')]
    assert issues[0]['found'] == [{'source_term': 'manger', 'target': 'pesebre'}]
    assert issues[0]['action'] == 'retranslate'


def test_term_consistency_has_no_false_positive_on_reordered_names():
    """p61: «Northern Claw» → «Garra Norte» reorders words; it is not an inconsistency."""
    from astro_translate.text_quality import term_consistency_issues
    data = json.loads((BOOK / 'work/production/p0061-translation.json').read_text())
    assert term_consistency_issues(data['blocks']) == []


def test_page_issues_report_term_inconsistency_on_composed_p47():
    from astro_translate.text_quality import page_issues
    fit = json.loads((BOOK / 'work/production/p0047-fit.json').read_text())
    assert not [i for i in page_issues(fit['blocks']) if i['code'] == 'term_inconsistency']
    # Audited caption (batch-16) injected on the composed blocks of the saved page.
    for block in fit['blocks']:
        if block['id'] == 'caption_7b2e3b9610b0':
            block['target'] = WRONG_CAPTION
            block['target_runs'] = [{'text': WRONG_CAPTION, 'style': block['target_runs'][0]['style']}]
    codes = [(i['code'], i['unit_id']) for i in page_issues(fit['blocks'])]
    assert ('term_inconsistency', 'caption_7b2e3b9610b0') in codes


@pytest.mark.parametrize('text,ok', [('LA RUEDA DE LAS REGENCIAS', True), ('La rueda de las regencias', True),
                                     ('LA RUEDA DE LA REGENCIA', False), ('RUEDAS DE LAS REGENCIAS', True)])
def test_glossary_forms_in_uppercase_titles(text, ok):
    from astro_translate.glossary_forms import glossary_form_present
    assert glossary_form_present(text, 'rueda de las regencias') is ok


def test_glossary_qa_on_composed_uppercase_title_p176():
    """Saved candidate: the joined title is checked against the glossary despite caps."""
    from astro_translate.language import glossary_matches
    from astro_translate.glossary_forms import glossary_form_present
    import csv
    fit = json.loads((BOOK / 'work/production/p0176-fit.json').read_text())
    title = next(b for b in fit['blocks'] if b['source'] == 'THE WHEEL OF RULERSHIP')
    with open(BOOK / 'work/glossary/astro_en_es.csv') as stream:
        glossary = list(csv.DictReader(stream))
    terms = glossary_matches(title['source'], glossary)
    assert [t['target'] for t in terms] == ['rueda de las regencias']
    # Retranslated since the audit: the composed title carries the approved form.
    assert title['target'] == 'LA RUEDA DE LAS REGENCIAS'
    assert glossary_form_present(title['target'], terms[0]['target'])
    # The audited composed title (batch-16) is rejected despite the capitals.
    assert not glossary_form_present('LA RUEDA DE LA REGENCIA', terms[0]['target'])


@pytest.mark.parametrize('number', [142, 143, 144, 145])
def test_saved_translations_keeping_english_ordinals_are_rejected(number):
    """Found while extending the residual-English QA: «la casa 2nd», «CASA 2ND» (p142–145)."""
    from astro_translate.text_quality import text_issues
    data = json.loads((BOOK / f'work/production/p{number:04d}-translation.json').read_text())
    flagged = [u['id'] for u in data['blocks']
               if any(i['code'] == 'english_ordinal_suffix' for i in text_issues(u['source'], u['target'], u['id']))]
    assert flagged
    # Masked wire form, as seen by the translation validator and sentence memory.
    assert [i['code'] for i in text_issues('SIGNS AND {n1}ND HOUSE PLANETS', 'SIGNOS Y PLANETAS DE LA CASA {n1}ND', 'x')] == ['english_ordinal_suffix']
    assert text_issues('SIGNS AND {n1}ND HOUSE PLANETS', 'SIGNOS Y PLANETAS DE LA CASA {n1}', 'x') == []
    assert text_issues('The 3rd house', 'La 3.ª casa', 'x') == []


def test_real_extraction_routes_p176_title_jointly_and_p47_captions_after_body():
    """The real units, not mocks: stacked title pair found; captions are context-dependent."""
    import csv
    from astro_translate.consistency import build_plan
    from astro_translate.page_context import stacked_glossary_pairs, dependent_ids, term_context
    with open(BOOK / 'work/glossary/astro_en_es.csv') as stream:
        glossary = list(csv.DictReader(stream))
    with fitz.open(BOOK / 'source.pdf') as doc:
        plans = {}
        for n in (47, 176):
            units = [{**u, 'id': f'p{n:04d}_'+u['id'], 'page': n} for u in fidelity.extract_fidelity_units(doc[n-1])]
            plans[n] = (units, build_plan(units))
    units, plan = plans[176]
    entries = {e['id']: e for e in plan['entries'].values()}
    pairs = stacked_glossary_pairs(units, plan, glossary)
    assert [(entries[a]['source'], entries[b]['source'], row['target']) for a, b, row in pairs] == [
        ('THE WHEEL OF', 'RULERSHIP', 'rueda de las regencias')]
    units, plan = plans[47]
    entries = {e['id']: e for e in plan['entries'].values()}
    dependents = dependent_ids(plan)
    caption = next(i for i in dependents if entries[i]['source'].startswith('The Beehive'))
    body = {i: SPANISH.get(entries[i]['marked_source'] if entries[i]['markers'] else entries[i]['source'], 'x')
            for i in entries if i not in dependents}
    context = term_context(plan, caption, body)
    assert [c['source'] for c in context] == ['Its major star group, Praesepe, is also called the Beehive or Manger']
    assert 'Colmena' in context[0]['target']
