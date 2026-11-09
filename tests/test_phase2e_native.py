"""Real batch-2 defects, offline and frozen saved translations only."""
import json
from pathlib import Path
import pymupdf as fitz
import pytest
from astro_translate import fidelity
from astro_translate.core import Blocked
from astro_translate.production import Production

BOOK=Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
FIXTURES=Path(__file__).parent/'fixtures/phase2e'

@pytest.mark.parametrize('n',[38,40])
def test_isolated_numeric_span_retains_only_original_digit(n):
    from astro_translate.native_ordinals import _headings,_numeric_span_pdf
    with fitz.open(BOOK/'source.pdf') as doc:
        digit=list(_headings(doc[n-1]))[0][2]
        with _numeric_span_pdf(doc[n-1],digit) as isolated:
            spans=[s for b in isolated[0].get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
            assert len(spans)==1 and spans[0]['text']==digit['text']
            assert spans[0]['font']==digit['font'] and spans[0]['color']==digit['color']
            assert spans[0]['size']==pytest.approx(digit['size'],abs=.001)
            assert spans[0]['origin']==pytest.approx(digit['origin'],abs=.001)
            a=doc[n-1].get_pixmap(clip=fitz.Rect(digit['bbox']),dpi=220)
            b=isolated[0].get_pixmap(clip=fitz.Rect(digit['bbox']),dpi=220)
            assert a.samples==b.samples


def test_missing_translation_fails_explicitly_before_composition(tmp_path,monkeypatch):
    runner=Production(BOOK)
    runner.folder=tmp_path
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    monkeypatch.setattr(runner,'current',lambda *a:{'stage':'translated'})
    with pytest.raises(Blocked,match='(?i)(missing|requires|saved).*translation'):
        runner.compose_and_qa(21,[], 'offline')


def test_standalone_medium_heading_preserves_line_boundary():
    from astro_translate.run_in_layout import separate_standalone_heading
    payload=json.loads((FIXTURES/'p0026-translation.json').read_text())
    with fitz.open(BOOK/'source.pdf') as doc:
        unit=next(u for u in payload['blocks'] if u['source'].startswith('The signs in your chart'))
        updated=separate_standalone_heading(doc[25],unit)
        assert len(updated.get('paragraphs',[]))==2
        assert updated['paragraphs'][0]['target_runs'][0]['style']['size']==13
        assert all(r['style']['size']==11 for r in updated['paragraphs'][1]['target_runs'])
        assert ''.join(r['text'] for p in updated['paragraphs'] for r in p['target_runs'])==unit['target']


def test_mixed_numeric_word_is_granular_not_blocked():
    from astro_translate.llm import _mask_text
    values={}
    masked=_mask_text('1ST planet in 2026',[],values)
    assert '1' not in masked.replace('{n0}','').replace('{n1}','')
    assert 'ST planet in' in masked and len(values)==2


def test_real_separate_terminal_point_moves_with_translated_line():
    payload=json.loads((FIXTURES/'p0037-translation.json').read_text())
    with fitz.open(BOOK/'source.pdf') as doc:
        units=fidelity.apply_editorial_composition(doc[36],[{**u,'id':u['id'].removeprefix('p0037_')} for u in payload['blocks']])
        units=fidelity.finalize_editorial_composition(doc[36],units)
        unit=next(u for u in units if 'YOU LIKE TO WORK ALONE' in u['source'])
        assert unit.get('relocated_punctuation')
        assert unit['target'].endswith('.') and not unit['target'].endswith(' .')
        assert unit['target_runs'][-1]['text']=='.'


def test_pending_joined_quote_cannot_publish_source_placeholders(tmp_path,monkeypatch):
    payload=json.loads((FIXTURES/'p0023-translation.json').read_text())
    payload['blocks'][1]['translation_method']='requires_joined_quote_correction'
    runner=Production(BOOK)
    monkeypatch.setattr(fidelity,'_artifact_folder',lambda *a,**k:tmp_path)
    def forbidden(*a,**k):raise AssertionError('Reached composition without required joined quote')
    monkeypatch.setattr(fidelity,'apply_editorial_composition',forbidden)
    with pytest.raises(Blocked,match='Joined quote correction required'):
        fidelity.reinsert_fidelity(BOOK,payload,runner.glossary,runner.style,runner.protected,
            artifact_dir=tmp_path,corrections_path=tmp_path/'absent.json')


def test_page_reference_is_not_english_but_prose_still_is():
    from astro_translate.language import residual_english
    assert not residual_english(['pp. —', 'pp.166—183'])
    assert residual_english(['see the next pages'])
