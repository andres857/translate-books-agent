"""Profile isolation, explicit hooks, semantic cache and frozen pilot evidence."""
import ast
import re
import json
from pathlib import Path
import pytest
import pymupdf as fitz
from astro_translate.core import Blocked, sha256

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT/'books/the-secrets-of-astrology'


def test_profile_source_binding(tmp_path):
    from astro_translate.profiles import load_profile
    (tmp_path/'work').mkdir()
    (tmp_path/'source.pdf').write_bytes(b'wrong PDF')
    (tmp_path/'work/profile.toml').write_bytes((BOOK/'work/profile.toml').read_bytes())
    with pytest.raises(Blocked, match='source.*hash'):
        load_profile(tmp_path)


def test_profiles_are_isolated_and_have_local_policy():
    from astro_translate.profiles import load_profile
    secrets = load_profile(BOOK)
    wisdom = load_profile(ROOT/'books/astrology-wisdom-stars')
    assert secrets.source_sha256 != wisdom.source_sha256
    assert secrets.data['fonts']['classify_slab'] is True
    assert not wisdom.data['fonts']['classify_slab']
    assert secrets.data['editorial']['quoted_foreign_term']


def test_common_code_has_no_page_specific_conditions():
    violations = []
    for path in (ROOT/'src/astro_translate').glob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.search(r'\bp\d{4}[_-]',node.value):
                violations.append(f'{path.name}:{node.lineno}:literal-page-id')
            if not isinstance(node, ast.Compare): continue
            expressions = [node.left, *node.comparators]
            is_page = any(isinstance(n, ast.Attribute) and n.attr == 'number' or isinstance(n, ast.Name) and n.id in {'number','page_number','page_no'} for e in expressions for n in ast.walk(e))
            literal = any(isinstance(e, (ast.Tuple, ast.List, ast.Set)) or isinstance(e, ast.Constant) and isinstance(e.value, int) and e.value > 1 for e in expressions)
            if is_page and literal: violations.append(f'{path.name}:{node.lineno}')
    assert not violations, violations


def test_compatibility_context_does_not_replace_functions():
    import sys
    sys.path.insert(0,str(BOOK/'work/pilot'))
    import local_layout
    from astro_translate import fidelity, fonts, layout
    before = [fidelity.apply_editorial_composition, fidelity.extract_fidelity_units, fidelity._candidate_boxes, fonts.select_group_fonts, layout.safe_redaction_rects]
    with local_layout.composition_context():
        assert before == [fidelity.apply_editorial_composition, fidelity.extract_fidelity_units, fidelity._candidate_boxes, fonts.select_group_fonts, layout.safe_redaction_rects]


def test_vlm_preservation_is_candidate_bound():
    from astro_translate.qa_evidence import preserve_vlm
    old = {'candidate_sha256':'a','source_sha256':'s','vlm_complete':True,'vlm':{'verdict':'ok'},'vlm_local_review':{'checked':True}}
    fresh = {'candidate_sha256':'a','source_sha256':'s','vlm_complete':False,'local_fidelity_ok':True}
    assert preserve_vlm(fresh,old)['vlm_local_review'] == old['vlm_local_review']
    assert preserve_vlm(fresh,old)['vlm_complete'] is True
    assert preserve_vlm({**fresh,'candidate_sha256':'b'},old)['vlm_complete'] is False


def test_pilot_golden_current_candidates():
    frozen=json.loads((ROOT/'tests/golden/secrets_pilot.json').read_text())
    assert len(frozen)==20
    for n, expected in frozen.items():
        fit=json.loads((BOOK/f'work/pilot/p{int(n):04d}-fit.json').read_text())
        assert fit['fit_complete'] == expected['fit_complete']
        assert fit.get('output_sha256') == expected['output_sha256'], n
        if fit.get('output'): assert sha256(Path(fit['output'])) == expected['output_sha256'], n


def test_profile_rejects_changed_hook_before_import(tmp_path):
    from astro_translate.profiles import load_profile
    (tmp_path/'work').mkdir();(tmp_path/'source.pdf').write_bytes(b'synthetic')
    hook=tmp_path/'work/hooks.py';hook.write_text('raise AssertionError("must not import")')
    (tmp_path/'work/profile.toml').write_text(f'version = 1\nsource_sha256 = "{sha256(tmp_path/"source.pdf")}"\n[hooks]\npath = "work/hooks.py"\nsha256 = "stale"\n')
    with pytest.raises(Blocked,match='dependency hash'):load_profile(tmp_path)


def test_scoped_unit_override_rejects_changed_source():
    from astro_translate.profiles import load_profile
    profile=load_profile(BOOK)
    saved=json.loads((BOOK/'work/pilot/p0028-translation.json').read_text())['blocks']
    units=[{**u,'id':u['id'].removeprefix('p0028_')} for u in saved]
    units[0]['source']+=' changed'
    with fitz.open(BOOK/'source.pdf') as doc:
        with pytest.raises(Blocked,match='unit source hash'):profile.compose(doc[27],units)


def test_profile_rules_do_not_leak_to_unrelated_pdf():
    from astro_translate.fidelity import apply_editorial_composition
    from types import SimpleNamespace
    style={'font':'Helvetica','serif':False,'weight':400,'bold':False}
    unit={'source':'Aries','context':'table_label','style':style,'target_runs':[{'text':'Aries','style':style}],'kind':'label'}
    result=apply_editorial_composition(SimpleNamespace(number=6),[unit])
    assert result[0]['style']['weight']==400


def test_runners_never_assign_compositor_functions():
    for path in (BOOK/'work/pilot').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                assert not any(isinstance(t,ast.Attribute) and isinstance(t.value,ast.Name) and t.value.id in {'fidelity','fonts','layout'} for t in targets), path
