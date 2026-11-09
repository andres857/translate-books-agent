"""Production candidates are byte-deterministic (orchestrator report, 2026-11-09).

Every save of a candidate wrote a random trailer ``/ID`` (checked on p0037), and every
fresh font cache stamped its static instances with the current time (fontTools
``head.modified``), which changed the calibrated family name embedded in the PDF. Each
``produce --recompose`` therefore changed ``candidate_sha256`` for an identical page and
invalidated ``review-visual`` and every approval bound to that hash.
"""
from __future__ import annotations

import re

import pymupdf as fitz
import pytest

from test_composition_gaps import SECRETS, secrets

PRODUCTION = SECRETS/'work/production'


def _page_doc():
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 50), 'Medio Cielo', fontsize=12)
    doc.set_metadata({'creationDate': "D:20261109120000+00'00'", 'modDate': "D:20261109120001+00'00'",
                      'producer': 'test'})
    return doc


@pytest.mark.parametrize('saver', ['core', 'compact'])
def test_saved_pdf_has_content_derived_id_and_no_dates(saver, tmp_path):
    from astro_translate.candidate_storage import _save_compact
    from astro_translate.core import _save_pdf
    save = _save_pdf if saver == 'core' else _save_compact
    outputs = []
    for name in ('a.pdf', 'b.pdf'):
        with _page_doc() as doc:
            save(doc, tmp_path/name)
        outputs.append((tmp_path/name).read_bytes())
    assert outputs[0] == outputs[1]
    with fitz.open(tmp_path/'a.pdf') as doc:
        kind, value = doc.xref_get_key(-1, 'ID')
        assert kind == 'array'
        first, second = re.findall(r'<([0-9A-Fa-f]+)>', value)
        assert first == second and len(first) == 32
        assert not doc.metadata['creationDate'] and not doc.metadata['modDate']
        assert doc[0].get_text().strip() == 'Medio Cielo'
    # The identifier is derived from the content: other text, other /ID.
    with _page_doc() as doc:
        doc[0].insert_text((20, 80), 'Fondo del Cielo', fontsize=12)
        save(doc, tmp_path/'c.pdf')
    with fitz.open(tmp_path/'c.pdf') as doc:
        assert doc.xref_get_key(-1, 'ID')[1] != value


def test_extracted_page_is_deterministic_and_keeps_source_content(tmp_path):
    """extract_page of the same source page twice gives identical bytes."""
    from astro_translate.candidate_storage import extract_page
    with _page_doc() as doc:
        doc.save(tmp_path/'source.pdf')  # random /ID, as any ordinary save
    extract_page(tmp_path/'source.pdf', 1, tmp_path/'one.pdf')
    extract_page(tmp_path/'source.pdf', 1, tmp_path/'two.pdf')
    assert (tmp_path/'one.pdf').read_bytes() == (tmp_path/'two.pdf').read_bytes()


@secrets
def test_fallback_font_instances_are_byte_identical_across_fresh_caches(tmp_path):
    """Static/calibrated instances must not carry the build time (it renames the family)."""
    from astro_translate.fonts import fallback_font
    style = {'font': 'SohoPro-Light', 'size': 11.0, 'color': 0, 'weight': 300, 'bold': False,
             'italic': False, 'serif': True, 'width': 100}
    with fitz.open(SECRETS/'source.pdf') as doc:
        reference = next(fitz.Font(fontbuffer=doc.extract_font(f[0])[3]) for f in doc[22].get_fonts()
                         if 'SohoPro-Light' in f[3] and doc.extract_font(f[0])[3])
    one = fallback_font(style, tmp_path/'one', reference, classify_slab=True)
    two = fallback_font(style, tmp_path/'two', reference, classify_slab=True)
    assert one['sha256'] == two['sha256'] and one['name'] == two['name']


# Real page: p37 (SohoPro display title «BRAVE & / FEARLESS», body in SohoPro-Light, AstroSlab
# replacements), composed twice from its saved Sonnet translation in two private snapshot books,
# then extracted to its single page exactly as ``production.compose_and_qa`` does.
@secrets
@pytest.mark.skipif(not (PRODUCTION/'p0037-translation.json').is_file(), reason='Saved p37 translation not available')
def test_composing_p37_twice_gives_the_same_candidate_sha256(tmp_path):
    from test_batch16_composition import _saved_fit
    from astro_translate.candidate_storage import extract_candidate
    from astro_translate.core import sha256
    composed, extracted = [], []
    for run in ('first', 'second'):
        fit = _saved_fit(37, tmp_path/run)
        assert fit['fit_complete'], fit.get('failures')
        composed.append(sha256(fit['output']))
        extract_candidate(fit)
        assert fit['output_sha256'] == sha256(fit['output'])
        extracted.append(fit['output_sha256'])
    assert composed[0] == composed[1]
    assert extracted[0] == extracted[1]
    with fitz.open(fit['output']) as doc:
        identifier = doc.xref_get_key(-1, 'ID')[1]
    assert re.fullmatch(r'\[\s*<([0-9A-F]{32})>\s*<\1>\s*\]', identifier, re.I), identifier
