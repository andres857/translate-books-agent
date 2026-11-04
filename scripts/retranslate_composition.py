#!/usr/bin/env python3
"""Retranslate the authorized pilot case study from whole composition units.

Keeps the global sentence memory untouched; creates a hash-bound compositor
input rather than changing previously translated fragments or the glossary.
"""
from __future__ import annotations
import argparse,copy,json,re,tomllib
from dataclasses import asdict
from pathlib import Path
import pymupdf as fitz
from astro_translate.core import Blocked,atomic_json,read_extras,require_approval,sha256
from astro_translate.adapters import require_backend_approval
from astro_translate.consistency import _annotate,STYLE_MARKER,check_consistency
from astro_translate.fidelity import _make_unit,extract_fidelity_units,composition_sources_sha256
from astro_translate.llm import ROOT,run_task


def ordered_spans(unit):
    spans=copy.deepcopy(unit['spans'])
    for punctuation in unit.get('relocated_punctuation',[]):
        if punctuation not in spans:raise Blocked('Relocated punctuation lost its source provenance')
        spans.remove(punctuation);box=fitz.Rect(punctuation['bbox']);near=[]
        for i,span in enumerate(spans):
            r=fitz.Rect(span['bbox']);overlap=max(0,min(r.y1,box.y1)-max(r.y0,box.y0))
            if overlap>=min(r.height,box.height)*.6 and r.x1<=box.x0+1:near.append((r.x1,i))
        if not near:raise Blocked('Original punctuation cannot be assigned to a source line')
        spans.insert(max(near)[1]+1,punctuation)
    return spans


def make_plan(units,original_units,page):
    originals={unit['id']:unit for unit in original_units};requests=[];pieces={}
    for unit in units:
        spans=ordered_spans(unit)
        if unit.get('paragraphs'):
            if len(unit['paragraphs'])!=2:raise Blocked('Unexpected composition paragraph count')
            first=originals[unit['id']]['spans']
            if spans[:len(first)]!=first:raise Blocked('First paragraph no longer matches source spans')
            parts=[spans[:len(first)],spans[len(first):]]
        else:parts=[spans]
        pieces[unit['id']]=[]
        for index,part in enumerate(parts):
            composed=_make_unit(part,unit['source_block'],False,[])
            marked,markers,styles=_annotate(composed,0,len(composed['source']))
            identifier=f'p{page:04d}_{unit["id"]}_paragraph{index}'
            request={'id':identifier,'source':marked}
            if markers:request['_style_markers']=markers
            requests.append(request);pieces[unit['id']].append({'id':identifier,'source':composed['source'],'markers':markers,'styles':styles,'style':composed['style']})
    return {'page':page,'requests':requests,'pieces':pieces}


def decode_piece(piece,translation):
    markers=piece['markers']
    if not markers:return [{'text':translation,'style':piece['style']}]
    if STYLE_MARKER.findall(translation)!=markers or not translation.startswith(markers[0]) or not translation.endswith(markers[-1]):raise Blocked('Style markers changed in composition response')
    runs=[]
    for index,marker in enumerate(markers[:-1]):
        text=translation.split(marker,1)[1].split(markers[index+1],1)[0]
        if text:runs.append({'text':text,'style':piece['styles'][index]})
    return runs


def align_unique_term_style(source_runs,target_runs,term):
    """Restore a unique approved term's source style, never its translated text."""
    source=''.join(r['text'] for r in source_runs);target=''.join(r['text'] for r in target_runs)
    def matches(text,value):return list(re.finditer(r'(?<!\w)'+re.escape(value)+r'(?!\w)',text,re.I))
    sm=matches(source,term['source']);tm=matches(target,term['target'])
    if len(sm)!=1 or len(tm)!=1:return target_runs,[]
    cursor=0;styles=[]
    for run in source_runs:
        end=cursor+len(run['text'])
        if cursor<sm[0].end() and end>sm[0].start():styles.append(run['style'])
        cursor=end
    if not styles or any(st!=styles[0] for st in styles):return target_runs,[]
    out=[];cursor=0;changed=False
    for run in target_runs:
        end=cursor+len(run['text']);cuts=sorted({cursor,end,*[b for b in (tm[0].start(),tm[0].end()) if cursor<b<end]})
        for a,b in zip(cuts,cuts[1:]):
            style=styles[0] if a>=tm[0].start() and b<=tm[0].end() else run['style']
            changed|=style!=run['style'];text=run['text'][a-cursor:b-cursor]
            if out and out[-1]['style']==style:out[-1]['text']+=text
            else:out.append({'text':text,'style':style})
        cursor=end
    if ''.join(r['text'] for r in out)!=target:raise Blocked('Style alignment changed translated text')
    return out,[{'source_term':term['source'],'target_term':term['target'],'operation':'source_style_restored_from_unique_approved_term','text_changed':False}] if changed else []


def resolve_composition(units,plan,answers,glossary_terms=()):
    blocks=[];occurrences=[]
    for unit in units:
        runs=[];paragraphs=[];sources=[];style_records=[]
        for index,piece in enumerate(plan['pieces'][unit['id']]):
            part=decode_piece(piece,answers[piece['id']]);sources.append(piece['source'])
            source_marked=next(r['source'] for r in plan['requests'] if r['id']==piece['id'])
            source_runs=decode_piece(piece,source_marked)
            for term in glossary_terms:
                part,records=align_unique_term_style(source_runs,part,term);style_records.extend(records)
            if index and runs:
                runs[-1]['text']+=' ';paragraphs[-1]['target_runs'][-1]['text']+=' '
            runs+=copy.deepcopy(part)
            paragraphs.append({'target_runs':part,'indent':unit.get('paragraphs',[{'indent':unit.get('first_line_indent',0)}])[index]['indent']})
            occurrences.append({'source':piece['source'],'target':''.join(r['text'] for r in part),'page':plan['page'],'id':piece['id']})
        target=''.join(run['text'] for run in runs)
        block={'id':unit['id'],'source':unit['source'],'spans':unit['spans'],'target':target,'target_runs':runs,'translation_source':' '.join(sources)}
        if unit.get('paragraphs'):block['paragraphs']=paragraphs
        if style_records:block['style_alignment']=style_records
        blocks.append(block)
    issues=check_consistency(occurrences)
    if issues:raise Blocked('Repeated full source has inconsistent composition translations')
    return blocks,occurrences


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--book',type=Path,required=True);parser.add_argument('--config',type=Path,default=ROOT/'config/pipeline.toml');parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    book=args.book.resolve();config_path=args.config.resolve();config=tomllib.loads(config_path.read_text());page=146;folder=book/'work/pilot'
    def path(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    glossary=path(config['paths']['glossary']);style=path(config['paths']['style_guide']);protected=path(config['paths']['do_not_translate'])
    require_approval(book,glossary,style,protected);require_backend_approval(book,config_path,'pilot')
    fit_path=folder/'p0146-fit.json';fit_hash=sha256(fit_path);fit=json.loads(fit_path.read_text());units=fit['blocks']
    if fit['source_sha256']!=sha256(book/'source.pdf'):raise Blocked('Composition source PDF changed')
    with fitz.open(book/'source.pdf') as doc:original_units=extract_fidelity_units(doc[page-1],read_extras(protected))
    plan=make_plan(units,original_units,page);atomic_json(folder/'p0146-composition-plan.json',plan)
    chars=sum(len(request['source']) for request in plan['requests'])
    if chars>config['batching']['max_chars_per_call']:raise Blocked('Whole-page correction exceeds the approved single-call budget')
    print(json.dumps({'page':page,'paragraph_requests':len(plan['requests']),'composition_units':len(units),'characters':chars,'prepared':True}),flush=True)
    if args.prepare_only:return
    result=run_task('translate_page',{'_book':str(book),'_config':str(config_path),'_phase':'pilot','_page':page,'_pages':[page],'blocks':plan['requests'],'context':'Corrección de una página compuesta. Cada bloque es un párrafo completo o su continuación editorial, en orden de lectura. Traduce con gramática coherente entre bloques, sin traducir renglones o fragmentos de estilo aisladamente. Los marcadores delimitan énfasis tipográfico: conserva su orden pero permite reordenar palabras gramaticalmente dentro de los tramos para que la prosa sea natural y los términos enfatizados mantengan su énfasis. No dupliques conectores ni inventes contenido. Usa la perspectiva de toda la página para concordancia y continuaciones.'})
    atomic_json(folder/'p0146-composition-response.json',asdict(result))
    if sha256(fit_path)!=fit_hash:raise Blocked('Composition changed during inference; response retained but not applied')
    blocks,occurrences=resolve_composition(units,plan,{row['id']:row['translation'] for row in result.data['blocks']})
    provenance={k:v for k,v in asdict(result).items() if k!='data'};provenance.update({'phase':'pilot','operation':'whole_composition_retranslation','input_fit_sha256':fit_hash,'model_response':str(folder/'p0146-composition-response.json'),'plan':str(folder/'p0146-composition-plan.json'),'reason':'False table fragmentation produced ungrammatical prose; retranslated whole composition paragraphs through the approved backend.'})
    override={'page':page,'source_sha256':sha256(book/'source.pdf'),'glossary_sha256':sha256(glossary),'style_sha256':sha256(style),'do_not_translate_sha256':sha256(protected),'composition_sha256':composition_sources_sha256(units),'blocks':blocks,'provenance':provenance}
    atomic_json(folder/'p0146-composition-translations.json',override)
    atomic_json(folder/'p0146-composition-consistency.json',{'occurrences':occurrences,'issues':check_consistency(occurrences),'scope':'isolated whole-page composition; global sentence memory untouched'})
    atomic_json(folder/'p0146-composition-editorial-corrections.json',{'reason':provenance['reason'],'input_source_pdf_sha256':override['source_sha256'],'blocks':[{'id':row['id'],'source':next(p['source'] for pieces in plan['pieces'].values() for p in pieces if p['id']==row['id']),'corrections':row.get('grammatical_corrections',[])} for row in result.data['blocks']]})
    print(json.dumps({'override':str(folder/'p0146-composition-translations.json'),'backend':result.backend,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'latency_s':result.latency_s,'cached':result.cached}),flush=True)

if __name__=='__main__':main()
