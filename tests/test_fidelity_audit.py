import pymupdf as fitz
from astro_translate.fidelity_audit import audit_rendered_styles


def _fixture():
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((40, 70), 'Title', fontname='hebo', fontsize=12, color=(1, 0, 0))
    page.insert_text((40, 90), 'Body', fontname='helv', fontsize=10, color=(0, 0, 1))
    blocks = []
    for text, box, color, size, name in [
        ('Title', [39, 55, 110, 74], 0xff0000, 12, 'Helvetica-Bold'),
        ('Body', [39, 77, 110, 94], 0x0000ff, 10, 'Helvetica'),
    ]:
        blocks.append({'id': text, 'target': text, 'edit_bbox': box,
                       'target_runs': [{'text': text, 'style': {'color': color}}],
                       'font_runs': [{'engine_name': name, 'size': size}]})
    return doc, page, {'blocks': blocks}


def test_character_typography_audit_accepts_real_pdf():
    doc, page, result = _fixture()
    audit = audit_rendered_styles(page, result)
    assert audit['ok'] and audit['characters_checked'] == 9
    doc.close()


def test_color_swap_detected_even_when_palette_is_identical():
    doc, page, result = _fixture()
    a, b = result['blocks']
    a['target_runs'][0]['style']['color'], b['target_runs'][0]['style']['color'] = 0x0000ff, 0xff0000
    audit = audit_rendered_styles(page, result)
    assert not audit['ok']
    assert {x['reason'] for x in audit['issues']} == {'run_color_changed'}
    doc.close()


def test_wrong_weight_font_and_wrong_scale_detected():
    doc, page, result = _fixture()
    result['blocks'][0]['font_runs'][0]['engine_name'] = 'Helvetica'
    result['blocks'][1]['font_runs'][0]['size'] = 9
    audit = audit_rendered_styles(page, result)
    assert not audit['ok']
    assert {x['reason'] for x in audit['issues']} == {'run_font_changed', 'run_size_changed'}
    doc.close()
