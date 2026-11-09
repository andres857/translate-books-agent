"""Offline survey detectors with no compositor counterpart yet.

Each detector is local, deterministic and inference-free (the raster probe
uses the local PaddleOCR engine, never an LLM). They report structure; they
do not change composition. ``heuristic`` flags in survey.DETECTORS say so.
"""
from __future__ import annotations

from collections import defaultdict
import re

import pymupdf as fitz

from .core import PROTECTED, SYMBOL_FONT

# ---------------------------------------------------------------- tables
VALUE = re.compile(r'^[\s\d°º′″.,:/–—+\-]+$')
TABLE_MIN_ROWS = 3


def _lines(page):
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            spans = [s for s in line['spans'] if s['text'].strip()]
            if spans and tuple(line['dir']) == (1., 0.):
                yield line, spans


def _is_value(span):
    text = span['text'].strip()
    return bool(SYMBOL_FONT.search(span['font']) or (VALUE.match(text) and PROTECTED.search(text)))


def key_value_rows(page):
    """Lines alternating translatable label / protected value, >=2 pairs.

    e.g. p179 'Root 1  Symbol ☌  Angle 0°  Orb 8°' (labels SohoPro-Medium 7 pt,
    values SohoPro-Bold or AstrologyPi glyphs).
    """
    rows = []
    for line, spans in _lines(page):
        tokens = []
        for span in spans:
            kind = 'value' if _is_value(span) else 'label'
            text = re.sub(r'\s+', ' ', span['text'].strip())
            if tokens and tokens[-1][0] == kind == 'label':
                tokens[-1] = ('label', tokens[-1][1]+' '+text, tokens[-1][2] | fitz.Rect(span['bbox']))
            elif tokens and tokens[-1][0] == kind == 'value':
                tokens[-1] = ('value', tokens[-1][1], tokens[-1][2] | fitz.Rect(span['bbox']))
            else:
                tokens.append((kind, text, fitz.Rect(span['bbox'])))
        labels = [t for t in tokens if t[0] == 'label']
        pairs = sum(1 for a, b in zip(tokens, tokens[1:]) if a[0] == 'label' and b[0] == 'value')
        if pairs >= 2 and len(labels) == pairs and all(len(t[1]) <= 24 for t in labels):
            box = tokens[0][2]
            for t in tokens[1:]:
                box |= t[2]
            rows.append({'labels': tuple(t[1].casefold() for t in labels), 'bbox': box})
    return rows


def glyph_label_cells(page):
    """Symbol-font glyphs each captioned by a short label centred beneath it.

    e.g. p180 PRH17Astrology glyphs 18 pt with 'The Sun', 'The Moon'... 6 pt.
    """
    glyphs, labels = [], []
    for _, spans in _lines(page):
        for s in spans:
            text = s['text'].strip()
            if SYMBOL_FONT.search(s['font']) and len(text) == 1:
                glyphs.append(s)
            elif not SYMBOL_FONT.search(s['font']) and len(text.split()) <= 3 and not PROTECTED.search(text):
                labels.append(s)
    cells = []
    for g in glyphs:
        gb = fitz.Rect(g['bbox']); centre = (gb.x0+gb.x1)/2
        below = [l for l in labels if 0 <= l['bbox'][1]-gb.y1 <= g['size']*.5
                 and abs((l['bbox'][0]+l['bbox'][2])/2-centre) <= max(gb.width, 6)
                 and l['size'] < g['size']]
        if len(below) == 1:
            cells.append({'glyph_bbox': gb, 'label': below[0]['text'].strip(),
                          'bbox': gb | fitz.Rect(below[0]['bbox']), 'size': g['size']})
    return cells


def detect_tables(page):
    """Grid structures: repeated key/value rows or rows of captioned glyphs."""
    tables = []
    groups = defaultdict(list)
    for row in key_value_rows(page):
        groups[row['labels']].append(row)
    for labels, rows in groups.items():
        if len(rows) >= TABLE_MIN_ROWS:
            box = rows[0]['bbox']
            for r in rows[1:]:
                box |= r['bbox']
            tables.append({'kind': 'key_value_rows', 'columns': list(labels), 'rows': len(rows),
                           'bbox': [round(v, 2) for v in box], 'heuristic': True})
    cells = glyph_label_cells(page)
    rows = []
    for cell in sorted(cells, key=lambda c: (c['glyph_bbox'].y0, c['glyph_bbox'].x0)):
        row = next((r for r in rows if abs(r[0]['glyph_bbox'].y1-cell['glyph_bbox'].y1) <= 2), None)
        if row is None:
            rows.append([cell])
        else:
            row.append(cell)
    rows = [sorted(r, key=lambda c: c['glyph_bbox'].x0) for r in rows if len(r) >= 2]
    if any(len(r) >= 3 for r in rows) or len(rows) >= 2:
        for r in rows:
            box = r[0]['bbox']
            for c in r[1:]:
                box |= c['bbox']
            tables.append({'kind': 'glyph_label_row', 'columns': len(r), 'rows': 1,
                           'labels': [c['label'] for c in r], 'bbox': [round(v, 2) for v in box],
                           'heuristic': True})
    return tables


# ------------------------------------------------------- display quotes
def vector_quote_marks(page, box, size):
    """Filled glyph-like shapes (quote marks drawn as vectors) at the start
    (left/top) and end (right/bottom) of a display text box."""
    box = fitz.Rect(box)
    marks = []
    for d in page.get_drawings():
        if d.get('fill') is None or d['type'] not in ('f', 'fs'):
            continue
        r = fitz.Rect(d['rect'])
        if not (.4*size <= r.height <= 1.3*size and r.width <= .8*r.height):
            continue
        if not (box+(-2*size, -1.5*size, 2*size, 1.5*size)).contains(r):
            continue
        marks.append(r)
    start = [r for r in marks if r.x1 <= box.x0+size and r.y0 <= box.y0+size]
    end = [r for r in marks if r.x0 >= box.x0+box.width*.4 and r.y1 >= box.y1-size]
    return len(start), len(end)


def single_unit_quotes(page, units, known_ids=()):
    """A complete pull quote already extracted as one unit.

    The shared compositor rule joins several display units; p13's quote
    ('Your birth chart is an image of you...', SohoPro Light/Bold Compressed
    27 pt, 3 lines) is one unit framed by vector quote marks.
    """
    from .display_quotes import source_lines
    found = []
    for u in units:
        if u['id'] in known_ids or u.get('size', 0) < 20 or not u.get('spans'):
            continue
        text = u['source']
        fonts = {s['font'] for s in u['spans']}
        lines = source_lines({'spans': u['spans']})
        if len(lines) < 2 or len(text.split()) < 8 or text.isupper() or len(fonts) < 2:
            continue
        typed = bool(re.match(r'^[\s“"…]', text) and re.search(r'[.!?…][”"]\s*$', text))
        start, end = vector_quote_marks(page, u['bbox'], u['size'])
        if typed or (start and end):
            found.append({'bbox': [round(v, 2) for v in u['bbox']], 'unit_ids': [u['id']],
                          'quoted': True, 'quote_marks': 'text' if typed else 'vector',
                          'single_unit': True, 'heuristic': True})
    return found


# ------------------------------------------------------ raster text probe
OCR_MIN_SCORE = .8
OCR_MIN_LETTERS = 3
_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from paddleocr import PaddleOCR
        # oneDNN fails on this host for PP-OCRv6; plain CPU kernels work.
        _ENGINE = PaddleOCR(lang='en', device='cpu', enable_mkldnn=False, use_doc_orientation_classify=False,
                            use_doc_unwarping=False, use_textline_orientation=False)
    return _ENGINE


def probe_raster_text(page, engine=None, dpi=110):
    """Local OCR of the rendered page (raster only, native text excluded).

    Returns ``contains_text`` True/False, or None when OCR is unavailable:
    unknown stays unknown, never assumed True. Recognised strings stay local.
    """
    import tempfile
    from pathlib import Path
    try:
        engine = engine or _engine()
    except Exception as exc:  # missing/broken local OCR is explicit
        return {'contains_text': None, 'method': f'unavailable: {type(exc).__name__}: {exc}'[:200], 'lines': []}
    native = [fitz.Rect(s['bbox']) for b in page.get_text('dict')['blocks'] for l in b.get('lines', [])
              for s in l['spans'] if s['text'].strip()]
    scale = dpi/72
    with tempfile.TemporaryDirectory(prefix='astro-ocr-') as folder:
        path = Path(folder)/'page.png'
        page.get_pixmap(dpi=dpi).save(path)
        try:
            results = list(engine.predict(str(path)))
        except Exception as exc:
            return {'contains_text': None, 'method': f'failed: {type(exc).__name__}: {exc}'[:200], 'lines': []}
    lines = []
    for result in results:
        res = getattr(result, 'json', result)
        res = res.get('res', res)
        boxes = res.get('rec_boxes') or res.get('rec_polys') or []
        for i, (text, score) in enumerate(zip(res.get('rec_texts', []), res.get('rec_scores', []))):
            letters = sum(c.isalpha() for c in text)
            box = None
            if i < len(boxes):
                b = boxes[i]
                flat = [v for p in b for v in (p if isinstance(p, (list, tuple)) else [p])]
                xs, ys = flat[0::2], flat[1::2]
                box = fitz.Rect(min(xs)/scale, min(ys)/scale, max(xs)/scale, max(ys)/scale)
            if box is not None and any((box & n).get_area() > .5*box.get_area() for n in native):
                continue  # native text is already known
            if score >= OCR_MIN_SCORE and letters >= OCR_MIN_LETTERS:
                lines.append({'text': text, 'score': round(float(score), 3),
                              'bbox': [round(v, 2) for v in box] if box is not None else None})
    return {'contains_text': bool(lines), 'method': f'local PaddleOCR PP-OCRv6 score>={OCR_MIN_SCORE}, '
            f'>={OCR_MIN_LETTERS} letters, native spans excluded', 'lines': lines}
