"""Resumable production orchestration. All inference stays behind llm.run_task.

The long-lived writer lock is distinct from core.book_lock: composing/QA and the
LLM may themselves take the short manifest lock. Checkpoints are byte-bound;
semantic translation memory deliberately does not depend on implementation code.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import tomllib

import pymupdf as fitz

from . import adapters, fidelity, llm, safe_vlm
from .core import Blocked, atomic_json, book_lock, read_extras, require_approval, sha256
from .profiles import load_profile


# Exit codes (documented in __main__): 75 quota pause (llm.QuotaPaused), 76 G5 block.
G5_EXIT_CODE = 76
G5_THRESHOLD = .90


class G5Blocked(Blocked):
    """A closed batch failed G5; production may not advance beyond it."""
    exit_code = G5_EXIT_CODE


# Per-unit model outcomes: the page goes to review and stays retryable.
LLM_REVIEW_ERRORS = (llm.ValidationFailure, llm.BackendError)


def validate_g5_decision(data, batch):
    """Minimal schema for an orchestrator decision; raises G5Blocked if invalid."""
    problems = []
    if not isinstance(data, dict):
        raise G5Blocked(f'G5 decision for batch {batch} must be a JSON object')
    if data.get('decision') not in ('continue', 'recompose'):
        problems.append("decision must be 'continue' or 'recompose'")
    for key in ('approved_by', 'reason'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            problems.append(f'{key} must be a non-empty string')
    evidence = data.get('evidence')
    if not ((isinstance(evidence, str) and evidence.strip()) or (isinstance(evidence, list) and evidence
            and all(isinstance(x, str) and x.strip() for x in evidence))):
        problems.append('evidence must be a non-empty string or list of strings')
    if 'batch' in data and data['batch'] != batch:
        problems.append(f'batch field {data["batch"]!r} does not match batch {batch}')
    if problems:
        raise G5Blocked(f'Invalid G5 decision for batch {batch}: ' + '; '.join(problems))
    return data


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def selected_pages(spec, count):
    if spec is None:
        return list(range(1, count + 1))
    result = set()
    for term in spec.split(','):
        bounds = term.strip().split('-')
        if len(bounds) not in (1, 2):
            raise ValueError('Expected pages A-B or comma-separated ranges')
        start, end = int(bounds[0]), int(bounds[-1])
        if start < 1 or end > count or end < start:
            raise ValueError('Page selection out of bounds')
        result.update(range(start, end + 1))
    return sorted(result)


@contextmanager
def writer_lock(book):
    folder = Path(book)/'work'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'.production.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked('Another production/assembly/review writer is active') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.'+target.name)
    try:
        with os.fdopen(fd, 'wb') as out, Path(source).open('rb') as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def batch_metrics(rows):
    justified = lambda r: bool(r.get('deferred_regions') and all(
        x.get('bbox') and x.get('reason') for x in r['deferred_regions']))
    deferred = [r for r in rows if r['status'] == 'deferred_raster' and justified(r)]
    partial = [r for r in rows if r['status'] == 'partial_deferred'
               and r.get('native_qa_ok') and justified(r)]
    excluded = {r['page'] for r in deferred + partial}
    denominator = len(rows) - len(excluded)
    ok = sum(r['status'] == 'done_qa_ok' for r in rows)
    ratio = ok / denominator if denominator else None
    return {'pages': len(rows), 'qa_ok': ok, 'deferred_raster': len(deferred),
            'partial_deferred': len(partial), 'partial_pages': [r['page'] for r in partial],
            'denominator': denominator, 'qa_ok_ratio': ratio,
            'g5_pass': denominator > 0 and ratio >= .90,
            'review_pages': [r['page'] for r in rows if r['status'] != 'done_qa_ok' and r['page'] not in excluded]}


def consumption(records, backend):
    """Keep unknown provider usage/USD unknown; local cache zero is explicit."""
    metrics = {}
    # estimated_cost_usd is the CLI's API-equivalent figure for subscription calls, never a charge.
    for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'latency_s', 'cost_usd', 'estimated_cost_usd'):
        known = [r[key] for r in records if isinstance(r.get(key), (float, int))
                 and not (key=='cost_usd' and r.get('backend') in llm.SUBSCRIPTION_BACKENDS and r.get(key)==0
                          and r.get('status') not in ('cache_hit','block_cache_hit','sentence_cache_hit'))]
        metrics[key] = {'known_sum': sum(known), 'unknown_records': len(records)-len(known)}
    metrics.update(records=len(records), cache_records=sum(bool(r.get('cached')) for r in records),
                   backends=dict(Counter(r.get('backend', 'unknown') for r in records)),
                   backend_mismatches=[i for i, r in enumerate(records) if r.get('backend') != backend],
                   missing_backend_records=sum(not r.get('backend') for r in records))
    return metrics


def native_failure_causes(qa, fit):
    """Name the native checks that failed; the generic reason alone hid them (p176)."""
    causes = [] if fit.get('fit_complete') else ['fit_incomplete']
    for key in ('glossary_missing', 'text_quality_issues', 'native_residual_english'):
        if qa.get(key):
            causes.append(key)
    for key in ('numbers_equal', 'immutable_spans_equal', 'glyph_set_equal', 'images_equal', 'vectors_equal',
                'complete_translated_text', 'uniform_scale_groups', 'original_colors_verified'):
        if qa.get(key) is False:
            causes.append(key+'_failed')
    if qa.get('typography_audit', {}).get('ok') is False:
        causes.append('typography_audit_failed')
    return causes


def uncovered_deferred_text(qa, regions):
    """Omitted rotated source spans outside every justified deferred region.

    Spans without letters (separators «|», bullets, dots, bare digits) are not translatable
    text: they stay as they are and never count as untranslated English.
    """
    boxes = [fitz.Rect(r['bbox'])+(-.5, -.5, .5, .5) for r in regions if r.get('bbox') and r.get('reason')]
    return [item for item in qa.get('omitted_rotated_text', [])
            if any(c.isalpha() for c in item.get('text', ''))
            and not any(box.contains(fitz.Rect(item['bbox'])) for box in boxes)]


class Production:
    def __init__(self, book, config_path=None):
        self.book = Path(book).resolve()
        self.work = self.book/'work'
        self.folder = self.work/'production'
        self.config_path = Path(config_path or self.work/'pipeline.toml').resolve()
        if not self.config_path.exists() and config_path is None:
            self.config_path = llm.ROOT/'config/pipeline.toml'
        self.config = tomllib.loads(self.config_path.read_text())
        paths = self.config['paths']
        def path(key):
            value = Path(paths[key])
            return value if value.is_absolute() else llm.ROOT/value
        self.glossary, self.style, self.protected = map(path, ('glossary', 'style_guide', 'do_not_translate'))
        self.extras = read_extras(self.protected)
        self.profile = load_profile(self.book)
        self.source_hash = sha256(self.book/'source.pdf')
        self.folder.mkdir(parents=True, exist_ok=True)

    def gates(self):
        require_approval(self.book, self.glossary, self.style, self.protected)
        adapters.require_backend_approval(self.book, self.config_path, 'production')
        backend = read(self.work/'backend_approval.json')
        if 'production' not in backend.get('authorized_phases', []):
            raise Blocked('G1b must explicitly authorize production')
        pilot = read(self.work/'pilot_approval.json')
        if pilot.get('source_sha256', self.source_hash) != self.source_hash:
            raise Blocked('G4 source hash changed')
        if pilot.get('comparison_sha256'):
            candidate = Path(pilot['comparison_pdf'])
            if not candidate.is_absolute():
                candidate = llm.ROOT/candidate
            if not candidate.is_file() or sha256(candidate) != pilot['comparison_sha256']:
                raise Blocked('G4 comparison evidence changed')
        return pilot

    def dependencies(self):
        # QA/geometry checkpoints include code. Translation memory does not.
        from .translation_context import translation_context
        code = {p.name:sha256(p) for p in Path(__file__).parent.glob('*.py')}
        profile_files = {}
        def check(value):
            if isinstance(value, dict):
                if 'path' in value and 'sha256' in value:
                    p = self.profile.checked_file(value)
                    profile_files[str(p.relative_to(self.book))] = sha256(p)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(self.profile.data)
        return digest({'source':self.source_hash, 'profile':self.profile.data, 'profile_files':profile_files,
                       'code':code, 'config':sha256(self.config_path),
                       'semantic_context':translation_context(self.config, self.glossary, self.style, self.protected),
                       'gates':{n:sha256(self.work/n) for n in ('glossary_approval.json','backend_approval.json','pilot_approval.json')}})

    def path(self, n, suffix):
        return self.folder/f'p{n:04d}-{suffix}'

    def update(self, n, **values):
        with book_lock(self.book):
            manifest = read(self.work/'manifest.json')
            row = next(r for r in manifest['pages'] if r['page'] == n)
            row.update(values)
            atomic_json(self.work/'manifest.json', manifest)
        return row

    def checkpoint(self, n, dependency, **values):
        record = read(self.path(n, 'state.json'), {})
        record.pop('retry_llm', None)
        if values.get('stage') != 'complete':
            record.pop('status', None)
        record.update(values, dependency=dependency, source_sha256=self.source_hash)
        record['artifacts'] = {p.name:sha256(p) for p in self.folder.glob(f'p{n:04d}-*')
                               if p.is_file() and p != self.path(n, 'state.json')}
        atomic_json(self.path(n, 'state.json'), record)
        return record

    def current(self, n, dependency):
        state = read(self.path(n, 'state.json'), {})
        if state.get('dependency') != dependency or not state.get('artifacts'):
            return {}
        if any(not (self.folder/name).is_file() or sha256(self.folder/name) != value
               for name, value in state['artifacts'].items()):
            return {}
        return state

    def screenshot(self, n):
        target = self.folder/'renders'/f'p{n:04d}-before.png'
        if not target.exists():
            target.parent.mkdir(exist_ok=True)
            with fitz.open(self.book/'source.pdf') as doc:
                doc[n-1].get_pixmap(dpi=110).save(target)
        return str(target)

    def queue(self, n, status, reasons, regions=()):
        capture = self.folder/'renders'/f'p{n:04d}-comparison.png'
        if not capture.exists():
            capture = Path(self.screenshot(n))
        with book_lock(self.book):
            queue = read(self.work/'review_queue.json', [])
            queue = [r for r in queue if not (r.get('page') == n and r.get('phase') == 'production')]
            if status != 'done_qa_ok':
                queue.append({'page':n, 'phase':'production', 'status':status,
                              'reason':'; '.join(reasons), 'reasons':reasons,
                              'deferred_regions':list(regions), 'capture':str(capture),
                              'artifacts':[str(self.path(n, 'qa.json')), str(capture)]})
            atomic_json(self.work/'review_queue.json', queue)

    def inventory(self, row):
        """Known raster/curve regions; unknown raster coverage remains explicit."""
        n = row['page']
        regions = []
        with fitz.open(self.book/'source.pdf') as doc:
            page = doc[n-1]
            if not page.get_text().strip():
                return [{'bbox':list(page.rect), 'reason':'route_C_or_no_native_text', 'phase':4}]
            if row.get('route')=='C':
                regions.extend({'bbox':list(i['bbox']),'reason':'raster_inventory_with_native_text_overlay','phase':4} for i in page.get_image_info())
            for item in row.get('images', []):
                if item.get('has_text'):
                    regions.append({'bbox':item.get('bbox',item.get('rect')), 'reason':'unresolved_raster_text', 'phase':4})
            from .native_rotated import deferred_inventory
            regions.extend(deferred_inventory(page, self.extras))
            if not self.profile.page_policy(page).get('vertical'):
                regions.extend({'bbox':item['bbox'], 'reason':'unresolved_vertical_text', 'phase':4}
                               for item in fidelity.omitted_rotated_text(page,self.extras)
                               if tuple(item['direction'])==(0.,-1.))
        extra = read(self.work/'raster_inventory.json', {})
        if extra:
            if extra.get('source_sha256') != self.source_hash:
                raise Blocked('Raster inventory source hash changed')
            regions.extend(extra.get('pages', {}).get(str(n), []))
        return regions

    def reuse_pilot(self, n, approval, dependency):
        approved = approval.get('pages', {}).get(str(n))
        if approved is None:
            return False
        with fitz.open(self.book/'source.pdf') as doc:
            if not doc[n-1].get_text().strip():return False
        base = self.work/'pilot'
        fit = read(base/f'p{n:04d}-fit.json', {})
        qa = read(base/f'p{n:04d}-qa.json', {})
        candidate = Path(fit.get('output', self.book/'source.pdf'))
        if (not candidate.is_file() or sha256(candidate) != approved['candidate_sha256'] or
                fit.get('output_sha256') != approved['candidate_sha256']):
            raise Blocked('Approved pilot candidate changed; cannot recompose implicitly')
        if (qa.get('source_sha256') != self.source_hash or
                qa.get('candidate_sha256') != approved['candidate_sha256']):
            raise Blocked('Pilot QA source or candidate hash changed')
        for suffix in ('translation.json', 'fonts.json'):
            src = base/f'p{n:04d}-{suffix}'
            if src.exists():
                atomic_copy(src, self.path(n, suffix))
            else:
                atomic_json(self.path(n, suffix), {'reused_from_pilot':True, 'unavailable':True})
        destination = self.path(n, 'translated.pdf')
        atomic_copy(candidate, destination)
        from .candidate_storage import extract_page
        extract_page(destination,n,destination,source_index=fit.get('output_page',n-1))
        fit.update(output=str(destination), output_page=0, output_sha256=sha256(destination),
                   artifact_dir=str(self.folder), phase='production', reused_from_pilot=True)
        atomic_json(self.path(n, 'fit.json'), fit)
        regions = [{'bbox':r['bbox'], 'reason':'pilot_unresolved_rotated_text', 'phase':4}
                   for r in qa.get('omitted_rotated_text', [])]
        if not approved.get('local_fidelity_ok') and not regions:
            with fitz.open(self.book/'source.pdf') as doc:
                regions = [{'bbox':list(doc[n-1].rect), 'reason':'G4_deferred_artwork_or_scan', 'phase':4}]
        local = bool(approved.get('local_fidelity_ok') and qa.get('local_fidelity_ok'))
        native_ok = bool(qa.get('native_local_fidelity_ok',local) and fit.get('fit_complete'))
        status = ('partial_deferred' if native_ok else 'review') if regions else 'done_qa_ok' if local else 'review'
        qa.update(phase='production', candidate_sha256=sha256(destination), reused_from_pilot=True,
                  production_status=status, delegated_visual_review={'reviewer':approval['approved_by'],
                  'candidate_sha256':approved['candidate_sha256'], 'evidence':'work/pilot_approval.json'},
                  deferred_regions=regions, dependency=dependency, native_qa_ok=native_ok)
        atomic_json(self.path(n, 'qa.json'), qa)
        self.update(n, status=status, production_qa=str(self.path(n,'qa.json')), deferred_regions=regions,native_qa_ok=native_ok)
        self.queue(n,status,['G4 pilot candidate reused; unresolved artwork' if regions else 'local QA not OK'],regions)
        self.checkpoint(n,dependency,stage='complete',status=status,reused_from_pilot=True)
        return True

    def review_for(self, n, candidate_hash):
        reviews = read(self.folder/'visual_reviews.json', {})
        record=reviews.get(str(n), {}).get(candidate_hash)
        return record if record and record.get('source_sha256')==self.source_hash else None

    def finish_qa(self, n, fit, qa, regions, dependency):
        from .native_rotated import remaining_regions
        regions = remaining_regions(regions, fit)
        review = self.review_for(n, fit.get('output_sha256'))
        if review:
            qa['vlm_local_review'] = review
        vlm = qa.get('vlm', {}).get('native_vlm', {})
        vlm_ok = bool(qa.get('vlm_complete') and vlm.get('verdict') == 'ok' and not vlm.get('overlaps') and not vlm.get('residual_english'))
        with fitz.open(self.book/'source.pdf') as doc:
            graphics = bool(doc[n-1].get_images() or doc[n-1].get_drawings())
        reasons = []
        native_ok = bool(qa.get('native_local_fidelity_ok', qa.get('local_fidelity_ok')) and fit.get('fit_complete'))
        if not native_ok:
            reasons.append('local_fidelity_or_text_quality_failed')
            qa['native_failure_causes'] = native_failure_causes(qa, fit)
        # partial_deferred only when every untranslated rotated source span lies
        # in a registered Phase 4 region with bbox and reason.
        uncovered = uncovered_deferred_text(qa, regions)
        if uncovered:
            reasons.append('untranslated_text_outside_deferred_regions')
        if not vlm_ok and not review:
            reasons.append('vlm_missing_or_findings')
        if graphics and not review:
            reasons.append('visual_graphics_review_required')
        if qa.get('min_scale', 1) < .8 and not review:
            reasons.append('scale_below_0.80_requires_review')
        if regions:
            reasons.append('deferred_raster_phase4')
        justified = all(r.get('bbox') and r.get('reason') for r in regions)
        status = (('partial_deferred' if native_ok and justified and not uncovered else 'review') if regions
                  else 'review' if reasons else 'done_qa_ok')
        qa.update(production_status=status, dependency=dependency, deferred_regions=regions, native_qa_ok=native_ok,
                  production_reasons=reasons, untranslated_outside_deferred=uncovered, verdict='ok' if status=='done_qa_ok' else 'revisar',
                  raster_coverage='known regions inventoried; no OCR of unclassified images')
        atomic_json(self.path(n, 'qa.json'), qa)
        self.update(n,status=status,production_qa=str(self.path(n,'qa.json')),deferred_regions=regions,native_qa_ok=native_ok)
        self.queue(n,status,reasons,regions)
        self.checkpoint(n,dependency,stage='complete',status=status)
        return status

    def compose_and_qa(self, n, regions, dependency):
        state = self.current(n, dependency)
        fit = read(self.path(n,'fit.json'), {}) if state.get('stage') in ('composed','local_qa') else {}
        if not fit:
            payload = read(self.path(n,'translation.json'))
            if (not isinstance(payload,dict) or not payload.get('blocks') or
                    payload.get('source_sha256') != self.source_hash):
                raise Blocked('Missing saved source-bound native translation; prepare translation before composition')
            fit = fidelity.reinsert_fidelity(self.book, payload, self.glossary, self.style, self.protected,
                    config=self.config, artifact_dir=self.folder, phase='production')
            if fit.get('fit_complete'):
                from .vertical_labels import compose_production_vertical
                fit = compose_production_vertical(self, fit)
                from .native_rotated import compose_saved_rotated
                fit = compose_saved_rotated(self, fit)
                from .candidate_storage import extract_candidate
                # Rotated/vertical proofs follow the extracted candidate hash.
                extract_candidate(fit)
                atomic_json(self.path(n,'fit.json'),fit)
            self.checkpoint(n,dependency,stage='composed')
        if not fit.get('fit_complete'):
            qa = {'page':n,'source_sha256':self.source_hash,'local_fidelity_ok':False,
                  'fit_failures':fit.get('failures', []),'candidate_sha256':None}
            self.finish_qa(n,fit,qa,regions,dependency)
            return
        qa = read(self.path(n,'qa.json'), {}) if state.get('stage') == 'local_qa' else {}
        if not qa:
            qa = fidelity.fidelity_qa(self.book,fit,self.glossary,self.protected,artifact_dir=self.folder,phase='production')
            # Include the approved style guide in textual QA, beyond core checks.
            from .text_quality import page_issues
            with self.glossary.open() as stream:
                terms=list(csv.DictReader(stream))
            with fitz.open(self.book/'source.pdf') as src, fitz.open(fit['output']) as dst:
                # Same source term on a spread = same translation (facing leaf as evidence).
                facing=n+1 if (n-1)%2 else n-1
                spread=read(self.path(facing,'translation.json'),{}).get('blocks',[]) if 1<=facing<=len(src) else []
                issues=page_issues(fit['blocks'],source_page=src[n-1],candidate_page=dst[fit.get('output_page',n-1)],
                                   glossary=terms,style_guide=self.style.read_text(),spread_units=spread)
            qa['text_quality_issues']=issues
            # Same source phrase on other pages, other wording: minor, non-blocking
            # evidence for the auditor (G3 terms are enforced by the validator).
            from .consistency import phrase_consistency_issues
            book=[]
            for path in sorted(self.folder.glob('p*-translation.json')):
                other=read(path,{})
                if isinstance(other,dict) and other.get('page')!=n and other.get('blocks'):
                    book.extend({**b,'page':other['page']} for b in other['blocks'])
            qa['consistency_warnings']=phrase_consistency_issues([{**b,'page':n} for b in fit['blocks']],book,terms)
            qa['local_fidelity_ok']=bool(qa['local_fidelity_ok'] and not issues)
            qa['native_local_fidelity_ok']=bool(qa.get('native_local_fidelity_ok',qa['local_fidelity_ok']) and not issues)
            atomic_json(self.path(n,'qa.json'),qa)
            self.checkpoint(n,dependency,stage='local_qa')
        if not qa.get('vlm_complete'):
            try:
                fonts=read(self.path(n,'fonts.json'), [])
                trusted=[r['chosen']['path'] for r in fonts]+fit.get('extra_trusted_font_files', [])
                pair=safe_vlm.build_safe_pair(self.book,self.book/'source.pdf',fit['output'],n,fit.get('output_page',n-1)+1,
                      protected_terms=self.extras, trusted_text_font_files=trusted)
                judge=safe_vlm.judge_safe_pair(self.book,pair,self.config_path,phase='production')
                qa.update(vlm=judge,vlm_complete=True,vlm_notes=judge['native_vlm'].get('notes',''),
                          ordinal_mask_verification=pair.report['ordinal_mask_verification'])
            except llm.QuotaPaused:
                raise
            except Blocked as exc:
                qa.update(vlm_complete=False,vlm_notes=str(exc))
        self.finish_qa(n,fit,qa,regions,dependency)

    def cost_records(self):
        path=self.work/'costs.jsonl'
        rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        for row in rows:
            if (row.get('backend') in llm.SUBSCRIPTION_BACKENDS and row.get('cost_usd')==0
                    and row.get('status') not in ('cache_hit','block_cache_hit','sentence_cache_hit')):
                row['cost_usd']=None;row['cost_status']='unknown_legacy_zero'
        return rows

    def close_batch(self, record, *, paused=False, stopped=False):
        rows={r['page']:r for r in read(self.work/'manifest.json')['pages']}
        metrics=batch_metrics([rows[n] for n in record['pages']])
        costs=self.cost_records()[record['cost_start']:]
        usage=consumption(costs,self.config['provider']['backend'])
        metrics['g5_pass'] &= not usage['missing_backend_records']
        record.update(metrics=metrics,consumption=usage,closed_at=datetime.now(timezone.utc).isoformat(),
                      status='paused' if paused else 'stopped_repeated_error' if stopped else 'complete' if metrics['g5_pass'] else 'needs_fix')
        atomic_json(self.folder/f'batch-{record["id"]:02d}.json',record)
        return record

    # --- G5 gate --------------------------------------------------------------
    def g5_block_path(self):
        return self.folder/'g5_block.json'

    def g5_decision_path(self, batch):
        return self.folder/f'g5_decision-{batch:02d}.json'

    def g5_decision(self, batch):
        for path in (self.g5_decision_path(batch), self.folder/f'g5_decision-{batch}.json'):
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except ValueError:
                    raise G5Blocked(f'Invalid G5 decision for batch {batch}: {path.name} is not JSON') from None
                return validate_g5_decision(data, batch)
        return None

    def g5_metrics(self, pages, missing_backend_records=0):
        rows = {r['page']:r for r in read(self.work/'manifest.json')['pages']}
        metrics = batch_metrics([rows[n] for n in pages])
        metrics['g5_pass'] &= not missing_backend_records
        return metrics

    def g5_failed(self, metrics):
        return metrics['denominator'] > 0 and not metrics['g5_pass']

    def resolve_g5(self, block, resolution, **details):
        stamp = datetime.now(timezone.utc)
        archived = {**block, 'resolution':resolution, 'resolved_at':stamp.isoformat(), **details}
        atomic_json(self.folder/f'g5_block-resolved-{stamp.strftime("%Y%m%dT%H%M%S%fZ")}.json', archived)
        self.g5_block_path().unlink(missing_ok=True)
        return archived

    def g5_gate(self, selected):
        """Refuse pages outside an unresolved G5 block unless a valid decision exists."""
        block = read(self.g5_block_path())
        if not block:
            return None
        if set(selected) <= set(block['pages']):
            return block
        decision = self.g5_decision(block['batch'])
        if decision is None:
            raise G5Blocked(
                f"G5 failed for batch {block['batch']} (ratio {block['ratio']}, {block['ok']}/{block['in_scope']}); "
                f"recompose pages {block['pages']} or record {self.g5_decision_path(block['batch']).relative_to(self.book)}")
        self.resolve_g5(block, 'decision', decision=decision)
        return None

    def g5_reevaluate(self, block, *, by_batch=None, missing_backend_records=0):
        """Recompute G5 over the blocked batch pages; resolve or refresh the block."""
        metrics = self.g5_metrics(block['pages'], missing_backend_records)
        if not self.g5_failed(metrics):
            path = self.folder/f'batch-{block["batch"]:02d}.json'
            original = read(path)
            if original and original.get('status') == 'needs_fix':
                original.update(metrics=metrics, status='complete', g5_resolved_at=datetime.now(timezone.utc).isoformat())
                atomic_json(path, original)
            self.resolve_g5(block, 'recomposed', resolved_by_batch=by_batch, resolved_metrics=metrics)
            return None
        block.setdefault('reevaluations', []).append({
            'at':datetime.now(timezone.utc).isoformat(), 'by_batch':by_batch, 'ratio':metrics['qa_ok_ratio'],
            'ok':metrics['qa_ok'], 'in_scope':metrics['denominator']})
        block.update(ratio=metrics['qa_ok_ratio'], ok=metrics['qa_ok'], in_scope=metrics['denominator'])
        atomic_json(self.g5_block_path(), block)
        return block

    def g5_after_batch(self, closed, block):
        """Returns the active block after a closed (non-paused) batch, or None."""
        missing = closed.get('consumption', {}).get('missing_backend_records', 0)
        if block and set(closed['pages']) <= set(block['pages']):
            return self.g5_reevaluate(block, by_batch=closed['id'], missing_backend_records=missing)
        metrics = closed['metrics']
        if not self.g5_failed(metrics):
            return None
        block = {'batch':closed['id'], 'pages':closed['pages'], 'ratio':metrics['qa_ok_ratio'],
                 'ok':metrics['qa_ok'], 'in_scope':metrics['denominator'], 'threshold':G5_THRESHOLD,
                 'missing_backend_records':missing, 'batch_status':closed['status'],
                 'review_pages':metrics['review_pages'], 'exit_code':G5_EXIT_CODE,
                 'decision_path':str(self.g5_decision_path(closed['id']).relative_to(self.book)),
                 'created_at':datetime.now(timezone.utc).isoformat()}
        atomic_json(self.g5_block_path(), block)
        return block

    def run(self, *, batch_size=20, pages=None, resume=False, recompose=False):
        if not 1 <= batch_size <= 20:
            raise ValueError('batch-size must be between 1 and 20')
        if recompose and not pages:raise Blocked('--recompose requires explicit pages')
        approval=self.gates()
        manifest=read(self.work/'manifest.json')
        if not manifest or manifest.get('source_sha256') != self.source_hash:
            raise Blocked('A current triage manifest is required')
        with fitz.open(self.book/'source.pdf') as doc:
            if sorted(r['page'] for r in manifest['pages']) != list(range(1,len(doc)+1)):
                raise Blocked('Manifest pages must be complete and unique')
            selected=selected_pages(pages,len(doc))
        block=self.g5_gate(selected)
        dependency=self.dependencies()
        pending=[];skipped=[]
        for n in selected:
            state=self.current(n,dependency)
            if (not recompose and state.get('stage')=='complete' and state.get('status')!='failed'
                    and not state.get('retry_llm')):
                skipped.append(n)
            else:
                pending.append(n)
        previous=sorted(self.folder.glob('batch-*.json'),key=lambda p:read(p)['id'])
        open_batch=next((read(p) for p in reversed(previous) if read(p).get('status') in ('paused','running')),None)
        if open_batch and not resume:
            raise Blocked('Interrupted batch exists; use --resume')
        if open_batch:
            unfinished=[n for n in open_batch['pages'] if self.current(n,dependency).get('stage')!='complete']
            if not set(unfinished)<=set(selected):
                raise Blocked('--resume selection must include unfinished pages of the interrupted batch')
            if not unfinished:
                closed=self.close_batch(open_batch)
                block=self.g5_after_batch(closed,block)
                if block:
                    return {'status':'g5_blocked','batches':[closed],'skipped_current':skipped,'g5_block':block,
                            'not_started':[n for n in pending if n not in block['pages']]}
                if not pending:return {'status':closed['status'],'batches':[closed],'skipped_current':skipped}
                open_batch=None
        if not pending:
            if block:
                block=self.g5_reevaluate(block)
                if block:return {'status':'g5_blocked','batches':[],'skipped_current':skipped,'g5_block':block}
            return {'status':'complete','batches':[], 'skipped_current':skipped}
        llm._check_pause(self.book)
        groups=[]
        if open_batch and resume:
            remaining=[n for n in open_batch['pages'] if n in pending]
            if remaining:
                groups.append((remaining,open_batch))
                pending=[n for n in pending if n not in remaining]
        groups += [(pending[i:i+batch_size],None) for i in range(0,len(pending),batch_size)]
        next_id=max([read(p)['id'] for p in previous] or [0])+1
        batches=[];stopped=False
        for index,(numbers,record) in enumerate(groups):
            record=record or {'id':next_id,'pages':numbers,'cost_start':len(self.cost_records()),
                             'started_at':datetime.now(timezone.utc).isoformat(),'errors':[]}
            next_id=max(next_id,record['id']+1)
            record['status']='running'
            atomic_json(self.folder/f'batch-{record["id"]:02d}.json',record)
            repeated=Counter(e['exception'] for e in record['errors']);stopped=False;native=[];regions={};back_matter=[]
            def failure(n,exc):
                nonlocal stopped
                key=f'{type(exc).__module__}.{type(exc).__qualname__}:{exc}'
                # Validation failures (including an unavailable escalation model)
                # are per-unit outcomes already routed to review by llm.py: they
                # never count toward the three-repeated-errors stop.
                counted=not isinstance(exc,llm.ValidationFailure)
                if counted:
                    repeated[key]+=1;stopped=repeated[key]>=3
                record['errors'].append({'page':n,'exception':key,'repetitions':repeated[key],'counted':counted})
                if n is not None and isinstance(exc,LLM_REVIEW_ERRORS):
                    kind='llm_validation' if isinstance(exc,llm.ValidationFailure) else 'llm_backend'
                    self.update(n,status='review',production_error=key)
                    self.queue(n,'review',[f'{kind}: {type(exc).__name__}: {str(exc)[:500]}'])
                    self.checkpoint(n,dependency,stage='complete',status='review',retry_llm=True)
                elif n is not None:
                    self.update(n,status='failed',production_error=key)
                    self.queue(n,'failed',[key])
                    self.checkpoint(n,dependency,stage='complete',status='failed')
            try:
                llm._check_pause(self.book)
                rows={r['page']:r for r in read(self.work/'manifest.json')['pages']}
                for n in numbers:
                    try:
                        previous=self.current(n,dependency)
                        if previous.get('status')=='failed' or previous.get('retry_llm'):
                            self.path(n,'state.json').unlink(missing_ok=True)
                        if not recompose and self.reuse_pilot(n,approval,dependency):
                            continue
                        from .book_index import production_role
                        if production_role(self,n):
                            back_matter.append(n);continue  # index/glossary: book_index route
                        regions[n]=self.inventory(rows[n])
                        if any(r['reason']=='route_C_or_no_native_text' for r in regions[n]):
                            qa={'page':n,'source_sha256':self.source_hash,'local_fidelity_ok':False,
                                'candidate_sha256':None,'deferred_regions':regions[n], 'production_status':'deferred_raster'}
                            atomic_json(self.path(n,'qa.json'),qa)
                            self.update(n,status='deferred_raster',deferred_regions=regions[n])
                            self.queue(n,'deferred_raster',['route_C_or_no_native_text'],regions[n])
                            self.checkpoint(n,dependency,stage='complete',status='deferred_raster')
                        else:
                            if recompose:
                                payload=read(self.path(n,'translation.json'),{})
                                if not payload.get('blocks') or payload.get('source_sha256')!=self.source_hash:
                                    raise Blocked('Recomposition requires saved source-bound native translation; prepare missing translation first')
                                # Reinsert validates every source span and target before publishing.
                                self.checkpoint(n,dependency,stage='translated')
                            native.append(n)
                    except llm.QuotaPaused:
                        raise
                    except Exception as exc:
                        failure(n,exc)
                        if stopped:break
                if not stopped:
                    untranslated=[n for n in native if not self.current(n,dependency)]
                    if untranslated:
                        try:
                            adapters.fidelity_translate(self.book,self.glossary,self.style,self.protected,
                                ','.join(map(str,untranslated)),config_path=self.config_path,reinsert_pages=False,phase='production')
                            for n in untranslated:self.checkpoint(n,dependency,stage='translated')
                        except llm.QuotaPaused:
                            raise
                        except Exception as exc:
                            failure(None,exc)
                            # Isolate failures without repeatedly retrying the
                            # whole batch. Cache/memory retains successful calls.
                            for n in untranslated:
                                try:
                                    adapters.fidelity_translate(self.book,self.glossary,self.style,self.protected,str(n),
                                        config_path=self.config_path,reinsert_pages=False,phase='production')
                                    self.checkpoint(n,dependency,stage='translated')
                                except llm.QuotaPaused:
                                    raise
                                except Exception as individual:
                                    failure(n,individual)
                                    if stopped:break
                    if not stopped:
                        for n in native:
                            state=read(self.path(n,'state.json'),{})
                            if state.get('dependency')==dependency and (state.get('status')=='failed' or state.get('retry_llm')):continue
                            try:self.compose_and_qa(n,regions[n],dependency)
                            except llm.QuotaPaused:raise
                            except Exception as exc:
                                failure(n,exc)
                                if stopped:break
                if not stopped and back_matter:
                    from .book_index import produce_back_matter
                    for n,exc in produce_back_matter(self,back_matter,dependency):
                        failure(n,exc)
                        if stopped:break
            except llm.QuotaPaused as exc:
                remaining=[n for n in numbers if self.current(n,dependency).get('stage')!='complete']
                with book_lock(self.book):
                    state=read(self.work/'manifest.json')
                    for row in state['pages']:
                        if row['page'] in remaining:row['status']='pending'
                    atomic_json(self.work/'manifest.json',state)
                    pause=read(self.work/'quota_pause.json',{})
                    pause.update(pages=remaining,exit_code=75,reason=str(exc))
                    pause.setdefault('resume_after_unix',time.time()+30)
                    atomic_json(self.work/'quota_pause.json',pause)
                self.close_batch(record,paused=True)
                raise
            closed=self.close_batch(record,stopped=stopped);batches.append(closed)
            block=self.g5_after_batch(closed,block)
            if stopped or block:
                # G5 is blocking: the next batch is never started.
                not_started=[n for later,_ in groups[index+1:] for n in later]
                status='stopped_repeated_error' if stopped else 'g5_blocked'
                return {'status':status,'batches':batches,'skipped_current':skipped,'g5_block':block,'not_started':not_started}
        return {'status':'needs_fix' if any(b['status']=='needs_fix' for b in batches) else 'complete',
                'batches':batches,'skipped_current':skipped}


def preview_production(book, *, pages=None, batch_size=20, recompose=False):
    """Read-only inventory: no runner construction, locks, gates or inference."""
    if not 1<=batch_size<=20:raise ValueError('batch-size must be between 1 and 20')
    if recompose and not pages:raise Blocked('--recompose requires explicit pages')
    book=Path(book).resolve()
    with fitz.open(book/'source.pdf') as source:
        selected=selected_pages(pages,len(source));rows=[]
        for n in selected:
            payload=read(book/f'work/production/p{n:04d}-translation.json',{})
            rows.append({'page':n,'native_text':bool(source[n-1].get_text().strip()),
                         'saved_translation':bool(payload.get('blocks'))})
    return {'dry_run':True,'llm_calls':0,'writes':0,'pages':rows,
            'g5_block':read(book/'work/production/g5_block.json'),
            'missing_saved_translation':[r['page'] for r in rows if r['native_text'] and not r['saved_translation']],
            'note':'Inventory only; does not assert gates, fit, QA or production readiness.'}


def produce(book, *, batch_size=20, pages=None, resume=False, config_path=None, recompose=False):
    with writer_lock(book):
        return Production(book,config_path).run(batch_size=batch_size,pages=pages,resume=resume,recompose=recompose)


def record_visual_review(book, page, reviewer, candidate_sha256, notes, *, config_path=None):
    if not reviewer.strip() or not notes.strip():
        raise ValueError('Reviewer and notes are required')
    with writer_lock(book):
        runner=Production(book,config_path);runner.gates()
        fit=read(runner.path(page,'fit.json'),{})
        if (not fit.get('fit_complete') or fit.get('output_sha256') != candidate_sha256 or
                sha256(fit['output']) != candidate_sha256):
            raise Blocked('Review must bind to the current complete candidate')
        qa=read(runner.path(page,'qa.json'),{})
        if qa.get('source_sha256')!=runner.source_hash or qa.get('candidate_sha256')!=candidate_sha256:
            raise Blocked('Review QA hashes are stale')
        dependency=runner.dependencies()
        if not runner.current(page,dependency):
            raise Blocked('Run current local QA before recording a visual review')
        record={'reviewer':reviewer,'candidate_sha256':candidate_sha256,'notes':notes,
                'source_sha256':runner.source_hash,'at':datetime.now(timezone.utc).isoformat(),
                'scope':'visual graphics review and VLM false positives; cannot waive local QA'}
        reviews=read(runner.folder/'visual_reviews.json',{})
        reviews.setdefault(str(page),{})[candidate_sha256]=record
        atomic_json(runner.folder/'visual_reviews.json',reviews)
        runner.finish_qa(page,fit,qa,qa.get('deferred_regions',[]),dependency)
        rows={r['page']:r for r in read(runner.work/'manifest.json')['pages']}
        for path in runner.folder.glob('batch-*.json'):
            batch=read(path)
            if page in batch['pages'] and batch['status'] in ('complete','needs_fix'):
                batch['metrics']=batch_metrics([rows[n] for n in batch['pages']])
                batch['metrics']['g5_pass'] &= not batch.get('consumption',{}).get('missing_backend_records',0)
                batch['status']='complete' if batch['metrics']['g5_pass'] else 'needs_fix'
                batch['review_updated_at']=record['at']
                atomic_json(path,batch)
        return record


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)  # same mode the former write_text produced
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _percent(part, whole):
    return part/whole*100 if whole else None


def _fmt_percent(value):
    return 'n/d' if value is None else f'{value:.1f}'.replace('.', ',')+' %'


def auditor_verdicts(audit_dir, candidates):
    """Newest book-auditor verdict per page bound to its current candidate hash.

    ``candidates`` maps page -> current candidate sha256 (pages without one are
    not auditable). Audits are ordered by their ``date`` field and then by file
    mtime; unreadable or page-less files (in progress, superseded) are skipped.
    """
    audits, skipped = [], []
    for path in sorted(Path(audit_dir).glob('batch-*.json')):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            skipped.append(path.name)
            continue
        if not isinstance(data, dict) or not isinstance(data.get('pages'), list):
            skipped.append(path.name)
            continue
        audits.append(((str(data.get('date') or ''), path.stat().st_mtime_ns, path.name), data.get('batch') or path.stem, data))
    chosen = {}
    for _, batch, data in sorted(audits, key=lambda item: item[0]):
        for entry in data['pages']:
            if not isinstance(entry, dict):
                continue
            n, sha = entry.get('page'), entry.get('candidate_sha256')
            if sha and candidates.get(n) == sha:
                chosen[n] = (batch, entry)
    defects = []
    for n in sorted(chosen):
        batch, entry = chosen[n]
        if entry.get('verdict') != 'defect':
            continue
        findings = [f for f in entry.get('findings', []) if isinstance(f, dict)]
        blocking = [f for f in findings if f.get('severity') == 'blocking'] or findings
        brief = lambda text: text if len(text) <= 140 else text[:139]+'…'
        defects.append({'page':n, 'batch':batch, 'findings':[{'code':f.get('code', '?'), 'evidence':brief(str(f.get('evidence', '')))}
                                                              for f in blocking]})
    verdicts = {str(n):chosen[n][1].get('verdict') for n in sorted(chosen)}
    audited = sum(v in ('ok', 'defect') for v in verdicts.values())
    ok = sum(v == 'ok' for v in verdicts.values())
    return {'verdicts':verdicts, 'audited':audited, 'ok':ok, 'percent':_percent(ok, audited),
            'deferred':sum(v == 'deferred' for v in verdicts.values()),
            'not_audited_pages':sorted(n for n in candidates if n not in chosen),
            'pages_without_candidate':None, 'defects':defects, 'skipped_files':skipped}


def assemble_final(book, *, config_path=None, include_review=False):
    """Final PDF, side-by-side proofs, provenance and quality report.

    Default: translated only with current QA ok, justified partial_deferred or a
    bound visual review. ``include_review`` also uses the current single-page
    candidate (fit_complete and matching sha256) of review pages; pages without
    candidate (deferred_raster, failed, missing/changed bytes) keep the original.
    """
    from .pilot_assembly import assemble
    with writer_lock(book):
        runner=Production(book,config_path);runner.gates()
        dependency=runner.dependencies();manifest=read(runner.work/'manifest.json')
        rows=[];provenance=[]
        with fitz.open(runner.book/'source.pdf') as doc:
            count=len(doc)
        if not manifest or manifest.get('source_sha256')!=runner.source_hash:
            raise Blocked('Assembly requires a current manifest')
        statuses={r['page']:r.get('status') for r in manifest['pages']}
        queue={}
        for item in read(runner.work/'review_queue.json',[]):
            if isinstance(item,dict) and item.get('phase')=='production':
                queue[item.get('page')]=list(item.get('reasons') or ([item['reason']] if item.get('reason') else []))
        candidates={}
        labels={'translated_ok':'Traduccion revisada','translated_partial_deferred':'Traduccion revisada - arte diferido',
                'translated_unreviewed':'Traduccion sin revisar - en review','original':'Original conservado - pendiente'}
        for n in range(1,count+1):
            state=runner.current(n,dependency);qa=read(runner.path(n,'qa.json'),{});fit=read(runner.path(n,'fit.json'),{})
            status=statuses.get(n) or state.get('status')
            output=Path(fit['output']) if fit.get('output') else None
            has_candidate=bool(fit.get('fit_complete') and fit.get('output_sha256') and output and output.is_file()
                               and status not in ('failed','deferred_raster') and sha256(output)==fit['output_sha256'])
            if has_candidate:
                candidates[n]=fit['output_sha256']
            reviewed=runner.review_for(n,fit.get('output_sha256'))
            use=bool(state and fit.get('fit_complete') and qa.get('local_fidelity_ok') and
                     ((qa.get('production_status')=='done_qa_ok' and not qa.get('deferred_regions'))
                      or (qa.get('production_status')=='partial_deferred' and qa.get('native_local_fidelity_ok'))
                      or reviewed))
            extra={}
            if use:
                kind='translated_partial_deferred' if qa.get('production_status')=='partial_deferred' else 'translated_ok'
                reason='current QA or visual review'
            elif include_review and has_candidate:
                kind='translated_unreviewed';reason='current candidate without QA ok (review)'
                extra={'dependency_current':bool(state),'qa_bound_to_candidate':qa.get('candidate_sha256')==fit['output_sha256']}
            else:
                kind='original'
                reason=(status if status in ('deferred_raster','failed') else
                        'not_qa_ok_without_include_review' if has_candidate else 'no_current_candidate')
            translated=kind!='original'
            candidate=output if translated else runner.book/'source.pdf'
            index=fit.get('output_page',n-1) if translated else n-1
            rows.append({'source_page':n,'candidate':candidate,'index':index,'label':labels[kind]})
            provenance.append({'page':n,'origin':'translated' if translated else 'original','provenance':kind,
                               'reason':reason,'status':status,'review_reasons':queue.get(n,[]),**extra,
                               'candidate_sha256':sha256(candidate),'candidate':str(candidate),'index':index})
        out=runner.book/'out';out.mkdir(exist_ok=True)
        translated=out/f'{runner.book.name}_es.pdf';comparison=out/f'{runner.book.name}_comparativa.pdf'
        proofs=assemble(runner.book/'source.pdf',rows,translated,comparison)
        usage=consumption(runner.cost_records(),runner.config['provider']['backend'])
        done=sum(s=='done_qa_ok' for s in statuses.values());partial=sum(s=='partial_deferred' for s in statuses.values())
        deferred=sum(statuses.get(n)=='deferred_raster' for n in range(1,count+1))
        n_translated=sum(r['origin']=='translated' for r in provenance)
        auditor=auditor_verdicts(runner.work/'audit',candidates)
        auditor['pages_without_candidate']=count-len(candidates)
        quality={'translated':{'pages':n_translated,'total':count,'percent':_percent(n_translated,count)},
                 'local_qa':{'done_qa_ok':done,'partial_deferred':partial,'ok':done+partial,'in_scope':count-deferred,
                             'deferred_raster':deferred,'percent':_percent(done+partial,count-deferred)},
                 'auditor':auditor}
        report={'source_sha256':runner.source_hash,'include_review':include_review,'pages':provenance,'proofs':proofs,
                'consumption':usage,'quality':quality,
                'translated_pdf':str(translated),'comparison_pdf':str(comparison),
                'original_pages':[r['page'] for r in provenance if r['origin']=='original'],
                'unreviewed_pages':[r['page'] for r in provenance if r['provenance']=='translated_unreviewed'],
                'complete_translation':all(r['origin']=='translated' for r in provenance),
                'output_sha256':sha256(translated),'comparison_sha256':sha256(comparison)}
        atomic_json(out/f'{runner.book.name}_provenance.json',report)
        cost=usage['cost_usd']
        summary=(f"Costo USD conocido: {cost['known_sum']:.6f}; registros con costo desconocido: {cost['unknown_records']}. "
                 'Los ceros históricos de backends de suscripción (codex_sdk, codex_exec, claude_cli) se normalizan como desconocidos; costs.jsonl permanece sin reescritura.')
        local=quality['local_qa']
        header=[f"- **Porcentaje de páginas traducidas:** {_fmt_percent(quality['translated']['percent'])} ({n_translated}/{count}).",
                f"- **Porcentaje de acierto según la QA local:** {_fmt_percent(local['percent'])} ({local['ok']}/{local['in_scope']} "
                f"páginas en alcance: {done} done_qa_ok + {partial} partial_deferred; {deferred} deferred_raster fuera de alcance).",
                f"- **Porcentaje de acierto según el book-auditor:** {_fmt_percent(auditor['percent'])} ({auditor['ok']}/{auditor['audited']} "
                f"páginas auditadas con su candidato actual); {len(auditor['not_audited_pages'])} páginas con candidato sin auditar; "
                f"{auditor['deferred']} diferidas por el auditor; {auditor['pages_without_candidate']} páginas sin candidato.",
                f"- **Páginas con defectos del auditor:** {len(auditor['defects'])}."]
        header+=[f"  - p{d['page']} ({d['batch']}): "+'; '.join(f"{f['code']} — {f['evidence']}" for f in d['findings'])
                 for d in auditor['defects']]
        by_kind=Counter(r['provenance'] for r in provenance)
        details=[f"Modo: {'--include-review' if include_review else 'por defecto (solo QA ok, partial_deferred o revisión visual)'}. "
                 f"Páginas: {count}. Traducidas: {n_translated} (translated_ok {by_kind['translated_ok']}, "
                 f"translated_partial_deferred {by_kind['translated_partial_deferred']}, translated_unreviewed {by_kind['translated_unreviewed']}). "
                 f"Original conservado: {by_kind['original']}."]
        if auditor['skipped_files']:
            details.append('Auditorías ignoradas (ilegibles o sin páginas): '+', '.join(auditor['skipped_files'])+'.')
        unreviewed=[f"- p{r['page']}: "+('; '.join(r['review_reasons']) or 'sin motivo registrado') for r in provenance
                    if r['provenance']=='translated_unreviewed']
        originals=[f"- p{r['page']}: {r['reason']}"+(f" ({'; '.join(r['review_reasons'])})" if r['review_reasons'] else '')
                   for r in provenance if r['origin']=='original']
        text=(f"# Informe de ensamblado — {runner.book.name}\n\n"+'\n'.join(header)+'\n\n'+'\n\n'.join(details)
              +f"\n\n{summary}\n\nPDF traducido: {translated.stat().st_size} bytes. Comparativa: {comparison.stat().st_size} bytes.\n")
        if unreviewed:
            text+='\n## Traducidas sin revisar (translated_unreviewed)\n\n'+'\n'.join(unreviewed)+'\n'
        if originals:
            text+='\n## Original conservado\n\n'+'\n'.join(originals)+'\n'
        atomic_text(out/f'{runner.book.name}_informe.md',text)
        return report
