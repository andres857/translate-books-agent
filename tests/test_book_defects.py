"""Fase 2H QA regressions: all local, no model/provider access."""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import pymupdf as fitz
from PIL import Image,ImageDraw

from astro_translate.text_quality import page_issues, text_issues, unit_issues


def codes(rows):return {row['code'] for row in rows}


def test_display_orphan_abbreviation_after_translation_is_reported():
    unit={'id':'p0045_u1','source':'LIGHT VS. DARKNESS','target':'LUZ FRENTE A. OSCURIDAD',
          'size':32,'bbox':[10,10,190,45],'style':{},'target_runs':[]}
    issues=unit_issues(unit)
    orphan=next(row for row in issues if row['code']=='orphan_abbreviation')
    assert orphan['abbreviation']=='a.' and orphan['source_abbreviation']=='vs.'


def test_display_leading_and_doubled_spaces_are_reported():
    unit={'id':'u','source':'ONE OF YOUR SKILLS','target':'  UNA DE TUS HABILIDADES',
          'size':14,'scale_group':'p0041_display_14.0','bbox':[10,10,190,45],'style':{},'target_runs':[]}
    assert {'display_leading_space','display_double_space'} <= codes(unit_issues(unit))
    unit['target']='UNA  DE TUS HABILIDADES'
    assert 'display_double_space' in codes(unit_issues(unit))
    assert 'display_leading_space' not in codes(unit_issues(unit))


def test_display_space_rule_covers_saved_display_units_from_pages_41_45_53():
    from pathlib import Path
    book=Path(__file__).resolve().parents[1]/'books'/'the-secrets-of-astrology'/'work'/'production'
    for page,identifier in ((41,'p0041_u0009'),(45,'p0045_u0021'),(53,'p0053_u0011')):
        payload=json.loads((book/f'p{page:04d}-translation.json').read_text())
        saved=next(unit for unit in payload['blocks'] if unit['id']==identifier)
        damaged={**saved,'target':'  '+saved['target']}
        assert 'display_leading_space' in codes(unit_issues(damaged)),identifier


def test_rendered_display_line_checks_leading_spaces_across_spans():
    with fitz.open() as document:
        source=document.new_page(width=240,height=90)
        candidate=document.new_page(width=240,height=90)
        candidate.insert_text((20,45),'  UNA DE TUS HABILIDADES',fontsize=14)
        source=document[0];candidate=document[1]
        unit={'id':'u','source':'ONE OF YOUR SKILLS','target':'UNA DE TUS HABILIDADES',
              'size':14,'scale_group':'p0041_display_14.0','bbox':[10,15,220,55],
              'edit_bbox':[10,15,220,55],'style':{},'target_runs':[]}
        issues=page_issues([unit],source_page=source,candidate_page=candidate)
    assert {'display_leading_space','display_double_space'}<=codes(issues)


def test_actual_ink_below_composition_frame_is_reported():
    with fitz.open() as document:
        source=document.new_page(width=200,height=100)
        panel=Image.new('RGB',(200,100),'white');ImageDraw.Draw(panel).rectangle((20,20,180,80),fill=(180,90,100))
        source.insert_image(source.rect,stream=_png_bytes(panel))
        candidate=document.new_page(width=200,height=100);candidate.insert_text((25,91),'texto fuera de caja',fontsize=12)
        source=document[0];candidate=document[1]
        unit={'id':'u','source':'text beyond box','target':'texto fuera de caja','bbox':[20,20,180,80],
              'edit_bbox':[20,20,180,80],'style':{},'target_runs':[]}
        issues=page_issues([unit],source_page=source,candidate_page=candidate)
    assert 'box_bottom_overflow' in codes(issues)


def _png_bytes(image):
    from io import BytesIO
    stream=BytesIO();image.save(stream,format='PNG');return stream.getvalue()


def test_uppercase_missing_accent_uses_lowercase_book_evidence():
    issues=text_issues('JUGUETON','JUGUETON','u',accent_vocabulary=['juguetón'])
    issue=next(row for row in issues if row['code']=='uppercase_accent')
    assert issue['expected']=='JUGUETÓN'


def test_protected_feminine_ordinal_does_not_break_all_caps_detection():
    unit={'id':'p0159_u0003','source':'PLANETS IN THE 9TH HOUSE',
          'target':'PLANETAS EN LA 9ª CASA','target_runs':[],
          'style':{},'style_runs':[]}
    assert 'source_case' not in codes(unit_issues(unit))


def test_repeated_ngram_already_present_in_source_is_not_new():
    text='la estrella la estrella'
    assert 'repeated_ngram' not in codes(text_issues(text,text,'u'))


def test_p104_p105_repetition_shapes_compare_adjacent_and_nonadjacent_source():
    # Saved validation responses had repeated Spanish scaffolding corresponding
    # to source contrast/parallelism, not new repeated content.
    assert not text_issues('our likes and dislikes',
        'lo que nos agrada y lo que nos desagrada','p0104_unit')
    assert not text_issues('you care about someone, you care deeply',
        'te importa alguien, te importa profundamente','p0105_unit')


def test_glossary_accepts_approved_related_term_inside_multiword_phrase_and_adjective_flexion():
    from astro_translate.glossary_forms import forms,glossary_form_present
    assert glossary_form_present('El recorrido de Júpiter por el zodíaco',
        'recorrido por el zodíaco',related_terms=['Júpiter'])
    assert not glossary_form_present('El recorrido de Mercurio por el zodíaco',
        'recorrido por el zodíaco',related_terms=['Júpiter'])
    assert {'uraniano','uraniana','uranianos','uranianas'}<=forms('uraniano')
    assert 'astróloga' in forms('astrólogo')
    assert {'luna','lunas','lunar','lunares'}<=forms('luna')
    assert glossary_form_present('Nodos lunares','nodos luna')
    assert glossary_form_present('Aspectos mayores y menores','aspectos menores',
                                 related_terms=['aspectos mayores'])
    assert not glossary_form_present('Planetas personales y sociales','planetas sociales')
    assert glossary_form_present('Los signos positivos del zodíaco son los signos de fuego y de aire',
                                 'signos del zodíaco',related_terms=['signos positivos'])
    assert glossary_form_present('Los signos negativos del zodíaco son los signos de tierra y de agua',
                                 'signos de agua',related_terms=['signos de tierra'])
    from astro_translate import llm
    wire={'blocks':[{'id':'u','source':'Jupiter circuit','glossary':[
        {'source':'Jupiter','target':'Júpiter'},
        {'source':'circuit through the zodiac','target':'recorrido por el zodíaco'}]}]}
    answer={'blocks':[{'id':'u','translation':'El recorrido de Júpiter por el zodíaco dura años'}]}
    assert llm._validate('translate_page',answer,wire,{'type':'object'})['blocks'][0]['id']=='u'


def test_report_collects_grouped_batch_and_qa_findings(tmp_path):
    from scripts.book_defect_report import collect_defects
    book=tmp_path/'book';folder=book/'work'/'phase2h';batch=folder/'batches';validation=folder/'validation'
    (book/'work').mkdir(parents=True);(book/'source.pdf').write_bytes(b'fixture source hash')
    (folder/'recomposition').mkdir(parents=True);batch.mkdir();validation.mkdir();(folder/'recomposition/evidence').mkdir()
    recomposed=folder/'input_snapshot/book/work/phase2h/recomposition';recomposed.mkdir(parents=True)
    qa={'text_quality_issues':[{'code':'orphan_abbreviation','unit_id':'u','action':'retranslate'}],
        'editorial_margin_issues':[]}
    (recomposed/'p0003-qa.json').write_text(json.dumps(qa))
    image=folder/'recomposition/evidence/p0003-comparison.png';image.write_bytes(b'png')
    saved=folder/'input_snapshot'/'book'/'work'/'production';saved.mkdir(parents=True)
    (saved/'p0106-translation.json').write_text(json.dumps({'blocks':[{'id':'p0106_u1',
        'source':'you are patient, very patient','target':'eres paciente, muy paciente'}]}))
    (batch/'batch-10.json').write_text(json.dumps({'status':'needs_fix','pages':[104,105],
        'metrics':{'review_pages':[104,105],'qa_ok_ratio':0.5,'g5_pass':False},'errors':[
        {'page':104,'exception':'astro_translate.llm.ValidationFailure:repeated_ngram for id p0104_u1'},
        {'page':105,'exception':'astro_translate.llm.ValidationFailure:repeated_ngram for id p0105_u2'},
        {'page':106,'exception':'astro_translate.core.Blocked:repeated_ngram for id p0106_u1'},
        {'page':107,'exception':'pymupdf.mupdf.FzErrorSystem:code=2: cannot fwrite: No space left on device'},
        {'page':108,'exception':'pymupdf.mupdf.FzErrorSystem:code=2: cannot fwrite: No space left on device'}]}))
    (validation/'bad.json').write_text(json.dumps({'pages':[104,105],
        'error':'repeated_ngram for id p0104_u1','ids':['p0104_u1']}))
    result=collect_defects(book,folder,batch,validation,{'pages':[{'page':3,'status':'review',
        'comparison':str(image)},{'page':105,'status':'native_translation_pending'},
        {'page':106,'status':'review'},{'page':107,'status':'error','error':'FzErrorSystem: unrelated error'},
        {'page':108,'status':'error','error':'FzErrorSystem: code=2: cannot fwrite: No space left on device'}]})
    orphan=next(row for row in result if row['code']=='orphan_abbreviation')
    assert orphan['pages']==[3] and len(orphan['examples'])==1
    repeated=[row for row in result if 'repeated_ngram' in row['code']]
    assert repeated and {104,105} <= set().union(*(set(row['before_pages']) for row in repeated))
    historical=next(row for row in repeated if row['code']=='llm_validation_repeated_ngram')
    assert historical['before_pages']==[104,105] and historical['pages']==[]
    production=next(row for row in repeated if row['code']=='production_repeated_ngram')
    assert production['pages']==[105]
    status=next(row for row in result if row['code']=='production_batch_status')
    assert status['before_pages']==[104,105] and status['before_page_count']==2
    replay=next(row for row in result if row['code']=='production_Blocked'
                and 'repeated_ngram' in row['cause'])
    assert replay['before_pages']==[106] and replay['page_count']==0
    disk=next(row for row in result if row['code']=='production_FzErrorSystem')
    assert disk['before_pages']==[107,108] and disk['pages']==[108]


def test_resolved_historical_validations_group_by_page_and_unit():
    from scripts.book_defect_report import _group_resolved_validation
    rows=[
        {'page':104,'unit_id':'p0104_u0','reason':'source parallelism','batch_file':'batch-10.json'},
        {'page':104,'unit_id':'p0104_u0','reason':'source parallelism','batch_file':'batch-11.json'},
        {'page':104,'unit_id':'p0104_u0','reason':'source parallelism','validation_file':'validation-104.json'},
        {'page':105,'unit_id':'p0105_u1','reason':'source parallelism','batch_file':'batch-11.json'},
    ]
    grouped=_group_resolved_validation(rows)
    assert [(row['page'],row['unit_id']) for row in grouped]==[
        (104,'p0104_u0'),(105,'p0105_u1')]
    assert grouped[0]['sources']==['batch-10.json','batch-11.json','validation-104.json']
