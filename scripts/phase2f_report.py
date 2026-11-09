"""Summarize offline F2F evidence and render source callout partitions."""
import argparse
import html
import json
from pathlib import Path
import pymupdf as fitz
from PIL import Image,ImageDraw
from astro_translate.core import atomic_json,sha256
from astro_translate.fidelity import extract_fidelity_units
from astro_translate.figure_captions import caption_units


def report(book):
    book=Path(book).resolve();folder=book/'work/phase2f';evidence=folder/'evidence'
    data=json.loads((folder/'report.json').read_text());rows=data['pages'];checks=[]
    for row in rows:
        path=folder/f'p{row["page"]:04d}-qa.json'
        if not path.exists() or not row.get('candidate'):continue
        qa=json.loads(path.read_text())
        checks.append({'page':row['page'],'local_fidelity_ok':qa['local_fidelity_ok'],
            'non_text_pixel_diff':qa['non_text_pixel_diff'],'typography':qa['typography_audit']['ok'],
            **{key:qa[key] for key in ('numbers_equal','immutable_spans_equal','images_equal','vectors_equal','uniform_scale_groups','original_colors_verified')},
            'min_scale':qa['min_scale']})
    plan=json.loads((folder/'retranslation-plan.json').read_text());figures=[]
    with fitz.open(book/'source.pdf') as doc:
        for item in plan['pages']:
            n=item['page'];page=doc[n-1];pix=page.get_pixmap(dpi=110)
            original=Image.frombytes('RGB',(pix.width,pix.height),pix.samples);overlay=original.copy();draw=ImageDraw.Draw(overlay)
            caps=caption_units(page,extract_fidelity_units(page));factor=110/72
            for i,u in enumerate(caps,1):
                x0,y0,x1,y1=[x*factor for x in u['bbox']]
                draw.rectangle((x0-2,y0-2,x1+2,y1+2),outline=(220,0,150),width=2)
                draw.text((x0,y0-15),f'callout {i}: {u["alignment"]}',fill=(150,0,100))
            side=Image.new('RGB',(pix.width*2,pix.height+30),'white');side.paste(original,(0,30));side.paste(overlay,(pix.width,30))
            ImageDraw.Draw(side).text((10,8),f'PDF {n} | original / source segmentation only; translation pending',fill='black')
            path=evidence/f'p{n:04d}-caption-partitions.png';side.save(path);figures.append(path)
    protected=json.loads((folder/'protected-before.json').read_text())
    changed=[p for p,h in protected.items() if not Path(p).is_file() or sha256(p)!=h]
    verification={'llm_calls':0,'vlm_calls':0,'production_executed':False,'pages':checks,
        'qa_ok':[r['page'] for r in rows if r.get('local_fidelity_ok')],
        'pending':[{'page':r['page'],'status':r['status'],'failures':r.get('failures',[])} for r in rows if not r.get('local_fidelity_ok')],
        'caption_jobs':sum(len(r['jobs']) for r in plan['pages']),
        'protected_files_checked':len(protected),'protected_files_changed':changed,
        'comparisons':{Path(r['comparison']).name:sha256(r['comparison']) for r in rows}}
    atomic_json(folder/'verification.json',verification)
    cards=[]
    for r in rows:
        path=Path(r['comparison']);cards.append(f'<section><h2>PDF {r["page"]}: {html.escape(r["status"])}</h2><a href="{path.name}"><img loading="lazy" src="{path.name}" alt="Comparison page {r["page"]}"></a></section>')
    for path in figures:cards.append(f'<section><h2>Geometría pendiente de retraducción</h2><img src="{path.name}"></section>')
    (evidence/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Fase 2F — evidencia offline</title><style>body{font-family:system-ui;max-width:1500px;margin:auto;padding:20px}img{max-width:100%}section{margin:36px 0;border-top:1px solid #bbb}</style><h1>Fase 2F — 41–60</h1><p>Sin LLM/VLM. Cuando no existe candidato admisible, la comparativa muestra el original a ambos lados. Las cajas de leyendas son planificación, no traducción.</p>'+''.join(cards))
    print(json.dumps({k:v for k,v in verification.items() if k not in ('pages','comparisons')},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--book',required=True);a=p.parse_args();report(a.book)
