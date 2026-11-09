"""Fit defects of the pseudo-translation of The Secrets of Astrology, pages 61–192.

Every case is a real unit of ``books/the-secrets-of-astrology/source.pdf`` (page, bbox and font
in each comment). No LLM: pseudo answers (``pseudo.pseudo_page_payload``) or identity answers.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from test_composition_gaps import SECRETS, _pseudo_fit, secrets


def _finalized(page_number):
    """Real page units after the book's composition finalizer, with identity targets."""
    from astro_translate import fidelity
    from astro_translate.core import read_extras
    from astro_translate.production import Production
    extras = read_extras(Production(SECRETS).protected)
    doc = fitz.open(SECRETS/'source.pdf')
    page = doc[page_number-1]
    units = fidelity.extract_fidelity_units(page, extras)
    units = [{**u, 'target': u['source'], 'target_runs': [{'text': u['source'][r['start']:r['end']], 'style': r['style']}
                                                         for r in u['style_runs']]} for u in units]
    units = fidelity.finalize_editorial_composition(page, fidelity.apply_editorial_composition(page, units))
    return doc, page, units, extras


# RUN-IN p91 «THE SUN THROUGH THE SIGNS»: twelve entries, SohoPro-Bold/Light 10 pt, leading 12 pt,
# columns x=88.4 and x=303.5. Each entry fills its three-line slot to 2 pt above a 0.5 pt rule
# (e.g. Taurus 88.4,171.7,263.6,205.7, rule y=207.8) and leaves ≈11.4 pt of white above it, under
# the previous rule (y=160.3). Growing only down kept three lines, so the 1.25 pseudo text only fit
# at 0.76 (scale_below_0.80). The entry may claim half the white gap above it (shared gap).
@secrets
def test_run_in_entry_claims_half_of_the_gap_above_it_p91():
    from astro_translate import fidelity
    from astro_translate.run_in_layout import _separators
    doc, page, units, extras = _finalized(91)
    with doc:
        taurus = next(u for u in units if u.get('run_in_heading') and u['source'].startswith('Taurus'))
        boxes = fidelity._candidate_boxes(page, taurus, units, fidelity.immutable_boxes(page, extras))
        top = fitz.Rect(taurus['bbox']).y0
        rule = max(r.y1 for r in _separators(page) if abs(r.x0-88.4) < 2 and r.y1 <= top)
        assert any(b.y0 < top-4 for b in boxes), boxes
        assert all(b.y0 >= rule+(top-rule)/2-.01 for b in boxes)
        # Boxes that keep the original top are tried first (no move unless needed).
        assert boxes[0].y0 == pytest.approx(top)


@secrets
def test_run_in_columns_fit_at_point_eight_p91(tmp_path, monkeypatch):
    fit = _pseudo_fit(91, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    scales = {g: s for g, s in fit['group_scales'].items() if '_run_in_' in g}
    assert len(scales) == 2 and min(scales.values()) >= .8, scales


# MERGE p22: «YOUR SUN SIGN» body u0009 (366.0,309.4,514.1,418.4) SohoPro-Light 11 pt is followed in the
# same PDF block by a whitespace-only line and the 8 pt SohoPro-Medium cross-reference «The Sun / see
# pp.88—91» (438.0,432.9,514.1,453.9); likewise «The Moon  see pp.92—95» under u0011. Extraction keeps
# one translation unit (saved translations and ids stay valid), but a size change plus a vertical step
# separates composition units: the reference keeps its own 8 pt box and group, split at the unique
# target style-run boundary.
@secrets
def test_size_change_after_a_line_break_composes_the_reference_apart_p22(tmp_path, monkeypatch):
    fit = _pseudo_fit(22, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    for parent in ('u0009', 'u0011'):
        body = next(b for b in fit['blocks'] if b['id'] == parent)
        ref = next(b for b in fit['blocks'] if b['id'] == parent+'_split')
        assert {s['size'] for s in body['spans']} == {11.0} and 'see pp.' not in body['source']
        assert {s['size'] for s in ref['spans']} == {8.0} and 'see pp.' in ref['source']
        assert {r['style']['size'] for r in ref['target_runs']} == {8.0}
        assert ref['scale_group'].startswith(body['scale_group'])  # printed with its paragraph
        assert fitz.Rect(ref['edit_bbox']).y0 >= fitz.Rect(body['edit_bbox']).y1-1


# The same rule in the local QA: a composed unit whose spans change size across a line break.
@secrets
def test_local_qa_flags_a_merged_size_break_p22():
    from astro_translate.fidelity import extract_fidelity_units
    from astro_translate.text_quality import page_issues
    with fitz.open(SECRETS/'source.pdf') as doc:
        page = doc[21]
        from astro_translate.fidelity import _make_unit
        merged = next(u for u in extract_fidelity_units(page) if u['source'].startswith('We say'))
        k = next(i for i, sp in enumerate(merged['spans']) if sp['size'] == 8)
        body, ref = (_make_unit(merged['spans'][:k], 8, False, []), _make_unit(merged['spans'][k:], 8, False, []))
        body['id'], ref['id'] = 'u0009', 'u0009_split'
        clean = [{**u, 'target': u['source']} for u in (body, ref)]
        assert not [i for i in page_issues(clean) if i['code'] == 'merged_style_break']
        found = [i for i in page_issues([{**merged, 'target': merged['source']}]) if i['code'] == 'merged_style_break']
    assert len(found) == 1 and found[0]['action'] == 'recompose'


# BIND p173: the display quote «In the sign on the MC, we find qualities for which we want to be /
# known, admired, and respected» (u0007 141,523,384,581 + u0008 141,583,401,611, SohoPro 27 pt) is now
# joined as u0007_quote before composition. The page binding (units_sha256 of the reviewed «legend»
# override) hashed the joined unit instead of its unchanged source members → «Profile unit source hash
# changed». The joined unit keeps its members; the binding hashes the same source content as before.
@secrets
def test_joined_quote_keeps_the_profile_binding_p173(tmp_path, monkeypatch):
    fit = _pseudo_fit(173, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    quote = next(b for b in fit['blocks'] if b['id'] == 'u0007_quote')
    assert [m['id'] for m in quote['source_members']] == ['u0007', 'u0008']


# BIND p175: «The Nodes represent cycles of crisis re-evaluation / release, and then forward motion…»
# (u0007 92,530,458,557 + u0008 92,560,460,616, 27 pt) is covered by the reviewed composition override
# p0175-composition-translations.json (one joined u0007 with its traced commas). That saved override
# supplies every target of the page, so no generic joined-quote retranslation is required there.
@secrets
def test_saved_composition_override_owns_its_quote_p175(tmp_path, monkeypatch):
    from astro_translate import pseudo
    from astro_translate.composition_corrections import quote_job
    from astro_translate.core import sha256
    with fitz.open(SECRETS/'source.pdf') as doc:
        assert quote_job(doc[174], pseudo.pseudo_page_payload(doc[174], 175, sha256(SECRETS/'source.pdf'), extras=(),
                                                               glossary=(), expansion=1.25)[0]['blocks'], '') == []
    fit = _pseudo_fit(175, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')


# BIND p141: the reviewed clitic repair (scope work/pilot/p0141-clitic-scope.json, u0021 «…to protect» /
# u0022 «yourself.») is bound to the source and to the reviewed target. A new translation (here pseudo)
# changes the target: the repair no longer applies, but the source binding still holds.
@secrets
def test_clitic_scope_only_applies_to_its_reviewed_target_p141(tmp_path, monkeypatch):
    fit = _pseudo_fit(141, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    assert not any(b.get('orthographic_repair') for b in fit['blocks'])


# MERGE p170: PDF block 7 holds the end of the «Ascendant» call-out (SohoPro-LightCondensed 9 pt,
# right-aligned at x≈292, «…Ascendant is your chart ruler.» 200.5,428.1,290.7,437.1) and, starting
# above it in the next column, the bold heading «The Ascendant–Descendant axis» (SohoPro-MediumCondensed
# 364.1,422.4,475.0,431.4). Composed as one box, the chart_callouts and caption groups failed together
# at 0.75. Another weight starting above, in another column, is composed as a separate unit.
@secrets
def test_weight_change_starting_above_in_another_column_composes_apart_p170(tmp_path, monkeypatch):
    fit = _pseudo_fit(170, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    heading = next(b for b in fit['blocks'] if b['id'] == 'u0009_split')
    assert heading['source'].startswith('The Ascendant–Descendant axis')
    assert 'chart ruler' not in heading['source']
    assert fitz.Rect(heading['edit_bbox']).x0 > 360
    assert min(fit['group_scales'].values()) >= .75


# INLINE p128: «…from the Greek word plouton, meaning “wealth”…» (SohoPro-LightCondensed, «plouton» set
# in italics by a sheared text matrix, not by the font: span flags say roman). Production restores the
# italic run where the foreign word survives in the target (repair_composition + source_emphasis). The
# pseudo-translator rewrote the foreign word, so the local QA reported inline_style: a pseudo artifact.
# Pseudo answers now keep such source-italic foreign words, as a translator does.
@secrets
def test_pseudo_keeps_matrix_italic_foreign_word_and_its_italic_p128(tmp_path, monkeypatch):
    fit = _pseudo_fit(128, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    unit = next(b for b in fit['blocks'] if 'plouton' in b['source'])
    runs = [r for r in unit['target_runs'] if 'plouton' in r['text']]
    assert len(runs) == 1 and runs[0]['style']['italic'] and runs[0]['text'].strip() == 'plouton'


# HEADROOM p4: credits caption u0019 «Illustrations Cerise Lindsey, 2020.» (77.5,290.5,205.9,298.5),
# 8 pt slab on the yellow panel, composed by caption_growth.insert_anchored. Story clips its ink to
# the layout rectangle and puts the first baseline 0.8 em × line-height below its top, so the acute of
# the pseudo «Á» lost its tip (ink top 290.22 for a box top of 290.50). The anchored insertion now
# keeps fonts.insert_htmlbox_headroom: same lines and baselines, only the clip moves out.
@secrets
def test_anchored_caption_keeps_the_capital_accent_p4(tmp_path, monkeypatch):
    import numpy as np
    fit = _pseudo_fit(4, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    block = next(b for b in fit['blocks'] if b['id'] == 'u0019')
    assert block.get('caption_growth')
    box = fitz.Rect(block['edit_bbox'])
    with fitz.open(fit['output']) as doc:
        page = doc[fit.get('output_page', 3)]
        chars = [c for b in page.get_text('rawdict', clip=box+(0, -3, 0, 0))['blocks'] for l in b.get('lines', [])
                 for s in l['spans'] for c in s['chars'] if c['c'] == 'Á']
        assert chars
        r = fitz.Rect(chars[0]['bbox'])
        clip = fitz.Rect(r.x0, box.y0-4, r.x1, box.y1)
        pix = page.get_pixmap(dpi=600, clip=clip)
    ink = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    top = clip.y0+np.where(ink[:, :, :3].max(axis=2) < 120)[0].min()*72/600
    assert top < box.y0-.5, (top, box.y0)


# HEADROOM column_flow: Story frames clip their ink like insert_htmlbox. The written clip of each frame
# moves out by the headroom (top and sides only); placement and baselines do not change.
def test_story_clip_widening_moves_only_the_clip():
    import io
    from astro_translate.column_flow import _widen_clips
    css = 'body,div{margin:0;padding:0;line-height:1.05} p{margin:0}'
    html = '<div><p><span style="font-size:20pt">ÓÍÉ ÁNGEL</span></p></div>'
    stream = io.BytesIO()
    writer = fitz.DocumentWriter(stream)
    device = writer.begin_page(fitz.Rect(0, 0, 300, 200))
    story = fitz.Story(html, user_css=css)
    story.place(fitz.Rect(20, 50, 280, 150))
    story.draw(device)
    writer.end_page()
    writer.close()
    plain = fitz.open(stream=stream.getvalue(), filetype='pdf')
    wide = fitz.open(stream=_widen_clips(stream.getvalue(), 12), filetype='pdf')
    assert [s['bbox'] for b in plain[0].get_text('dict')['blocks'] for l in b['lines'] for s in l['spans']] == \
           [s['bbox'] for b in wide[0].get_text('dict')['blocks'] for l in b['lines'] for s in l['spans']]
    import numpy as np
    def top(doc):
        pix = doc[0].get_pixmap(dpi=144)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return np.where(a[:, :, :3].min(axis=(1, 2)) < 200)[0].min()/2
    assert top(plain) == 50 and top(wide) < 50


# SHORT FORM p179 «MAJOR/MINOR ASPECTS» fields: «Root 8 Symbol ☌ Angle 45° Orb 2°», SohoPro-Medium 7 pt
# labels squeezed between protected values (e.g. u0003_cell0 «Root» 154.2,103.9,173.5,110.9 before «8»).
# No free space: the composition stops with protected_label_no_space and the review needs a short form.
# Only the first failing cell got a request, so a review would need one compose round per cell. All
# cells that cannot fit alone at 0.75 now get a request at once; no inference is made.
@secrets
def test_every_label_without_space_gets_a_short_form_request_p179(tmp_path, monkeypatch):
    import json
    from astro_translate.core import Blocked
    from astro_translate.protected_labels import FAILURE
    with pytest.raises(Blocked):
        _pseudo_fit(179, tmp_path, monkeypatch)
    folder = tmp_path/'input_snapshot/book/work/compose'
    fit = json.loads((folder/'p0179-fit.json').read_text())
    requests = json.loads((folder/'p0179-short-form-requests.json').read_text())['requests']
    failed = [f for f in fit['failures'] if f['reason'] == FAILURE]
    assert len(failed) > 1
    asked = {r['unit_id'] for r in requests}
    assert len(asked) > 1 and len(asked) == len(requests)
    assert all(f['needs_short_form'] == (f['id'] in asked) for f in fit['failures'])


# DISPLAY p97: centered display quote «"A PERSON'S CHART IS THE DOOR OPENING US INTO THEIR MYTHICAL
# SYSTEM."» (u0000_quote, six lines, ≈99–450 pt wide, SohoPro LightCompressed/HeavyCompressed 65 pt) on a
# flat orange page. Each line was limited to the width of the source quote box, so at 0.75 the longer
# pseudo wording had no line partition. The centered quote may grow symmetrically into free, flat
# space at both sides (same centers), never over other units or artwork.
@secrets
def test_centered_display_quote_grows_symmetrically_into_free_space_p97(tmp_path, monkeypatch):
    fit = _pseudo_fit(97, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    quote = next(b for b in fit['blocks'] if b.get('display_quote'))
    assert quote['scale'] >= .75
    box, source = fitz.Rect(quote['edit_bbox']), fitz.Rect(quote['bbox'])
    assert box.x0+box.x1 == pytest.approx(source.x0+source.x1, abs=.01)


# DISPLAY p183: heading u0025 «IDENTIFYING THEMES IN YOUR CHART» (35.5,101.2,359.9,117.2, 16 pt bold) and
# intro u0026 (35.4,129.4,408.1,161.4, 14 pt light) are set over the pale raster chart-wheel watermark
# that the original text already overlaps. Only column stories accepted that verified pale underlay, so
# every box of these blocks failed with native_ink_over_raster_artwork, even at 0.75.
@secrets
def test_pale_watermark_under_original_text_is_background_for_every_block_p183(tmp_path, monkeypatch):
    fit = _pseudo_fit(183, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')
    for ident in ('u0025', 'u0026'):
        block = next(b for b in fit['blocks'] if b['id'] == ident)
        assert block['scale'] >= .75 and block['raster_guard']['ok']


# PSEUDO/VALIDATOR p183: run-in «Aspect patterns» + body «Aspect patterns reflect a complex inner pattern»
# (SohoPro-Medium/Light 9 pt, column 2 of STEP 1). The source repeats a 2-word term; the approved glossary
# form «Configuraciones de aspectos» has 3 words, so every valid answer (pseudo or LLM) repeats a 3-gram
# and was rejected as a new repeated_ngram: pseudo_generation_failed. A source repetition licenses one
# target repetition of the same occurrence, whatever its word count.
@secrets
def test_source_repetition_licenses_a_longer_glossary_repetition_p183():
    from astro_translate import pseudo
    from astro_translate.core import sha256
    from astro_translate.production import Production
    from astro_translate.core import read_extras
    from astro_translate.text_quality import text_issues
    source = '{v1}Aspect patterns {v2}Aspect patterns reflect a complex inner pattern{v3}'
    target = '{v1}Configuraciones de aspectos {v2}Configuraciones de aspectos reflejan un patrón interno complejo{v3}'
    assert not [i for i in text_issues(source, target, 'p0183') if i['code'] == 'repeated_ngram']
    # A repetition the source does not have is still reported.
    assert [i for i in text_issues('{v1}Aspect patterns reflect a pattern{v2}',
                                   '{v1}Configuraciones de aspectos configuraciones de aspectos reflejan{v2}',
                                   'x') if i['code'] == 'repeated_ngram']
    runner = Production(SECRETS)
    with fitz.open(SECRETS/'source.pdf') as doc:
        _, _, failures = pseudo.pseudo_page_payload(doc[182], 183, sha256(SECRETS/'source.pdf'),
                                                    extras=read_extras(runner.protected),
                                                    glossary=pseudo._terms(runner.glossary), expansion=1.25)
    assert failures == []


# PROTECTED p139: the chart angles «D C» (509.2,248.7,524.2,257.7) and «I C» (139.9,405.7,151.4,414.7),
# SohoPro-Bold 9 pt, are letter-spaced MC/IC-type tokens; «MC» on the same wheel was already protected.
# They were sent to translation as text (pseudo «Fá C») and their 9 pt group failed at 0.75.
@secrets
def test_letter_spaced_chart_angles_are_protected_tokens_p139(tmp_path, monkeypatch):
    from astro_translate.fidelity import extract_fidelity_units, immutable_span
    assert immutable_span({'source': 'D C', 'font': 'SohoPro-Bold'})
    assert immutable_span({'source': 'I C', 'font': 'SohoPro-Bold'})
    assert not immutable_span({'source': 'I am', 'font': 'SohoPro-Bold'})
    with fitz.open(SECRETS/'source.pdf') as doc:
        assert not [u for u in extract_fidelity_units(doc[138]) if u['source'] in ('D C', 'I C')]
    fit = _pseudo_fit(139, tmp_path, monkeypatch)
    assert fit['fit_complete'], fit.get('failures')


# NEEDS_SHORT_FORM p138: diagram label u0009 «Friendships» (492.0,161.8,540.5,170.3, SohoPro-Italic 8.5 pt)
# sits between the protected «11TH» and the editorial edge (543.6). The pseudo word is 56.3 pt wide at
# 0.75 and cannot wrap; there is no free space. The page stops with a prepared short-form request
# (reason needs_short_form), no inference.
@secrets
def test_word_without_free_space_is_blocked_as_needs_short_form_p138(tmp_path, monkeypatch):
    import json
    fit = _pseudo_fit(138, tmp_path, monkeypatch)
    assert not fit['fit_complete']
    blocked = {f['id']: f for f in fit['failures']}
    assert blocked['u0009']['reason'] == 'needs_short_form' and blocked['u0009']['needs_short_form']
    folder = tmp_path/'input_snapshot/book/work/compose'
    requests = json.loads((folder/'p0138-short-form-requests.json').read_text())['requests']
    assert 'u0009' in {r['unit_id'] for r in requests}
    assert all(f['reason'] in ('needs_short_form', 'group_blocked_by_sibling', 'fit_failed_at_0.75')
               for f in fit['failures'])
