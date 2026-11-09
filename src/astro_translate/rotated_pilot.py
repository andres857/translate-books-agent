"""Shared rotated-label geometry and dispatch to source-bound book recipes."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from collections import Counter

import numpy as np
import pymupdf as fitz
from PIL import Image,ImageDraw

from .core import Blocked,atomic_json,sha256,require_approval,PROTECTED,_save_pdf
from .fonts import original_font,fallback_font,covers


def _chars(page,full_metrics=False):
    old=fitz.TOOLS.set_small_glyph_heights()
    try:
        fitz.TOOLS.set_small_glyph_heights(not full_metrics)
        return [{**c,'font':s['font'],'size':s['size'],'color':s['color'],'direction':list(l['dir'])}
                for b in page.get_text('rawdict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] for c in s['chars']]
    finally:fitz.TOOLS.set_small_glyph_heights(old)


def _rect_union(chars):
    rect=fitz.Rect(chars[0]['bbox'])
    for c in chars[1:]:rect|=fitz.Rect(c['bbox'])
    return rect


def _safe_redact(page,units):
    """Tiny glyph-center annotations avoid collateral adjacent text deletion."""
    wanted=[c for u in units for c in u['characters'] if c['c'].strip()]
    selected=[];others=[]
    for c in _chars(page,full_metrics=True):
        found=any(c['c']==w['c'] and c['font']==w['font'] and abs(c['origin'][0]-w['origin'][0])<.015 and abs(c['origin'][1]-w['origin'][1])<.015 for w in wanted)
        (selected if found else others).append(c)
    if len(selected)!=len(wanted):raise Blocked('Rotated source glyphs absent or changed in candidate')
    boxes=[]
    for c in selected:
        r=fitz.Rect(c['bbox']);center=(r.tl+r.br)/2
        tiny=fitz.Rect(center.x-.015,center.y-.015,center.x+.015,center.y+.015)
        if any(tiny.intersects(fitz.Rect(x['bbox'])) for x in others if x['c'].strip()):
            raise Blocked('Rotated redaction overlaps unrelated glyph')
        boxes.append(tiny)
    for r in boxes:page.add_redact_annot(r,fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    return [list(r) for r in boxes]


def curve_coefficients(characters):
    """Least-squares original baseline; source positions are never inferred by LLM."""
    points=np.asarray([c['origin'] for c in characters if c['c'].strip()],dtype=float)
    if len(points)<3 or np.ptp(points[:,0])<10:raise Blocked('Insufficient original curve geometry')
    coeff=np.polyfit(points[:,0],points[:,1],2)
    residual=float(np.max(np.abs(np.polyval(coeff,points[:,0])-points[:,1])))
    if residual>.3:raise Blocked('Original curve cannot be represented by a smooth quadratic safely')
    return coeff.tolist(),residual


def curved_positions(target,font,size,coeff,start):
    positions=[];x=start
    for character in target:
        y=float(np.polyval(coeff,x));slope=2*coeff[0]*x+coeff[1]
        angle=-math.degrees(math.atan(slope))
        positions.append({'character':character,'origin':[x,y],'rotate_degrees':angle})
        x+=font.text_length(character,fontsize=size)
    return positions


def separator_character_proofs(source_page,candidate_page):
    """Actual per-character operators, independent of rawdict line grouping."""
    def visible_bars(page):
        rows=[]
        for span in page.get_texttrace():
            for char in span['chars']:
                if char[0]!=ord('|') or not page.rect.contains(fitz.Point(char[2])):continue
                rows.append({'unicode':char[0],'glyph_id':char[1],'origin':list(char[2]),'bbox':list(char[3]),
                             **{k:list(span[k]) if isinstance(span[k],tuple) else span[k]
                                for k in ('dir','font','size','color','type','opacity')}})
        return sorted(rows,key=lambda x:x['origin'])
    before=visible_bars(source_page);after=visible_bars(candidate_page)
    if len(before)!=len(after):raise Blocked('Original separators missing or duplicated')
    proof=[{'source':a,'candidate':b,'exact_equal':a==b} for a,b in zip(before,after)]
    if not all(p['exact_equal'] for p in proof):raise Blocked('Original separator character operators changed')
    return proof


def _book_rotated(book):
    from .profiles import load_profile, _module, file_digest
    profile=load_profile(book)
    record=profile.data.get('files',{}).get('rotated')
    if not record:raise Blocked('No source-bound rotated plan for this book')
    path=profile.checked_file(record)
    return _module(str(path),file_digest(path))

def prepare_plan(book,number):
    return _book_rotated(book).prepare_plan(book,number)

def apply_plan(book,number,candidate,*,candidate_page=None):
    return _book_rotated(book).apply_plan(book,number,candidate,candidate_page=candidate_page)
