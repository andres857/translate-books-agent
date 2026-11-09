"""`compose-offline --pseudo`: deterministic pseudo-Spanish through the real compositor, no LLM."""
from collections import Counter
import json
from pathlib import Path
import re
import shutil

import pymupdf as fitz
import pytest

from astro_translate import llm, pseudo
from astro_translate.core import PROTECTED
from astro_translate.numeric_order import numeric_quantifier_issues
from astro_translate.text_quality import marked_style_issues, opening_punctuation_issues, text_issues

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT/'books/the-secrets-of-astrology'
STYLE = re.compile(r'\{v\d+\}')
LONG = ('The Sun moves through all twelve signs of the zodiac in 365 days, and each sign '
        'takes about 30° of the wheel. Mars rules Aries, while the Moon rules Cancer and moods')


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('pseudo composition must never call run_task')
    from astro_translate import adapters, safe_vlm, composition_corrections
    for module in (llm, adapters, safe_vlm):
        monkeypatch.setattr(module, 'run_task', fail, raising=False)
    monkeypatch.setattr(safe_vlm, 'judge_safe_pair', fail)


def letters(text):
    return len(re.sub(r'\{[nv]\d+\}', '', PROTECTED.sub('', text)))


def test_pseudo_text_is_deterministic_expanded_and_preserves_protected_values():
    out = pseudo.pseudo_text(LONG)
    assert out == pseudo.pseudo_text(LONG)
    assert Counter(PROTECTED.findall(out)) == Counter(PROTECTED.findall(LONG))
    assert 1.18 <= letters(out)/letters(LONG) <= 1.32
    assert set(out) & set('áéíóúñ')
    assert not set(re.findall(r'[A-Za-z]+', out)) & {'The', 'Sun', 'moves', 'signs', 'zodiac', 'wheel'}
    assert text_issues(LONG, out, 'x') == [] and not numeric_quantifier_issues(LONG, out)
    assert out[0].isupper()
    assert all(w[1:] == w[1:].lower() for w in re.findall(r'[^\W\d_]+', out) if w[:1].isupper() and not w.isupper())


def test_pseudo_text_respects_case_markers_extras_and_glossary():
    marked = '{v1}ARIES THE RAM{v2}{v3}is bold and quick to act{v4}'
    out = pseudo.pseudo_text(marked, extras=['RAM'], glossary=[{'source': 'Aries', 'target': 'Aries'},
                                                                 {'source': 'quick', 'target': 'rápido'}])
    assert STYLE.findall(out) == STYLE.findall(marked)
    first = out.split('{v2}')[0].removeprefix('{v1}')
    assert first == first.upper() and 'RAM' in first and 'ARIES' in first
    assert 'rápido' in out
    assert marked_style_issues(marked, out, 'x') == []
    heading = pseudo.pseudo_text('How astrology began')  # p12 subhead: growth letters stay lower-case
    assert heading[1:] == heading[1:].lower() and heading[0].isupper()
    assert pseudo.pseudo_text('ARIES') == pseudo.pseudo_text('ARIES').upper() and len(pseudo.pseudo_text('ARIES')) >= 6


def test_pseudo_text_adds_spanish_opening_marks():
    out = pseudo.pseudo_text('What is a horoscope? It is a map. How wonderful!')
    assert opening_punctuation_issues(out, 'x') == []
    assert out.count('¿') >= 1 and out.count('¡') >= 1


def test_defect_groups_report_scale_fit_fonts_margins_overlap_and_overflow(tmp_path):
    folder = tmp_path/'compose'
    folder.mkdir()
    fit = {'page': 7, 'fit_complete': True, 'group_scales': {'p0007_body_9.5': .78, 'p0007_display_40': 1},
           'blocks': [{'id': 'u0001', 'scale_group': 'p0007_body_9.5', 'scale': .78, 'edit_bbox': [10, 10, 100, 50]},
                      {'id': 'u0002', 'scale_group': 'p0007_display_40', 'scale': 1, 'edit_bbox': [10, 60, 100, 90]}]}
    failed = {'page': 8, 'fit_complete': False, 'failures': [
        {'id': 'u0003', 'group': 'p0008_caption_8.0_300', 'source': 'x', 'target': 'y'},
        {'id': 'u0009_story', 'group': 'story_u0009', 'reason': 'column_story_does_not_fit_at_0.75'}]}
    fonts = [{'scale_group': 'p0007_body_9.5', 'original_name': 'SohoPro-Light', 'original_missing': ['á', 'ñ'],
              'group_fallback': True, 'original_empty_glyph_outline': False, 'original_text_roundtrip_failed': False,
              'chosen': {'path': '/x/AstroSlab.ttf', 'source': 'fallback'}}]
    qa = {'page': 7, 'non_text_pixel_diff': .002, 'numbers_equal': True, 'images_equal': True,
          'complete_translated_text': False, 'typography_audit': {'ok': True},
          'editorial_margin_issues': [{'code': 'editorial_margin', 'unit_id': 'u0001'}],
          'text_quality_issues': [{'code': 'repeated_ngram', 'unit_id': 'u0001'}],
          'residual_english': ['the end'], 'glossary_missing': []}
    rows = [{'page': 7, 'status': 'review', 'new_text_overlaps': [{'a': 'x', 'b': 'y', 'bbox': [1, 1, 2, 2]}],
             'page_overflow': [{'text': 'tail', 'bbox': [600, 10, 640, 20]}]},
            {'page': 8, 'status': 'fit_failed'}]
    for name, data in (('p0007-fit.json', fit), ('p0008-fit.json', failed), ('p0007-fonts.json', fonts),
                       ('p0007-qa.json', qa)):
        (folder/name).write_text(json.dumps(data))
    groups = pseudo.mechanical_groups(rows, folder)
    codes = {g['code'] for g in groups}
    assert {'scale_below_0.80', 'fit_failed', 'font_fallback', 'ink_outside_edit_boxes', 'new_text_overlap',
            'page_bounds_overflow', 'qa_complete_translated_text'} <= codes
    scale = next(g for g in groups if g['code'] == 'scale_below_0.80')
    assert scale['pages'] == [7] and scale['cause'].endswith('(body)')
    fit_causes = {g['cause'] for g in groups if g['code'] == 'fit_failed'}
    assert 'column_story_does_not_fit_at_0.75' in fit_causes and any('caption' in c for c in fit_causes)
    font = next(g for g in groups if g['code'] == 'font_fallback')
    assert font['cause'] == 'SohoPro-Light: sin cobertura del texto traducido' and font['missing_chars'] == 'áñ'
    lexical = {g['code']: g['lexical_pseudo_artifact'] for g in groups}
    assert lexical['residual_english'] is True and lexical['scale_below_0.80'] is False


secrets = pytest.mark.skipif(not (SECRETS/'source.pdf').is_file(), reason='Secrets source PDF not available')


@pytest.fixture
def secrets_copy(tmp_path, monkeypatch):
    """Real PDF/profile/manifest in a temporary book; only the G3 gate is doubled."""
    from astro_translate import core, fidelity
    from astro_translate.profiles import load_profile
    book = tmp_path/'book'
    (book/'work').mkdir(parents=True)
    shutil.copyfile(SECRETS/'source.pdf', book/'source.pdf')
    for name in ('profile.toml', 'pipeline.toml', 'manifest.json'):
        shutil.copyfile(SECRETS/'work'/name, book/'work'/name)
    profile = load_profile(SECRETS)

    def copy(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                target = book/value['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(profile.checked_file(value), target)
            for child in value.values():
                copy(child)
    copy(profile.data)
    for module in (core, fidelity):
        monkeypatch.setattr(module, 'require_approval', lambda *a, **k: {}, raising=False)
    return book


@secrets
def test_real_pages_compose_offline_without_inference_or_production_writes(secrets_copy):
    book = secrets_copy
    watched = [book/'work'/n for n in ('manifest.json', 'costs.jsonl', 'llm_cache.sqlite', 'review_queue.json')]
    before = {p: p.read_bytes() if p.exists() else None for p in watched}
    report = pseudo.compose_offline(book, pages='12,38')
    assert report['llm_calls'] == 0 and report['mode'] == 'pseudo'
    assert {p: p.read_bytes() if p.exists() else None for p in watched} == before
    assert not (book/'work/production').exists()
    rows = {r['page']: r for r in report['pages']}
    assert set(rows) == {12, 38}
    assert all(r['status'] != 'error' for r in rows.values()), rows
    folder = book/'work/pseudo'
    saved = json.loads((folder/'input_snapshot/book/work/production/p0012-translation.json').read_text())
    assert all(Counter(PROTECTED.findall(u['target'])) == Counter(PROTECTED.findall(u['source'])) for u in saved['blocks'])
    assert sum(u['target'] != u['source'] for u in saved['blocks']) >= len(saved['blocks'])-1
    defects = json.loads((folder/'defects.json').read_text())
    assert defects['mode'] == 'pseudo' and defects['llm_calls'] == 0
    assert any(d['code'] in ('font_fallback', 'font_fallback_applied') for d in defects['defects'])
    assert (folder/'defects.md').read_text().startswith('# Defectos agrupados')
    composed = [r for r in rows.values() if r.get('candidate')]
    for row in composed:
        with fitz.open(row['candidate']) as doc:
            assert set(doc[0].get_text()) & set('áéíóúñ')


@secrets
def test_compose_offline_rejects_output_outside_book_work(secrets_copy, tmp_path):
    with pytest.raises(ValueError):
        pseudo.compose_offline(secrets_copy, pages='12', output=tmp_path/'elsewhere')


@secrets
def test_cli_compose_offline_requires_pseudo_and_reports(secrets_copy, capsys):
    from astro_translate.__main__ import main
    with pytest.raises(SystemExit):
        main(['compose-offline', '--book', str(secrets_copy), '--pages', '38'])
    capsys.readouterr()
    assert main(['compose-offline', '--book', str(secrets_copy), '--pseudo', '--pages', '38', '--expansion', '1.25']) == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index('{\n'):])
    assert summary['mode'] == 'pseudo' and summary['llm_calls'] == 0
    assert summary['defects_md'].endswith('work/pseudo/defects.md')


@secrets
def test_roundoff_verifier_reads_single_page_candidates(tmp_path):
    """Regression found by pseudo p7: candidates are one page since Fase 2I (output_page=0)."""
    from astro_translate.candidate_storage import extract_page
    from astro_translate.composition_patterns import verify_immutable_float_roundoff
    single = extract_page(SECRETS/'source.pdf', 7, tmp_path/'p0007.pdf')
    with fitz.open(SECRETS/'source.pdf') as source, fitz.open(single) as candidate:
        assert len(candidate) == 1
        assert verify_immutable_float_roundoff(source, candidate, 7, (), candidate_index=0)['ok']


@secrets
def test_real_toc_page_7_pseudo_composes_without_error(secrets_copy):
    report = pseudo.compose_offline(secrets_copy, pages='7')
    row = report['pages'][0]
    assert row['status'] != 'error', row.get('trace')
