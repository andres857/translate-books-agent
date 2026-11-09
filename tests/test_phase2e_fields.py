"""Stored styled field rows retain their original baseline beside glyphs."""
import json
from copy import deepcopy
from pathlib import Path
import pymupdf as fitz
import pytest
from astro_translate import fidelity
from astro_translate.core import Blocked
from astro_translate.production import Production

BOOK=Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
FIXTURES=Path(__file__).parent/'fixtures/phase2e'


def table(page):
    payload=json.loads((FIXTURES/'p0038-translation.json').read_text())
    units=[{**u,'id':u['id'].removeprefix('p0038_')} for u in payload['blocks']]
    units=fidelity.apply_editorial_composition(page,units)
    return next(u for u in units if u['id']=='u0006')


def test_saved_field_rows_partition_styles_without_translating():
    from astro_translate.structured_layout import prepare_saved_field_rows
    with fitz.open(BOOK/'source.pdf') as doc:
        unit=table(doc[37]);rows=prepare_saved_field_rows(doc[37],[unit])
        assert len(rows)==7
        assert len(rows[0]['spans'])==1
        assert rows[0]['source'].strip()=='Symbol'
        assert rows[0]['target']==unit['target_runs'][0]['text'].split()[0]
        assert fidelity._normalized(''.join(u['target'] for u in rows))==fidelity._normalized(unit['target'])
        assert [s for u in rows for s in u['spans']]==unit['spans']
        assert len({u['scale_group'] for u in rows})==1
        assert rows[0]['bbox']==unit['spans'][0]['bbox']
        ambiguous=deepcopy(unit);ambiguous['target_runs'][0]['text']+='unmatched '
        ambiguous['target']=''.join(r['text'] for r in ambiguous['target_runs'])
        with pytest.raises(Blocked,match='field row'):
            prepare_saved_field_rows(doc[37],[ambiguous])


def test_real_field_table_keeps_symbol_label_alongside_native_glyph(tmp_path,monkeypatch):
    runner=Production(BOOK)
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    payload=json.loads((FIXTURES/'p0038-translation.json').read_text())
    fit=fidelity.reinsert_fidelity(BOOK,payload,runner.glossary,runner.style,runner.protected,
        artifact_dir=tmp_path,phase='production',corrections_path=tmp_path/'absent.json')
    assert fit['fit_complete'],fit.get('failures')
    qa=fidelity.fidelity_qa(BOOK,fit,runner.glossary,runner.protected,artifact_dir=tmp_path,update_manifest=False)
    assert qa['local_fidelity_ok'],qa['text_quality_issues']
    assert qa['non_text_pixel_diff']==0
