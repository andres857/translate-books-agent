"""Pilot editorial changes remain scoped and preserve protected inputs."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('pilot_sample',Path('scripts/pilot_sample.py'))
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)


def test_nodes_correction_is_exact_and_audited():
    source='Where the paths of the Sun and Moon cross in space. Symbolizes fated meetings and experiences and the balancing of past and future.'
    payload={'page':121,'blocks':[{'id':'p0121_u0014','source':source,'target':'Simboliza encuentros.','target_runs':[{'text':'Simboliza encuentros.','style':{}}]}, {'id':'other','source':'It symbolizes birth.','target':'Simboliza el nacimiento.','target_runs':[{'text':'Simboliza el nacimiento.','style':{}}]}]}
    result=pilot.user_corrections(payload)
    assert result['blocks'][0]['target']=='Simbolizan encuentros.'
    assert result['blocks'][0]['target_runs'][0]['text']=='Simbolizan encuentros.'
    assert result['blocks'][1]['target']=='Simboliza el nacimiento.'
    assert result['editorial_corrections'][0]['source']==source
    assert result['editorial_corrections'][0]['before']=='Simboliza encuentros.'


def test_standalone_ordinal_suffix_is_kept_with_numeric_notation():
    from astro_translate.fidelity import immutable_span
    for suffix in ("st","nd","rd","th","ST"):
        assert immutable_span({"source":suffix,"font":"SohoPro"})
    assert not immutable_span({"source":"The 1st house","font":"SohoPro"})
