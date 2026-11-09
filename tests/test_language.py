from astro_translate.language import residual_english

def test_clear_english_and_spanish():
    assert residual_english(['The planets move through the houses and describe the meaning of our birth chart.'])
    assert residual_english(['Los planetas recorren las casas y describen el significado de nuestra carta natal.'])==[]

def test_allowed_terms():
    assert residual_english(['London','Ascendente'],allowed=['London','Ascendente'])==[]

def test_residual_english_ignores_ordinal_label_made_only_of_spanish_function_words():
    assert residual_english(['Y DE LA ª','de la 9ª'])==[]

def test_longest_nonoverlapping_glossary_matches():
    from astro_translate.language import glossary_matches
    rows=[{'source':'Sun','target':'Sol'},{'source':'Sun sign','target':'signo solar'}]
    assert [r['target'] for r in glossary_matches('Sun sign',rows)]==['signo solar']
    assert {r['target'] for r in glossary_matches('Sun sign and Sun',rows)}=={'signo solar','Sol'}

def test_protected_mixed_prose_remains_visible_to_local_detector():
    import pymupdf as f
    from astro_translate.language import qa_texts
    d=f.open(); p=d.new_page(); p.insert_text((40,50),'The planets move through 12 houses')
    texts=qa_texts(p)
    assert texts and '12' not in texts[0]
    assert residual_english(texts)
    d.close()
