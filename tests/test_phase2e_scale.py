"""Explain the source-artwork bound on the trait-page sibling scale."""
import json
from pathlib import Path
import pymupdf as fitz
from astro_translate import fidelity
from astro_translate.fonts import select_group_fonts
from astro_translate.text_quality import repair_composition,source_emphasis,matrix_italics

BOOK=Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
FIXTURES=Path(__file__).parent/'fixtures/phase2e'


def test_trait_body_scale_is_bounded_by_neighboring_raster_art(tmp_path):
    payload=json.loads((FIXTURES/'p0037-translation.json').read_text())
    with fitz.open(BOOK/'source.pdf') as doc:
        page=doc[36]
        units=[{**u,'id':u['id'].removeprefix('p0037_')} for u in payload['blocks']]
        units=fidelity.finalize_editorial_composition(page,fidelity.apply_editorial_composition(page,units))
        shears=matrix_italics(page)
        units=[repair_composition(u,emphasis=source_emphasis(page,u,shears=shears)) for u in units]
        unit=next(u for u in units if u['id']=='u0004')
        siblings=[u for u in units if u['id'] in ('u0001','u0004','u0009','u0021')]
        assert len({u['scale_group'] for u in siblings})==1
        fonts,_=select_group_fonts(doc,page,units,tmp_path)
        box=fitz.Rect(unit['bbox'])+(0,0,0,12)
        mask=fidelity._vector_mask(page)
        ok,larger=fidelity._test_fit(page,unit,box,.89,fonts,tmp_path,mask)
        assert not ok and larger['raster_guard']['raster_overlap_pixels']>0
        ok,fitting=fidelity._test_fit(page,unit,box,.88,fonts,tmp_path,mask)
        assert ok and fitting['raster_guard']['raster_overlap_pixels']==0
        assert fitting['scale']>=.75
