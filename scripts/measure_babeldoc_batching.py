#!/usr/bin/env python3
"""Offline instrumentation of installed BabelDOC batching using real safe spans.

This is deliberately NOT a full BabelDOC PDF/layout benchmark: its installed
process_page scheduler is fed a controlled IL (one paragraph per safe span),
without downloading model weights or invoking any translator. Counts document
that page-level grouping cannot be guaranteed by BaseTranslator injection.
"""
import argparse,json,time
from pathlib import Path
from types import SimpleNamespace
import pymupdf,tiktoken
from babeldoc.format.pdf.document_il import Page,PdfParagraph,PdfParagraphComposition,PdfSameStyleUnicodeCharacters
from babeldoc.format.pdf.document_il.midend.il_translator import PageTranslateTracker
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
from astro_translate.core import extract_units,read_extras,atomic_json

p=argparse.ArgumentParser();p.add_argument('--book',type=Path,required=True);p.add_argument('--pages',default='7,14,15,121,133');a=p.parse_args()
class Collector:
    def __init__(self):self.batches=[]
    def submit(self,fn,batch,*args,**kwargs):self.batches.append(len(batch.paragraphs))

context=SimpleNamespace(first_paragraph=None,recent_title_paragraph=None)
runner=ILTranslatorLLMOnly.__new__(ILTranslatorLLMOnly)
runner.translation_config=SimpleNamespace(raise_if_cancelled=lambda:None,min_text_length=1,shared_context_cross_split_part=context)
runner.shared_context_cross_split_part=context;runner.mid=0;runner.tokenizer=tiktoken.get_encoding('o200k_base')
rows=[]
with pymupdf.open(a.book/'source.pdf') as doc:
    for no in map(int,a.pages.split(',')):
        start=time.monotonic();units=extract_units(doc[no-1],read_extras('glossary/do_not_translate.txt'));extract_s=time.monotonic()-start
        page=Page(pdf_paragraph=[PdfParagraph(unicode=u['source'],debug_id=str(i),layout_label='text',pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(unicode=u['source']))]) for i,u in enumerate(units)])
        collector=Collector();start=time.monotonic()
        runner.process_page(page,collector,tracker=PageTranslateTracker(),translated_ids=set())
        rows.append({'page':no,'safe_spans':len(units),'scheduler_batches':collector.batches,'scheduler_calls':len(collector.batches),'extraction_s':extract_s,'scheduler_s':time.monotonic()-start,'own_route_page_calls':1})
result={'method':'Installed BabelDOC process_page with controlled IL from real safe spans; no model calls','not_measured':['full BabelDOC PDF pipeline latency','BabelDOC/Codex latency ratio','BabelDOC layout quality'],'limitation':'Paragraph segmentation comes from PyMuPDF safe spans, not BabelDOC frontend; counts are scheduler characterization, not production call predictions. Cross-page/column batches omitted.','pages':rows}
atomic_json(a.book/'work/backend_smoke/babeldoc_batching.json',result)
print(json.dumps(result,indent=2))
