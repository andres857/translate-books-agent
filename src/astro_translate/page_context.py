"""Page-level translation context for sentence-memory requests (auditor batch-16).

No inference here. The plan deduplicates clauses across pages, so a figure
caption reached the model without the body it names (p47: «The Beehive» →
«El Pesebre» while the body says «la Colmena»), and a stacked title split over
two units never met its glossary phrase (p176: THE WHEEL OF / RULERSHIP →
«LA RUEDA DE LA REGENCIA», approved «rueda de las regencias»). This module

* orders requests body-first and packs whole pages per call;
* attaches to caption/label requests the already translated body clauses of
  the same page or spread that share a proper name (``term_context``);
* attaches to two stacked lines whose glossary phrase crosses the line break a
  joint constraint (``line_continuation`` / ``_joined_glossary``).

Validation of both lives in ``llm._validate``; QA in ``text_quality``.
"""
from __future__ import annotations

import re

from .language import glossary_matches

MAX_CONTEXT_PAIRS = 6
MAX_CONTEXT_CHARS = 1800


def is_body(unit):
    return unit.get('kind') == 'paragraph' and unit.get('context', 'running_text') == 'running_text'


def facing(number):
    """Facing leaf (1-based), the parity used by editorial_margins."""
    return number+1 if (number-1) % 2 else number-1


def request_page(request):
    match = re.match(r'^p(\d+)_', request['id'])
    return int(match.group(1)) if match else 0


def occurrences_by_request(plan):
    entries = {key: entry['id'] for key, entry in plan['entries'].items()}
    result = {}
    for item in plan['units']:
        for piece in item['pieces']:
            if 'occurrence' not in piece:
                continue
            occurrence = plan['occurrences'][piece['occurrence']]
            result.setdefault(entries[occurrence['key']], []).append((item['unit'], occurrence))
    return result


def dependent_ids(plan):
    """Requests whose every occurrence is a caption, label or callout."""
    found = occurrences_by_request(plan)
    return {rid for rid, rows in found.items() if rows and not any(is_body(unit) for unit, _ in rows)}


def page_groups(requests, limit):
    """Pack whole pages per call; only a page larger than ``limit`` is split."""
    pages = {}
    for request in requests:
        pages.setdefault(request_page(request), []).append(request)
    groups, current, size = [], [], 0
    for rows in pages.values():
        length = sum(len(r['source']) for r in rows)
        if current and size+length > limit:
            groups.append(current); current, size = [], 0
        if length > limit:
            for row in rows:
                if current and size+len(row['source']) > limit:
                    groups.append(current); current, size = [], 0
                current.append(row); size += len(row['source'])
            continue
        current.extend(rows); size += length
    if current:
        groups.append(current)
    return groups


def _names(text):
    from .text_quality import _name_runs, SENTENCE_BREAK, STYLE
    text = STYLE.sub('', text)
    return {name for sentence in SENTENCE_BREAK.split(text) for name in _name_runs(sentence, initial=True)}


def term_context(plan, request_id, plain_answers, saved_units=()):
    """Body clauses (source, translated target) of the same page/spread naming the same terms.

    ``plain_answers`` maps request id → decoded plain target. ``saved_units``
    are body units of facing pages translated in an earlier run.
    """
    found = occurrences_by_request(plan)
    rows = found.get(request_id, [])
    if not rows:
        return []
    names = _names(rows[0][1]['source'])
    if not names:
        return []
    pages = {unit['page'] for unit, _ in rows}
    pages |= {facing(p) for p in pages}
    candidates = []
    for rid, items in found.items():
        if rid == request_id or rid not in plain_answers:
            continue
        for unit, occurrence in items:
            if unit['page'] in pages and is_body(unit):
                candidates.append((occurrence['source'], plain_answers[rid]))
    for unit in saved_units:
        if unit.get('page') in pages and is_body(unit) and unit.get('target'):
            candidates.append((unit['source'], unit['target']))
    result, seen, size = [], set(), 0
    for source, target in candidates:
        if (source, target) in seen or not names & _names(source):
            continue
        if len(result) >= MAX_CONTEXT_PAIRS or size+len(source)+len(target) > MAX_CONTEXT_CHARS:
            break
        seen.add((source, target)); size += len(source)+len(target)
        result.append({'source': source, 'target': target})
    return result


def stacked_glossary_pairs(units, plan, glossary):
    """Consecutive stacked lines of one page whose approved phrase crosses the break.

    Returns (upper request id, lower request id, glossary row) for the clause
    that ends the upper line and the clause that opens the lower one.
    """
    pieces = {item['unit']['id']: [p for p in item['pieces'] if 'occurrence' in p] for item in plan['units']}
    entries = {key: entry['id'] for key, entry in plan['entries'].items()}
    result = []
    for upper, lower in zip(units, units[1:]):
        if upper.get('page') != lower.get('page') or not pieces.get(upper['id']) or not pieces.get(lower['id']):
            continue
        a, b = upper.get('bbox'), lower.get('bbox')
        size = upper.get('size') or upper.get('style', {}).get('size')
        if not a or not b or not size or abs(size-(lower.get('size') or lower.get('style', {}).get('size') or 0)) > .1:
            continue
        if not (-1 <= b[1]-a[3] <= size*.6 and min(a[2], b[2]) > max(a[0], b[0])):
            continue
        if re.search(r'[.!?;:][”’"]?$', upper['source'].rstrip()):
            continue
        joined = upper['source'].rstrip()+' '+lower['source'].lstrip()
        boundary = len(upper['source'].rstrip())
        for row in glossary_matches(joined, glossary):
            pattern = r'(?<!\w)'+re.sub(r'\\ ', r'[\\s\\-‐‑]+', re.escape(row['source']))+r'(?!\w)'
            if any(m.start() < boundary < m.end() for m in re.finditer(pattern, joined, re.I)):
                last = plan['occurrences'][pieces[upper['id']][-1]['occurrence']]
                first = plan['occurrences'][pieces[lower['id']][0]['occurrence']]
                result.append((entries[last['key']], entries[first['key']], row))
    return result


def joined_glossary_ok(upper_target, lower_target, row):
    from .glossary_forms import glossary_form_present
    text = re.sub(r'\{v\d+\}', '', upper_target.rstrip()+' '+lower_target.lstrip())
    return glossary_form_present(text, row['target'])


def annotate(request, *, context=(), joined=()):
    """Copy of a wire request with its context and joint constraints."""
    row = dict(request)
    if context:
        row['term_context'] = list(context)
    if joined:
        row['line_continuation'] = [{'side': j['side'], 'line_source': j['line_source'],
                                     'glossary': {'source': j['source'], 'target': j['target']}} for j in joined]
        row['_joined_glossary'] = [{'partner': j['partner'], 'side': j['side'], 'target': j['target'],
                                    'source': j['source']} for j in joined]
    return row


# ------------------------------------------------ story continuations (auditor batch-21)
#
# p14 ends «…heavenly bodies, and Egyptian» and p15 opens «symbolism.»: two
# fragments of one sentence, translated apart («y las egipcias | simbolismo.»).
# A paragraph that does not end its sentence and the next paragraph of the same
# body size, in reading order, that opens in lower case are one sentence. They
# are sent as one request, split by a style marker at the column/page break.

COLUMN_TOLERANCE = 12.0
OPEN_END = re.compile(r'[.!?:;…][”’"»)\]]*$')
STORY_BREAK_INSTRUCTIONS = (
    'One sentence continues across a column or page break. The story_break marker is not a style '
    'change: it marks where the printed text moves to the next column or page. Translate the whole '
    'sentence as one natural Spanish sentence and keep that marker exactly once, at a word boundary '
    'with a space next to it: the text before it ends the first fragment, the text after it opens the '
    'second. Each fragment keeps at least one word. Words may cross the marker when Spanish word order '
    'requires it, but both fragments joined with a space must read as one grammatical sentence.')


def open_ended(unit):
    """The unit's text stops before its sentence ends (no terminal punctuation)."""
    text = unit.get('source', '').rstrip()
    return bool(text) and not OPEN_END.search(text)


def opens_lowercase(unit):
    text = unit.get('source', '').lstrip()
    return bool(text) and text[0].isalpha() and text[0].islower()


def _size_at(unit, index):
    for run in unit.get('style_runs', []):
        if run['start'] <= index < run['end']:
            return run['style'].get('size')
    return unit.get('size') or unit.get('style', {}).get('size')


def _unit_size(unit):
    """Dominant size by characters: a subhead merged into a paragraph keeps the body size."""
    sizes = {}
    for run in unit.get('style_runs', []):
        size = run['style'].get('size')
        if size:
            sizes[round(size, 1)] = sizes.get(round(size, 1), 0)+run['end']-run['start']
    if sizes:
        return max(sizes.items(), key=lambda item: (item[1], -item[0]))[0]
    return unit.get('size') or unit.get('style', {}).get('size')


def reading_order(units):
    """Units of one page by column (x0 within COLUMN_TOLERANCE), then top to bottom."""
    columns = []
    for unit in sorted(units, key=lambda u: (u['bbox'][0], u['bbox'][1])):
        if columns and unit['bbox'][0]-columns[-1][0] <= COLUMN_TOLERANCE:
            columns[-1][1].append(unit)
        else:
            columns.append((unit['bbox'][0], [unit]))
    return [u for _, column in columns for u in sorted(column, key=lambda u: (u['bbox'][1], u['bbox'][0]))]


def _stacked(upper, lower):
    """``lower`` is the next line under ``upper`` (same page and size, overlapping x)."""
    a, b = upper.get('bbox'), lower.get('bbox')
    size, other = _unit_size(upper), _unit_size(lower)
    if upper.get('page') != lower.get('page') or not a or not b or not size or not other or abs(size-other) > .1:
        return False
    return -1 <= b[1]-a[3] <= size*.6 and min(a[2], b[2]) > max(a[0], b[0])


REFERENCE = re.compile(r'(?<![\w.])\d+(?:[–\-/]\d+)*(?![\w])')


def _references(unit, index):
    """Three or more numbers in the joining sentence: an index line."""
    from .consistency import _ranges
    ranges = list(_ranges(unit['source']))
    start, end = ranges[index]
    return len(REFERENCE.findall(unit['source'][start:end])) >= 3


def story_continuations(units):
    """(tail id, head id, 'column'|'page') for sentences split between body paragraphs.

    Same page: consecutive paragraphs of one body size in reading order. Next
    page: the last paragraph of that size on page N and the first on page N+1.
    The tail must be open-ended and the head must open in lower case, at the
    same size at the break (a subhead closing the tail never joins). A line set
    directly under the tail, or over the head, other than its partner, means
    the text continues there instead (p175/p176 display quotes): no join.
    Locally kept units (dates, preserved works, quotations) never join but
    still count as neighbouring lines.
    """
    by_page, lines = {}, {}
    for unit in units:
        if not unit.get('bbox') or unit.get('page') is None:
            continue
        lines.setdefault(unit['page'], []).append(unit)
        if is_body(unit) and not unit.get('translation_method') and unit.get('source', '').strip():
            by_page.setdefault(unit['page'], []).append(unit)
    sequences = {}
    for page, rows in by_page.items():
        for unit in reading_order(rows):
            size = _unit_size(unit)
            if size:
                sequences.setdefault((page, round(size, 1)), []).append(unit)
    pairs, tails, heads = [], set(), set()

    def consider(tail, head, boundary):
        if tail['id'] in tails or head['id'] in heads or tail['id'] == head['id']:
            return
        if not (open_ended(tail) and opens_lowercase(head)):
            return
        end = len(tail['source'].rstrip())-1
        start = len(head['source'])-len(head['source'].lstrip())
        a, b = _size_at(tail, end), _size_at(head, start)
        if not a or not b or abs(a-b) > .1:
            return
        if _references(tail, -1) or _references(head, 0):
            return  # index entries: page lists, not a sentence
        if any(other is not head and _stacked(tail, other) for other in lines[tail['page']]):
            return
        if any(other is not tail and _stacked(other, head) for other in lines[head['page']]):
            return
        tails.add(tail['id']); heads.add(head['id'])
        pairs.append((tail['id'], head['id'], boundary))
    for (page, size), rows in sorted(sequences.items()):
        for tail, head in zip(rows, rows[1:]):
            consider(tail, head, 'column')
        following = sequences.get((page+1, size))
        if following:
            consider(rows[-1], following[0], 'page')
    return pairs


def _stacked_below(units):
    """Ids of lines set directly under an open-ended line of the same page and size."""
    pages = {}
    for unit in units:
        pages.setdefault(unit.get('page'), []).append(unit)
    return {lower['id'] for rows in pages.values() for upper in rows if open_ended(upper)
            for lower in rows if upper is not lower and _stacked(upper, lower)}


def initial_upper_units(units):
    """Running-text headings/titles whose source opens with a capital (audit batch-21, p21).

    «Earth-centred» → «geocéntrico»: a subhead must open with a capital as in
    the source. Only running-text labels at least as large as the page's body
    text count. Paragraphs (a continuation may open in lower case), all-caps
    titles (``source_case`` owns them), lines continuing a stacked line above,
    locally kept units and pages without body text are excluded.
    """
    below = _stacked_below(units)
    body = {}
    for unit in units:
        if is_body(unit):
            for run in unit.get('style_runs', []):
                key = (unit.get('page'), round(run['style'].get('size') or 0, 1))
                body[key] = body.get(key, 0)+run['end']-run['start']
    body_size = {}
    for (page, size), count in body.items():
        if size and count > body.get((page, body_size.get(page)), 0):
            body_size[page] = size
    result = set()
    for unit in units:
        source = unit.get('source', '').strip()
        if (unit.get('kind') != 'label' or unit.get('context', 'running_text') != 'running_text'
                or unit['id'] in below or unit.get('translation_method') or unit.get('native_ordinal')):
            continue
        # A subhead is set at least at the page's body size; smaller labels
        # (diagram legends, epithets under a name) are not judged.
        size = _unit_size(unit)
        if unit.get('page') not in body_size or not size or size < body_size[unit.get('page')]-.1:
            continue
        if not source or not source[0].isupper() or not any(c.islower() for c in source):
            continue
        if re.search(r'[.!?]\s+\S', source):
            continue
        result.add(unit['id'])
    return result


def neighbour_fragment(unit, side):
    """A context-only copy of a unit of a non-selected page: only the fragment
    that joins the selected page (its last sentence for a tail, its first for a
    head), with style runs rebased. It is never written as a page translation."""
    from .consistency import _ranges
    ranges = list(_ranges(unit['source']))
    start, end = ranges[-1] if side == 'tail' else ranges[0]
    runs = []
    for run in unit.get('style_runs', []):
        a, b = max(start, run['start']), min(end, run['end'])
        if a < b:
            runs.append({**run, 'start': a-start, 'end': b-start})
    copy = {**unit, 'source': unit['source'][start:end], 'style_runs': runs, 'spans': [],
            'story_context_only': True}
    if runs:
        copy['style'] = runs[0]['style']
    return copy


def _saved_units(folder, number):
    import json
    path = folder/f'p{number:04d}-translation.json'
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('blocks'), list):
        return None
    return [{**u, 'page': u.get('page', number)} for u in data['blocks']]


def cross_page_continuity(folder, number):
    """QA of sentences continued from/to the facing page: (blocking, warnings).

    Uses the saved translations of N-1, N and N+1 (a neighbour not yet
    translated is not evidence). Blocking ``cross_page_sentence_not_joint``:
    this page's fragment was translated apart from its other half (retranslate
    this page). Warning ``cross_page_partner_stale``: this page holds the joint
    translation but the neighbour's saved fragment predates it (retranslate the
    neighbour).
    """
    from pathlib import Path
    folder = Path(folder)
    pages = {n: _saved_units(folder, n) for n in (number-1, number, number+1) if n >= 1}
    if not pages.get(number):
        return [], []
    units = [u for rows in pages.values() if rows for u in rows]
    byid = {u['id']: u for u in units}
    blocking, warnings = [], []
    for tail, head, boundary in story_continuations(units):
        if boundary != 'page' or number not in (byid[tail]['page'], byid[head]['page']):
            continue
        own, other = (byid[tail], byid[head]) if byid[tail]['page'] == number else (byid[head], byid[tail])
        marks = [c for c in own.get('story_continuation', []) if c.get('partner') == other['id']]
        evidence = dict(partner=other['id'], partner_page=other['page'], boundary='page')
        if not marks:
            blocking.append(dict(unit_id=own['id'], code='cross_page_sentence_not_joint', action='retranslate', **evidence))
            continue
        key = marks[0].get('sentence_key')
        if not any(c.get('partner') == own['id'] and c.get('sentence_key') == key
                   for c in other.get('story_continuation', [])):
            warnings.append(dict(unit_id=own['id'], code='cross_page_partner_stale', action='retranslate_partner',
                                 severity='minor', **evidence))
    return blocking, warnings
