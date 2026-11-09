"""I7 real saved translations; offline composition only, outputs in tmp_path."""
import copy
import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import fidelity, fonts

BOOK = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology'
PILOT = BOOK / 'work/pilot'
sys.path.insert(0, str(PILOT))


def chart_units(page):
    from local_layout import book_composition
    saved = json.loads((PILOT / 'p0169-translation.json').read_text())
    canonical = {u['id']: u for u in fidelity.extract_fidelity_units(page)}
    units = []
    for supplied in saved['blocks']:
        own = canonical[supplied['id'].removeprefix('p0169_')]
        assert own['source'] == supplied['source'] and own['spans'] == supplied['spans']
        units.append({**own, 'target': supplied['target'], 'target_runs': supplied['target_runs']})
    return fidelity.finalize_editorial_composition(page, book_composition(page, units))


def test_real_chart_has_eight_complete_title_body_blocks():
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        units = chart_units(doc[168])
        groups = [u for u in units if u.get('chart_callout')]
        assert len(groups) == 8
        assert len({u['scale_group'] for u in groups}) == 1
        saved = json.loads((PILOT / 'p0169-translation.json').read_text())['blocks']
        for u in groups:
            members = [v for v in saved if v['id'].removeprefix('p0169_') in u['composed_ids']]
            assert fidelity._normalized(u['target']) == fidelity._normalized(''.join(v['target'] for v in members))
            assert len(u['paragraphs']) == 2
            assert u['target_runs'][0]['style']['weight'] == 500
            assert u['target_runs'][-1]['style']['weight'] == 300
        assert next(u for u in groups if u['id'] == 'u0003')['alignment'] == 'right'
        assert 'u0016' in next(u for u in groups if u['id'] == 'u0014')['composed_ids']


def test_real_chart_fits_uniformly_without_shortening(tmp_path):
    from local_layout import composition_context
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        page = doc[168]
        units = chart_units(page)
        groups = [u for u in units if u.get('chart_callout')]
        assert len(groups) == 8
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        mask = fidelity._vector_mask(page)
        guards = fidelity.immutable_boxes(page, ())
        occupied = []
        for u in groups:
            boxes = fidelity._candidate_boxes(page, u, units, guards)
            assert boxes, u['id']
            assert all(page.rect.contains(b) and not any(b.intersects(g) for g in guards) for b in boxes)
            accepted = None
            for box in boxes:
                if any(box.intersects(r) for r in occupied):
                    continue
                ok, info = fidelity._test_fit(page, u, box, .8, mapping, tmp_path, mask)
                if ok:
                    accepted = box
                    assert info['vector_overlap_pixels'] == 0 and info['raster_guard']['ok']
                    break
            assert accepted is not None, u['id']
            occupied.append(accepted)


@pytest.mark.parametrize('number', [86, 87])
def test_real_vertical_ruler_fits_saved_targets_and_keeps_graphics(number, tmp_path):
    from vertical_local import fit_vertical, draw_vertical
    from astro_translate.rotated_pilot import _safe_redact, _chars
    plan = json.loads((PILOT / 'vertical/plan.json').read_text())
    targets = json.loads((PILOT / 'vertical/targets.json').read_text())
    answers = {r['id']: r['translation'] for r in targets['data']['labels']}
    units = [u for u in plan['units'] if u['page'] == number]
    with fitz.open(BOOK / 'source.pdf') as doc:
        page = doc[number-1]
        original = page.get_pixmap(dpi=110).samples
        accepted, mapping, diagnostics = fit_vertical(page, page, units, answers, tmp_path)
        assert accepted, diagnostics[-5:]
        assert len(accepted) == len(units)
        assert len({u['scale'] for u in accepted}) == 1
        assert min(u['scale'] for u in accepted) >= .75
        assert all(u['target'] == answers[u['id']] for u in accepted)
        assert page.get_pixmap(dpi=110).samples == original
        candidate = fitz.open(stream=doc.tobytes(), filetype='pdf')
        cp = candidate[number-1]
        before = [{k: v for k, v in d.items() if k != 'seqno'} for d in cp.get_drawings()]
        _safe_redact(cp, accepted)
        for u in accepted:
            draw_vertical(cp, u, u['target'], mapping[u['font']]['path'], u['render_size'], u['extension'])
        assert [{k: v for k, v in d.items() if k != 'seqno'} for d in cp.get_drawings()] == before
        for u in accepted:
            assert fidelity._normalized(u['target']) in fidelity._normalized(cp.get_text())
        assert_pixels_unchanged_outside_text(page, cp, accepted)
        candidate.close()


def test_real_chart_transaction_has_no_failures_and_preserves_protected_art(tmp_path, monkeypatch):
    from local_layout import composition_context
    from astro_translate.core import read_extras
    from astro_translate.rotated_pilot import _chars
    # Redirect artifacts only; keep real source validation, G3 check, fonts,
    # PyMuPDF composition, extraction and collision checks intact.
    monkeypatch.setattr(fidelity, '_artifact_folder', lambda *a, **kw: tmp_path)
    payload = json.loads((PILOT / 'p0169-translation.json').read_text())
    glossary = BOOK / 'work/glossary'
    with composition_context():
        result = fidelity.reinsert_fidelity(
            BOOK, payload, glossary / 'astro_en_es.csv', glossary / 'style_guide.md',
            glossary / 'do_not_translate.txt', artifact_dir=tmp_path, phase='pilot')
    assert result['fit_complete'], result['failures']
    assert not result['failures']
    assert result['group_scales']['p0169_chart_callouts'] == 1.0
    assert min(result['group_scales'].values()) == 1.0
    groups = [u for u in result['blocks'] if u.get('chart_callout')]
    assert len(groups) == 8 and len({u['scale'] for u in groups}) == 1
    with fitz.open(BOOK / 'source.pdf') as original, fitz.open(result['output']) as translated:
        page, candidate = original[168], translated[168]
        assert not (fidelity._immutable_characters(page, read_extras(glossary/'do_not_translate.txt')) - fidelity._immutable_characters(candidate, (), False))
        normalize = lambda p: [{k:v for k,v in d.items() if k != 'seqno'} for d in p.get_drawings()]
        assert normalize(page) == normalize(candidate)
        for u in groups:
            assert fidelity._normalized(u['target']) in fidelity._normalized(candidate.get_text())
        assert_pixels_unchanged_outside_text(page, candidate, result['blocks'])
        # The long heading wraps to exactly two lines at the selected scale.
        heading = next(u for u in groups if u['id'] == 'u0003')
        font = heading['font_runs'][0]['engine_name']
        lines = [l for b in candidate.get_text('dict')['blocks'] for l in b.get('lines', [])
                 if any(s['font'].replace('-', ' ') == font.replace('-', ' ') and fitz.Rect(heading['edit_bbox']).contains(fitz.Rect(s['bbox'])) for s in l['spans'])]
        assert len(lines) == 2
        candidate.get_pixmap(dpi=110).save(tmp_path/'p169.png')
    (tmp_path/'scales.json').write_text(json.dumps(result['group_scales']))


def assert_pixels_unchanged_outside_text(before, after, units):
    import numpy as np
    from PIL import Image, ImageDraw
    a, b = before.get_pixmap(dpi=110), after.get_pixmap(dpi=110)
    mask = Image.new('L', (a.width, a.height), 255)
    draw = ImageDraw.Draw(mask)
    for u in units:
        # ink_bbox: accented-capital ink above the box (fonts.insert_htmlbox_headroom),
        # excluded exactly as in fidelity_qa's non_text_pixel_diff mask.
        for box in (u['bbox'], u['edit_bbox'])+((u['ink_bbox'],) if u.get('ink_bbox') else ()):
            x0, y0, x1, y1 = [v*110/72 for v in box]
            draw.rectangle((x0-2,y0-2,x1+2,y1+2), fill=0)
    aa = np.frombuffer(a.samples, dtype=np.uint8).reshape(a.height,a.width,3)
    bb = np.frombuffer(b.samples, dtype=np.uint8).reshape(b.height,b.width,3)
    assert not np.any(np.any(aa != bb, axis=2) & (np.asarray(mask)>0))


def test_vertical_compose_real_candidate_transaction(tmp_path, monkeypatch):
    """Execute the supplement function, not rebuild_pilot; redirect ALL outputs."""
    import vertical_local as vertical
    from astro_translate.core import Blocked
    folder = tmp_path / 'pilot'
    vfolder = folder / 'vertical'
    vfolder.mkdir(parents=True)
    for name in ('plan.json', 'targets.json'):
        (vfolder/name).write_bytes((PILOT/'vertical'/name).read_bytes())
    isolated_book = tmp_path / 'book'
    isolated_book.mkdir()
    (isolated_book/'source.pdf').symlink_to(BOOK/'source.pdf')
    # The supplement now requires an explicit source-bound book profile. Copy
    # its small dependency artifacts into this isolated book, never write back
    # through symlinks to live work files.
    import shutil, tomllib
    profile=tomllib.loads((BOOK/'work/profile.toml').read_text())
    records=[profile['hooks'],*profile['files'].values()]
    records += [p['composition_translations'] for p in profile['pages'].values() if 'composition_translations' in p]
    for relative in ['work/profile.toml']+[r['path'] for r in records]:
        target=isolated_book/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(BOOK/relative,target)
    # fidelity_qa updates book/work/manifest.json independently of artifact_dir.
    # Keep that side effect inside the temporary book as well.
    monkeypatch.setattr(vertical, 'BOOK', isolated_book)
    monkeypatch.setattr(vertical, 'FOLDER', folder)
    monkeypatch.setattr(vertical, 'V', vfolder)
    monkeypatch.setattr(fidelity, '_artifact_folder', lambda *a, **kw: folder)
    def no_llm(*args, **kwargs):
        raise AssertionError('I7 must not call an LLM')
    monkeypatch.setattr(vertical, 'run_task', no_llm)
    from local_layout import composition_context
    g = BOOK/'work/glossary'
    # Live fit.json may already include translated verticals. Reapplying them
    # would collide with themselves; exercise the actual native -> supplement flow.
    with composition_context():
        native = fidelity.reinsert_fidelity(
            BOOK, json.loads((PILOT/'p0086-translation.json').read_text()),
            g/'astro_en_es.csv', g/'style_guide.md', g/'do_not_translate.txt',
            artifact_dir=folder, phase='pilot')
    assert native['fit_complete'], native['failures']
    vertical.compose(86)
    result = json.loads((folder/'p0086-fit.json').read_text())
    assert result['group_scales']['p0086_vertical'] == .98
    report = json.loads((folder/'p0086-qa.json').read_text())
    assert report['non_text_pixel_diff'] == 0
    assert report['vectors_equal'] and report['images_equal']
    assert report['numbers_equal'] and report['glyph_set_equal']
    assert not [r for r in report['omitted_rotated_text'] if r['direction'] == [0., -1.]]
    assert report['complete_translated_text']
    assert report['typography_audit']['ok']


def test_missing_vertical_translation_blocks_without_inference(tmp_path):
    from vertical_local import fit_vertical
    from astro_translate.core import Blocked
    plan = json.loads((PILOT/'vertical/plan.json').read_text())
    units = [u for u in plan['units'] if u['page'] == 86]
    with fitz.open(BOOK/'source.pdf') as doc, pytest.raises(Blocked, match='Missing saved'):
        fit_vertical(doc[85], doc[85], units, {}, tmp_path)
