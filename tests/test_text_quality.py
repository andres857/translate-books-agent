"""I6 regressions from saved pilot artifacts; never call a provider."""
import copy
import json
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import llm
from astro_translate.text_quality import (
    text_issues, unit_issues, page_issues, repair_composition,
    source_emphasis, glyph_boundaries, constrain_glyph_spacing,
)

BOOK = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology'
PILOT = BOOK / 'work/pilot'


def saved(n):
    return json.loads((PILOT / f'p{n:04d}-translation.json').read_text())['blocks']


def historical69():
    # Freeze the real pre-correction regression, not a mutable current artifact.
    return json.loads((PILOT/'history/retranslate-p0069-translation-2lw8b7bk/base-translation.json').read_text())


def codes(issues):
    return {i['code'] for i in issues}


def test_real_p69_continuation_repetition_has_both_unit_ids():
    issues = page_issues(historical69()['blocks'])
    duplicate = [i for i in issues if i['code'] == 'repeated_ngram']
    assert {i['unit_id'] for i in duplicate} == {'p0069_u0004', 'p0069_u0005'}
    assert all(i['action'] == 'retranslate' for i in duplicate)
    assert 'asterism_agreement' in codes(issues)
    assert 'redundant_clitic' in codes(issues)


@pytest.mark.parametrize(('source', 'target', 'code'), [
    ('EDUCATION', 'EDUCACION', 'uppercase_accent'),
    ('COMMUNICATION', 'COMUNICACION', 'uppercase_accent'),
    ('The Zodiac', 'El ZODIACO', 'zodiac_spelling'),
    ('The zodiac', 'el zodiaco', 'zodiac_spelling'),
    ('The Teapot asterism', 'el asterismo de la Tetera de la Tetera', 'repeated_ngram'),
])
def test_reported_text_defects_rejected_by_wire_validator(source, target, code):
    assert code in codes(text_issues(source, target, 'unit'))
    with pytest.raises(llm.ValidationFailure, match=code) as exc:
        llm._validate('translate_page', {'blocks': [{'id': 'unit', 'translation': target}]},
                      {'blocks': [{'id': 'unit', 'source': source}]}, {'type': 'object'})
    assert exc.value.ids == {'unit'}


@pytest.mark.parametrize(('source', 'target'), [
    ('no more, no more', 'nunca más, nunca más'),
    ('EDUCATION', 'EDUCACIÓN'), ('The zodiac', 'el zodíaco'),
    ('WILL BE', 'SERA'),  # ambiguous será/sera: deliberately outside the lexicon
    ('a very bright star', 'una estrella muy muy brillante'),  # n=1 excluded
    ('two clauses', 'de la Tetera. De la Tetera'),  # sentence boundary
    ('find it in Sagittarius', 'encontrarla en Sagitario'),
])
def test_conservative_checks_have_no_false_positives(source, target):
    assert not text_issues(source, target, 'unit')


def test_accent_vocabulary_can_come_from_glossary_and_style():
    assert 'uppercase_accent' in codes(text_issues('X', 'INTUICION', 'u',
        glossary=[{'source': 'intuition', 'target': 'intuición'}]))
    assert 'uppercase_accent' in codes(text_issues('X', 'PERCEPCION', 'u',
        style_guide='Forma exigida: percepción.'))


def test_inline_style_lost_and_identical_word_repaired_without_text_change():
    base = {'font': 'Helvetica', 'size': 10, 'italic': False, 'bold': False}
    emph = {**base, 'italic': True}
    u = {'id': 'u', 'source': 'Greek plouton word', 'target': 'vocablo plouton griego',
         'style': base, 'style_runs': [
             {'start': 0, 'end': 6, 'style': base},
             {'start': 6, 'end': 13, 'style': emph},
             {'start': 13, 'end': 17, 'style': base}],
         'target_runs': [{'text': 'vocablo plouton griego', 'style': base}]}
    before = copy.deepcopy(u)
    assert 'inline_style' in codes(unit_issues(u))
    fixed = repair_composition(u)
    assert u == before and fixed['target'] == u['target']
    assert next(r for r in fixed['target_runs'] if 'plouton' in r['text'])['style']['italic']
    assert not unit_issues(fixed)


def test_empty_style_segment_cannot_pass_marker_validation():
    source = '{v1}Greek {v2}plouton{v3} word{v4}'
    target = '{v1}vocablo plouton{v2}{v3} griego{v4}'
    with pytest.raises(llm.ValidationFailure, match='inline_style'):
        llm._validate('translate_page', {'blocks': [{'id': 'u', 'translation': target}]},
            {'blocks': [{'id': 'u', 'source': source}],
             '_style_orders': {'u': ['{v1}', '{v2}', '{v3}', '{v4}']}}, {'type': 'object'})


def test_real_plouton_matrix_emphasis_and_repair():
    u = next(u for u in saved(128) if 'plouton' in u['source'])
    with fitz.open(BOOK / 'source.pdf') as doc:
        emphasis = source_emphasis(doc[127], u)
        assert any(e['text'] == 'plouton' and e['style']['italic'] for e in emphasis)
        assert 'inline_style' in codes(unit_issues(u, emphasis=emphasis))
        fixed = repair_composition(u, emphasis=emphasis)
    assert fixed['target'] == u['target']
    run = next(r for r in fixed['target_runs'] if r['text'] == 'plouton')
    assert run['style']['italic'] and run['style']['force_fallback']
    assert not unit_issues(fixed, emphasis=emphasis)


def test_embedded_glyph_space_is_local_and_idempotent():
    style = {'italic': False, 'bold': False}
    u = {'id': 'u', 'source': 'Symbol ♈', 'target': 'Símbolo♈', 'style': style,
         'target_runs': [{'text': 'Símbolo', 'style': style}, {'text': '♈', 'style': style}]}
    assert codes(unit_issues(u)) == {'glyph_spacing'}
    fixed = repair_composition(u)
    assert fixed['target'] == 'Símbolo ♈'
    assert repair_composition(fixed) == fixed


@pytest.mark.parametrize('n', [34, 60])
def test_real_external_glyph_gap_is_detected_and_composition_box_reserved(n):
    fit = json.loads((PILOT / f'p{n:04d}-fit.json').read_text())
    unit = next(u for u in fit['blocks'] if u['source'] == 'Symbol')
    with fitz.open(BOOK / 'source.pdf') as source, fitz.open(PILOT / f'p{n:04d}-translated.pdf') as target:
        boundaries = glyph_boundaries(source[n-1], [unit])
        assert len(boundaries) == 1
        issues = page_issues([unit], source_page=source[n-1], candidate_page=target[n-1])
        assert 'glyph_spacing' not in codes(issues)
        # The current candidate is fixed. Reproduce the narrow gap locally
        # against the same real source boundary to retain detection coverage.
        with fitz.open() as probe:
            page = probe.new_page(width=source[n-1].rect.width,height=source[n-1].rect.height)
            size = unit['size']
            right = boundaries[0]['glyph_bbox'][0]-.1
            width = fitz.get_text_length(unit['target'],fontname='helv',fontsize=size)
            page.insert_text((right-width,unit['bbox'][3]-1),unit['target'],fontsize=size)
            bad = page_issues([unit],source_page=source[n-1],candidate_page=page)
            assert 'glyph_spacing' in codes(bad)
            assert any(i.get('actual_gap', 999) < boundaries[0]['minimum_gap'] for i in bad)
        prepared = constrain_glyph_spacing(source[n-1], [unit])[0]
        from astro_translate.fidelity import _candidate_boxes
        boxes = _candidate_boxes(source[n-1], prepared, [prepared], [])
        assert boxes
        assert all(b.x1 <= boundaries[0]['glyph_bbox'][0] - boundaries[0]['minimum_gap'] for b in boxes)


def test_current_p100_and_zodiac_already_accented_in_saved_artifacts():
    for n in (34, 60, 100):
        assert not [i for i in page_issues(saved(n)) if i['code'] in ('uppercase_accent', 'zodiac_spelling')]


def test_caption_retranslation_routes_exact_units_with_safe_neighbor_context(monkeypatch):
    from astro_translate.retranslation import validate_units, correct_translation
    data = historical69()
    assert set(validate_units(data)) == {'p0069_u0004', 'p0069_u0005', 'p0069_u0012'}
    calls = []
    def fake(task, payload):
        calls.append(payload)
        wire, _ = llm._prepare(task, payload, [], [])
        neighbours = [r for r in wire['blocks'] if r.get('quality_neighbors')]
        assert len(neighbours) == 2
        assert any(n.get('request_id') for r in neighbours for n in r['quality_neighbors'])
        raise llm.ValidationFailure('test stop: no provider permitted')
    monkeypatch.setattr(llm, 'run_task', fake)
    with pytest.raises(llm.ValidationFailure, match='test stop'):
        correct_translation(data, payload_factory=lambda plan: {'blocks': plan['requests']})
    assert len(calls) == 1
    # Automatic repair isolates the last bad sentence in the body, plus two captions.
    assert len(calls[0]['blocks']) == 3


def test_sentence_memory_does_not_reuse_pre_i6_bad_text(tmp_path):
    from astro_translate.consistency import SentenceMemory, build_plan
    unit = next(u for u in saved(100) if u['source'].startswith('EDUCATION'))
    entry = next(iter(build_plan([unit])['entries'].values()))
    memory = SentenceMemory(tmp_path, 'i6')
    legacy = {'plain': 'EDUCACION', 'translation': 'EDUCACION', 'marked_source': entry['marked_source']}
    try:
        memory.db.execute('INSERT INTO sentence_memory VALUES (?,?,?)', ('i6', entry['source'], json.dumps(legacy)))
        memory.db.commit()
        assert memory.get(entry) is None
    finally:
        memory.close()


def test_lost_translated_bold_run_requires_retranslation():
    normal = {'bold': False, 'italic': False}
    bold = {**normal, 'bold': True}
    unit = {'id': 'u', 'source': 'very bright star', 'target': 'estrella muy brillante', 'style': normal,
            'style_runs': [{'start': 0, 'end': 5, 'style': normal},
                           {'start': 5, 'end': 11, 'style': bold}, {'start': 11, 'end': 16, 'style': normal}],
            'target_runs': [{'text': 'estrella muy brillante', 'style': normal}]}
    assert unit_issues(unit) == [{'unit_id': 'u', 'code': 'inline_style', 'action': 'retranslate'}]
    assert repair_composition(unit) == unit


def test_plouton_composes_with_an_actual_italic_font(tmp_path):
    from astro_translate.fidelity import _html_for
    from astro_translate.fonts import select_group_fonts
    unit = next(u for u in saved(128) if 'plouton' in u['source'])
    with fitz.open(BOOK/'source.pdf') as source:
        unit = repair_composition(unit, emphasis=source_emphasis(source[127], unit))
        fonts, _ = select_group_fonts(source, source[127], [unit], tmp_path, classify_slab=True)
        content, css, _ = _html_for(unit, fonts, tmp_path, 1)
        with fitz.open() as proof:
            page = proof.new_page(width=800, height=800)
            spare, _ = page.insert_htmlbox(fitz.Rect(10, 10, 790, 790), content, css=css,
                                           archive=fitz.Archive(str(tmp_path)), scale_low=1)
            assert spare >= 0
            spans = [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
            actual = [s for s in spans if 'plouton' in s['text']]
            assert len(actual) == 1 and actual[0]['flags'] & 2
            assert actual[0]['text'].strip() == 'plouton'


def test_local_inline_fix_keeps_paragraph_runs_in_sync():
    unit = next(u for u in saved(128) if 'plouton' in u['source'])
    unit['paragraphs'] = [{'target_runs': copy.deepcopy(unit['target_runs']), 'indent': 0}]
    with fitz.open(BOOK/'source.pdf') as doc:
        fixed = repair_composition(unit, emphasis=source_emphasis(doc[127], unit))
    assert fixed['target_runs'] == fixed['paragraphs'][0]['target_runs']


def test_all_twenty_current_pages_have_only_evidenced_i6_issues():
    import importlib.util
    spec = importlib.util.spec_from_file_location('audit_i6', BOOK.parents[1]/'scripts/audit_text_quality.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit(BOOK)
    assert len(report['pages']) == 20
    assert not [r for r in report['pages'] if r['retranslate_units']]
    # Phase 2E also detects the saved p175 space before comma. Its current
    # PDF is already normalized: this is a saved-target diagnosis, not a new
    # defect in the golden candidate.
    assert report['recompose_pages'] == [128, 175]
    p175=next(row for row in report['pages'] if row['page']==175)
    assert any(i['code']=='punctuation_spacing' for i in p175['saved_issues'])
    assert not any(i['code']=='punctuation_spacing' for i in p175['candidate_issues'])
    evidence = report['p69_model_cache_evidence']
    assert {e['translation'] for e in evidence if e['table'] == 'responses'} == {
        'es la asterismo de la Tetera', 'de la Tetera'}
    assert not [i for r in report['pages'] for i in r['saved_issues']+r['candidate_issues']
                if i['code'] in ('uppercase_accent', 'zodiac_spelling')]
