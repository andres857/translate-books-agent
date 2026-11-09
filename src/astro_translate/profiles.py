"""Source-bound book policy and explicit compositor hooks.

Unknown PDFs use conservative defaults. A present but stale profile blocks; it
never silently falls back. Modules live in the book's work directory and are
verified before import. No global function replacement or process-wide context.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import tomllib
from functools import lru_cache
from .core import Blocked, sha256


def units_digest(units):
    """Content binding of a page's units: ids, sources and spans, in id order.

    Routing order is not content: production stores model units before local
    (date/preserved) ones, a re-extraction yields id order. Both bind equally.
    """
    # A joined correction (e.g. a display quote) binds through its unchanged members.
    flat=[m for u in units for m in (u.get('source_members') or [u])]
    rows=sorted(({k:u[k] for k in ('id','source','spans')} for u in flat),key=lambda u:u['id'])
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


@lru_cache(maxsize=64)
def _digest_file(path, size, mtime, ctime):
    return sha256(path)


def file_digest(path):
    path=Path(path).resolve();s=path.stat()
    return _digest_file(str(path),s.st_size,s.st_mtime_ns,s.st_ctime_ns)


@lru_cache(maxsize=32)
def _module(path, digest):
    name='_astro_book_'+digest
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class BookProfile:
    def __init__(self,book=None,data=None):
        self.book=Path(book) if book else None
        self.data=data or {};self.source_sha256=self.data.get('source_sha256')
        self.hooks=None
        module=self.data.get('hooks')
        if module:
            path=self.checked_file(module)
            self.hooks=_module(str(path),file_digest(path))

    def checked_file(self,record):
        path=(self.book/record['path']).resolve()
        if not path.is_relative_to((self.book/'work').resolve()):raise Blocked('Profile dependency must be within book/work')
        if not path.is_file() or file_digest(path)!=record['sha256']:raise Blocked('Profile dependency hash changed: '+str(path))
        return path

    def page_policy(self,page):
        defaults = dict(run_in=True, callouts=True, protected_rows=True, vertical=True, split_ordinal=True) if self.data.get('composition') == 'patterns' else {}
        override = self.data.get('pages',{}).get(str(page.number+1),{})
        if override.get('policy_sha256'):
            content = {k:v for k,v in override.items() if k != 'policy_sha256'}
            actual = hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if actual != override['policy_sha256']:raise Blocked('Profile policy override hash changed')
        return {**defaults,**self.data.get('patterns',{}),**override}

    def extract(self,page,units,drawings):
        if self.hooks and hasattr(self.hooks,'extract'):return self.hooks.extract(page,units,drawings)
        if self.page_policy(page).get('split_ordinal'):
            from .fidelity import _make_unit
            return [child for u in units for child in ([_make_unit([s],u['source_block'],False,drawings) for s in u['spans']] if u['source']=='THE SIGN OF THE ZODIAC' and u['size']==11 and len(u['spans'])==2 else [u])]
        return units

    def compose(self,page,units):
        policy=self.page_policy(page)
        if policy.get('units_sha256') and units_digest(units)!=policy['units_sha256']:
            raise Blocked('Profile unit source hash changed')
        if self.hooks and hasattr(self.hooks,'compose'):
            return self.hooks.compose(self,page,units)
        from copy import deepcopy
        units=deepcopy(units)
        for u in units:
            def correct(style):
                style=dict(style);name=style['font'].lower()
                if any(x in name for x in ('dinnext','frutiger')):style.update(serif=False,force_fallback=True)
                if 'heavy' in name:style.update(weight=900,bold=True,force_fallback=True)
                return style
            u['style']=correct(u['style'])
            u['target_runs']=[{**r,'style':correct(r['style'])} for r in u['target_runs']]
        return units

    def finalize(self,page,units):
        if self.data.get('composition')!='patterns':return units
        from .native_ordinals import adapt_headings
        from .run_in_layout import finalize_paragraphs
        from .chart_callouts import finalize_callouts
        from .contents_layout import prepare_contents
        from .caption_growth import prepare_captions
        from .protected_labels import partition_rows
        policy=self.page_policy(page)
        if self.data.get('editorial',{}).get('ordinals'):
            from .secrets_ordinal_policy import SECRETS_POLICY
            if self.source_sha256!=SECRETS_POLICY.source_sha256:raise Blocked('Ordinal policy is not bound to this book source hash')
            units=adapt_headings(page,units,policy=SECRETS_POLICY)
            from .native_ordinals import parenthesized_ordinals
            units=parenthesized_ordinals(units)
        from .structured_layout import prepare_saved_field_rows
        units=prepare_saved_field_rows(page,units)
        units=finalize_paragraphs(page,units)
        from .punctuation_layout import attach_terminal_punctuation
        units=attach_terminal_punctuation(page,units)
        if policy.get('callouts'):units=finalize_callouts(page,units)
        units=prepare_contents(page,units)
        if policy.get('protected_rows'):units=partition_rows(page,units)
        return prepare_captions(page,units)

    def candidates(self,page,unit,units,guards,default):
        if self.data.get('composition')!='patterns':return default(page,unit,units,guards)
        from .composition_patterns import shifted_candidates,exact_vector_mask
        from .contents_layout import candidate_boxes
        from .chart_callouts import callout_candidates
        from .run_in_layout import paragraph_candidates
        if unit.get('contents_column'):return candidate_boxes(page,unit,units,guards)
        if unit.get('chart_callout'):return callout_candidates(page,unit,units,guards,exact_vector_mask(page))
        if unit.get('run_in_heading'):return paragraph_candidates(page,unit,units,guards)
        return default(page,unit,units,guards)+(shifted_candidates(page,unit,units,guards) if not (unit.get('protected_label') or unit.get('caption_growth')) else [])

    def vector_mask(self,page,dpi,default):
        if self.data.get('composition')=='patterns':
            from .composition_patterns import exact_vector_mask
            return exact_vector_mask(page,dpi)
        return default(page,dpi)

    def redactions(self,page,unit,default):
        if unit.get('precise_edit_rects'):
            import pymupdf as fitz
            return [fitz.Rect(r) for r in unit['precise_edit_rects']]
        try:return default(page,unit)
        except Blocked:
            if self.data.get('composition')!='patterns':raise
            from .composition_patterns import precise_redaction_rects
            return precise_redaction_rects(page,unit)

    def art_exception(self,number):
        return self.data.get('pages',{}).get(str(number),{}).get('art_exception')

    def language_exceptions(self,source,candidate):
        """Exact, source-identical phrases opted into by this source-bound book."""
        import re
        findings=[]
        for rule in self.data.get('language_exceptions',[]):
            if source.number+1 not in rule['pages']:continue
            if rule.get('context')!='credits':raise Blocked('Unsupported language exception context')
            before=source.get_text();after=candidate.get_text()
            roles=re.findall(r'\b(?:editor|designer|publisher|copyright|art director|publishing|ISBN)\b',before,re.I)
            if len(roles)<4:continue
            text=rule['text']
            if not text.strip():raise Blocked('Empty profile language exception')
            pattern=r'(?<!\w)'+re.escape(text)+r'(?!\w)'
            if re.search(pattern,before) and re.search(pattern,after):
                findings.append({'code':rule['code'],'text':text,'source_identical':True,
                                 'scope':'book_profile_credits','page':source.number+1,
                                 'source_sha256':self.source_sha256})
        return findings


def load_profile(book):
    book=Path(book).resolve()
    path=book/'work/profile.toml'
    if not path.exists():path=book/'profile.toml'
    if not path.exists():return BookProfile(book)
    data=tomllib.loads(path.read_text())
    if data.get('version')!=1:raise Blocked('Unsupported book profile version')
    if not (book/'source.pdf').is_file() or file_digest(book/'source.pdf')!=data.get('source_sha256'):
        raise Blocked('Profile source PDF hash mismatch')
    return BookProfile(book,data)


def profile_for_page(page):
    doc=getattr(page,'parent',None);name=getattr(doc,'name',None)
    if name:
        path=Path(name).resolve()
        if path.name=='source.pdf':return load_profile(path.parent)
    return BookProfile()
