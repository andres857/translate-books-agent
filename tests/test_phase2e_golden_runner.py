"""Golden reporter must retain failed-fit evidence, never a stale success."""
import importlib.util
import json
from pathlib import Path


def load_runner():
    path=Path(__file__).resolve().parents[1]/'scripts/check_phase2c_golden.py'
    spec=importlib.util.spec_from_file_location('golden_runner',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_failed_fit_without_output_is_reported_and_copied(tmp_path):
    runner=load_runner();pilot=tmp_path/'pilot';previous=tmp_path/'snapshot'/'pilot'
    pilot.mkdir();previous.mkdir(parents=True)
    fit={'page':141,'fit_complete':False,'failures':[{'reason':'does_not_fit'}]}
    qa={'local_fidelity_ok':False,'vlm_complete':False}
    (pilot/'p0141-fit.json').write_text(json.dumps(fit))
    (pilot/'p0141-qa.json').write_text(json.dumps(qa))
    (previous/'p0141-qa.json').write_text(json.dumps({'local_fidelity_ok':True,'vlm_complete':True}))
    row=runner.comparison_row(141,{'output_sha256':'frozen','fit_complete':True},pilot,previous.parent)
    assert not row['byte_identical'] and row['actual_sha256'] is None
    assert not row['fit_complete'] and not row['local_fidelity_ok']
    runner.copy_changed_artifacts(row,pilot,tmp_path/'evidence')
    assert json.loads((tmp_path/'evidence/p0141/p0141-fit.json').read_text())==fit
    assert (tmp_path/'evidence/p0141/p0141-qa.json').exists()
