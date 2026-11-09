from astro_translate.consistency import SentenceMemory
from astro_translate.core import sha256


def test_context_ignores_code_but_invalidates_semantic_inputs(tmp_path):
    from astro_translate.translation_context import translation_context
    for name in ('g.csv','s.md','d.txt','prompt.md','schema.json'):(tmp_path/name).write_text(name)
    config={'provider':{'backend':'codex_sdk'},'tasks':{'translate_page':{'model':'test','effort':'low','prompt':str(tmp_path/'prompt.md'),'schema':str(tmp_path/'schema.json')}}}
    args=(config,tmp_path/'g.csv',tmp_path/'s.md',tmp_path/'d.txt')
    first=translation_context(*args)
    (tmp_path/'implementation.py').write_text('changed implementation')
    assert translation_context(*args)==first
    memory=SentenceMemory(tmp_path,first)
    entry={'id':'x','source':'Sun','marked_source':'Sun','markers':[]}
    memory.put(entry,'Sol');memory.close()
    memory=SentenceMemory(tmp_path,translation_context(*args))
    assert memory.get(entry)=='Sol';memory.close()
    config['tasks']['translate_page']['effort']='high'
    assert translation_context(*args)!=first
    config['tasks']['translate_page']['effort']='low'
    (tmp_path/'s.md').write_text('new style')
    assert translation_context(*args)!=first


def test_memory_migration_validates_conflicts_and_keeps_old_rows(tmp_path):
    from astro_translate.translation_context import migrate_memory
    entry={'id':'x','source':'Sun','marked_source':'Sun','markers':[]}
    old=SentenceMemory(tmp_path,'old');old.put(entry,'Sol');old.close()
    report=migrate_memory(tmp_path,'old','new',[entry])
    assert report['copied']==1
    for context in ('old','new'):
        memory=SentenceMemory(tmp_path,context);assert memory.get(entry)=='Sol';memory.close()
    assert migrate_memory(tmp_path,'old','new',[entry])['copied']==0


def test_block_keys_ignore_implementation_fingerprint_and_migrate(tmp_path):
    import json,sqlite3
    from astro_translate.translation_context import block_keys,migrate_block_cache
    (tmp_path/'g.csv').write_text('source,target\nSun,Sol\n')
    (tmp_path/'s.md').write_text('Spanish');(tmp_path/'d.txt').write_text('')
    (tmp_path/'prompt.md').write_text('Translate')
    schema={'type':'object','properties':{'blocks':{'type':'array','items':{'type':'object','properties':{'id':{'type':'string'},'translation':{'type':'string'},'glossary_terms_used':{'type':'array','items':{'type':'string'}}},'required':['id','translation','glossary_terms_used']}}},'required':['blocks']}
    (tmp_path/'schema.json').write_text(json.dumps(schema))
    cfg={'provider':{'backend':'codex_sdk'},'tasks':{'translate_page':{'model':'test','effort':'low','prompt':str(tmp_path/'prompt.md'),'schema':str(tmp_path/'schema.json')}}}
    args=(cfg,tmp_path/'g.csv',tmp_path/'s.md',tmp_path/'d.txt')
    payload={'dependency_fingerprint':'old-code-hash','blocks':[{'id':'x','source':'Sun'}]}
    changed={**payload,'dependency_fingerprint':'new-code-hash'}
    assert block_keys(*args,payload)==block_keys(*args,changed)
    old=block_keys(*args,payload,legacy=True)['x']
    new=block_keys(*args,payload)['x']
    db=sqlite3.connect(tmp_path/'llm_cache.sqlite');db.execute('CREATE TABLE blocks (key TEXT PRIMARY KEY,result TEXT NOT NULL)')
    db.execute('INSERT INTO blocks VALUES (?,?)',(old,json.dumps({'id':'x','translation':'Sol','glossary_terms_used':['Sun']})));db.commit();db.close()
    assert migrate_block_cache(tmp_path,*args,payload)['copied']==1
    db=sqlite3.connect(tmp_path/'llm_cache.sqlite');assert db.execute('select count(*) from blocks').fetchone()[0]==2
    assert db.execute('select result from blocks where key=?',(new,)).fetchone();db.close()
    # A changed semantic dependency cannot claim an old entry.
    (tmp_path/'s.md').write_text('Changed policy')
    assert migrate_block_cache(tmp_path,*args,payload)['copied']==0


def test_response_key_ignores_code_and_tracks_mask_and_memory(tmp_path,monkeypatch):
    import json
    from astro_translate.translation_context import response_key
    from astro_translate import llm,consistency
    for name in ('g.csv','s.md','d.txt','prompt.md'):(tmp_path/name).write_text(name)
    (tmp_path/'schema.json').write_text('{}')
    cfg={'provider':{'backend':'codex_sdk'},'tasks':{'translate_page':{'model':'test','effort':'low','prompt':str(tmp_path/'prompt.md'),'schema':str(tmp_path/'schema.json')}}}
    args=(cfg,tmp_path/'g.csv',tmp_path/'s.md',tmp_path/'d.txt')
    a={'dependency_fingerprint':'old','blocks':[{'id':'a','source':'Sun'}]}
    b={**a,'dependency_fingerprint':'new'}
    original=response_key(*args,a)
    assert response_key(*args,b)==original
    monkeypatch.setattr(llm,'MASK_VERSION','new-mask')
    assert response_key(*args,a)!=original
    changed=response_key(*args,a)
    monkeypatch.setattr(consistency,'MEMORY_VERSION','new-memory')
    assert response_key(*args,a)!=changed


def test_memory_context_tracks_prompt_instructions_not_their_code_location(tmp_path):
    from astro_translate.translation_context import translation_context
    for name in ('g','s','d','p','j'):(tmp_path/name).write_text(name)
    cfg={'tasks':{'translate_page':{'model':'test','effort':'low','prompt':str(tmp_path/'p'),'schema':str(tmp_path/'j')}}}
    args=(cfg,tmp_path/'g',tmp_path/'s',tmp_path/'d')
    first=translation_context(*args,instructions={'coordination_instruction':'preserve terms'})
    assert first==translation_context(*args,instructions={'coordination_instruction':'preserve terms'})
    assert first!=translation_context(*args,instructions={'coordination_instruction':'new policy'})
