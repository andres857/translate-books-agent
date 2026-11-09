"""Offline pseudo-translation of the whole book through the real production compositor.

``compose_offline`` replaces every LLM answer by a deterministic pseudo-Spanish
string, then runs the same composition path as ``Production.compose_and_qa``
(``reinsert_fidelity`` + vertical/rotated labels + single-page extraction +
``fidelity_qa`` + ``page_issues``) minus the VLM judge. Nothing is translated:
pseudo words are generated from hashes, approved glossary target forms are
inserted verbatim where the glossary matches (so glossary-bound layout rules
behave as in production), dates use the local deterministic converter, and
protected values are masked/restored exactly as on the LLM wire.

Isolation: all writes go to ``work/pseudo/`` (a snapshot book whose source is
a symlink). The manifest, LLM cache, costs, review queue and production
candidates of the real book are never written. ``llm.run_task`` is never called.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import time

import pymupdf as fitz

from .core import Blocked, PROTECTED, atomic_json, read_extras, sha256

PSEUDO_VERSION = 'pseudo-es-v1'
VOWELS = 'aeiou'
ACCENTED = {'a': 'á', 'e': 'é', 'i': 'í', 'o': 'ó', 'u': 'ú'}
CONSONANTS = 'bcdfglmnprstvz'
FILLERS = ('ñandú', 'acción', 'canción', 'región', 'según', 'también', 'corazón', 'ilusión', 'órbita',
           'pequeña', 'mañana', '¿qué?', 'búho', 'país', 'jamás', '¡sí!', 'azúcar', 'lágrima',
           'música', 'último')
WORD = re.compile(r'[^\W\d_]+')
MARKER = re.compile(r'\{[nv]\d+\}')
# Codes that judge wording, not layout: meaningless (or expected) on pseudo text.
LEXICAL_CODES = frozenset({'repeated_ngram', 'uppercase_accent', 'zodiac_spelling', 'asterism_agreement',
                           'redundant_clitic', 'orphan_abbreviation', 'residual_english', 'glossary_missing',
                           'numeric_order', 'term_inconsistency', 'english_ordinal_suffix'})
# Verified, expected substitutions: reported apart from layout defects.
INFORMATIVE_CODES = frozenset({'font_fallback_applied'})
QA_FLAGS = ('numbers_equal', 'glyph_set_equal', 'immutable_spans_equal', 'images_equal', 'vectors_equal',
            'font_files_embedded_verified', 'complete_translated_text', 'uniform_scale_groups',
            'original_colors_verified')
ROOT = Path(__file__).resolve().parents[2]


def _hash(*parts):
    return hashlib.sha256('\x1f'.join(map(str, parts)).encode()).digest()


def _case(word, model):
    if len(model) > 1 and model.isupper():
        return word.upper()
    if model[:1].isupper():
        return word[:1].upper()+word[1:]
    return word


def _pseudo_word(word, salt):
    """Same length, same case pattern, one accent; stable per (word, salt)."""
    key = word.casefold()
    digest = _hash(PSEUDO_VERSION, salt, key)
    chars = []
    for i, c in enumerate(key):
        b = digest[i % len(digest)]
        if c in 'aeiouáéíóúü':
            chars.append(VOWELS[b % 5])
        elif c.isalpha():
            chars.append('ñ' if b % 23 == 0 and i else CONSONANTS[b % len(CONSONANTS)])
        else:
            chars.append(c)
    vowels = [i for i, c in enumerate(chars) if c in VOWELS]
    if vowels and (len(chars) >= 3 or digest[-1] % 2):
        i = vowels[digest[-2] % len(vowels)]
        chars[i] = ACCENTED[chars[i]]
    elif not vowels and len(chars) >= 2:
        chars[-1] = 'á'
    return _case(''.join(chars), word)


def _glossary_spans(text, glossary, blocked):
    spans = []
    for row in sorted(glossary, key=lambda r: -len(r.get('source', ''))):
        source, target = row.get('source', ''), row.get('target', '')
        if not source.strip() or not target.strip():
            continue
        pattern = re.sub(r'\\ ', r'[\\s\\-‐‑]+', re.escape(source))
        for m in re.finditer(r'(?<!\w)'+pattern+r'(?!\w)', text, re.I):
            if any(m.start() < b and m.end() > a for a, b, _ in spans+blocked):
                continue
            spans.append((m.start(), m.end(), _case(target, m.group()) if m.group().isupper() or
                          m.group()[:1].isupper() else target))
    return spans


def _openers(text):
    from .text_quality import opening_punctuation_issues
    for _ in range(40):
        issues = opening_punctuation_issues(text, 'pseudo')
        if not issues:
            return text
        issue = issues[0]
        index = issue['start']
        while index < len(text):
            m = MARKER.match(text, index)
            if m:
                index = m.end()
            elif text[index].isspace() or not (text[index].isalnum() or text[index] in '“"‘\''):
                index += 1
            else:
                break
        text = text[:index]+issue['opening']+text[index:]
    return text


def pseudo_text(text, *, expansion=1.25, extras=(), glossary=(), salt=0, fillers=True):
    """Deterministic pseudo-Spanish for one wire string; markers/values verbatim."""
    from .llm import _mask_text, PLACEHOLDER
    mapping = {}
    masked = _mask_text(text, list(extras), mapping)
    blocked = [(m.start(), m.end(), None) for m in PLACEHOLDER.finditer(masked)]
    gloss = _glossary_spans(masked, glossary, blocked)
    fixed = sorted(blocked+gloss)
    # Free segments between fixed spans; words are rewritten only there.
    pieces, cursor = [], 0
    for a, b, replacement in fixed:
        if cursor < a:
            pieces.append(['free', masked[cursor:a]])
        pieces.append(['fixed', masked[a:b] if replacement is None else replacement])
        cursor = b
    if cursor < len(masked):
        pieces.append(['free', masked[cursor:]])
    words = []
    for index, piece in enumerate(pieces):
        if piece[0] != 'free':
            continue
        out, last = [], 0
        for m in WORD.finditer(piece[1]):
            out.append(piece[1][last:m.start()])
            out.append(_pseudo_word(m.group(), salt))
            words.append((index, len(out)-1, m.group()))
            last = m.end()
        out.append(piece[1][last:])
        piece.append(out)
    source_letters = sum(len(p[1]) for p in pieces if p[0] == 'free')
    extra = round(source_letters*(expansion-1)) if words and expansion > 1 else 0
    grown, after = defaultdict(str), defaultdict(str)

    def grow(n):
        ranked = words if len(words) < 4 else sorted(words, key=lambda w: -len(w[2]))[:6]
        for k in range(n):
            index, slot, original = ranked[k % len(ranked)]
            letter = 'áéíóún'[k % 6]
            grown[(index, slot)] += letter.upper() if len(original) > 1 and original.isupper() else letter
    if extra and words:
        if len(words) <= 3 or extra < 5 or not fillers:
            grow(extra)
        else:
            slots = words[:-1]
            start = _hash(PSEUDO_VERSION, 'filler', salt, masked)[0] % len(FILLERS)
            chosen = []
            while True:
                f = FILLERS[(start+len(chosen)) % len(FILLERS)]
                if extra-sum(len(x)+1 for x in chosen) < len(f)+1:
                    break
                chosen.append(f)
            for k, f in enumerate(chosen):
                index, slot, original = slots[min(len(slots)-1, (k*len(slots))//len(chosen)+len(slots)//(2*len(chosen)))]
                upper = len(original) > 1 and original.isupper()
                after[(index, slot)] += ' '+(f.upper() if upper else f)
            grow(extra-sum(len(x)+1 for x in chosen))
    for (index, slot), letters in grown.items():
        pieces[index][2][slot] += letters
    for (index, slot), text_after in after.items():
        pieces[index][2][slot] += text_after
    result = ''.join(''.join(p[2]) if p[0] == 'free' else p[1] for p in pieces)
    result = _openers(result)
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(), m.group()), result)


def validated(source, *, expansion=1.25, extras=(), glossary=(), request=None):
    """First salt whose output passes the same local validators as an LLM answer."""
    from .numeric_order import numeric_quantifier_issues
    from .text_quality import text_issues, marked_style_issues
    style = re.compile(r'\{v\d+\}')
    for salt in range(8):
        for use_fillers in (True, False):
            out = pseudo_text(source, expansion=expansion, extras=extras, glossary=glossary, salt=salt,
                              fillers=use_fillers)
            if style.findall(out) != style.findall(source):
                continue
            plain = lambda s: style.sub('', s)
            if Counter(PROTECTED.findall(plain(out))) != Counter(PROTECTED.findall(plain(source))):
                continue
            if text_issues(source, out, 'pseudo') or marked_style_issues(source, out, 'pseudo'):
                continue
            if numeric_quantifier_issues(source, out):
                continue
            if request is not None:
                from .retranslation import validate_answer
                try:
                    validate_answer(request, out, extras=extras)
                except (ValueError, Blocked):
                    continue
            return out, None
    return source, 'pseudo_generation_failed'


def _terms(path):
    with Path(path).open() as stream:
        return list(csv.DictReader(stream))


def pseudo_page_payload(page, number, source_hash, *, extras, glossary, expansion):
    """Mirror adapters.fidelity_translate unit routing with pseudo answers."""
    from .consistency import build_plan, resolve_plan
    from .dates import translate_date, looks_like_date
    from .fidelity import extract_fidelity_units
    from .editorial_rules import protected_editorial_units
    from .composition_corrections import quote_job
    from .style_spans import decode
    extracted = extract_fidelity_units(page, extras)
    editorial = {x['unit_id'] for x in protected_editorial_units(page, extracted)}
    jobs = quote_job(page, extracted, source_hash)
    quote_ids = {i for job in jobs for i in job['unit']['composed_ids']}
    local, model, failures = [], [], []
    for unit in extracted:
        unit = {**unit, 'id': f'p{number:04d}_'+unit['id'], 'page': number}
        runs = [{'text': unit['source'][r['start']:r['end']], 'style': r['style']} for r in unit['style_runs']]
        if unit['id'].split('_', 1)[1] in quote_ids:
            local.append({**unit, 'target': unit['source'], 'target_runs': runs,
                          'translation_method': 'requires_joined_quote_correction'})
        elif unit['id'].split('_', 1)[1] in editorial:
            local.append({**unit, 'target': unit['source'], 'target_runs': runs,
                          'translation_method': 'preserved_published_work'})
        else:
            target = translate_date(unit['source'])
            if target is not None:
                local.append({**unit, 'target': target, 'target_runs': [{'text': target, 'style': unit['style']}],
                              'translation_method': 'deterministic_date'})
            elif looks_like_date(unit['source']):
                raise Blocked('Unsupported date label must be handled locally before inference')
            else:
                model.append(unit)
    plan = build_plan(model)
    answers = {}
    # A translator keeps a source-italic foreign word (italic by text matrix,
    # e.g. «plouton»); composition then restores its italic run. Pseudo too.
    from .text_quality import matrix_italics
    kept = list(extras)+[i['text'] for i in matrix_italics(page) if WORD.search(i['text'])]
    for request in plan['requests']:
        answers[request['id']], failure = validated(request['source'], expansion=expansion, extras=kept,
                                                    glossary=glossary)
        if failure:
            failures.append({'request_id': request['id'], 'reason': failure})
    resolved = resolve_plan(plan, answers)
    units = resolved['units']+local  # production order (adapters.fidelity_translate)
    corrections = []
    for job in jobs:
        answer, failure = validated(job['request']['source'], expansion=expansion, extras=extras,
                                    glossary=glossary, request=job['request'])
        if failure:
            failures.append({'request_id': job['request']['id'], 'reason': failure})
            continue
        decode(answer, job['request']['_style_pairs'])
        corrections.append({**job, 'translation': answer,
                            'provenance': {'backend': 'pseudo_offline', 'model': PSEUDO_VERSION,
                                           'entry_point': 'astro_translate.pseudo (no inference)',
                                           'response_sha256': hashlib.sha256(answer.encode()).hexdigest()}})
    return ({'page': number, 'source_sha256': source_hash, 'pseudo': PSEUDO_VERSION, 'blocks': units},
            corrections, failures)


def _snapshot(book, folder):
    """Private book copy: profile + hashed dependencies + config; source is a symlink."""
    from .profiles import load_profile
    root = folder/'input_snapshot'
    if root.exists():
        shutil.rmtree(root)
    snap = root/'book'
    (snap/'work').mkdir(parents=True)
    (root/'batches').mkdir()
    (root/'llm_validation').mkdir()
    (snap/'source.pdf').symlink_to((book/'source.pdf').resolve())
    for name in ('profile.toml', 'pipeline.toml', 'raster_inventory.json'):
        if (book/'work'/name).is_file():
            shutil.copy2(book/'work'/name, snap/'work'/name)
    profile = load_profile(book)

    def referenced(node):
        if isinstance(node, dict):
            if isinstance(node.get('path'), str) and node.get('sha256'):
                source = profile.checked_file(node)
                target = snap/node['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            for child in node.values():
                referenced(child)
        elif isinstance(node, list):
            for child in node:
                referenced(child)
    referenced(profile.data)
    glossary = profile.data.get('editorial', {}).get('glossary')
    if glossary and (book/glossary).is_file() and not (snap/glossary).exists():
        # Read-only access for glossary-bound hooks; a symlink, never a copy or edit.
        (snap/glossary).parent.mkdir(parents=True, exist_ok=True)
        (snap/glossary).symlink_to((book/glossary).resolve())
    return snap


def _labels(runner, n, extras, glossary, expansion, source_hash):
    """Pseudo answers for vertical/rotated label routes, bound like saved targets."""
    from .production import digest
    from .vertical_labels import extract_vertical
    from .native_rotated import extract_straight_labels
    from .dates import translate_date
    with fitz.open(runner.book/'source.pdf') as doc:
        page = doc[n-1]
        def answer(unit):
            date = translate_date(unit['source'])
            return date if date is not None else validated(unit['source'], expansion=expansion, extras=extras,
                                                           glossary=glossary)[0]
        if runner.profile.page_policy(page).get('vertical'):
            units = extract_vertical(page, runner.extras)
            if units:
                identity = digest({'source': sha256(runner.book/'source.pdf'), 'units': units,
                                   'glossary': sha256(runner.glossary), 'style': sha256(runner.style),
                                   'config': sha256(runner.config_path), 'protected': sha256(runner.protected)})
                atomic_json(runner.path(n, 'vertical.json'),
                            {'identity': identity, 'answers': {u['id']: answer(u) for u in units}, 'pseudo': PSEUDO_VERSION})
        units = extract_straight_labels(page, runner.extras)
        if units:
            dependencies = {name: sha256(path) for name, path in [('glossary', runner.glossary), ('style', runner.style),
                                                                  ('protected', runner.protected), ('config', runner.config_path)]}
            atomic_json(runner.path(n, 'rotated.json'),
                        {'identity': digest({'source_sha256': source_hash, 'units': units}),
                         'dependencies': dependencies, 'answers': {u['id']: answer(u) for u in units},
                         'pseudo': PSEUDO_VERSION})


def _line_boxes(page):
    rows = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans'])
            if re.search(r'\w', text):
                rows.append((fitz.Rect(line['bbox']), text.strip()))
    return rows


def _overlaps(rows):
    found = []
    for i, (a, ta) in enumerate(rows):
        for b, tb in rows[i+1:]:
            inter = a & b
            if inter.is_empty:
                continue
            if inter.get_area() > .25*min(a.get_area(), b.get_area()):
                found.append((a, b, ta, tb))
    return found


def _new_overlaps_and_overflow(source_page, candidate_page):
    """Rendered line overlaps / page overflow absent from the source page."""
    before = _overlaps(_line_boxes(source_page))
    seen = [(a, b) for a, b, _, _ in before]
    near = lambda r, s: max(abs(x-y) for x, y in zip(r, s)) < 1
    overlaps = []
    for a, b, ta, tb in _overlaps(_line_boxes(candidate_page)):
        if any((near(a, x) and near(b, y)) or (near(a, y) and near(b, x)) for x, y in seen):
            continue
        overlaps.append({'a': ta[:60], 'b': tb[:60], 'bbox': [round(v, 2) for v in (a & b)]})
    rect = candidate_page.rect
    source_out = [r for r, _ in _line_boxes(source_page) if not rect.contains(r)]
    overflow = [{'text': t[:60], 'bbox': [round(v, 2) for v in r]} for r, t in _line_boxes(candidate_page)
                if not (rect+(-.5, -.5, .5, .5)).contains(r) and not any(near(r, s) for s in source_out)]
    return overlaps, overflow


def _compose_page(runner, snap, book, n, payload, folder, evidence, manifest_row, terms, style_text):
    """Production.compose_and_qa without VLM, manifest, queue or checkpoints."""
    from . import fidelity
    from .text_quality import page_issues
    from .vertical_labels import compose_production_vertical
    from .native_rotated import compose_saved_rotated
    from .candidate_storage import extract_candidate
    row = {'page': n, 'llm_calls': 0}
    regions = runner.inventory(manifest_row or {'page': n})
    row['deferred_regions'] = len(regions)
    if any(r['reason'] == 'route_C_or_no_native_text' for r in regions):
        row['status'] = 'deferred_raster'
        return row
    fit = fidelity.reinsert_fidelity(snap, payload, runner.glossary, runner.style, runner.protected,
                                     config=runner.config, artifact_dir=folder, phase='production', approval_book=book)
    row.update(fit_complete=fit['fit_complete'], failures=fit.get('failures', []))
    if not fit['fit_complete']:
        row['status'] = 'fit_failed'
        return row
    fit = compose_production_vertical(runner, fit)
    fit = compose_saved_rotated(runner, fit)
    extract_candidate(fit)
    atomic_json(folder/f'p{n:04d}-fit.json', fit)
    qa = fidelity.fidelity_qa(snap, fit, runner.glossary, runner.protected, artifact_dir=folder,
                              phase='production', update_manifest=False)
    with fitz.open(snap/'source.pdf') as src, fitz.open(fit['output']) as dst:
        issues = page_issues(fit['blocks'], source_page=src[n-1], candidate_page=dst[0],
                             glossary=terms, style_guide=style_text)
        overlaps, overflow = _new_overlaps_and_overflow(src[n-1], dst[0])
    # Production replaces the list with page_issues; the report keeps the union.
    known = {json.dumps(i, sort_keys=True, default=str) for i in qa['text_quality_issues']}
    qa['text_quality_issues'] += [i for i in issues if json.dumps(i, sort_keys=True, default=str) not in known]
    qa.update(vlm_complete=False, vlm_notes='Not called: pseudo-translation offline composition',
              pseudo=PSEUDO_VERSION)
    atomic_json(folder/f'p{n:04d}-qa.json', qa)
    row.update(candidate=fit['output'], min_scale=qa['min_scale'], local_fidelity_ok=qa['local_fidelity_ok'],
               native_local_fidelity_ok=qa.get('native_local_fidelity_ok'), new_text_overlaps=overlaps,
               page_overflow=overflow, vertical_failure=fit.get('vertical_failure', {}).get('reason'),
               rotated_pending=fit.get('rotated_pending', []))
    mechanical = [i for i in qa['text_quality_issues'] if i.get('code') not in LEXICAL_CODES]
    row['status'] = 'composed' if not mechanical and not overlaps and not overflow and qa['min_scale'] >= .8 else 'review'
    return row


def _comparison(source, row, evidence):
    from PIL import Image, ImageDraw
    n = row['page']
    pix = source[n-1].get_pixmap(dpi=80)
    a = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    b = a.copy()
    if row.get('candidate'):
        with fitz.open(row['candidate']) as dst:
            pix = dst[0].get_pixmap(dpi=80)
            b = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    side = Image.new('RGB', (a.width+b.width, a.height+28), 'white')
    side.paste(a, (0, 28))
    side.paste(b, (a.width, 28))
    ImageDraw.Draw(side).text((8, 8), f'PDF {n} | ORIGINAL / PSEUDO {row["status"]} | offline, no LLM/VLM', fill='black')
    target = evidence/f'p{n:04d}-comparison.png'
    side.save(target)
    return str(target)


def _kind(group):
    """Scale-group family without page/size identifiers (body, caption, display, story, ...)."""
    name = re.sub(r'^p\d{4}_', '', str(group))
    name = re.sub(r'_[\d.]+(?=_|$)', '', name)
    return re.sub(r'_(?:b\d+|u\d+\w*)$', '', name) or 'unknown'


def mechanical_groups(rows, folder):
    """Layout/QA defect groups from saved pseudo fit/QA/font artifacts."""
    folder = Path(folder)
    groups = {}

    def add(code, cause, page, detail, action='recompose', unit_id=None):
        row = groups.setdefault((code, cause), {'code': code, 'cause': cause, 'pages': set(), 'before_pages': [],
                                                'details': [], 'action': action, 'unit_ids': [],
                                                'lexical_pseudo_artifact': code in LEXICAL_CODES})
        row['pages'].add(page)
        if unit_id and unit_id not in row['unit_ids']:
            row['unit_ids'].append(unit_id)
        if detail and len(row['details']) < 30 and detail not in row['details']:
            row['details'].append(detail)
    read = lambda p, d: json.loads(p.read_text()) if p.exists() else d
    for r in rows:
        n = r['page']
        fit = read(folder/f'p{n:04d}-fit.json', {})
        qa = read(folder/f'p{n:04d}-qa.json', {})
        fonts = read(folder/f'p{n:04d}-fonts.json', [])
        for failure in (fit.get('failures', []) if not fit.get('fit_complete', True) else []):
            cause = failure.get('reason') or f'grupo {_kind(failure.get("group"))} no cabe a escala 0,75'
            add('fit_failed', cause, n, {'page': n, **{k: failure.get(k) for k in ('id', 'group', 'reason')}},
                action='retranslate_or_short_form', unit_id=failure.get('id'))
        for group, scale in sorted(fit.get('group_scales', {}).items()):
            if scale < .8:
                ids = [b['id'] for b in fit.get('blocks', []) if b.get('scale_group') == group]
                add('scale_below_0.80', f'escala de grupo < 0,80 ({_kind(group)})', n,
                    {'page': n, 'group': group, 'scale': scale, 'unit_ids': ids[:10]}, action='review')
        for audit in fonts:
            reasons = [k for k in ('original_missing', 'original_empty_glyph_outline', 'original_text_roundtrip_failed')
                       if audit.get(k)]
            if audit.get('group_fallback') and reasons:
                missing = ''.join(audit.get('original_missing') or [])
                name = re.sub(r'^[A-Z]{6}\+', '', audit['original_name'])
                cause = (f'{name}: sin cobertura del texto traducido' if missing else
                         f'{name}: {reasons[0].removeprefix("original_")}')
                verification = audit.get('fallback_verification') or {}
                # A verified style-equivalent fallback is informative, not a defect.
                code = 'font_fallback_applied' if verification.get('ok') else 'font_fallback'
                add(code, cause, n, {'page': n, 'group': audit.get('scale_group'), 'missing': missing,
                    'chosen': Path(audit.get('chosen', {}).get('path', '')).name,
                    'base': (verification.get('checks') or {}).get('base'),
                    'problems': verification.get('problems', ['unverified'])},
                    action='none' if verification.get('ok') else 'verify_fallback')
                groups[(code, cause)].setdefault('missing_chars', set()).update(missing)
        if qa:
            if qa.get('non_text_pixel_diff', 0) > 0:
                add('ink_outside_edit_boxes', 'tinta nueva fuera de las cajas editadas', n,
                    {'page': n, 'non_text_pixel_diff': qa['non_text_pixel_diff']})
            for key in QA_FLAGS:
                if qa.get(key) is False:
                    add('qa_'+key, f'QA local: {key} = false', n, {'page': n})
            if qa.get('typography_audit', {}).get('ok') is False:
                add('typography_audit', 'auditoría tipográfica no satisfecha', n,
                    {'page': n, 'audit': {k: v for k, v in qa['typography_audit'].items() if k != 'ok'}})
            if qa.get('omitted_rotated_text'):
                add('rotated_text_untranslated', 'texto rotado/curvo sin componer (Fase 4)', n,
                    {'page': n, 'spans': len(qa['omitted_rotated_text'])}, action='phase4')
            if qa.get('residual_english'):
                add('residual_english', 'inglés residual en el candidato', n,
                    {'page': n, 'texts': qa['residual_english'][:5]}, action='review')
            if qa.get('glossary_missing'):
                add('glossary_missing', 'forma de glosario ausente', n, {'page': n, 'terms': qa['glossary_missing']})
        for item in r.get('new_text_overlaps', []):
            add('new_text_overlap', 'líneas de texto solapadas que no lo estaban en el original', n,
                {'page': n, **item})
        for item in r.get('page_overflow', []):
            add('page_bounds_overflow', 'texto fuera de los límites de la página', n, {'page': n, **item})
        if r.get('vertical_failure'):
            add('vertical_label_no_fit', r['vertical_failure'], n, {'page': n}, action='review')
        if r.get('rotated_pending'):
            add('rotated_labels_pending', 'etiquetas rectas rotadas sin respuesta guardada', n,
                {'page': n, 'ids': r['rotated_pending'][:10]})
        for item in r.get('pseudo_failures', []):
            add('pseudo_generation_failed', 'el pseudo-texto no pasó los validadores locales', n,
                {'page': n, **item}, action='fix_pseudo')
    result = []
    for row in groups.values():
        if 'missing_chars' in row:
            row['missing_chars'] = ''.join(sorted(row['missing_chars']))
        row['pages'] = sorted(row['pages'])
        row['page_count'] = len(row['pages'])
        row['before_page_count'] = 0
        result.append(row)
    return result


def _collect_defects():
    """Shared grouping logic of scripts/book_defect_report.py (loaded, not copied)."""
    path = ROOT/'scripts/book_defect_report.py'
    spec = importlib.util.spec_from_file_location('_astro_book_defect_report', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.collect_defects


def _merge(base, extra):
    merged = {}
    for row in base+extra:
        key = (row['code'], row['cause'])
        if key not in merged:
            merged[key] = {**row, 'pages': list(row.get('pages', [])), 'details': list(row.get('details', []))}
            merged[key].setdefault('lexical_pseudo_artifact', row['code'] in LEXICAL_CODES)
            continue
        target = merged[key]
        target['pages'] = sorted(set(target['pages']) | set(row.get('pages', [])))
        target['details'] = (target['details']+[d for d in row.get('details', []) if d not in target['details']])[:30]
        target['unit_ids'] = list(dict.fromkeys(target.get('unit_ids', [])+row.get('unit_ids', [])))
    for row in merged.values():
        row['page_count'] = len(row['pages'])
    return sorted(merged.values(), key=lambda r: (r['lexical_pseudo_artifact'], -r['page_count'], r['code'], r['cause']))


def _write_report(book, folder, rows, defects, expansion, elapsed):
    statuses = Counter(r['status'] for r in rows)
    summary = {'book': book.name, 'mode': 'pseudo', 'pseudo_version': PSEUDO_VERSION, 'expansion': expansion,
               'source_sha256': sha256(book/'source.pdf'), 'pages': [r['page'] for r in rows],
               'statuses': dict(sorted(statuses.items())), 'llm_calls': 0, 'vlm_calls': 0,
               'elapsed_s': round(elapsed, 1), 'defects': defects,
               'note': ('Pseudo-texto determinista sin LLM; los grupos marcados lexical_pseudo_artifact '
                        'juzgan redacción y no son defectos de maquetación.')}
    atomic_json(folder/'defects.json', summary)
    informative = [d for d in defects if d['code'] in INFORMATIVE_CODES]
    layout = [d for d in defects if not d['lexical_pseudo_artifact'] and d['code'] not in INFORMATIVE_CODES]
    lexical = [d for d in defects if d['lexical_pseudo_artifact']]
    def table(items):
        lines = ['| Código / causa | Páginas | # | Ejemplos | Acción |', '|---|---|---:|---|---|']
        for d in items:
            links = ', '.join(f"[p{e['page']}]({e['png']})" for e in d.get('examples', []))
            pages = ', '.join(map(str, d['pages'][:40]))+(' …' if len(d['pages']) > 40 else '')
            lines.append(f"| `{d['code']}` — {d['cause']} | {pages} | {d['page_count']} | {links} | {d['action']} |")
        return lines
    lines = [f'# Defectos agrupados — {book.name} (pseudo-traducción)', '',
             f'Composición offline con pseudo-español determinista ({PSEUDO_VERSION}, expansión {expansion:.2f}). '
             f'Páginas: {len(rows)}. Estados: ' + ', '.join(f'{k} {v}' for k, v in sorted(statuses.items())) +
             '. Sin llamadas LLM/VLM; sin escritura en manifest, caché, costos ni candidatos de producción.', '',
             '## Defectos de maquetación por causa', ''] + table(layout)
    lines += ['', '## Artefactos léxicos del pseudo-texto (no son defectos de maquetación)', ''] + table(lexical)
    lines += ['', '## Fallback de fuente aplicado (informativo, no es defecto)', '',
              'Sustitución verificada por `fonts.verify_fallback`: cobertura con tinta, clase, cursiva, '
              'ancho calibrado y peso.', ''] + table(informative)
    lines += ['', 'Cada grupo conserva hasta 30 detalles en `defects.json`. Comparativas en `evidence/`.', '']
    (folder/'defects.md').write_text('\n'.join(lines))
    return summary


def compose_offline(book, *, pages=None, expansion=1.25, output=None):
    from .production import Production, read, selected_pages
    book = Path(book).resolve()
    started = time.monotonic()
    folder = Path(output).resolve() if output else (book/'work/pseudo').resolve()
    work = (book/'work').resolve()
    if not folder.is_relative_to(work) or folder == work or folder.is_relative_to(work/'production'):
        raise ValueError('Pseudo output must be a private directory below book/work (not production)')
    if not 1 <= expansion <= 2:
        raise ValueError('expansion must be between 1.0 and 2.0')
    folder.mkdir(parents=True, exist_ok=True)
    snap = _snapshot(book, folder)
    runner = Production(snap)
    compose_dir = snap/'work/compose'
    compose_dir.mkdir(parents=True, exist_ok=True)
    runner.folder = compose_dir
    evidence = folder/'evidence'
    if evidence.exists():
        shutil.rmtree(evidence)
    evidence.mkdir()
    terms = _terms(runner.glossary)
    style_text = runner.style.read_text()
    extras = read_extras(runner.protected)
    source_hash = sha256(book/'source.pdf')
    manifest = read(book/'work/manifest.json', {})
    manifest_rows = {r['page']: r for r in manifest.get('pages', [])} if manifest.get('source_sha256') == source_hash else {}
    deps = {'source_sha256': source_hash, 'glossary_sha256': sha256(runner.glossary),
            'style_sha256': sha256(runner.style), 'protected_sha256': sha256(runner.protected)}
    rows = []
    with fitz.open(book/'source.pdf') as source:
        numbers = selected_pages(pages, len(source))
        corrections = []
        payloads = {}
        for n in numbers:
            page = source[n-1]
            if not page.get_text().strip():
                continue
            payload, quote_rows, failures = pseudo_page_payload(page, n, source_hash, extras=extras,
                                                                glossary=terms, expansion=expansion)
            payloads[n] = (payload, failures)
            corrections += quote_rows
            atomic_json(snap/'work/production'/f'p{n:04d}-translation.json', payload)
        atomic_json(snap/'work/production/composition_corrections.json', {**deps, 'corrections': corrections})
        for n in numbers:
            row = {'page': n, 'status': 'pending', 'llm_calls': 0}
            try:
                if n not in payloads:
                    row.update(status='deferred_raster', reason='no_native_text')
                else:
                    payload, failures = payloads[n]
                    from .book_index import pseudo_row
                    back = pseudo_row(snap/'source.pdf', n, compose_dir, expansion, extras, terms)
                    if back is not None:  # index/glossary: book_index route
                        row = back
                    else:
                        _labels(runner, n, extras, terms, expansion, source_hash)
                        row = _compose_page(runner, snap, book, n, payload, compose_dir, evidence,
                                            manifest_rows.get(n), terms, style_text)
                    row['pseudo_failures'] = failures
            except Exception as exc:  # recorded per page; the run continues
                import traceback
                row.update(status='error', error=f'{type(exc).__name__}: {exc}',
                           trace=traceback.format_exc(limit=-4).splitlines()[-9:])
            row['comparison'] = _comparison(source, row, evidence)
            rows.append(row)
            atomic_json(folder/'report.json', {'pages': rows, 'llm_calls': 0, 'mode': 'pseudo',
                                               'source_sha256': source_hash})
            print(json.dumps({k: v for k, v in row.items() if k in ('page', 'status', 'min_scale', 'error')},
                             ensure_ascii=False), flush=True)
    collect = _collect_defects()
    base = collect(book, folder, folder/'input_snapshot/batches', folder/'input_snapshot/llm_validation',
                   {'pages': rows}, recomposition=compose_dir)
    # Fit failures without a reason are regrouped by scale-group kind below.
    base = [row for row in base if not (row['code'] == 'fit_failure' and row['cause'] == 'unknown fit failure')]
    for row in base:
        row['lexical_pseudo_artifact'] = row['code'] in LEXICAL_CODES
    defects = _merge(base, mechanical_groups(rows, compose_dir))
    images = {r['page']: r['comparison'] for r in rows}
    for d in defects:
        d['examples'] = [{'page': p, 'png': str(Path(images[p]).relative_to(folder))}
                         for p in d['pages'][:2] if p in images]
    summary = _write_report(book, folder, rows, defects, expansion, time.monotonic()-started)
    return {'mode': 'pseudo', 'llm_calls': 0, 'pages': rows, 'statuses': summary['statuses'],
            'defect_groups': len(defects), 'defects_md': str(folder/'defects.md'),
            'defects_json': str(folder/'defects.json'), 'elapsed_s': summary['elapsed_s']}
