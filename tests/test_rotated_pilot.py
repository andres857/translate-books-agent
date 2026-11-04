import math
import pytest
import pymupdf as fitz
from astro_translate.core import Blocked
from astro_translate.rotated_pilot import _chars,_safe_redact,curve_coefficients,curved_positions


def test_vertical_redaction_preserves_nearby_numbers_and_drawing():
    doc=fitz.open();page=doc.new_page(width=200,height=120)
    page.insert_text((50,80),'Neptune',fontsize=7,rotate=90)
    page.insert_text((60,80),'123',fontsize=7,rotate=90)
    page.draw_line((40,10),(70,10),color=(1,0,0))
    chosen=[c for c in _chars(page) if abs(c['origin'][0]-50)<.01]
    drawings=page.get_drawings()
    _safe_redact(page,[{'characters':chosen}])
    assert 'Neptune' not in page.get_text()
    assert '123' in page.get_text()
    normalize=lambda rows:[{k:v for k,v in r.items() if k!='seqno'} for r in rows]
    assert normalize(page.get_drawings())==normalize(drawings)


def test_curved_baseline_and_letter_rotation_come_from_source_geometry():
    chars=[{'c':'A','origin':[float(x),.002*x*x-.2*x+80]} for x in range(20,100,10)]
    coeff,residual=curve_coefficients(chars)
    assert residual<1e-8
    font=fitz.Font('helv');p=curved_positions('TEST',font,10,coeff,25)
    assert p[0]['origin']==pytest.approx([25,76.25])
    assert p[0]['rotate_degrees']==pytest.approx(-math.degrees(math.atan(-.1)))
    assert p[-1]['origin'][0]>p[0]['origin'][0]
    assert p[-1]['rotate_degrees']!=p[0]['rotate_degrees']


def test_curve_with_unexplained_jump_is_rejected():
    chars=[{'c':'A','origin':[float(x),60.]} for x in range(20,100,10)]
    chars[3]['origin'][1]+=10
    with pytest.raises(Blocked,match='smooth quadratic'):curve_coefficients(chars)


def test_curved_pdf_letters_follow_the_requested_baseline_and_tangent():
    doc=fitz.open();page=doc.new_page(width=200,height=120)
    coeff=[.002,-.2,80];positions=curved_positions('TEST',fitz.Font('helv'),10,coeff,25)
    for p in positions:
        point=fitz.Point(p['origin'])
        page.insert_text(point,p['character'],fontsize=10,color=(.2,.3,.4),morph=(point,fitz.Matrix(p['rotate_degrees'])))
    chars=[c for c in _chars(page) if c['c'].strip()]
    assert ''.join(c['c'] for c in chars)=='TEST'
    for actual,expected,trace in zip(chars,positions,page.get_texttrace()):
        assert actual['origin']==pytest.approx(expected['origin'],abs=.002)
        slope=2*coeff[0]*expected['origin'][0]+coeff[1]
        # rawdict groups close tangents under the first line direction; trace
        # retains the actual individual text operator's transform.
        assert trace['dir'][1]/trace['dir'][0]==pytest.approx(slope,abs=.002)


def test_separator_operator_proof_survives_adjacent_text_redaction():
    from astro_translate.rotated_pilot import separator_character_proofs
    source=fitz.open();page=source.new_page(width=200,height=100)
    page.insert_text((20,50),'ABC',fontsize=10,morph=(fitz.Point(20,50),fitz.Matrix(2)))
    page.insert_text((45,49),'|',fontsize=10,morph=(fitz.Point(45,49),fitz.Matrix(3)))
    candidate=fitz.open(stream=source.tobytes(),filetype='pdf');after=candidate[0]
    _safe_redact(after,[{'characters':[c for c in _chars(after) if c['c'] in 'ABC']}])
    proof=separator_character_proofs(page,after)
    assert len(proof)==1 and proof[0]['exact_equal']
    after.insert_text((70,50),'|',fontsize=10)
    with pytest.raises(Blocked,match='missing or duplicated'):separator_character_proofs(page,after)
