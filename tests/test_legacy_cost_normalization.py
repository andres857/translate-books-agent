import json
import fitz
from pathlib import Path
import runpy
estimate=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/estimate_quota.py'))['estimate']


def test_legacy_codex_zero_cost_normalized_in_batch_and_projection(tmp_path):
    book=tmp_path/'book';(book/'work').mkdir(parents=True)
    doc=fitz.open();doc.new_page();doc.save(book/'source.pdf');doc.close()
    rows=[{'task':'translate_page','phase':'reinsertion','page':1,'backend':'codex_sdk','status':'ok','cached':False,'cost_usd':0,
           'input_tokens':10,'cached_input_tokens':0,'output_tokens':2},
          {'task':'translate_page','phase':'reinsertion','page':1,'backend':'codex_sdk','status':'cache_hit','cached':True,'cost_usd':0,
           'input_tokens':0,'cached_input_tokens':0,'output_tokens':0}]
    (book/'work/costs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    result=estimate(book,phase='reinsertion')
    assert result['cost_usd_known']==0
    assert result['cost_usd_unknown_records']==1
    assert result['cost_usd_complete'] is False
