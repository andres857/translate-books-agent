"""Gap 8 (G-ROUTE): full-page raster + visible native text is mixed, not scanned.

Real cases: chapter openings of The Secrets of Astrology (p11/25/85/137/167,
e.g. p11 'CHAPTER' SohoPro-Bold 17 pt at (413,240,501,257) over a full-page
raster) and the same openings in Astrology Using the Wisdom of the Stars.
Re-triage must merge into an existing manifest without dropping page state.
"""
import json
import shutil
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate.core import Blocked, classify_page, retriage, triage

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT/'books/the-secrets-of-astrology'
WISDOM = ROOT/'books/astrology-wisdom-stars'
OPENINGS = [11, 25, 85, 137, 167]
REAL_SCANS = [1, 8, 10, 24, 32, 58, 84, 96, 110, 136, 152, 166, 184]
secrets = pytest.mark.skipif(not (SECRETS/'source.pdf').is_file(), reason='Secrets source PDF not available')
wisdom = pytest.mark.skipif(not (WISDOM/'source.pdf').is_file(), reason='Wisdom source PDF not available')


@secrets
def test_secrets_chapter_openings_are_mixed_and_real_scans_stay_scanned():
    with fitz.open(SECRETS/'source.pdf') as doc:
        for n in OPENINGS:
            row = classify_page(doc[n-1])
            assert row['type'] == 'mixed' and row['route'] == 'A', n
            assert row['visible_native_letters'] >= 8, n
        for n in REAL_SCANS:
            row = classify_page(doc[n-1])
            # Slug lines (Helvetica at y=679) lie outside the page box.
            assert row['type'] == 'scanned' and row['route'] == 'C', n
            assert row['visible_native_letters'] == 0, n
        counts = [classify_page(p)['type'] for p in doc]
    assert counts.count('scanned') == 13


@wisdom
def test_wisdom_openings_mixed_without_new_scans():
    manifest = json.loads((WISDOM/'work/manifest.json').read_text())
    before = {r['page']: r['type'] for r in manifest['pages']}
    with fitz.open(WISDOM/'source.pdf') as doc:
        after = {p.number+1: classify_page(p)['type'] for p in doc}
    changed = {n for n in before if before[n] != after[n]}
    # Only scanned->mixed transitions, and only for pages with visible native words.
    assert changed and all(before[n] == 'scanned' and after[n] == 'mixed' for n in changed)
    assert {13, 27, 61, 89, 119, 155, 199} <= changed
    assert all(after[n] == 'scanned' for n in (1, 4, 12, 26, 46, 60))


def test_invisible_ocr_layer_keeps_scan(tmp_path):
    book = tmp_path/'book'; (book/'work').mkdir(parents=True)
    doc = fitz.open(); page = doc.new_page(width=300, height=400)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 40), False); pix.set_rect(pix.irect, (200, 200, 200))
    page.insert_image(page.rect, pixmap=pix)
    page.insert_text((40, 60), 'Hidden OCR words', fontsize=10, render_mode=3)
    visible = doc.new_page(width=300, height=400)
    visible.insert_image(visible.rect, pixmap=pix)
    visible.insert_text((40, 60), 'CHAPTER TITLE', fontsize=17)
    doc.save(book/'source.pdf')
    with fitz.open(book/'source.pdf') as d:
        assert classify_page(d[0])['type'] == 'scanned'
        assert classify_page(d[1])['type'] == 'mixed'


def _book_copy(tmp_path, source):
    """Temporary book whose manifest is the real one as triage v1 left it.

    The real manifest may already be re-triaged (orchestrator applied
    ``retriage --apply``). The copy undoes that in memory only: pages with a
    ``retriage_history`` get back their first recorded ``from`` type/route and
    the openings are forced to scanned/route C, so the tests never depend on
    the live state of the book. The real manifest is only read.
    """
    book = tmp_path/'book'; (book/'work').mkdir(parents=True)
    (book/'source.pdf').symlink_to(source/'source.pdf')
    manifest = json.loads((source/'work/manifest.json').read_text())
    for row in manifest['pages']:
        history = row.pop('retriage_history', None)
        if history:
            row.update(type=history[0]['from']['type'], route=history[0]['from']['route'])
        if row['page'] in OPENINGS:
            row.update(type='scanned', route='C')
        if history or row['page'] in OPENINGS:
            row['stages'] = {'C': row.get('stages', {}).get('C', 'pending')}
    manifest['triage_version'] = 1
    manifest['scanned_ratio'] = sum(r['type'] == 'scanned' for r in manifest['pages'])/len(manifest['pages'])
    (book/'work/manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return book


@secrets
def test_retriage_dry_run_then_apply_preserves_state(tmp_path):
    book = _book_copy(tmp_path, SECRETS)
    original = json.loads((book/'work/manifest.json').read_text())
    plan = retriage(book, original.get('triage_config', {}))
    assert plan['applied'] is False
    assert json.loads((book/'work/manifest.json').read_text()) == original
    changed = {c['page'] for c in plan['changes']}
    assert set(OPENINGS) <= changed
    assert all(c['from']['type'] == 'scanned' and c['to']['type'] == 'mixed' for c in plan['changes'])
    applied = retriage(book, original.get('triage_config', {}), apply=True)
    assert applied['applied'] is True
    after = json.loads((book/'work/manifest.json').read_text())
    old = {r['page']: r for r in original['pages']}
    for row in after['pages']:
        before = old[row['page']]
        # Status, attempts, QA links and every other key survive.
        for key, value in before.items():
            if key in ('type', 'route', 'stages'):
                continue
            assert row[key] == value, (row['page'], key)
        if row['page'] in changed:
            assert row['type'] == 'mixed' and row['route'] == 'A'
            assert set(row['stages']) == {'A', 'B', 'C'} and row['stages']['A'] == 'pending'
            assert row['retriage_history'][-1]['from']['route'] == 'C'
        else:
            assert row == before
    assert after['scanned_ratio'] == pytest.approx(13/192)
    # Idempotent: a second run has nothing to change.
    assert retriage(book, original.get('triage_config', {}))['changes'] == []


def test_triage_with_changed_config_merges_instead_of_overwriting(tmp_path):
    book = tmp_path/'book'; (book/'work').mkdir(parents=True)
    doc = fitz.open(); doc.new_page().insert_text((72, 72), 'Plain native text page', fontsize=11)
    doc.save(book/'source.pdf')
    first = triage(book)
    first['pages'][0].update(status='done_qa_ok', attempts=3, production_qa='x.json')
    (book/'work/manifest.json').write_text(json.dumps(first))
    second = triage(book, {'scanned_char_threshold': 50})
    row = second['pages'][0]
    assert row['status'] == 'done_qa_ok' and row['attempts'] == 3 and row['production_qa'] == 'x.json'
    assert second['triage_config'] == {'scanned_char_threshold': 50}


def test_retriage_refuses_changed_source(tmp_path):
    book = tmp_path/'book'; (book/'work').mkdir(parents=True)
    doc = fitz.open(); doc.new_page().insert_text((72, 72), 'Text', fontsize=11); doc.save(book/'source.pdf')
    triage(book)
    with open(book/'source.pdf', 'ab') as stream:
        stream.write(b'\n')
    with pytest.raises(Blocked):
        retriage(book, {})


@secrets
def test_cli_retriage_is_dry_run_by_default(tmp_path, capsys):
    from astro_translate.__main__ import main
    book = _book_copy(tmp_path, SECRETS)
    before = (book/'work/manifest.json').read_bytes()
    assert main(['retriage', '--book', str(book)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['applied'] is False and {c['page'] for c in out['changes']} >= set(OPENINGS)
    assert (book/'work/manifest.json').read_bytes() == before
