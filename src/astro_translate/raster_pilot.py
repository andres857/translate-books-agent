"""Bounded, audited raster pilot and external ephemeris appendix.

OCR is evidence, never permission to erase arbitrary pixels. The cover pilot
only edits locally inspected horizontal text on a demonstrably flat background.
Curved labels remain explicit review items, rather than guessed masks.
"""
from __future__ import annotations

import io
import json
import html
import math
import hashlib
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import pymupdf as fitz

from .core import Blocked, atomic_json, require_approval, sha256, _save_pdf
from .fonts import fallback_font


def page_usage(book,page):
    """Count actual inference once; cached rerenders are not new model usage."""
    path=Path(book)/'work/costs.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    selected=[r for r in rows if r.get('phase')=='pilot' and r.get('task')=='translate_page'
              and (r.get('page')==page or r.get('pages')==[page])]
    real=[r for r in selected if not r.get('cached') and r.get('status')=='ok']
    return {'model_calls':len(real),'input_tokens':sum(r.get('input_tokens') or 0 for r in real),
            'output_tokens':sum(r.get('output_tokens') or 0 for r in real),
            'cached_input_tokens':sum(r.get('cached_input_tokens') or 0 for r in real),
            'latency_s':sum(r.get('latency_s') or 0 for r in real),
            'cache_hits':sum(bool(r.get('cached')) for r in selected),
            'failed_calls_with_unknown_usage':sum(r.get('status')=='backend_error' and r.get('input_tokens') is None for r in selected),
            'billing':'ChatGPT subscription; no paid API fallback.'}


def clear_reviewed_flat_rectangles(image, regions, *, tolerance=20):
    """Return a copy; require audited regions and flat full boundary rings.

    The source image stays untouched. Protected content inside a region or
    missing local review is an error, including when a provider supplied a box.
    """
    original=image.convert('RGB'); cleaned=original.copy(); draw=ImageDraw.Draw(cleaned)
    mask=Image.new('1',original.size); md=ImageDraw.Draw(mask)
    reports=[]
    for region in regions:
        if not region.get('reviewed_text_only') or not region.get('reviewed_by'):
            raise Blocked('Raster cleanup needs a local text-only audit')
        if region.get('protected_content'):
            raise Blocked('Raster cleanup intersects protected content')
        box=region['pixel_bbox']; x0,y0,x1,y1=map(int,box)
        if not (1<=x0<x1<original.width-1 and 1<=y0<y1<original.height-1):
            raise Blocked('Raster cleanup box out of bounds')
        border=[original.getpixel((x,y)) for x in range(x0,x1) for y in (y0,y1-1)]
        border += [original.getpixel((x,y)) for y in range(y0,y1) for x in (x0,x1-1)]
        color=Counter(border).most_common(1)[0][0]
        if any(max(abs(c[k]-color[k]) for k in range(3))>tolerance for c in border):
            raise Blocked('Raster text boundary is not flat; do not erase artwork')
        draw.rectangle((x0,y0,x1-1,y1-1),fill=color)
        md.rectangle((x0,y0,x1-1,y1-1),fill=1)
        reports.append({'pixel_bbox':box,'background_rgb':list(color),'boundary_samples':len(border)})
    a=np.asarray(original);b=np.asarray(cleaned);m=np.asarray(mask,dtype=bool)
    outside=int(np.count_nonzero(np.any(a!=b,axis=2)&~m))
    if outside:raise Blocked('Raster cleanup altered pixels outside reviewed text')
    return cleaned, {'outside_text_changed_pixels':outside,'regions':reports}


def _raster_profile(book):
    from .profiles import load_profile,_module,file_digest
    profile=load_profile(book)
    record=profile.data.get('files',{}).get('raster')
    if not record:raise Blocked('No source-bound raster recipe for this book')
    path=profile.checked_file(record)
    return _module(str(path),file_digest(path))

def cover_plan(book):
    return _raster_profile(book).cover_plan(book)

def build_cover(book,config_path='config/pipeline.toml'):
    return _raster_profile(book).build_cover(book,config_path)

def build_mercury_annex(book,config_path='config/pipeline.toml'):
    return _raster_profile(book).build_mercury_annex(book,config_path)

def remove_mercury_footer_literal(data):
    book=Path(__file__).resolve().parents[2]/'books/mercury-technical-annex'
    return _raster_profile(book).remove_mercury_footer_literal(data)
