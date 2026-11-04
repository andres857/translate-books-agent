"""Generate reproducible G1 evidence, with simulated glossary approval ONLY here.
Run: .venv/bin/python tests/generate_fixture.py
No books or external APIs are involved.
"""
import importlib.util
import shutil
from pathlib import Path
import pymupdf as fitz
from astro_translate.core import approve_glossary, extract_units, reinsert, visual_qa, atomic_json
spec=importlib.util.spec_from_file_location('fixture_tests',Path(__file__).with_name('test_pipeline.py'))
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
base=Path(__file__).parent/'fixtures'
if (base/'book').exists(): shutil.rmtree(base/'book')
book=module.book.__wrapped__(base)
g,s=module.glossary.__wrapped__(base)
approve_glossary(book,g,s,'SYNTHETIC TEST ONLY')
with fitz.open(book/'source.pdf') as doc:
    block=next(u for u in extract_units(doc[1]) if u['source']=='Planet')
result=reinsert(book,{'page':2,'blocks':[{**block,'target':'Planeta'}]},g,s,font_path='fonts/NotoSans-Regular.ttf')
qa=visual_qa(book,result['output'],result,g)
atomic_json(base/'g1-evidence.json',{'kind':'synthetic offline test only','fit':result,'qa':qa,'real_api_calls':0})
print(book/'source.pdf')
