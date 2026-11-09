"""E1: real curved operator geometry plus offline straight-label mechanics."""
from pathlib import Path
import pymupdf as fitz
import pytest

BOOK=Path(__file__).resolve().parents[1]/'books/the-secrets-of-astrology'


def test_partial_deferred_is_neither_ok_nor_failure():
    from astro_translate.production import batch_metrics
    rows=[{'page':1,'status':'done_qa_ok'},
          {'page':2,'status':'partial_deferred','native_qa_ok':True,
           'deferred_regions':[{'bbox':[1,2,3,4],'reason':'curved_glyphs','phase':4}]}]
    report=batch_metrics(rows)
    assert report['qa_ok']==1
    assert report['partial_deferred']==1
    assert report['denominator']==1
    assert report['review_pages']==[]
    rows[-1]['native_qa_ok']=False
    assert batch_metrics(rows)['denominator']==2
    assert batch_metrics(rows)['review_pages']==[2]


@pytest.mark.parametrize('number',range(26,32))
def test_real_wheels_are_curves_not_straight_radial_words(number):
    from astro_translate.native_rotated import extract_straight_labels, deferred_inventory
    with fitz.open(BOOK/'source.pdf') as doc:
        assert extract_straight_labels(doc[number-1])==[]
        inventory=deferred_inventory(doc[number-1])
        assert inventory
        assert all(r['phase']==4 and r['reason']=='curved_or_fragmented_native_text' for r in inventory)
        assert all(r['source_spans'] and r['bbox'] for r in inventory)


def test_straight_arbitrary_rotation_preserves_baseline_center_and_color(tmp_path):
    from astro_translate.native_rotated import extract_straight_labels, compose_labels
    source=fitz.open();page=source.new_page(width=220,height=220)
    anchor=fitz.Point(70,140)
    page.insert_text(anchor,'PLANET',fontsize=12,color=(.8,.1,.2),morph=(anchor,fitz.Matrix(37)))
    page.draw_line((10,10),(200,10),color=(0,0,1))
    units=extract_straight_labels(page)
    assert len(units)==1 and units[0]['source']=='PLANET'
    candidate=fitz.open(stream=source.tobytes(),filetype='pdf')
    rows,audit=compose_labels(page,candidate[0],units,{units[0]['id']:'PLANETA'},tmp_path)
    assert len(rows)==1
    row=rows[0]
    assert row['scale']>=.75
    assert 'PLANETA' in candidate[0].get_text()
    span=candidate[0].get_texttrace()[0]
    assert span['dir']==pytest.approx(units[0]['direction'],abs=1e-5)
    assert span['color']==pytest.approx((.8,.1,.2),abs=.005)
    assert row['baseline_center']==pytest.approx(units[0]['baseline_center'],abs=.005)
    assert audit and row['font_runs']
    assert candidate[0].get_drawings()[0]['items']==page.get_drawings()[0]['items']


def test_curved_multichar_raw_span_is_not_misclassified():
    from astro_translate.native_rotated import extract_straight_labels
    doc=fitz.open();p=doc.new_page()
    for i,letter in enumerate('CURVE'):
        point=fitz.Point(60+i*8,90+i*i*.15)
        p.insert_text(point,letter,fontsize=10,morph=(point,fitz.Matrix(-i*2)))
    assert extract_straight_labels(p)==[]


@pytest.mark.parametrize('native_ok,status',[(True,'partial_deferred'),(False,'review')])
def test_native_publication_does_not_hide_a_real_failure(tmp_path,native_ok,status):
    from types import SimpleNamespace
    from astro_translate.production import Production
    source=fitz.open();source.new_page();source.save(tmp_path/'source.pdf')
    saved={}
    runner=SimpleNamespace(book=tmp_path,review_for=lambda *a:None,
        path=lambda n,suffix:tmp_path/suffix,
        update=lambda n,**values:saved.update(values),queue=lambda *a:None,checkpoint=lambda *a,**k:None)
    qa={'local_fidelity_ok':False,'native_local_fidelity_ok':native_ok,'min_scale':1}
    fit={'fit_complete':True,'output_sha256':'test'}
    regions=[{'bbox':[10,10,20,20],'reason':'curved_glyphs','phase':4}]
    assert Production.finish_qa(runner,1,fit,qa,regions,'dep')==status
    assert saved['native_qa_ok']==native_ok
    assert qa['verdict']!='ok'


def test_rotated_dates_are_local_and_model_values_are_masked():
    from astro_translate.native_rotated import label_requests
    from astro_translate.llm import _prepare
    requests,local=label_requests([{'id':'date','source':'21 MARCH'},
                                   {'id':'label','source':'CHART 21'}])
    assert list(local)==['date']
    assert requests==[{'id':'label','source':'CHART 21'}]
    wire,maps=_prepare('translate_labels',{'labels':requests},(),())
    assert '21' not in wire['labels'][0]['source']
    assert maps['label']


def test_saved_rotated_dependencies_are_checked_before_opening_candidate(tmp_path):
    from types import SimpleNamespace
    from astro_translate.native_rotated import extract_straight_labels,compose_saved_rotated
    from astro_translate.production import digest
    from astro_translate.core import sha256,atomic_json
    source=fitz.open();p=source.new_page();anchor=fitz.Point(70,140)
    p.insert_text(anchor,'PLANET',morph=(anchor,fitz.Matrix(30)))
    source.save(tmp_path/'source.pdf');source.close()
    files={}
    for name in ('glossary','style','protected','config_path'):
        files[name]=tmp_path/(name+'.txt');files[name].write_text(name)
    runner=SimpleNamespace(book=tmp_path,extras=(),folder=tmp_path,
        path=lambda n,s:tmp_path/f'p{n:04d}-{s}',**files)
    with fitz.open(tmp_path/'source.pdf') as source:units=extract_straight_labels(source[0])
    source_hash=sha256(tmp_path/'source.pdf')
    identity=digest({'source_sha256':source_hash,'units':units})
    deps={name:sha256(files[attr]) for name,attr in [('glossary','glossary'),('style','style'),('protected','protected'),('config','config_path')]}
    atomic_json(runner.path(1,'rotated.json'),{'identity':identity,'dependencies':deps,'answers':{units[0]['id']:'PLANETA'}})
    for attr in files:
        original=files[attr].read_text();files[attr].write_text('changed')
        result=compose_saved_rotated(runner,{'page':1,'source_sha256':source_hash,'output':'DOES_NOT_EXIST'})
        assert result['rotated_pending']==[units[0]['id']]
        files[attr].write_text(original)


def _rotated_script():
    import importlib.util
    path=BOOK.parents[1]/'scripts/prepare_rotated_labels.py'
    spec=importlib.util.spec_from_file_location('prepare_rotated_labels',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_prepare_rotated_cli_quota_uses_exit_75(monkeypatch):
    script=_rotated_script()
    from astro_translate.llm import QuotaPaused
    monkeypatch.setattr(script,'prepare',lambda *a,**k: (_ for _ in ()).throw(QuotaPaused('OFFLINE pause')))
    assert script.main(['--book','unused','--pages','1','--plan','unused','--execute'])==75


def test_partial_low_scale_stays_pending_with_warning(tmp_path):
    from types import SimpleNamespace
    from astro_translate.production import Production,batch_metrics
    source=fitz.open();source.new_page();source.save(tmp_path/'source.pdf')
    saved={}
    runner=SimpleNamespace(book=tmp_path,review_for=lambda *a:None,path=lambda n,s:tmp_path/s,
        update=lambda n,**values:saved.update(values),queue=lambda *a:None,checkpoint=lambda *a,**k:None)
    qa={'local_fidelity_ok':False,'native_local_fidelity_ok':True,'min_scale':.78}
    regions=[{'bbox':[1,2,3,4],'reason':'curve','phase':4}]
    assert Production.finish_qa(runner,1,{'fit_complete':True},qa,regions,'dep')=='partial_deferred'
    assert 'scale_below_0.80_requires_review' in qa['production_reasons']
    metric=batch_metrics([{'page':1,**saved}])
    assert metric['partial_deferred']==1 and metric['qa_ok']==0 and metric['review_pages']==[]


def test_saved_rotated_answers_use_book_lock_and_recheck_dependencies(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from contextlib import contextmanager
    from astro_translate.core import sha256,Blocked
    script=_rotated_script();files={}
    for name in ('glossary','style','protected','config_path'):
        files[name]=tmp_path/(name+'.txt');files[name].write_text(name)
    (tmp_path/'source.pdf').write_bytes(b'offline-test-source-identity')
    runner=SimpleNamespace(book=tmp_path,source_hash=sha256(tmp_path/'source.pdf'),
        path=lambda n,s:tmp_path/f'p{n:04d}-{s}',**files)
    dependencies={n:sha256(files[a]) for n,a in [('glossary','glossary'),('style','style'),('protected','protected'),('config','config_path')]}
    row={'page':1,'identity':'bound','dependencies':dependencies}
    held=[];writes=[]
    @contextmanager
    def locked(book):
        assert book==tmp_path
        held.append(True)
        yield
        held.pop()
    def write(path,value):
        assert held
        writes.append(value)
    monkeypatch.setattr(script,'book_lock',locked,raising=False)
    monkeypatch.setattr(script,'atomic_json',write)
    script.save_answers(runner,row,{'label':'test'})
    assert len(writes)==1
    files['style'].write_text('changed during model request')
    with pytest.raises(Blocked,match='dependencies changed'):
        script.save_answers(runner,row,{'label':'test'})
    assert len(writes)==1
