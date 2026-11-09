"""Semantic translation identities and non-destructive, validated memory migration.

Code files and PDF geometry deliberately do not participate. MASK/MEMORY_VERSION
are explicit contracts; bump them when masking or phrase semantics change.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .core import sha256


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def context_data(config,glossary,style,protected,task='translate_page',root=None):
    from .consistency import MEMORY_VERSION
    from .llm import MASK_VERSION,ROOT
    root=Path(root or ROOT)
    def resolve(value):
        path=Path(value);return path if path.is_absolute() else root/path
    cfg=config['tasks'][task]
    return {'memory_version':MEMORY_VERSION,'mask_version':MASK_VERSION,'task':task,
            'backend':config.get('provider',{}).get('backend','claude_cli'),
            'model':cfg['model'],'effort':cfg['effort'],
            'glossary':sha256(glossary),'style':sha256(style),'protected':sha256(protected),
            'prompt':sha256(resolve(cfg['prompt'])),'schema':sha256(resolve(cfg['schema']))}


def translation_context(*args,instructions=None,**kwargs):
    data=context_data(*args,**kwargs)
    if instructions:data['prompt_instructions']=instructions
    return digest(data)


def semantic_payload(payload):
    # Historical dependency_fingerprint included implementation/PDF hashes. The
    # boundary already hashes every semantic dependency and all instructions.
    return {k:v for k,v in payload.items() if not k.startswith('_') and k!='dependency_fingerprint'}


def migrate_memory(work,old_context,new_context,entries,validator=None):
    """Copy only entries revalidated against known source plans; retain originals.

    Caller must prove matching semantic dependencies for old_context first.
    Conflicting valid translations are reported, never silently overwritten.
    """
    from .consistency import SentenceMemory
    old=SentenceMemory(work,old_context);new=SentenceMemory(work,new_context)
    report={'old_context':old_context,'new_context':new_context,'copied':0,'existing':0,'invalid':[],'conflicts':[]}
    try:
        for entry in entries:
            try:
                target=old.get(entry)
                if target is None:
                    if old.db.execute('SELECT 1 FROM sentence_memory WHERE context=? AND source=?',(old_context,entry['source'])).fetchone():
                        report['invalid'].append({'id':entry['id'],'reason':'Existing memory rejected by current numeric/text/style validation'})
                    continue
                if validator:validator(entry,target)
                previous=new.get(entry)
                if previous is not None:
                    if previous!=target:report['conflicts'].append(entry['id'])
                    else:report['existing']+=1
                    continue
                new.put(entry,target);report['copied']+=1
            except (ValueError,KeyError) as exc:report['invalid'].append({'id':entry['id'],'reason':str(exc)})
        return report
    finally:old.close();new.close()


def block_keys(config,glossary,style,protected,payload,task='translate_page',*,legacy=False):
    import unicodedata
    from .llm import TASK_ARRAY
    common=context_data(config,glossary,style,protected,task)
    if legacy:common.pop('memory_version')
    array=TASK_ARRAY[task]
    if any(r.get('_style_pairs') for r in payload[array]):
        from .style_spans import PAIR_INSTRUCTION
        common['style_pair_contract']=PAIR_INSTRUCTION
        from .emphasis import INSTRUCTION
        common['flat_emphasis_contract']=INSTRUCTION
    context={k:v for k,v in payload.items() if not k.startswith('_') and k!=array and (legacy or k!='dependency_fingerprint')}
    return {r['id']:digest({**{k:v for k,v in common.items() if r.get('_style_pairs') or k not in ('style_pair_contract','flat_emphasis_contract')},'context':context,'row':{
        k:unicodedata.normalize('NFC',v.replace('\r\n','\n')) if isinstance(v,str) else v
        for k,v in r.items() if k not in ('id','bbox','size','rotate','font')}}) for r in payload[array]}


def migrate_block_cache(work,config,glossary,style,protected,payload,task='translate_page'):
    """Copy exact old keys, after current validation; never infer cache provenance.

    The payload must be reconstructed from a saved input plan. An unmatched key
    stays in its old namespace. Neither old entries nor invalid outputs are lost.
    """
    import sqlite3
    from .llm import TASK_ARRAY,ROOT,validate_cached_translation,ValidationFailure
    old=block_keys(config,glossary,style,protected,payload,task,legacy=True)
    new=block_keys(config,glossary,style,protected,payload,task)
    schema=Path(config['tasks'][task]['schema']);schema=schema if schema.is_absolute() else ROOT/schema
    schema=json.loads(schema.read_text());array=TASK_ARRAY[task]
    db=sqlite3.connect(Path(work)/'llm_cache.sqlite',timeout=30)
    report={'copied':0,'existing':0,'missing':0,'invalid':[],'conflicts':[]}
    try:
        with db:
            for row in payload[array]:
                found=db.execute('SELECT result FROM blocks WHERE key=?',(old[row['id']],)).fetchone()
                if not found:report['missing']+=1;continue
                saved=json.loads(found[0]);saved['id']=row['id'];saved.pop('_cache_provenance',None)
                try:validate_cached_translation(task,{array:[saved]},{**payload,array:[row]},glossary,style,protected,schema)
                except ValidationFailure as exc:report['invalid'].append({'id':row['id'],'reason':str(exc)});continue
                previous=db.execute('SELECT result FROM blocks WHERE key=?',(new[row['id']],)).fetchone()
                if previous:
                    if json.loads(previous[0])['translation']!=saved['translation']:report['conflicts'].append(row['id'])
                    else:report['existing']+=1
                else:
                    db.execute('INSERT INTO blocks VALUES (?,?)',(new[row['id']],json.dumps(saved,ensure_ascii=False)));report['copied']+=1
    finally:db.close()
    return report


def response_key(config,glossary,style,protected,payload,task='translate_page',*,legacy=False):
    import unicodedata
    from .llm import ROOT
    def normalize(value):
        if isinstance(value,str):return unicodedata.normalize('NFC',value.replace('\r\n','\n'))
        if isinstance(value,list):return [normalize(v) for v in value]
        if isinstance(value,dict):return {k:normalize(v) for k,v in value.items()}
        return value
    common=context_data(config,glossary,style,protected,task)
    from .llm import TASK_ARRAY
    if any(r.get('_style_pairs') for r in payload.get(TASK_ARRAY.get(task,''),[])):
        from .style_spans import PAIR_INSTRUCTION
        common['style_pair_contract']=PAIR_INSTRUCTION
        from .emphasis import INSTRUCTION
        common['flat_emphasis_contract']=INSTRUCTION
    cfg=config['tasks'][task]
    def path(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    common.pop('prompt');common['template']=path(cfg['prompt']).read_text()
    common['schema']=json.loads(path(cfg['schema']).read_text())
    if legacy:common.pop('memory_version')
    normalized={k:v for k,v in payload.items() if not k.startswith('_')} if legacy else semantic_payload(payload)
    return digest({**common,'payload':normalize(normalized),'images':[sha256(p) for p in payload.get('_images',[])]})


def migrate_responses(work,config,glossary,style,protected,payload,task='translate_page'):
    """Reconstruct original batches by saved response IDs and prove the old key."""
    import sqlite3
    from .llm import TASK_ARRAY,ROOT,validate_cached_translation,ValidationFailure
    array=TASK_ARRAY[task];rows={r['id']:r for r in payload[array]}
    schema=Path(config['tasks'][task]['schema']);schema=schema if schema.is_absolute() else ROOT/schema
    schema=json.loads(schema.read_text())
    db=sqlite3.connect(Path(work)/'llm_cache.sqlite',timeout=30)
    report={'copied':0,'existing':0,'invalid':[]}
    try:
        with db:
            for key,raw in db.execute('SELECT key,result FROM responses').fetchall():
                saved=json.loads(raw);answers=saved.get('data',{}).get(array,[])
                if not answers or any(a['id'] not in rows for a in answers):continue
                # Batches preserve original request order, not response order.
                ids={a['id'] for a in answers};batch={**payload,array:[r for r in payload[array] if r['id'] in ids]}
                if response_key(config,glossary,style,protected,batch,task,legacy=True)!=key:continue
                if any(saved.get(k)!=context_data(config,glossary,style,protected,task)[k] for k in ('backend','model','effort')):continue
                try:validate_cached_translation(task,saved['data'],batch,glossary,style,protected,schema)
                except ValidationFailure as exc:report['invalid'].append({'key':key,'reason':str(exc)});continue
                new=response_key(config,glossary,style,protected,batch,task)
                if db.execute('SELECT 1 FROM responses WHERE key=?',(new,)).fetchone():report['existing']+=1
                else:db.execute('INSERT INTO responses VALUES (?,?)',(new,raw));report['copied']+=1
    finally:db.close()
    return report


def legacy_memory_context(config,glossary,style,protected,task='translate_page'):
    """Exact pre-profile adapter key; a compatibility lookup, never code hashing."""
    data=context_data(config,glossary,style,protected,task)
    data.pop('model');data.pop('effort');data['task']=config['tasks'][task]
    return digest(data)
