#!/usr/bin/env python3
"""Reuse one current, validated memory translation in the final pilot composition.

No inference and no hand-authored target text. This restores a previously
translated continuation, joins it to its paragraph, and restores source punctuation.
"""
import argparse,copy,hashlib,json,sqlite3,tomllib
from pathlib import Path
from astro_translate.core import Blocked,atomic_json,require_approval,sha256
from astro_translate.fidelity import apply_composition_overrides,composition_sources_sha256
from astro_translate.consistency import MEMORY_VERSION,check_consistency
from astro_translate.llm import ROOT,MASK_VERSION


def merge_validated_continuation(old_blocks,source_blocks,previous,cached):
    old={b['id']:b for b in old_blocks};current={b['id']:b for b in source_blocks}
    if set(current)!=(set(old)-{'u0022'}):raise Blocked('Unexpected composition units changed during continuation repair')
    fragment=old['u0022'];owner=old['u0021'];new=current['u0021']
    if previous['source']!=fragment['source'] or previous['spans']!=fragment['spans']:raise Blocked('Reused continuation source provenance differs')
    if cached['marked_source']!=previous['source'] or cached['plain']!=previous['target']:raise Blocked('Reused continuation differs from current sentence memory')
    if ''.join(r['text'] for r in previous['target_runs'])!=previous['target']:raise Blocked('Reused continuation styles do not reconstruct its target')
    punctuation=new['spans'][-1]
    if punctuation['source']!='.' or new['source']!=owner['source']+' '+fragment['source']+punctuation['source']:raise Blocked('Joined continuation or source punctuation changed')
    if new['spans'][:-1]!=owner['spans']+fragment['spans']:raise Blocked('Joined source spans changed')
    repaired=[]
    for unit in source_blocks:
        if unit['id']!='u0021':
            prior=old[unit['id']]
            if prior['source']!=unit['source'] or prior['spans']!=unit['spans']:raise Blocked('Unrelated unit changed')
            repaired.append(copy.deepcopy(prior));continue
        runs=copy.deepcopy(owner['target_runs']);runs[-1]['text']+=' ';runs+=copy.deepcopy(previous['target_runs']);runs.append({'text':punctuation['source'],'style':punctuation['style']})
        repaired.append({**copy.deepcopy(owner),'source':unit['source'],'spans':unit['spans'],'target':''.join(r['text'] for r in runs),'target_runs':runs,'translation_source':unit['source'],'reused_continuation':{'source':previous['source'],'target':previous['target'],'method':'current_sentence_memory_and_original_pilot_payload'}})
    return repaired


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--book',type=Path,required=True);args=parser.parse_args();book=args.book.resolve();folder=book/'work/pilot';cfg=tomllib.loads((ROOT/'config/pipeline.toml').read_text());task=cfg['tasks']['translate_page']
    def path(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    glossary=path(cfg['paths']['glossary']);style=path(cfg['paths']['style_guide']);protected=path(cfg['paths']['do_not_translate']);require_approval(book,glossary,style,protected)
    payload_path=folder/'p0146-translation.json';payload=json.loads(payload_path.read_text());previous=next(b for b in payload['blocks'] if b['id']=='p0146_u0022')
    if payload['source_sha256']!=sha256(book/'source.pdf'):raise Blocked('Original pilot payload references another source PDF')
    dependencies={'memory_version':MEMORY_VERSION,'mask_version':MASK_VERSION,'backend':cfg['provider']['backend'],'task':task,'glossary':sha256(glossary),'style':sha256(style),'protected':sha256(protected),'prompt':sha256(path(task['prompt'])),'schema':sha256(path(task['schema']))}
    context=hashlib.sha256(json.dumps(dependencies,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if context!=payload['memory_context']:raise Blocked('Translation memory context is not current')
    db=sqlite3.connect(f'file:{book/"work/llm_cache.sqlite"}?mode=ro',uri=True)
    try:row=db.execute('SELECT entry FROM sentence_memory WHERE context=? AND source=?',(context,previous['source'])).fetchone()
    finally:db.close()
    if not row:raise Blocked('No validated current sentence memory translation exists')
    cached=json.loads(row[0]);source_path=folder/'p0146-composition-sources-final.json';sources=json.loads(source_path.read_text());override_path=folder/'p0146-composition-translations.json';override=json.loads(override_path.read_text());old=copy.deepcopy(override)
    if composition_sources_sha256(sources['blocks'])!=sources['composition_sha256']:raise Blocked('New composition hash is invalid')
    if 'u0022' not in {b['id'] for b in override['blocks']}:
        if override.get('provenance',{}).get('continuation_repair',{}).get('source_composition_sha256')!=sources['composition_sha256']:raise Blocked('An unrelated override already removed the continuation')
        checked=apply_composition_overrides(book,sources['blocks'],override_path,146,glossary,style,protected)
        print(json.dumps({'stable':True,'units':len(checked),'resumed':True,'new_llm_calls':0}),flush=True);return
    blocks=merge_validated_continuation(override['blocks'],sources['blocks'],previous,cached)
    record={'operation':'reuse_current_validated_continuation','page':146,'owner':'u0021','removed_fragment':'u0022','source':previous['source'],'prior_valid_target':previous['target'],'reason':'Rejected semantic addition in fresh model output; reused the original valid translation and composed its continuation in the same paragraph.','previous_payload_sha256':sha256(payload_path),'memory_context':context,'source_composition_sha256':sources['composition_sha256'],'source_composition_file_sha256':sha256(source_path),'new_llm_calls':0,'new_input_tokens':0,'new_output_tokens':0,'other_units_unchanged':True}
    override.update(blocks=blocks,composition_sha256=sources['composition_sha256']);override['provenance']['continuation_repair']=record
    history=folder/'composition_history';history.mkdir(exist_ok=True);atomic_json(history/'p0146-before-continuation-override.json',old)
    atomic_json(override_path,override)
    checked=apply_composition_overrides(book,sources['blocks'],override_path,146,glossary,style,protected)
    audit=json.loads((folder/'p0146-composition-audit.json').read_text());audit.update(status='stable_after_continuation_repair',units=len(checked),continuation_repair=record,override_sha256=sha256(override_path),protected_counters_validated=True)
    atomic_json(folder/'p0146-composition-audit.json',audit);atomic_json(folder/'p0146-continuation-repair.json',record)
    consistency_path=folder/'p0146-composition-consistency.json';consistency=json.loads(consistency_path.read_text());merged=next(b for b in blocks if b['id']=='u0021')
    consistency['occurrences']=[{**r,'source':merged['source'],'target':merged['target']} if r['id']=='p0146_u0021_paragraph0' else r for r in consistency['occurrences'] if r['id']!='p0146_u0022_paragraph0']
    consistency['issues']=check_consistency(consistency['occurrences']);consistency['continuation_repair']=record;atomic_json(consistency_path,consistency)
    print(json.dumps({'stable':True,'units':len(checked),'override_sha256':sha256(override_path),'composition_sha256':sources['composition_sha256'],'new_llm_calls':0}),flush=True)

if __name__=='__main__':main()
