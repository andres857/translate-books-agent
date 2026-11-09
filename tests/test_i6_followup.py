"""Real pilot regressions, offline only; no candidate artifacts are rewritten."""
import copy
import json
from pathlib import Path

import pymupdf as fitz
import pytest
from pilot_fixtures import pre_resegment

from astro_translate.fidelity import extract_fidelity_units, verified_editorial_text
from astro_translate.text_quality import page_issues

BOOK = Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'
PILOT = BOOK/'work/pilot'


def artifact(n, kind='fit'):
    return json.loads((PILOT/f'p{n:04d}-{kind}.json').read_text())


def test_real_p179_protected_cells_do_not_measure_symbol_font_as_label():
    with fitz.open(BOOK/'source.pdf') as src, fitz.open(PILOT/'p0179-translated.pdf') as dst:
        issues = page_issues(artifact(179)['blocks'], source_page=src[178], candidate_page=dst[178])
    assert not [i for i in issues if i['code'] == 'glyph_spacing']


def test_real_p69_caption_is_one_centered_unit_and_other_ids_stay_stable():
    with fitz.open(BOOK/'source.pdf') as doc:
        units = extract_fidelity_units(doc[68],merge_captions=True)
    caption = [u for u in units if 'This central area' in u['source']]
    assert len(caption) == 1
    assert caption[0]['source'] == 'This central area is the Teapot asterism'
    assert caption[0]['alignment'] == 'center'
    assert caption[0]['merged_from'] == ['u0003', 'u0004', 'u0005']
    assert next(u for u in units if u['id'] == 'u0012')['source'] == next(
        u['source'] for u in artifact(69, 'translation')['blocks'] if u['id'] == 'p0069_u0012')


def test_real_p128_quoted_foreign_term_is_scoped_to_italic_occurrence():
    from astro_translate.text_quality import quoted_foreign_terms
    with fitz.open(BOOK/'source.pdf') as src, fitz.open(PILOT/'p0128-translated.pdf') as dst:
        data = artifact(128)
        proofs = quoted_foreign_terms(src[127], dst[127], data['blocks'])
        assert len(proofs) == 1
        assert proofs[0]['code'] == 'quoted_foreign_term'
        assert proofs[0]['text'] == 'plouton'
        assert any('plouton' in t for t in verified_editorial_text(dst, dst[127], data))
        assert not any('plouton' in t for t in verified_editorial_text(dst, dst[127], data, quoted_terms=proofs))
        bad = copy.deepcopy(data['blocks'])
        for u in bad:
            for r in u['target_runs']:
                r['style']['italic'] = False
        assert not quoted_foreign_terms(src[127], dst[127], bad)


def fresh(n):
    with fitz.open(BOOK/'source.pdf') as doc:
        return [{**u, 'id': f'p{n:04d}_'+u['id'], 'page': n} for u in extract_fidelity_units(doc[n-1],merge_captions=True)]


def test_resegment_sends_only_joins_and_preserves_every_other_byte(monkeypatch):
    from astro_translate import llm
    from astro_translate.resegmentation import plan_resegmentation, resegment_bytes, block_slices
    raw = pre_resegment(69)
    units = fresh(69)
    plan = plan_resegmentation(json.loads(raw), units)
    assert len(plan['joined_units']) == 4
    calls = []
    def fake(task, payload):
        from types import SimpleNamespace
        calls.append(payload)
        # Echo-only fake: tests routing/validation, not translation quality.
        return SimpleNamespace(data={'blocks': [dict(id=r['id'], translation=r['source']) for r in payload['blocks']]}, cached=True)
    monkeypatch.setattr(llm, 'run_task', fake)
    updated, audit = resegment_bytes(raw, units, payload_factory=lambda p: {'blocks': p['requests']})
    assert len(calls) == 1 and len(calls[0]['blocks']) == 4
    old_parts, new_parts = block_slices(raw), block_slices(updated)
    removed = {i for row in plan['replacements'] for i in row['old_ids']}
    assert all(raw[a:b] == updated[new_parts[i][0]:new_parts[i][1]] for i,(a,b) in old_parts.items() if i not in removed)
    assert not removed.intersection(new_parts)
    assert audit['run_task_calls'] == 1
    again, log = resegment_bytes(updated, units, no_llm=True)
    assert again == updated and log['run_task_calls'] == 0


def test_resegment_blocks_stale_source_and_no_llm(monkeypatch):
    from astro_translate import llm
    from astro_translate.resegmentation import resegment_bytes
    raw = pre_resegment(69)
    monkeypatch.setattr(llm, 'run_task', lambda *a, **kw: pytest.fail('provider forbidden'))
    with pytest.raises(ValueError, match='no-llm'):
        resegment_bytes(raw, fresh(69), no_llm=True)
    units = fresh(69)
    units[0]['spans'][0]['source'] += ' changed'
    with pytest.raises(ValueError, match='source'):
        resegment_bytes(raw, units, no_llm=True)


@pytest.mark.parametrize('defect', ['style', 'color', 'gap', 'alignment', 'drawing', 'terminal'])
def test_caption_join_rejects_incompatible_geometry_or_style(defect):
    from astro_translate.captions import merge_caption_units
    from astro_translate.fidelity import _make_unit
    style = dict(font='Helvetica', size=9, color=0, weight=400, bold=False, italic=False, serif=False, width=100)
    def unit(i, y):
        span = dict(source='caption fragment', bbox=[100,y,170,y+9], font='Helvetica', size=9,
                    color=0, flags=0, rotate=0, style=copy.deepcopy(style))
        return {**_make_unit([span],i,False,[]), 'id':f'u{i:04d}', 'container_id':f'c{i}', 'scale_group':'captions'}
    units = [unit(1,100), unit(2,110)]
    with fitz.open() as doc:
        page = doc.new_page()
        assert len(merge_caption_units(page, units)) == 1
        if defect in ('style','color'):
            units[1]['spans'][0]['style']['bold' if defect=='style' else 'color'] = True if defect=='style' else 1234
        elif defect == 'gap':
            units[1]['spans'][0]['bbox'] = [100,130,170,139]
        elif defect == 'alignment':
            units[1]['spans'][0]['bbox'] = [300,110,370,119]
        elif defect == 'drawing':
            page.draw_line((90,109.5),(180,109.5))
        else:
            units[0]['source'] += '.'
        assert len(merge_caption_units(page, units)) == 2


def test_old_composition_override_survives_join_without_replacing_new_target(tmp_path):
    import sys
    sys.path.insert(0,str(PILOT))
    import local_layout
    from astro_translate.fidelity import apply_composition_overrides
    import tomllib
    cfg = tomllib.loads((BOOK/'work/pipeline.toml').read_text())
    paths = [BOOK.parents[1]/cfg['paths'][k] for k in ('glossary','style_guide','do_not_translate')]
    old = json.loads(pre_resegment(180))['blocks']
    # Offline echo fixture for the new unit; old saved translations for the rest.
    byid = {u['id']: u for u in old}
    units = []
    for u in fresh(180):
        if u['id'] in byid:
            u = copy.deepcopy(byid[u['id']])
        else:
            u = {**u, 'target':u['source'], 'target_runs':[{'text':u['source'],'style':u['style']}]}
        units.append({**u, 'id':u['id'].removeprefix('p0180_')})
    with fitz.open(BOOK/'source.pdf') as src:
        composed = local_layout.book_composition(src[179],units)
    result = apply_composition_overrides(BOOK,composed,PILOT/'p0180-composition-translations.json',180,*paths)
    new = next(u for u in result if u.get('merged_from'))
    assert new['target'] == new['source']
    assert all('composition_override_sha256' in u for u in result if not u.get('merged_from'))


@pytest.mark.parametrize('defect', ['none','source_plain','target_plain','different','long','not_mention'])
def test_foreign_exception_requires_all_three_constraints(defect):
    from astro_translate.fidelity import _make_unit, span_style
    from astro_translate.text_quality import quoted_foreign_terms
    term = 'alpha beta gamma delta' if defect=='long' else 'plouton'
    prefix = 'A description ' if defect=='not_mention' else 'The word '
    with fitz.open() as source, fitz.open() as target:
        a,b = source.new_page(),target.new_page()
        a.insert_text((50,80),prefix,fontsize=12)
        a.insert_text((50+fitz.get_text_length(prefix,fontsize=12),80),term,
                      fontname='helv' if defect=='source_plain' else 'heit',fontsize=12)
        spans = []
        for block in a.get_text('dict')['blocks']:
            for line in block.get('lines',[]):
                for s in line['spans']:
                    spans.append(dict(source=s['text'],bbox=list(s['bbox']),font=s['font'],size=s['size'],color=s['color'],flags=s['flags'],rotate=0,style=span_style(s)))
        u = {**_make_unit(spans,0,False,[]),'id':'u0000'}
        text = 'Plouton' if defect=='different' else term
        b.insert_text((80,80),text,fontname='helv' if defect=='target_plain' else 'heit',fontsize=12)
        u.update(target=text,target_runs=[dict(text=text,style={'italic':True})],edit_bbox=[40,60,400,95])
        proofs = quoted_foreign_terms(a,b,[u])
        assert bool(proofs) == (defect=='none')


def test_resegment_atomic_archive_and_p169_full_label_guard(tmp_path,monkeypatch):
    from astro_translate import llm
    from astro_translate.resegmentation import rewrite_resegmentation, verified_migration_history
    from astro_translate.core import sha256
    from types import SimpleNamespace
    book = tmp_path/'book'
    folder = book/'work/pilot'
    folder.mkdir(parents=True)
    # Read-only symlink for hash verification; the source is never opened to write.
    (book/'source.pdf').symlink_to(BOOK/'source.pdf')
    path = folder/'p0169-translation.json'
    original = pre_resegment(int(path.name[1:5]))
    path.write_bytes(original)
    baseline = folder/'before.json'
    baseline.write_bytes(original)
    monkeypatch.setattr(llm,'run_task',lambda task,p:SimpleNamespace(
        data={'blocks':[dict(id=r['id'],translation=r['source']) for r in p['blocks']]}))
    record = rewrite_resegmentation(book,path,fresh(169),payload_factory=lambda p:{'blocks':p['requests']})
    audit = json.loads(record.read_text())
    assert Path(audit['previous_artifact']).read_bytes() == original
    assert audit['status']=='complete' and audit['output_sha256']==sha256(path)
    assert verified_migration_history(baseline,path)
    current = json.loads(path.read_bytes())
    next(u for u in current['blocks'] if u['id']=='p0169_u0003')['target'] += 'changed'
    path.write_text(json.dumps(current))
    assert not verified_migration_history(baseline,path)


def test_resegment_invalid_response_or_concurrent_change_never_replaces_file(tmp_path,monkeypatch):
    from astro_translate import llm
    from astro_translate.resegmentation import rewrite_resegmentation
    from types import SimpleNamespace
    book = tmp_path/'book'
    folder = book/'work/pilot'
    folder.mkdir(parents=True)
    (book/'source.pdf').symlink_to(BOOK/'source.pdf')
    path = folder/'p0069-translation.json'
    original = pre_resegment(int(path.name[1:5]))
    path.write_bytes(original)
    monkeypatch.setattr(llm,'run_task',lambda *a:SimpleNamespace(data={'blocks':[]}))
    with pytest.raises(ValueError,match='ids differ'):
        rewrite_resegmentation(book,path,fresh(69),payload_factory=lambda p:{'blocks':p['requests']})
    assert path.read_bytes() == original and not (folder/'history').exists()
    def concurrent(task,p):
        path.write_bytes(original+b'\n')
        return SimpleNamespace(data={'blocks':[dict(id=r['id'],translation=r['source']) for r in p['blocks']]})
    monkeypatch.setattr(llm,'run_task',concurrent)
    with pytest.raises(ValueError,match='concurrently'):
        rewrite_resegmentation(book,path,fresh(69),payload_factory=lambda p:{'blocks':p['requests']})
    assert path.read_bytes() == original+b'\n' and not (folder/'history').exists()


def test_inventory_covers_twenty_pages_without_migrating_any_input(tmp_path):
    import importlib.util
    from astro_translate.core import sha256
    spec = importlib.util.spec_from_file_location('caption_inventory',BOOK.parents[1]/'scripts/audit_caption_segmentation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    live = module.audit(BOOK)
    assert live['resegment_pages'] == []
    assert live['joined_units'] == live['replaced_fragments'] == 0
    # Audit an exact historical snapshot, then assert live migrated inputs are
    # already current. No provider calls and no production artifact writes.
    book = tmp_path/'book'
    folder = book/'work/pilot'
    folder.mkdir(parents=True)
    (book/'source.pdf').symlink_to(BOOK/'source.pdf')
    (book/'work/pipeline.toml').symlink_to(BOOK/'work/pipeline.toml')
    (folder/'selection.json').symlink_to(PILOT/'selection.json')
    for n in json.loads((PILOT/'selection.json').read_text())['selected_pages']:
        (folder/f'p{n:04d}-translation.json').write_bytes(pre_resegment(n))
    for n in (34,60,128,179):
        for suffix in ('fit.json','translated.pdf'):
            name = f'p{n:04d}-{suffix}'
            (folder/name).symlink_to(PILOT/name)
    # The CLI reports commands relative to the repository; tests only read.
    (tmp_path/'books').symlink_to(BOOK.parent, target_is_directory=True)
    module.__file__ = str(tmp_path/'scripts/audit_caption_segmentation.py')
    report = module.audit(book)
    assert len(report['pages'])==20
    assert report['resegment_pages']==[35,69,86,87,169,180]
    assert report['joined_units']==21 and report['replaced_fragments']==50
    assert all(sha256(Path(p))==digest for p,digest in report['input_sha256'].items())
    assert not [i for p in report['pages'] for i in p.get('candidate_text_quality_issues',[])]
    assert [p['page'] for p in report['pages'] if p.get('language_exceptions')]==[128]


def test_new_centered_caption_keeps_i4_alignment_policy():
    import sys
    sys.path.insert(0,str(PILOT))
    from local_layout import book_composition, composition_context
    import astro_translate.fidelity as fidelity
    with fitz.open(BOOK/'source.pdf') as doc, composition_context():
        u = next(u for u in fidelity.extract_fidelity_units(doc[179]) if u.get('merged_from'))
        u.update(target=u['source'],target_runs=[dict(text=u['source'],style=u['style'])])
        composed = fidelity.finalize_editorial_composition(doc[179],book_composition(doc[179],[u]))[0]
        assert composed['alignment']=='center' and composed['centered_caption']
        assert composed['column_bounds']==[u['bbox'][0],u['bbox'][2]]
