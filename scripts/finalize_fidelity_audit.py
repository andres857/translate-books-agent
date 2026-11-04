#!/usr/bin/env python3
"""Refresh the offline audit against the final three-page fidelity PDF."""
import argparse
import json
import re
import hashlib
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import pymupdf
from astro_translate.consistency import check_rendered_consistency,normalize_source
from astro_translate.core import atomic_json,sha256


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--book',type=Path,required=True);parser.add_argument('--pdf',type=Path);args=parser.parse_args(argv)
    work=args.book/'work/fidelity';pdf=args.pdf or args.book/'out/fidelidad_1d.pdf';audit_path=work/'llm_audit.json'
    audit=json.loads(audit_path.read_text());occurrences=json.loads((work/'consistency.json').read_text())['occurrences']
    with pymupdf.open(pdf) as doc:
        if len(doc)!=3:raise ValueError('Final fidelity audit requires exactly three pages in order 7, 15, 121')
        texts={number:doc[index].get_text() for index,number in enumerate((7,15,121))}
    issues=check_rendered_consistency(occurrences,texts)
    repeated=defaultdict(list)
    for occurrence in occurrences:repeated[(occurrence['page'],occurrence['source'])].append(occurrence)
    counts=[]
    for (page,source),copies in repeated.items():
        if len(copies)<2:continue
        target=normalize_source(copies[0]['target']).casefold();text=normalize_source(texts[page]).casefold()
        counts.append({'page':page,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'expected_copies':len(copies),'observed_copies':len(re.findall(r'(?<!\w)'+re.escape(target)+r'(?!\w)',text))})
    dates=[];questions=[]
    for number in (7,15,121):
        payload_path=work/f'p{number:04d}-translation.json';payload=json.loads(payload_path.read_text());audit['sha256'][str(payload_path)]=sha256(payload_path)
        normalized=normalize_source(texts[number])
        for unit in payload['blocks']:
            if unit.get('translation_method')=='deterministic_date':dates.append({'page':number,'id':unit['id'],'present':normalize_source(unit['target']) in normalized})
            if unit.get('editorial_punctuation'):questions.append({'page':number,'id':unit['id'],'opening_mark_present':'¿' in unit['target'] and normalize_source(unit['target']) in normalized})
    audit['rendered_consistency']={'status':'final_pdf_checked','pdf':str(pdf),'pdf_sha256':sha256(pdf),'page_mapping':[7,15,121],'issues':issues,'repeated_clause_counts':counts,'date_checks':dates,'editorial_question_checks':questions,'passed':not issues and all(r['present'] for r in dates) and all(r['opening_mark_present'] for r in questions),'note':'Text existence/repetition checks supplement the independent geometry, glyph, font and visual QA. Concatenated label/date words remain failures.'}
    audit['sha256'][str(pdf)]=sha256(pdf);audit['final_render_audited_at']=datetime.now(timezone.utc).isoformat();atomic_json(audit_path,audit)
    print(json.dumps({'audit':str(audit_path),'render_checks_passed':audit['rendered_consistency']['passed'],'consistency_issues':len(issues),'dates_present':sum(r['present'] for r in dates),'date_total':len(dates),'questions_correct':sum(r['opening_mark_present'] for r in questions),'pdf_sha256':sha256(pdf)},ensure_ascii=False,indent=2))
    return 0 if audit['rendered_consistency']['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
