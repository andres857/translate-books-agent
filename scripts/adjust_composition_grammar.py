#!/usr/bin/env python3
"""One authorized, narrow grammar correction for pilot p146; no manual targets."""
import csv,importlib.util,json,tomllib
from dataclasses import asdict
from pathlib import Path
from astro_translate.core import Blocked,atomic_json,sha256,require_approval
from astro_translate.adapters import require_backend_approval
from astro_translate.llm import ROOT,run_task


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--book',type=Path,required=True);args=parser.parse_args();book=args.book.resolve();folder=book/'work/pilot';cfg_path=ROOT/'config/pipeline.toml';cfg=tomllib.loads(cfg_path.read_text())
    paths={k:ROOT/cfg['paths'][k] for k in ('glossary','style_guide','do_not_translate')}
    require_approval(book,paths['glossary'],paths['style_guide'],paths['do_not_translate']);require_backend_approval(book,cfg_path,'pilot')
    spec=importlib.util.spec_from_file_location('composition',ROOT/'scripts/retranslate_composition.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    plan=json.loads((folder/'p0146-composition-plan.json').read_text());response=json.loads((folder/'p0146-composition-response.json').read_text());override=json.loads((folder/'p0146-composition-translations.json').read_text());fit=json.loads((folder/'p0146-fit.json').read_text());fit_hash=sha256(folder/'p0146-fit.json')
    ids={'p0146_u0001_paragraph1','p0146_u0026_paragraph0'};requests=[row for row in plan['requests'] if row['id'] in ids]
    if len(requests)!=2:raise Blocked('Expected exactly two approved grammar correction blocks')
    history=folder/'composition_history';history.mkdir(exist_ok=True)
    atomic_json(history/'p0146-before-grammar-override.json',override);atomic_json(history/'p0146-before-grammar-response.json',response)
    context={'operation':'Corrección editorial focalizada autorizada. Traduce de nuevo solo estos párrafos completos; no alteres contenido ni valores protegidos.','rules':['Corrige concordancia de determinantes, nombres y referentes. Un ángulo individual expresado mediante una sigla protegida es singular; no uses determinante plural para ese referente.','Las dos referencias de casas forman una enumeración: usa una única frase nominal plural que abarque la lista completa, o referencias singulares coordinadas. No multipliques cada referencia como si designara varios grupos de casas.','No dupliques artículos ni conjunciones junto a los marcadores. Respeta la forma obligatoria del glosario regente de la carta, completa y contigua.','Los marcadores preservan el orden de estilos, pero no obligan a una sintaxis inglesa. Usa conectores y sustantivos de apoyo en tramos sin énfasis adyacentes; conserva los nombres enfatizados sin extender el énfasis a palabras de apoyo.'], 'inline_constraints':{'p0146_u0001_paragraph1':'El tramo {v2} contiene el nombre compuesto enfatizado. El sustantivo que lo describe no está enfatizado en el origen y puede ir antes en {v1} para obtener sintaxis española natural.','p0146_u0026_paragraph0':'La única entidad protegida aquí es un ángulo individual: su determinante debe ser singular. Los marcadores de estilo no pueden interrumpir la concordancia.'}}
    result=run_task('translate_page',{'_book':str(book),'_config':str(cfg_path),'_phase':'pilot','_page':146,'_pages':[146],'blocks':requests,'context':context})
    atomic_json(folder/'p0146-composition-grammar-response.json',asdict(result))
    if sha256(folder/'p0146-fit.json')!=fit_hash:raise Blocked('Composition changed during grammar adjustment')
    revised={row['id']:row for row in result.data['blocks']};response['data']['blocks']=[revised.get(row['id'],row) for row in response['data']['blocks']]
    terms=[row for row in csv.DictReader(paths['glossary'].open()) if row['source'].casefold()=='conjunction']
    blocks,occurrences=module.resolve_composition(fit['blocks'],plan,{row['id']:row['translation'] for row in response['data']['blocks']},terms)
    previous=override['provenance'];adjustment={k:v for k,v in asdict(result).items() if k!='data'}
    provenance={**previous,'operation':'whole_composition_retranslation_and_authorized_grammar_adjustment','calls':[previous,adjustment],'grammar_adjusted_ids':sorted(ids),'grammar_response':str(folder/'p0146-composition-grammar-response.json')}
    for field in ('input_tokens','output_tokens','cached_input_tokens','latency_s'):
        a=previous.get(field);b=adjustment.get(field);provenance[field]=a+b if a is not None and b is not None else None
    override.update(blocks=blocks,provenance=provenance);atomic_json(folder/'p0146-composition-translations.json',override)
    atomic_json(folder/'p0146-composition-final-response.json',response)
    atomic_json(folder/'p0146-composition-consistency.json',{'occurrences':occurrences,'issues':module.check_consistency(occurrences),'scope':'isolated whole-page composition; global sentence memory untouched'})
    atomic_json(folder/'p0146-composition-grammar-record.json',{'source_pdf_sha256':override['source_sha256'],'operation':'grammar_validation_adjustment','reason':context,'responses':[{'id':row['id'],'source':next(p['source'] for pieces in plan['pieces'].values() for p in pieces if p['id']==row['id']),'grammatical_corrections':row.get('grammatical_corrections',[])} for row in result.data['blocks']],'usage':adjustment})
    print(json.dumps({'override':str(folder/'p0146-composition-translations.json'),'adjustment_usage':adjustment,'cumulative_input_tokens':provenance['input_tokens'],'cumulative_output_tokens':provenance['output_tokens']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
