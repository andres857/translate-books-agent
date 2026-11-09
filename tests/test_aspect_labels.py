"""I5: saved p179 labels, actual fonts/rendering, no backend or pilot writes."""
import copy
import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import fidelity, fonts

BOOK = Path(__file__).resolve().parents[1] / 'books/the-secrets-of-astrology'
sys.path.insert(0, str(BOOK / 'work/pilot'))
from local_layout import composition_context, book_composition, precise_redaction_rects


def saved_units(page):
    payload = json.loads((BOOK / 'work/pilot/p0179-translation.json').read_text())
    canonical = {u['id']: u for u in fidelity.extract_fidelity_units(page)}
    units = []
    for supplied in payload['blocks']:
        own = canonical[supplied['id'].split('_', 1)[1]]
        assert supplied['source'] == own['source'] and supplied['spans'] == own['spans']
        units.append({**own, 'target': supplied['target'], 'target_runs': supplied['target_runs']})
    return fidelity.finalize_editorial_composition(page, book_composition(page, units))


def labels(units):
    return [u for u in units if set(u['source'].split()) <= {'Root', 'Symbol', 'Angle', 'Orb'}]


def fit_labels(page, units, mapping, root, mask):
    guards = fidelity.immutable_boxes(page, ())
    members = labels(units)
    for percent in range(100, 74, -1):
        chosen = []
        for unit in members:
            for box in fidelity._candidate_boxes(page, unit, units, guards):
                ok, info = fidelity._test_fit(page, unit, box, percent/100, mapping, root, mask)
                if ok:
                    chosen.append((unit, info))
                    break
            else:
                break
        if len(chosen) == len(members):
            return percent/100, chosen
    return None, []


@pytest.mark.parametrize('y,x', [(215.4, 374), (261.7, 374), (103.9, 154)],
                         ids=['semicuadratura', 'sesquicuadratura', 'conjuncion'])
def test_real_rows_have_one_label_per_span(y, x):
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        units = labels(saved_units(doc[178]))
        row = [u for u in units if abs(u['bbox'][1]-y) < .1 and x <= u['bbox'][0] < x+150]
        assert [u['source'] for u in row] == ['Root', 'Symbol', 'Angle', 'Orb']
        assert all(len(u['spans']) == 1 for u in row)


def protected_signature(page, original_fonts=None):
    return [(s['font'], s['size'], s['color'], [(c['c'], c['origin'], c['bbox']) for c in s['chars']])
            for b in page.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans']
            if (original_fonts is None or s['font'] in original_fonts)
            and fidelity.immutable_span({'source': ''.join(c['c'] for c in s['chars']), 'font': s['font']})]


def test_real_table_render_has_clearance_and_unchanged_values(tmp_path):
    with fitz.open(BOOK / 'source.pdf') as doc, composition_context():
        page = doc[178]
        units = saved_units(page)
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        mask = fidelity._vector_mask(page)
        scale, chosen = fit_labels(page, units, mapping, tmp_path, mask)
        assert scale is not None, 'Full labels do not fit at minimum 0.75'
        # Exercise the same insertion path as production, only in a disposable PDF.
        out = fitz.open(stream=doc.tobytes(), filetype='pdf')
        candidate = out[178]
        before = protected_signature(page)
        for unit, _ in chosen:
            for rect in precise_redaction_rects(page, unit):
                candidate.add_redact_annot(rect, fill=False)
        candidate.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        boxes = []
        # Compare non-whitespace glyph boxes: symbol spans include trailing
        # spaces that reach into the next label's genuine free gap.
        guards = [fitz.Rect(c[2]) for _, _, _, chars in before for c in chars if c[0].strip()]
        for unit, info in chosen:
            overlay = fitz.open()
            p = overlay.new_page(width=page.rect.width, height=page.rect.height)
            if unit.get('protected_label'):
                from astro_translate.protected_labels import label_overlay
                overlay.close()
                overlay, finished = label_overlay(page, unit, fitz.Rect(info['box']), scale, mapping)
                assert finished
                p = overlay[0]
            else:
                content, css, _ = fidelity._html_for(unit, mapping, tmp_path, scale)
                p.insert_htmlbox(fitz.Rect(info['box']), content, css=css, archive=fitz.Archive(str(tmp_path)), scale_low=1)
            spans = [s for b in p.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
            assert len(spans) == 1 and spans[0]['text'] == unit['target']
            box = fitz.Rect(spans[0]['bbox'])
            assert not any(box.intersects(g) for g in guards), (unit['source'], box)
            assert not any(box.intersects(other) for other in boxes)
            neighbours = [g for g in guards if g.y0 < box.y1 and g.y1 > box.y0]
            right = min((g.x0 for g in neighbours if g.x0 >= box.x1), default=page.rect.x1)
            left = max((g.x1 for g in neighbours if g.x1 <= box.x0), default=0)
            assert right-box.x1 >= .25*spans[0]['size']-.01, unit['id']
            assert box.x0-left >= .25*spans[0]['size']-.01, unit['id']
            assert info['vector_overlap_pixels'] == 0 and info['raster_guard']['ok']
            candidate.show_pdf_page(page.rect, overlay, 0)
            overlay.close()
            boxes.append(box)
        assert protected_signature(candidate, {s[0] for s in before}) == before
        assert len(chosen) == 36
        assert len({info['scale'] for _, info in chosen}) == 1
        assert scale == .76
        out.close()


def test_actual_reinsertion_in_temporary_artifact_folder(tmp_path, monkeypatch):
    """Real approved saved payload and production fitter; never write pilot outputs."""
    from astro_translate.fidelity_audit import audit_rendered_styles
    from astro_translate import protected_labels
    payload = json.loads((BOOK / 'work/pilot/p0179-translation.json').read_text())
    glossary = BOOK / 'work/glossary'
    monkeypatch.setattr(fidelity, '_artifact_folder', lambda *a, **k: tmp_path)
    stop = protected_labels.stop_failed_labels
    (tmp_path/'work').mkdir()
    monkeypatch.setattr(protected_labels, 'stop_failed_labels',
                        lambda book, folder, page, result: stop(tmp_path, folder, page, result))
    with composition_context():
        result = fidelity.reinsert_fidelity(
            BOOK, payload, glossary/'astro_en_es.csv', glossary/'style_guide.md',
            glossary/'do_not_translate.txt', artifact_dir=tmp_path, phase='pilot')
    assert result['fit_complete'], result['failures']
    fields = [u for u in result['blocks'] if u.get('protected_label')]
    assert len(fields) == 36 and {u['scale'] for u in fields} == {.76}
    assert all(u['review'] for u in fields)  # Below .80 is explicitly reviewable.
    with fitz.open(BOOK/'source.pdf') as source, fitz.open(result['output']) as out:
        before = protected_signature(source[178])
        assert protected_signature(out[178], {s[0] for s in before}) == before
        assert audit_rendered_styles(out[178], {**result, 'blocks': fields})['ok']
        # Retain only a disposable test render, not the published comparison.
        out[178].get_pixmap(dpi=110).save(tmp_path/'p179-i5.png')


def test_anchor_shift_at_floor_and_impossible_gap(tmp_path):
    from astro_translate.protected_labels import label_overlay
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        page = doc[178]
        units = saved_units(page)
        mapping, _ = fonts.select_group_fonts(doc, page, units, tmp_path)
        unit = copy.deepcopy(next(u for u in labels(units) if u['source'] == 'Angle'))
        unit['protected_label']['anchor'] += 5
        box = fidelity._candidate_boxes(page, unit, units, [])[0]
        overlay, ok = label_overlay(page, unit, box, .76, mapping)
        assert not ok  # Must try uniform shrinking before shifting left.
        overlay.close()
        overlay, ok = label_overlay(page, unit, box, .75, mapping)
        assert ok
        span = overlay[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        assert span['origin'][0] < unit['protected_label']['anchor']
        overlay.close()
        # Reuse the production guard: successful placement must still reject
        # rendered ink over vector artwork, even in an otherwise roomy gap.
        import numpy as np
        mask = np.ones_like(fidelity._vector_mask(page))
        ok, info = fidelity._test_fit(page, unit, box, .75, mapping, tmp_path, mask)
        assert not ok and info['vector_overlap_pixels'] > 0
        unit['protected_label']['left'] = unit['protected_label']['right']-3
        box = fidelity._candidate_boxes(page, unit, units, [])[0]
        overlay, ok = label_overlay(page, unit, box, .75, mapping)
        assert not ok and not overlay[0].get_text()
        overlay.close()
        overlay, ok = label_overlay(page, unit, box, .74, mapping)
        assert not ok
        overlay.close()


def test_failed_labels_record_evidence_and_stop(tmp_path):
    from astro_translate.core import Blocked, atomic_json
    from astro_translate.protected_labels import FAILURE, stop_failed_labels
    folder = tmp_path/'work/pilot'
    folder.mkdir(parents=True)
    result = {'page': 179, 'fit_complete': False, 'failures': [{'id': 'field', 'reason': FAILURE}]}
    atomic_json(folder/'p0179-fit.json', result)
    atomic_json(tmp_path/'work/review_queue.json', [{'page': 1, 'reason': 'prior'}])
    with fitz.open(BOOK/'source.pdf') as doc:
        with pytest.raises(Blocked, match='human-approved supplement'):
            stop_failed_labels(tmp_path, folder, doc[178], result)
    queue = json.loads((tmp_path/'work/review_queue.json').read_text())
    assert queue[0] == {'page': 1, 'reason': 'prior'}
    assert queue[1]['reason'] == FAILURE
    assert all(Path(p).exists() for p in queue[1]['artifacts'])
    assert not (folder/'p0179-translated.pdf').exists()
