"""Back matter: alphabetical index and glossary, re-sorted for Spanish.

Editorial policy (delegated decision, 2026-11-08, recorded in
docs/decisiones.md): entries are translated through ``llm.run_task`` only
(never in conversation); they are re-sorted alphabetically for Spanish
(es-ES collation, initial articles ignored, ñ after n, digits numeric);
page locators are copied verbatim (pagination does not change) and never
sent to a model; the whole run of index pages is re-flowed into the same
column grid and typography, with regenerated letter headings. The glossary is
re-flowed as one card per entry (term + definition, never split) in the same
Spanish order. Detection is structural; there are no page numbers here.

Unsupported shapes raise ``Blocked`` with an explicit reason; nothing is
composed partially.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import unicodedata

import pymupdf as fitz

from .core import Blocked, PROTECTED, SYMBOL_FONT, atomic_json, sha256

VERSION = 1
LOCATOR = r'\d+(?:[-–]\d+)?'
LOCATORS = re.compile(r'^(?P<term>.*?\D)\s+(?P<loc>'+LOCATOR+r'(?:,\s*'+LOCATOR+r')*),?\s*$')
ONLY_LOCATORS = re.compile(r'^\s*'+LOCATOR+r'(?:,\s*'+LOCATOR+r')*,?\s*$')
ARTICLES = ('el', 'la', 'los', 'las', 'lo', 'un', 'una', 'unos', 'unas')
HEADER_BAND = .1   # running heads/folios live in the top 10 % of the page
MIN_SCALE = .75
CONTEXT = {
    'index': ('Back-of-book index of an astrology book (EN->ES). Each label is an index heading, a '
              'subheading, a cross-reference label ("see", "see also") or a cross-reference target. '
              'Subheadings read after their parent heading (parent "Aries" + subheading "Chiron in" means '
              '"Chiron in Aries"); translate them so they still read that way. Index style: keep lowercase '
              'initials unless a proper noun; no page numbers; no added articles at the start.'),
    'glossary': ('Glossary of an astrology book (EN->ES). Labels are glossary headwords; blocks are their '
                 'definitions. Keep each headword short, in its dictionary form.'),
}


# ------------------------------------------------------------- collation
def _fold(word):
    out = []
    for ch in unicodedata.normalize('NFD', word.casefold()):
        if unicodedata.combining(ch):
            if ch == '̃' and out and out[-1] == 'n':
                out[-1] = 'n\x7f'  # ñ sorts after every n-word
            continue
        out.append(ch)
    return ''.join(out)


def sort_key(term):
    """es-ES index order: word by word, initial article ignored, accents and
    case only break ties, digit runs compared numerically (1.ª < 2.ª < 10.ª)."""
    text = term.strip()
    words = re.findall(r'[^\W_]+', text)
    if len(words) > 1 and words[0].casefold() in ARTICLES:
        words = words[1:]
    primary = []
    for word in words:
        chunks = re.findall(r'\d+|\D+', _fold(word))
        primary.append(tuple((0, int(c), '') if c.isdigit() else (1, 0, c) for c in chunks))
    accents = tuple(unicodedata.normalize('NFD', w.casefold()) for w in words)
    case = tuple(w.swapcase() for w in words)
    return (tuple(primary), accents, case, text)


def heading_letter(term):
    key = sort_key(term)[0]
    if not key:
        return '#'
    first = key[0][0]
    if first[0] == 0:
        return '#'
    return 'Ñ' if first[2].startswith('n\x7f') else first[2][0].upper()


# -------------------------------------------------------------- extraction
def _lines(page):
    """Body lines with spans, styles and the x of the first visible glyph."""
    from .fidelity import span_style
    rows = []
    raw = page.get_text('rawdict')
    for block in raw['blocks']:
        for line in block.get('lines', []):
            if tuple(line['dir']) != (1., 0.):
                continue
            spans = []
            for s in line['spans']:
                text = ''.join(c['c'] for c in s['chars'])
                if not text.strip():
                    continue
                spans.append({'source': text, 'font': s['font'], 'size': s['size'], 'color': s['color'],
                              'bbox': list(s['bbox']), 'origin': list(s['chars'][0]['origin']),
                              'style': span_style(s),
                              'x': next(c['bbox'][0] for c in s['chars'] if c['c'].strip())})
            if not spans:
                continue
            box = fitz.Rect(spans[0]['bbox'])
            for s in spans[1:]:
                box |= fitz.Rect(s['bbox'])
            rows.append({'spans': spans, 'text': ''.join(s['source'] for s in spans).strip(), 'bbox': box,
                         'x': spans[0]['x'], 'size': max(s['size'] for s in spans),
                         'baseline': spans[0]['origin'][1]})
    return rows


def _body(page, rows):
    top = page.rect.height*HEADER_BAND
    return [r for r in rows if r['bbox'].y0 >= top and r['size'] < 20 and not SYMBOL_FONT.search(r['spans'][0]['font'])]


def _is_heading(row, body_size):
    t = row['text']
    return (len(t) == 1 and t.isalpha() and t.isupper() and row['spans'][0]['style']['bold']
            and row['size'] >= 1.3*body_size)


def _body_size(rows):
    sizes = Counter(round(s['size'], 1) for r in rows for s in r['spans'] for _ in s['source'])
    return sizes.most_common(1)[0][0] if sizes else 0


def page_role(page):
    """'index', 'glossary' or None, from structure only."""
    body = _body(page, _lines(page))
    if len(body) < 20:
        return None
    size = _body_size(body)
    headings = [r for r in body if _is_heading(r, size)]
    entries = [r for r in body if not _is_heading(r, size)]
    located = sum(bool(LOCATORS.match(r['text']) or ONLY_LOCATORS.match(r['text'])) for r in entries)
    if headings and len(entries) >= 30 and located >= .5*len(entries):
        return 'index'
    starts = [r for r in entries if len(r['spans']) >= 2 and r['spans'][0]['style']['bold']
              and not r['spans'][1]['style']['bold'] and 1 <= len(r['spans'][0]['source'].split()) <= 4]
    if len(starts) >= 8:
        terms = [r['spans'][0]['source'].strip().casefold() for r in sorted(starts, key=lambda r: (_column_x(r), r['bbox'].y0))]
        ordered = sum(a <= b for a, b in zip(terms, terms[1:]))
        if ordered >= .9*(len(terms)-1) and len(starts) >= .15*len(entries):
            return 'glossary'
    return None


def _column_x(row):
    return round(row['x']/50)


def _columns(lines, gap=100):
    """Column origins = minimum glyph x of each cluster of line starts."""
    xs = sorted(r['x'] for r in lines)
    clusters = []
    for x in xs:
        if clusters and x-clusters[-1][0] < gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [min(c) for c in clusters]


def _column_of(x, origins):
    return max(i for i, o in enumerate(origins) if x >= o-4) if x >= origins[0]-4 else 0


def _page_labels(page, rows, body_ids):
    """Running heads and titles (translatable, outside the body); folios stay."""
    labels = []
    for r in rows:
        if id(r) in body_ids or PROTECTED.fullmatch(r['text']) or SYMBOL_FONT.search(r['spans'][0]['font']):
            continue
        spans = [s for s in r['spans'] if not PROTECTED.fullmatch(s['source'].strip())]
        if not spans:
            continue
        labels.append({'text': ' '.join(s['source'].strip() for s in spans), 'spans': spans,
                       'bbox': [round(v, 2) for v in r['bbox']]})
    return labels


def analyze(doc):
    """Group contiguous pages with the same back-matter role into structures."""
    structures, current = [], None
    for page in doc:
        role = page_role(page)
        if role and current and current['kind'] == role and current['pages'][-1] == page.number:
            current['pages'].append(page.number+1)
        elif role:
            current = {'kind': role, 'pages': [page.number+1]}
            structures.append(current)
        else:
            current = None
    result = []
    for s in structures:
        build = _build_index if s['kind'] == 'index' else _build_glossary
        result.append(build(doc, s['pages']))
    return result


def structure_for(doc, number):
    for s in analyze(doc):
        if number in s['pages']:
            return s
    return None


def _style_key(style):
    return json.dumps(style, sort_keys=True)


def _measure_common(doc, pages):
    per_page, all_body = {}, []
    for n in pages:
        page = doc[n-1]
        rows = _lines(page)
        body = _body(page, rows)
        per_page[n] = (page, rows, body)
        all_body += body
    return per_page, all_body


def _pitch(columns):
    diffs = [round(b['bbox'].y0-a['bbox'].y0, 2) for col in columns for a, b in zip(col, col[1:])
             if 0 < b['bbox'].y0-a['bbox'].y0 < 2*a['size']]
    if not diffs:
        raise Blocked('Back matter: cannot measure line pitch')
    return Counter(diffs).most_common(1)[0][0]


def _build_index(doc, pages):
    per_page, all_body = _measure_common(doc, pages)
    size = _body_size(all_body)
    origins = _columns([r for r in all_body if not _is_heading(r, size)])
    sequence, frames, labels, removal = [], [], {}, []
    columns_all = []
    for n in pages:
        page, rows, body = per_page[n]
        body_ids = {id(r) for r in body}
        labels[n] = _page_labels(page, rows, body_ids)
        removal += [{'page': n, 'source': s['source'], 'font': s['font'], 'bbox': s['bbox']}
                    for r in body for s in r['spans']]
        cols = [[] for _ in origins]
        for r in body:
            cols[_column_of(r['x'], origins)].append(r)
        for i, col in enumerate(cols):
            col.sort(key=lambda r: r['bbox'].y0)
            columns_all.append(col)
            sequence += [(n, i, r) for r in col]
    pitch = _pitch([[r for r in c if not _is_heading(r, size)] for c in columns_all])
    lines = [r for _, _, r in sequence if not _is_heading(r, size)]
    offsets = sorted(r['x']-origins[_column_of(r['x'], origins)] for r in lines)
    sub = statistics.median([o for o in offsets if 4 <= o < 12.5] or [8.5])
    cont = statistics.median([o for o in offsets if o >= 12.5] or [2*sub])
    measure = max(r['bbox'].x1-origins[_column_of(r['x'], origins)] for r in lines)
    baseline = statistics.median(r['baseline']-r['bbox'].y0 for r in lines)
    # Headings: glyph baseline relative to the slot of the line that follows.
    head_offsets = []
    for (n, i, r), nxt in zip(sequence, sequence[1:]):
        if _is_heading(r, size) and nxt[0] == n and nxt[1] == i:
            head_offsets.append(r['baseline']-(nxt[2]['bbox'].y0-pitch))
    head_baseline = statistics.median(head_offsets) if head_offsets else baseline
    bottom = max(r['bbox'].y0 for r in lines)
    for n in pages:
        for i, origin in enumerate(origins):
            col = [r for (m, j, r) in sequence if m == n and j == i]
            if not col:
                continue
            first = col[0]
            if _is_heading(first, size):
                nxt = next((r for r in col[1:]), None)
                top = (nxt['bbox'].y0-pitch) if nxt else first['bbox'].y0
            else:
                top = first['bbox'].y0
            frames.append({'page': n, 'column': i, 'x0': origin, 'top': top, 'bottom': bottom})
    entries = _parse_index(sequence, origins, size, sub, cont)
    styles = _index_styles(sequence, size)
    return {'kind': 'index', 'version': VERSION, 'pages': pages, 'entries': entries, 'frames': frames,
            'metrics': {'pitch': pitch, 'size': size, 'sub_indent': sub, 'cont_indent': cont, 'measure': measure,
                        'baseline': baseline, 'heading_baseline': head_baseline},
            'styles': styles, 'labels': labels, 'removal': removal,
            'locators': Counter(PROTECTED.findall(' '.join(r['text'] for r in lines)))}


def _index_styles(sequence, size):
    body, italic, heading = Counter(), Counter(), Counter()
    for _, _, r in sequence:
        for s in r['spans']:
            target = heading if _is_heading(r, size) else italic if s['style']['italic'] else body
            target[_style_key(s['style'])] += len(s['source'])
    pick = lambda c: json.loads(c.most_common(1)[0][0]) if c else None
    styles = {'body': pick(body), 'italic': pick(italic) or pick(body), 'heading': pick(heading)}
    if not styles['body'] or not styles['heading']:
        raise Blocked('Index: body or letter-heading style not found')
    return styles


def _parse_index(sequence, origins, size, sub, cont):
    entries, current, owner = [], None, None
    for n, i, r in sequence:
        if _is_heading(r, size):
            continue
        offset = r['x']-origins[i]
        level = 0 if offset < (sub/2) else 1 if offset < (sub+cont)/2 else 2
        if ONLY_LOCATORS.match(r['text']):
            level = 2  # a bare locator list always continues the line above
        runs = [(s['source'], 'italic' if s['style']['italic'] else 'body') for s in r['spans']]
        if level == 2:
            if owner is None:
                raise Blocked(f'Index: continuation line without an entry (page {n})')
            owner['runs'].append((' ', 'body'))
            owner['runs'] += runs
            owner['source_lines'].append({'page': n, 'text': r['text']})
            continue
        is_xref = runs[0][1] == 'italic' and re.match(r'\s*see\b', runs[0][0])
        if is_xref:
            if current is None:
                raise Blocked(f'Index: cross-reference before any heading (page {n})')
            owner = {'runs': list(runs), 'source_lines': [{'page': n, 'text': r['text']}]}
            current['xref_line'] = owner
            continue
        item = {'runs': list(runs), 'source_lines': [{'page': n, 'text': r['text']}], 'subs': []}
        if level == 0:
            current = item
            entries.append(item)
        else:
            if current is None:
                raise Blocked(f'Index: subheading before any heading (page {n})')
            current['subs'].append(item)
        owner = item
    return [_finish_entry(e) for e in entries]


def _split_runs(runs):
    """term+locators text, xref label, xref targets (all whitespace-normalized)."""
    plain, label, rest, seen = '', None, '', False
    for text, kind in runs:
        if kind == 'italic' and not seen and re.match(r'\s*see\b', text):
            seen, label = True, re.sub(r'\s+', ' ', text).strip()
            continue
        if seen:
            rest += text
        else:
            plain += text
    plain = re.sub(r'\s+', ' ', plain).strip()
    targets = [t.strip() for t in re.sub(r'\s+', ' ', rest).split(';') if t.strip()] if seen else []
    return plain, label, targets


def _term_locators(text):
    if ONLY_LOCATORS.match(text):
        raise Blocked('Index: entry without a term')
    m = LOCATORS.match(text)
    if m:
        return m['term'].rstrip(' ,'), m['loc'].strip()  # verbatim (line breaks joined by one space)
    return text.rstrip(' ,'), ''


def _finish_entry(item):
    plain, label, targets = _split_runs(item['runs'])
    term, locators = _term_locators(plain) if plain else ('', '')
    if not term:
        raise Blocked('Index: empty heading')
    out = {'term': term, 'locators': locators, 'subs': [], 'xref': None}
    if label:
        out['xref'] = {'label': label, 'targets': targets, 'inline': True}
    if item.get('xref_line'):
        _, xlabel, xtargets = _split_runs(item['xref_line']['runs'])
        if out['xref']:
            raise Blocked('Index: two cross-references for one heading')
        out['xref'] = {'label': xlabel, 'targets': xtargets, 'inline': False}
    for s in item['subs']:
        splain, slabel, stargets = _split_runs(s['runs'])
        sterm, sloc = _term_locators(splain)
        if slabel:
            raise Blocked('Index: cross-reference inside a subheading is not supported')
        out['subs'].append({'term': sterm, 'locators': sloc})
    return out


def _build_glossary(doc, pages):
    per_page, all_body = _measure_common(doc, pages)
    size = _body_size(all_body)
    origins = _columns(all_body)
    sequence, labels, removal, columns_all = [], {}, [], []
    for n in pages:
        page, rows, body = per_page[n]
        labels[n] = _page_labels(page, rows, {id(r) for r in body})
        removal += [{'page': n, 'source': s['source'], 'font': s['font'], 'bbox': s['bbox']}
                    for r in body for s in r['spans']]
        cols = [[] for _ in origins]
        for r in body:
            cols[_column_of(r['x'], origins)].append(r)
        for i, col in enumerate(cols):
            col.sort(key=lambda r: r['bbox'].y0)
            columns_all.append(col)
            sequence += [(n, i, r) for r in col]
    entries, current, gaps, last = [], None, [], None
    for n, i, r in sequence:
        spans = r['spans']
        starts = (len(spans) >= 2 and spans[0]['style']['bold'] and not spans[1]['style']['bold'])
        if starts:
            current = {'term': spans[0]['source'].strip(), 'definition': '', 'size': r['size'],
                       'term_style': spans[0]['style'], 'source_lines': []}
            entries.append(current)
            if last is not None and last[0] == n and last[1] == i:
                gaps.append(r['bbox'].y0-last[2]['bbox'].y0)
            spans = spans[1:]
        elif current is None:
            raise Blocked(f'Glossary: definition text before the first headword (page {n})')
        current['definition'] = (current['definition']+' '+''.join(s['source'] for s in spans)).strip()
        current['source_lines'].append({'page': n, 'text': r['text']})
        last = (n, i, r)
    for e in entries:
        e['definition'] = re.sub(r'\s+', ' ', e['definition']).strip()
    pitch = _pitch(columns_all)
    entry_gap = statistics.median([g-pitch for g in gaps if g > 1.5*pitch]) if gaps else pitch
    lines = [r for _, _, r in sequence]
    measure = max(r['bbox'].x1-origins[_column_of(r['x'], origins)] for r in lines)
    baseline = statistics.median(r['baseline']-r['bbox'].y0 for r in lines)
    bottom = max(r['bbox'].y0 for r in lines)
    frames = []
    for n in pages:
        for i, origin in enumerate(origins):
            col = [r for (m, j, r) in sequence if m == n and j == i]
            if col:
                frames.append({'page': n, 'column': i, 'x0': origin, 'top': col[0]['bbox'].y0, 'bottom': bottom})
    body = Counter(); term = Counter()
    for _, _, r in sequence:
        for k, s in enumerate(r['spans']):
            (term if s['style']['bold'] else body)[_style_key(s['style'])] += len(s['source'])
    styles = {'body': json.loads(body.most_common(1)[0][0]), 'term': json.loads(term.most_common(1)[0][0])}
    return {'kind': 'glossary', 'version': VERSION, 'pages': pages, 'entries': entries, 'frames': frames,
            'metrics': {'pitch': pitch, 'size': size, 'entry_gap': entry_gap, 'measure': measure, 'baseline': baseline},
            'styles': styles, 'labels': labels, 'removal': removal,
            'locators': Counter(PROTECTED.findall(' '.join(r['text'] for r in lines)))}


# ------------------------------------------------------------ translation
def _uid(kind, text, pages):
    return f'p{pages[0]:04d}_{kind}_'+hashlib.sha256(text.encode()).hexdigest()[:12]


def requests(structure):
    """Unique source strings to translate; locators are never included."""
    labels, blocks = {}, {}
    pages = structure['pages']
    def add(kind, text):
        if text and not PROTECTED.fullmatch(text):
            labels.setdefault(text, _uid(kind, text, pages))
    for n in pages:
        for label in structure['labels'].get(n, []):
            add('label', label['text'])
    if structure['kind'] == 'index':
        for e in structure['entries']:
            add('term', e['term'])
            for s in e['subs']:
                add('term', s['term'])
            if e['xref']:
                add('xref', e['xref']['label'])
                for t in e['xref']['targets']:
                    add('term', t)
    else:
        for e in structure['entries']:
            add('term', e['term'])
            blocks.setdefault(e['definition'], _uid('def', e['definition'], pages))
    return ({'labels': [{'id': i, 'source': s} for s, i in labels.items()],
             'blocks': [{'id': i, 'source': s} for s, i in blocks.items()]})


def identity(structure, source_sha256):
    return hashlib.sha256(json.dumps({'version': VERSION, 'source_sha256': source_sha256,
                                      'requests': requests(structure)}, sort_keys=True).encode()).hexdigest()


def _batches(rows, limit=6000, count=120):
    batch, size = [], 0
    for row in rows:
        if batch and (size+len(row['source']) > limit or len(batch) >= count):
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += len(row['source'])
    if batch:
        yield batch


def translate(structure, translator):
    """``translator(task, rows, context) -> {id: target}``; returns source->target."""
    req = requests(structure)
    answers = {}
    for task, key in (('translate_labels', 'labels'), ('translate_page', 'blocks')):
        for batch in _batches(req[key]):
            got = translator(task, batch, CONTEXT[structure['kind']])
            if set(got) != {r['id'] for r in batch}:
                raise Blocked('Back matter translation ids differ from the request')
            answers.update(got)
    return {r['source']: answers[r['id']] for key in ('labels', 'blocks') for r in req[key]}


def run_task_translator(runner, pages):
    """Production translator: every call goes through llm.run_task."""
    from . import llm
    def call(task, rows, context):
        key = 'labels' if task == 'translate_labels' else 'blocks'
        result = llm.run_task(task, {'_book': str(runner.book), '_config': str(runner.config_path),
                                     '_phase': 'production', '_pages': list(pages), 'context': context, key: rows})
        return {r['id']: r['translation'] for r in result.data[key]}
    return call


# ---------------------------------------------------------------- layout
def _wrap(runs, width, first_indent, next_indent, measure):
    """Greedy word wrap over styled runs; returns [(indent, [(text, kind)])]."""
    words = []
    for text, kind in runs:
        for token in re.findall(r'\S+\s*', text):
            words.append((token, kind))
    lines, current, indent = [], [], first_indent
    def length(items):
        return sum(width(t, k) for t, k in items)
    for word in words:
        trial = current+[word]
        if current and length([(t.rstrip(), k) if j == len(trial)-1 else (t, k) for j, (t, k) in enumerate(trial)]) > measure-indent:
            lines.append((indent, current))
            current, indent = [word], next_indent
        else:
            current = trial
    if current:
        lines.append((indent, current))
    for indent, items in lines:
        if length([(items[-1][0].rstrip(), items[-1][1])]) > measure-indent:
            raise Blocked('Back matter: a single word is wider than the column measure')
    return [(indent, _merge([(t, k) for t, k in items])) for indent, items in lines]


def _merge(items):
    out = []
    for text, kind in items:
        if out and out[-1][1] == kind:
            out[-1] = (out[-1][0]+text, kind)
        else:
            out.append((text, kind))
    if out:
        out[-1] = (out[-1][0].rstrip(), out[-1][1])
    return out


def _sorted_index(structure, answers):
    t = lambda s: answers.get(s, s)
    entries = []
    for e in structure['entries']:
        subs = sorted(({'term': t(s['term']), 'locators': s['locators'], 'source': s['term']} for s in e['subs']),
                      key=lambda s: sort_key(s['term']))
        xref = None
        if e['xref']:
            xref = {**e['xref'], 'label': t(e['xref']['label']),
                    'targets': sorted((t(x) for x in e['xref']['targets']), key=sort_key)}
        entries.append({'term': t(e['term']), 'source': e['term'], 'locators': e['locators'], 'subs': subs, 'xref': xref})
    entries.sort(key=lambda e: sort_key(e['term']))
    return entries


def _index_blocks(entries):
    """Logical items: ('heading', letter) and ('line', level, runs)."""
    items, letter = [], None
    for e in entries:
        first = heading_letter(e['term'])
        if first != letter:
            items.append(('heading', first, [(first, 'heading')]))
            letter = first
        runs = [(e['term']+(' '+e['locators'] if e['locators'] else ''), 'body')]
        if e['xref'] and e['xref']['inline']:
            runs += [(' ', 'body'), (e['xref']['label'], 'italic'), (' '+'; '.join(e['xref']['targets']), 'body')]
        items.append(('line', 0, runs))
        for s in e['subs']:
            items.append(('line', 1, [(s['term']+(' '+s['locators'] if s['locators'] else ''), 'body')]))
        if e['xref'] and not e['xref']['inline']:
            items.append(('line', 1, [(e['xref']['label'], 'italic'), (' '+'; '.join(e['xref']['targets']), 'body')]))
    return items


def _flow_index(structure, items, fonts, scale):
    m = structure['metrics']
    pitch, size = m['pitch']*scale, m['size']*scale
    width = lambda text, kind: fonts[kind]['font'].text_length(text, fontsize=size)
    placed, frames = [], structure['frames']
    f, slot = 0, 0
    def slots(frame):
        return int(math.floor((frame['bottom']-frame['top'])/pitch+1e-6))+1
    for index, item in enumerate(items):
        if item[0] == 'heading':
            need = 2  # heading slot + the first line that follows it
            while f < len(frames):
                blank = 0 if slot == 0 else 1
                if slot+blank+need <= slots(frames[f]):
                    break
                f, slot = f+1, 0
            if f >= len(frames):
                return None
            slot += 0 if slot == 0 else 1
            frame = frames[f]
            y = frame['top']+slot*pitch
            placed.append({'page': frame['page'], 'kind': 'heading', 'x': frame['x0'],
                           'baseline': y+(m['heading_baseline']-m['pitch'])*scale+pitch, 'runs': item[2],
                           'size': structure['styles']['heading']['size']*scale})
            slot += 1
            continue
        _, level, runs = item
        first = 0 if level == 0 else m['sub_indent']*scale
        lines = _wrap(runs, width, first, m['cont_indent']*scale, m['measure']*scale)
        while f < len(frames) and slot+len(lines) > slots(frames[f]):
            f, slot = f+1, 0
        if f >= len(frames):
            return None
        frame = frames[f]
        for indent, line_runs in lines:
            y = frame['top']+slot*pitch
            placed.append({'page': frame['page'], 'kind': 'line', 'x': frame['x0']+indent,
                           'baseline': y+m['baseline']*scale, 'runs': line_runs, 'size': size,
                           'right': frame['x0']+indent+sum(width(t, k) for t, k in line_runs)})
            slot += 1
    return placed


def _flow_glossary(structure, entries, fonts, scale):
    m = structure['metrics']
    placed, frames = [], structure['frames']
    f, y = 0, frames[0]['top'] if frames else 0
    first_in_frame = True
    for e in entries:
        esize = e['size']*scale
        pitch = m['pitch']*scale*e['size']/m['size']
        width = lambda text, kind: fonts[kind]['font'].text_length(text, fontsize=esize)
        lines = _wrap([(e['term_t']+' ', 'term'), (e['definition_t'], 'body')], width, 0, 0, m['measure']*scale)
        height = pitch*(len(lines)-1)
        while f < len(frames):
            gap = 0 if first_in_frame else m['entry_gap']*scale+pitch
            if y+gap+height <= frames[f]['bottom']+.01:
                y += gap
                break
            f += 1
            if f < len(frames):
                y, first_in_frame = frames[f]['top'], True
        if f >= len(frames):
            return None
        frame = frames[f]
        for k, (indent, line_runs) in enumerate(lines):
            top = y+k*pitch
            placed.append({'page': frame['page'], 'kind': 'line', 'x': frame['x0']+indent,
                           'baseline': top+m['baseline']*scale, 'runs': line_runs, 'size': esize,
                           'right': frame['x0']+indent+sum(width(t, kk) for t, kk in line_runs)})
        y += height
        first_in_frame = False
    return placed


def layout(structure, answers, fonts):
    """Uniform scale for the whole structure, from 1.00 down to 0.75."""
    if structure['kind'] == 'index':
        entries = _sorted_index(structure, answers)
        items = _index_blocks(entries)
        order = [e['term'] for e in entries]
    else:
        entries = sorted(({**e, 'term_t': answers.get(e['term'], e['term']),
                           'definition_t': answers.get(e['definition'], e['definition'])}
                          for e in structure['entries']), key=lambda e: sort_key(e['term_t']))
        order = [e['term_t'] for e in entries]
    for percent in range(100, int(MIN_SCALE*100)-1, -1):
        scale = percent/100
        placed = (_flow_index(structure, items, fonts, scale) if structure['kind'] == 'index'
                  else _flow_glossary(structure, entries, fonts, scale))
        if placed is not None:
            return {'scale': scale, 'placed': placed, 'order': order}
    raise Blocked(f'{structure["kind"].capitalize()} does not fit its {len(structure["pages"])} pages at scale >= {MIN_SCALE}')


# ------------------------------------------------------------ composition
def _fonts(doc, structure, answers, folder):
    from .fonts import select_group_fonts
    page = doc[structure['pages'][0]-1]
    texts = {}
    if structure['kind'] == 'index':
        for e in structure['entries']:
            texts.setdefault('body', []).append(answers.get(e['term'], e['term'])+' '+e['locators'])
            for s in e['subs']:
                texts['body'].append(answers.get(s['term'], s['term'])+' '+s['locators'])
            if e['xref']:
                texts.setdefault('italic', []).append(answers.get(e['xref']['label'], e['xref']['label']))
                texts['body'] += [answers.get(t, t)+';' for t in e['xref']['targets']]
        texts['heading'] = ['ABCDEFGHIJKLMNÑOPQRSTUVWXYZ#']
    else:
        for e in structure['entries']:
            texts.setdefault('term', []).append(answers.get(e['term'], e['term']))
            texts.setdefault('body', []).append(answers.get(e['definition'], e['definition']))
    units = [{'id': kind, 'scale_group': structure['kind'], 'style': structure['styles'][kind],
              'target': ' '.join(t), 'target_runs': [{'text': ' '.join(t), 'style': structure['styles'][kind]}]}
             for kind, t in texts.items()]
    mapping, audit = select_group_fonts(doc, page, units, Path(folder))
    chosen = {u['id']: mapping[(structure['kind'], tuple(sorted(u['style'].items())))] for u in units}
    return chosen, audit


def _label_layout(doc, n, label, target, folder):
    """Same baseline and size; grows toward free side, shrinks to >= 0.75."""
    from .fonts import select_group_fonts
    page = doc[n-1]
    span = label['spans'][0]
    style = span['style']
    unit = {'id': 'label', 'scale_group': f'label_{n}_{label["bbox"]}', 'style': style, 'target': target,
            'target_runs': [{'text': target, 'style': style}]}
    mapping, audit = select_group_fonts(doc, page, [unit], Path(folder))
    choice = mapping[(unit['scale_group'], tuple(sorted(style.items())))]
    box = fitz.Rect(label['bbox'])
    others = [fitz.Rect(s['bbox']) for b in page.get_text('dict')['blocks'] for l in b.get('lines', [])
              for s in l['spans'] if s['text'].strip() and not box.intersects(fitz.Rect(s['bbox']))
              and abs((s['bbox'][1]+s['bbox'][3])/2-(box.y0+box.y1)/2) < box.height]
    right_aligned = any(o.x0 >= box.x1-1 and o.x0-box.x1 < 3*style['size'] for o in others) and box.x0 > page.rect.width/2
    margin = 18
    if right_aligned:
        limit = box.x1-max([o.x1 for o in others if o.x1 <= box.x0]+[margin])
    else:
        limit = min([o.x0 for o in others if o.x0 >= box.x1]+[page.rect.width-margin])-box.x0
    for percent in range(100, int(MIN_SCALE*100)-1, -1):
        size = style['size']*percent/100
        w = choice['font'].text_length(target, fontsize=size)
        if w <= limit:
            x = box.x1-w if right_aligned else span['origin'][0]
            return {'page': n, 'kind': 'label', 'x': x, 'baseline': span['origin'][1], 'size': size,
                    'runs': [(target, 'label')], 'font': choice, 'color': style['color'], 'scale': percent/100,
                    'right': x+w}, audit
    raise Blocked(f'Back matter label does not fit at {MIN_SCALE}: page {n}')


def _page_without_text(doc, n, spans):
    """Single-page copy without the given native spans.

    Identity removal first (exact glyph operators); if the content stream is
    not supported by it, a fresh copy is redacted by span rectangles with
    images and vector art explicitly preserved. Retained text is verified.
    """
    from .native_redaction import remove_source_spans
    units = [{'id': 'back_matter', 'spans': [{'source': s['source'], 'font': s['font'], 'bbox': s['bbox']}
                                             for s in spans]}]
    one = fitz.open()
    one.insert_pdf(doc, from_page=n-1, to_page=n-1)
    try:
        return one, remove_source_spans(one[0], units)
    except Exception as exc:  # MuPDF wraps callback errors in FzErrorGeneric
        reason = f'{type(exc).__name__}: {exc}'.splitlines()[0][:160]
        one.close()
    one = fitz.open()
    one.insert_pdf(doc, from_page=n-1, to_page=n-1)
    page = one[0]
    keep = Counter((s['text'], s['font']) for b in doc[n-1].get_text('dict')['blocks'] for l in b.get('lines', [])
                   for s in l['spans'] if s['text'].strip())
    keep -= Counter((s['source'], s['font']) for s in spans)
    for s in spans:
        page.add_redact_annot(fitz.Rect(s['bbox']), fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    after = Counter((s['text'], s['font']) for b in page.get_text('dict')['blocks'] for l in b.get('lines', [])
                    for s in l['spans'] if s['text'].strip())
    if after != keep:
        raise Blocked('Back matter redaction changed text outside the edited spans')
    return one, {'method': 'text_redaction', 'removed_spans': len(spans), 'identity_removal_failed': reason}


def _draw(page, placed, fonts, styles, registered):
    for item in placed:
        x = item['x']
        for text, kind in item['runs']:
            choice = item.get('font') or fonts[kind]
            name = 'BM'+choice['sha256'][:16]
            if name not in registered:
                page.insert_font(fontname=name, fontfile=choice['path'])
                registered.add(name)
            color = item.get('color', styles.get(kind, {}).get('color', 0))
            rgb = tuple(((color >> s) & 255)/255 for s in (16, 8, 0))
            page.insert_text(fitz.Point(x, item['baseline']), text, fontname=name, fontsize=item['size'], color=rgb)
            x += choice['font'].text_length(text, fontsize=item['size'])


def compose(source_pdf, structure, answers, folder, out_paths):
    """Write one single-page candidate per structure page; return the report."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with fitz.open(source_pdf) as doc:
        fonts, audit = _fonts(doc, structure, answers, folder/'fonts')
        flow = layout(structure, answers, fonts)
        labels, label_audit = [], []
        for n in structure['pages']:
            for label in structure['labels'].get(n, []):
                placed, a = _label_layout(doc, n, label, answers.get(label['text'], label['text']), folder/'fonts')
                labels.append(placed)
                label_audit += a
        styles = dict(structure['styles'])
        pages = {}
        for n in structure['pages']:
            spans = [s for s in structure['removal'] if s['page'] == n]
            spans += [s for label in structure['labels'].get(n, []) for s in label['spans']]
            one, removal = _page_without_text(doc, n, spans)
            page = one[0]
            registered = set()
            mine = [p for p in flow['placed'] if p['page'] == n]
            _draw(page, mine, fonts, styles, registered)
            _draw(page, [p for p in labels if p['page'] == n], fonts, styles, registered)
            out = Path(out_paths[n])
            out.parent.mkdir(parents=True, exist_ok=True)
            from .core import _save_pdf
            _save_pdf(one, out)
            one.close()
            pages[n] = {'output': str(out), 'output_sha256': sha256(out), 'removal': removal,
                        'lines': len(mine), 'labels': [p['runs'][0][0] for p in labels if p['page'] == n]}
    return {'kind': structure['kind'], 'pages': pages, 'scale': flow['scale'],
            'label_scales': [p['scale'] for p in labels], 'order': flow['order'],
            'font_audit': audit+label_audit,
            'trusted_font_files': sorted({f['path'] for f in fonts.values()} | {p['font']['path'] for p in labels})}


# -------------------------------------------------------------------- QA
def local_qa(source_pdf, structure, report, extras=(), allowed=()):
    """Locators (multiset over the structure), folios, scale, outside-frame ink."""
    import numpy as np
    from .language import residual_english, qa_texts
    findings = {}
    after_tokens = Counter()
    with fitz.open(source_pdf) as doc:
        for n in structure['pages']:
            src = doc[n-1]
            with fitz.open(report['pages'][n]['output']) as cand:
                dst = cand[0]
                body_top = src.rect.height*HEADER_BAND
                # Letter headings (regenerated, larger than body) are not locators.
                after_tokens += Counter(PROTECTED.findall(' '.join(
                    s['text'] for b in dst.get_text('dict')['blocks'] for l in b.get('lines', [])
                    for s in l['spans'] if s['bbox'][1] >= body_top and s['size'] < 20
                    and s['size'] <= structure['metrics']['size']*1.2)))
                folio = lambda p: Counter(PROTECTED.findall(' '.join(
                    s['text'] for b in p.get_text('dict')['blocks'] for l in b.get('lines', [])
                    for s in l['spans'] if s['bbox'][3] < body_top)))
                editable = [fitz.Rect(f['x0']-2, f['top']-structure['metrics']['pitch'], f['x0']+structure['metrics']['measure']+6, f['bottom']+structure['metrics']['pitch']+2)
                            for f in structure['frames'] if f['page'] == n]
                for label in structure['labels'].get(n, []):
                    box = fitz.Rect(label['bbox'])
                    pad = .35*box.height  # accents above capitals
                    editable.append(fitz.Rect(18, box.y0-pad, src.rect.width-18, box.y1+2))
                a = src.get_pixmap(dpi=110); b = dst.get_pixmap(dpi=110)
                A = np.frombuffer(a.samples, np.uint8).reshape(a.height, a.width, a.n)[:, :, :3]
                B = np.frombuffer(b.samples, np.uint8).reshape(b.height, b.width, b.n)[:, :, :3]
                mask = np.ones(A.shape[:2], bool)
                k = 110/72
                for r in editable:
                    mask[max(0, int(r.y0*k)):int(math.ceil(r.y1*k)), max(0, int(r.x0*k)):int(math.ceil(r.x1*k))] = False
                delta = np.any(A != B, axis=2)
                diff = float(delta[mask].mean()) if mask.any() else 0.
                residual = residual_english(qa_texts(dst, extras), allowed)
                overflow = [s['text'] for blk in dst.get_text('dict')['blocks'] for l in blk.get('lines', [])
                            for s in l['spans'] if s['text'].strip() and not dst.rect.contains(fitz.Rect(s['bbox']))]
                findings[n] = {'folios_equal': folio(src) == folio(dst), 'non_text_pixel_diff': diff,
                               'residual_english': residual, 'overflow': overflow}
    locators_equal = after_tokens == Counter(structure['locators'])
    scale = min([report['scale']]+report['label_scales'])
    for n, f in findings.items():
        f.update(locators_equal=locators_equal, min_scale=scale,
                 local_ok=bool(locators_equal and f['folios_equal'] and f['non_text_pixel_diff'] <= .01
                               and not f['residual_english'] and not f['overflow'] and scale >= MIN_SCALE))
    return findings


# ------------------------------------------------------------- production
def production_role(runner, n):
    with fitz.open(runner.book/'source.pdf') as doc:
        return page_role(doc[n-1])


def _saved_answers(runner, structure, translator):
    path = runner.folder/f'back_matter-{structure["kind"]}-p{structure["pages"][0]:04d}.json'
    deps = {name: sha256(p) for name, p in (('glossary', runner.glossary), ('style', runner.style),
                                              ('protected', runner.protected), ('config', runner.config_path))}
    ident = identity(structure, runner.source_hash)
    saved = json.loads(path.read_text()) if path.is_file() else {}
    if saved.get('identity') == ident and saved.get('dependencies') == deps:
        return saved['answers'], path
    answers = translate(structure, translator)
    atomic_json(path, {'identity': ident, 'dependencies': deps, 'answers': answers, 'version': VERSION,
                       'policy': 'es-ES order, articles ignored, locators verbatim'})
    return answers, path


def produce_back_matter(runner, pages, dependency, translator=None):
    """Translate (run_task), compose, QA and finish each selected page.

    Returns [(page, exception)] for the caller's failure accounting.
    """
    from . import llm
    failures = []
    with fitz.open(runner.book/'source.pdf') as doc:
        structures = [s for s in analyze(doc) if set(s['pages']) & set(pages)]
    for structure in structures:
        mine = [n for n in structure['pages'] if n in pages]
        try:
            answers, saved = _saved_answers(runner, structure, translator or run_task_translator(runner, structure['pages']))
            for n in mine:
                runner.checkpoint(n, dependency, stage='translated')
            outputs = {n: runner.path(n, 'translated.pdf') for n in structure['pages']}
            report = compose(runner.book/'source.pdf', structure, answers, runner.folder, outputs)
            allowed = []
            import csv
            with runner.glossary.open() as stream:
                allowed = [r['target'] for r in csv.DictReader(stream)]
            findings = local_qa(runner.book/'source.pdf', structure, report, runner.extras, list(runner.extras)+allowed)
        except llm.QuotaPaused:
            raise
        except Exception as exc:
            failures += [(n, exc) for n in mine]
            continue
        for n in mine:
            page = report['pages'][n]
            fit = {'page': n, 'source_sha256': runner.source_hash, 'output': page['output'], 'output_page': 0,
                   'output_sha256': page['output_sha256'], 'fit_complete': True, 'phase': 'production',
                   'back_matter': structure['kind'], 'blocks': [], 'group_scales': {structure['kind']: report['scale']},
                   'extra_trusted_font_files': report['trusted_font_files'], 'answers': str(saved)}
            atomic_json(runner.path(n, 'fit.json'), fit)
            runner.checkpoint(n, dependency, stage='composed')
            f = findings[n]
            qa = {'page': n, 'source_sha256': runner.source_hash, 'candidate_sha256': page['output_sha256'],
                  'back_matter': structure['kind'], 'policy': CONTEXT[structure['kind']][:60],
                  'local_fidelity_ok': f['local_ok'], 'native_local_fidelity_ok': f['local_ok'],
                  'min_scale': f['min_scale'], 'non_text_pixel_diff': f['non_text_pixel_diff'],
                  'residual_english': f['residual_english'], 'locators_equal': f['locators_equal'],
                  'folios_equal': f['folios_equal'], 'overflow': f['overflow'], 'order': report['order'],
                  'text_quality_issues': []}
            try:
                from . import safe_vlm
                pair = safe_vlm.build_safe_pair(runner.book, runner.book/'source.pdf', page['output'], n, 1,
                                                protected_terms=runner.extras,
                                                trusted_text_font_files=report['trusted_font_files'])
                judge = safe_vlm.judge_safe_pair(runner.book, pair, runner.config_path, phase='production')
                qa.update(vlm=judge, vlm_complete=True, vlm_notes=judge['native_vlm'].get('notes', ''))
            except llm.QuotaPaused:
                raise
            except Blocked as exc:
                qa.update(vlm_complete=False, vlm_notes=str(exc))
            atomic_json(runner.path(n, 'qa.json'), qa)
            runner.checkpoint(n, dependency, stage='local_qa')
            runner.finish_qa(n, fit, qa, [], dependency)
    return failures


# ----------------------------------------------------------------- pseudo
def pseudo_translator(expansion=1.25, extras=(), glossary=()):
    from .pseudo import validated
    def call(task, rows, context):
        return {r['id']: validated(r['source'], expansion=expansion, extras=extras, glossary=glossary)[0] for r in rows}
    return call


_PSEUDO_CACHE = {}


def pseudo_row(source_pdf, n, folder, expansion=1.25, extras=(), glossary=()):
    """compose-offline hook: None when page n is not back matter."""
    with fitz.open(source_pdf) as doc:
        if not page_role(doc[n-1]):
            return None
        structure = structure_for(doc, n)
    key = (str(source_pdf), str(folder), expansion, tuple(structure['pages']))
    if key not in _PSEUDO_CACHE:
        answers = translate(structure, pseudo_translator(expansion, extras, glossary))
        outputs = {m: Path(folder)/f'p{m:04d}-translated.pdf' for m in structure['pages']}
        report = compose(source_pdf, structure, answers, folder, outputs)
        findings = local_qa(source_pdf, structure, report, extras)
        _PSEUDO_CACHE[key] = (report, findings)
    report, findings = _PSEUDO_CACHE[key]
    f = findings[n]
    mechanical = not (f['locators_equal'] and f['folios_equal'] and not f['overflow'] and f['non_text_pixel_diff'] <= .01)
    return {'page': n, 'llm_calls': 0, 'back_matter': structure['kind'], 'candidate': report['pages'][n]['output'],
            'min_scale': f['min_scale'], 'local_fidelity_ok': f['local_ok'], 'locators_equal': f['locators_equal'],
            'non_text_pixel_diff': f['non_text_pixel_diff'], 'page_overflow': f['overflow'], 'new_text_overlaps': [],
            'status': 'review' if mechanical or f['min_scale'] < .8 else 'composed'}
