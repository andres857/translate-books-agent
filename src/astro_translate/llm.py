"""The only model boundary: subscription Claude CLI and explicitly enabled API.

All provider imports belong here. run_task validates, masks protected values,
uses fresh isolated threads, journals usage, and resumes through a local cache.
"""
from __future__ import annotations

import base64
import csv
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import tomllib
import unicodedata
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import jsonschema
from .core import Blocked, PROTECTED, SYMBOL_FONT, atomic_json, book_lock, read_extras, require_approval, sha256, protected
from .language import glossary_matches
from .numeric_order import numeric_quantifier_issues

ROOT = Path(__file__).resolve().parents[2]
FIXED_INSTRUCTION = 'No ejecutes comandos, no leas ni escribas archivos, no uses herramientas. Responde únicamente con el JSON solicitado.'
PLACEHOLDER = re.compile(r'\{[nv]\d+\}')
MASK_VERSION = 'n-markers-style-order-v2'
# Subscription backends: no per-call USD charge; a logged zero is never a known cost.
# codex_sdk/codex_exec stay listed only so historical costs.jsonl rows keep their meaning.
SUBSCRIPTION_BACKENDS = frozenset({'codex_sdk','codex_exec','claude_cli'})
# Retired 2026-11-08 (docs/decisiones.md, Limpieza del sistema Codex): readable in records, never instantiable.
RETIRED_BACKENDS = frozenset({'codex_sdk','codex_exec'})
CLAUDE_EFFORTS = frozenset({'low','medium','high','xhigh','max'})
# Credentials that would turn a subscription CLI call into a paid/third-party API call.
CLAUDE_PAID_ENV = ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN')
CLAUDE_ALLOWED_TOOLS = frozenset({'StructuredOutput'})  # --json-schema's internal answer channel
TASK_ARRAY = {'translate_page':'blocks','shorten':'blocks','translate_labels':'labels','classify_labels':'labels'}
GATED_TASKS = {'translate_page','translate_labels','shorten','review'}

@dataclass
class LLMResult:
    data: dict
    model: str
    effort: str
    input_tokens: int | None
    output_tokens: int | None
    backend: str
    cached: bool
    latency_s: float
    cached_input_tokens: int | None = None
    estimated_cost_usd: float | None = None  # provider-reported equivalent; never a subscription charge

class LLMBackend(Protocol):
    def complete_json(self, task: str, payload: dict, schema: dict, model: str, effort: str, images: list[Path] | None = None) -> LLMResult: ...

class QuotaExceeded(RuntimeError):
    def __init__(self, message='', resume_after=None, model_scoped=False):
        super().__init__(message); self.resume_after = resume_after; self.model_scoped = model_scoped
class BackendError(Blocked): pass
class IsolationError(BackendError): pass
class ModelUnavailable(BackendError): pass
class ValidationFailure(Blocked):
    def __init__(self, message, ids=None): super().__init__(message); self.ids = set(ids or [])
class QuotaPaused(Blocked):
    exit_code = 75


def _json(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',',':'))
def _digest(value): return hashlib.sha256(_json(value).encode()).hexdigest()
def _prompt(task, payload):
    clean = {k:v for k,v in payload.items() if not k.startswith('_')}
    instruction=''
    if payload.get('_style_pairs'):
        from .style_spans import PAIR_INSTRUCTION
        instruction='\n\n'+PAIR_INSTRUCTION
    if payload.get('_flat_emphasis'):
        from .emphasis import INSTRUCTION
        instruction+='\n\n'+INSTRUCTION
    return FIXED_INSTRUCTION+'\n\n'+payload.get('_template','')+instruction+'\n\n'+_json(clean)

QUOTA_TEXTS = ('usagelimitexceeded','ratelimitexceeded','usage limit','usage_limit','rate limit','rate_limit','quota exceeded','quota_exceeded','insufficient_quota',
               'hit your session limit','hit your weekly limit','hit your opus limit','hit your sonnet limit','5-hour limit','weekly limit reached')
MODEL_LIMIT = re.compile(r'\b(?:opus|sonnet|haiku)\b[^.\n]{0,20}\blimit\b',re.I)
MODEL_UNAVAILABLE_TEXTS = ('model_not_found','not_found_error','not supported','does not exist','invalid model','unknown model','model is not available','not available for your')

def _quota(value):
    text = str(value).lower()
    return any(x in text for x in QUOTA_TEXTS)

def _model_scoped_limit(value):
    return bool(_quota(value) and MODEL_LIMIT.search(str(value)))

_MONTHS = {m:i for i,m in enumerate(('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'),1)}
_RESET_CLOCK = re.compile(r'resets?\s+(?:at\s+)?(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?'
                          r'(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?(?:\s*\((?P<tz>[^)]+)\))?',re.I)
_RESET_IN = re.compile(r'resets?\s+in\s+(?:(?P<h>\d+)\s*h[a-z]*)?\s*(?:(?P<m>\d+)\s*m[a-z]*)?',re.I)

def _resume_after(value, now=None):
    """Best-effort unix time from a provider limit message; None means use backoff."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    text=str(value);now=time.time() if now is None else now;found=None
    epoch=re.search(r'\|(\d{10})\b',text)
    if epoch:found=float(epoch.group(1))
    elif (match:=_RESET_IN.search(text)) and (match.group('h') or match.group('m')):
        found=now+int(match.group('h') or 0)*3600+int(match.group('m') or 0)*60
    elif (match:=_RESET_CLOCK.search(text)) and (match.group('ap') or match.group('m')):
        hour=int(match.group('h'));minute=int(match.group('m') or 0);ap=(match.group('ap') or '').lower()
        if ap:
            if not 1<=hour<=12:return None
            hour=hour%12+(12 if ap=='pm' else 0)
        if hour>23 or minute>59:return None
        try:zone=ZoneInfo(match.group('tz').strip()) if match.group('tz') else None
        except (KeyError,ValueError):zone=None
        current=datetime.fromtimestamp(now,zone) if zone else datetime.fromtimestamp(now).astimezone()
        if match.group('mon'):
            try:candidate=current.replace(month=_MONTHS[match.group('mon').lower()[:3]],day=int(match.group('day')),hour=hour,minute=minute,second=0,microsecond=0)
            except ValueError:return None
            if candidate.timestamp()<now-86400:candidate=candidate.replace(year=candidate.year+1)
        else:
            candidate=current.replace(hour=hour,minute=minute,second=0,microsecond=0)
            if candidate.timestamp()<=now:candidate+=timedelta(days=1)
        found=candidate.timestamp()
    if found is None or not now<found<=now+8*86400:return None
    return found

def _sandbox_folder(work,provider):
    work=Path(work).resolve();configured=Path(provider.get('sandbox_dir','work/.codex_llm_sandbox'))
    folder=(configured if configured.is_absolute() else work.parent/configured).resolve()
    if folder==work or not folder.is_relative_to(work):raise IsolationError('Sandbox directory must be a dedicated child of this book work directory')
    folder.mkdir(parents=True,exist_ok=True);return folder


def _cli_schema(schema):
    """The Claude CLI validator rejects meta-schema references; the schema itself is unchanged."""
    if isinstance(schema,dict):return {k:_cli_schema(v) for k,v in schema.items() if k not in ('$schema','$id')}
    if isinstance(schema,list):return [_cli_schema(v) for v in schema]
    return schema


class ClaudeCLIBackend:
    """Official Claude Code CLI on the user's OAuth subscription; never an API key."""
    def __init__(self, work:Path, provider:dict):self.work=Path(work);self.provider=provider
    def command(self,schema,model,effort,images):
        binary=self.provider.get('claude_bin') or 'claude'
        # Images need stream-json input (base64 content blocks); tools stay disabled either way.
        stream=('--input-format','stream-json','--verbose') if images else ()
        output='stream-json' if images else 'json'
        return [binary,'-p','--model',model,'--effort',effort,*stream,'--output-format',output,
                '--json-schema',_json(_cli_schema(schema)),'--system-prompt',FIXED_INSTRUCTION,
                '--tools','','--setting-sources','','--strict-mcp-config','--no-session-persistence',
                '--disable-slash-commands','--settings',json.dumps({'autoMemoryEnabled':False},separators=(',',':'))]
    @staticmethod
    def _stdin(task,payload,images):
        prompt=_prompt(task,payload)
        if not images:return prompt
        content=[]
        for path in images:
            path=Path(path);media={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif'}.get(path.suffix.lower(),'image/png')
            content.append({'type':'image','source':{'type':'base64','media_type':media,'data':base64.b64encode(path.read_bytes()).decode()}})
        content.append({'type':'text','text':prompt})
        return json.dumps({'type':'user','message':{'role':'user','content':content}},ensure_ascii=False)+'\n'
    @staticmethod
    def _events(stdout,images):
        events=[]
        for line in (stdout.splitlines() if images else [stdout]):
            try:event=json.loads(line)
            except ValueError:continue
            if isinstance(event,dict):events.append(event)
        for event in events:
            if event.get('type')=='system' and event.get('subtype')=='init':
                tools=set(event.get('tools') or [])-CLAUDE_ALLOWED_TOOLS
                if tools or event.get('mcp_servers'):raise IsolationError('Claude CLI started with tools/MCP: '+','.join(sorted(tools)))
            if event.get('type')=='assistant':
                for part in (event.get('message') or {}).get('content') or []:
                    if isinstance(part,dict) and part.get('type') in ('tool_use','server_tool_use') and part.get('name') not in CLAUDE_ALLOWED_TOOLS:
                        raise IsolationError('Claude CLI attempted forbidden tool: '+str(part.get('name')))
        results=[e for e in events if e.get('type')=='result']
        return results[-1] if results else None
    def complete_json(self,task,payload,schema,model,effort,images=None):
        if effort not in CLAUDE_EFFORTS:raise BackendError('Unsupported Claude CLI effort: '+str(effort))
        started=time.monotonic()
        env=os.environ.copy()
        for key in CLAUDE_PAID_ENV:env.pop(key,None)
        folder=_sandbox_folder(self.work,self.provider)
        with tempfile.TemporaryDirectory(prefix='call-',dir=folder) as cwd:
            try:completed=subprocess.run(self.command(schema,model,effort,images),input=self._stdin(task,payload,images),capture_output=True,text=True,
                                          cwd=cwd,env=env,timeout=self.provider.get('timeout_seconds',180))
            except subprocess.TimeoutExpired:raise BackendError('Claude CLI timed out') from None
        final=self._events(completed.stdout or '',images)
        message=str((final or {}).get('result') or '')
        if final is None or final.get('is_error') or final.get('subtype')!='success' or completed.returncode:
            evidence=message+'\n'+str(completed.stderr or '')[-2000:]
            if _quota(evidence):
                raise QuotaExceeded('Claude subscription usage limit',resume_after=_resume_after(evidence),model_scoped=_model_scoped_limit(evidence))
            detail=('subtype='+str((final or {}).get('subtype'))+'; '+message[:300]) if final else 'no result event; '+str(completed.stderr or '')[-300:]
            if any(x in evidence.lower() for x in MODEL_UNAVAILABLE_TEXTS):raise ModelUnavailable('Claude model unavailable: '+model+'; '+detail)
            raise BackendError('Claude CLI failed: '+detail)
        if final.get('permission_denials'):raise IsolationError('Claude CLI attempted tool use')
        usage=final.get('usage') or {}
        parts=[usage.get(k) for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens')]
        input_tokens=sum(x or 0 for x in parts) if usage.get('input_tokens') is not None else None
        effective=next(iter(final.get('modelUsage') or {}),None) or model
        data=final.get('structured_output')
        if data is None:
            # The model answered without structured output: central validation retries it.
            if not message.strip():raise BackendError('Claude CLI returned no structured output')
            data=message
        else:data=json.loads(json.dumps(data))
        cost=final.get('total_cost_usd')
        return LLMResult(data,effective,effort,input_tokens,usage.get('output_tokens'),'claude_cli',False,time.monotonic()-started,
                         cached_input_tokens=usage.get('cache_read_input_tokens'),estimated_cost_usd=cost if isinstance(cost,(int,float)) else None)


class OpenAIAPIBackend:
    def __init__(self,work:Path,provider:dict,client=None):
        self.provider=provider
        if provider.get('backend')!='openai_api' and provider.get('fallback')!='openai_api':raise BackendError('Paid OpenAI API not explicitly enabled')
        if client is None:
            if not os.environ.get('OPENAI_API_KEY'):raise BackendError('OPENAI_API_KEY is absent')
            from openai import OpenAI
            client=OpenAI(timeout=provider.get('timeout_seconds',180),max_retries=0)
        self.client=client
    def complete_json(self,task,payload,schema,model,effort,images=None):
        started=time.monotonic();prompt=_prompt(task,payload);input=prompt
        if images:
            content=[{'type':'input_text','text':prompt}]
            for path in images:content.append({'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(Path(path).read_bytes()).decode()})
            input=[{'role':'user','content':content}]
        try:result=self.client.responses.create(model=model,reasoning={'effort':effort},input=input,text={'format':{'type':'json_schema','name':task,'strict':True,'schema':schema}})
        except Exception as exc:
            if _quota(getattr(exc,'code','')) or _quota(type(exc).__name__):raise QuotaExceeded('OpenAI API quota/rate limit') from None
            raise BackendError('OpenAI API request failed: '+type(exc).__name__) from None
        try:data=json.loads(result.output_text)
        except ValueError:data=result.output_text
        usage=result.usage
        return LLMResult(data,model,effort,getattr(usage,'input_tokens',None),getattr(usage,'output_tokens',None),'openai_api',False,time.monotonic()-started,cached_input_tokens=getattr(getattr(usage,'input_tokens_details',None),'cached_tokens',None))


def _make_backend(name,work,provider):
    if name in RETIRED_BACKENDS:
        raise BackendError(f'Codex backend retired 2026-11-08: {name} is no longer available; configure provider.backend="claude_cli"')
    classes={'claude_cli':ClaudeCLIBackend,'openai_api':OpenAIAPIBackend}
    if name not in classes:raise BackendError('Unknown LLM backend')
    return classes[name](work,provider)


@contextmanager
def _parallel_slot(work,maximum):
    if not 1<=maximum<=4:raise Blocked('max_parallel must be between one and four until measured')
    folder=work/'.llm_slots';folder.mkdir(exist_ok=True)
    handles=[open(folder/str(i),'a') for i in range(maximum)]
    selected=None
    try:
        while selected is None:
            for handle in handles:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);selected=handle;break
                except BlockingIOError:pass
            if selected is None:time.sleep(.1)
        yield
    finally:
        for handle in handles:handle.close()


def _cache_connect(work):
    db=sqlite3.connect(work/'llm_cache.sqlite',timeout=30)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, result TEXT NOT NULL)')
    return db


def _mask_text(text,extras,mapping):
    existing={int(x[2:-1]) for x in PLACEHOLDER.findall(text) if x.startswith('{n')}
    counter=max(existing | {int(x[2:-1]) for x in mapping if x.startswith('{n')} | {0})+1
    extra_pattern='|'.join(re.escape(x) for x in sorted(extras,key=len,reverse=True) if x)
    pattern=re.compile(PLACEHOLDER.pattern+'|'+(r'(?<!\w)(?:'+extra_pattern+r')(?!\w)|' if extra_pattern else '')+PROTECTED.pattern)
    def replace(match):
        nonlocal counter
        value=match.group()
        if PLACEHOLDER.fullmatch(value):return value
        marker='{n'+str(counter)+'}';counter+=1;mapping[marker]=value;return marker
    return pattern.sub(replace,text)



def _symbol_provenance(row):
    fonts=[row.get('font',''),row.get('style',{}).get('font','')]
    fonts.extend(span.get('font','') for span in row.get('spans',[]))
    fonts.extend(run.get('style',{}).get('font','') for run in row.get('style_runs',[]))
    return any(SYMBOL_FONT.search(font) for font in fonts)

def _prepare(task,payload,extras,glossary):
    # Numeric metadata/id/limits are structural, not source numbers.
    def safe_context(value):
        if isinstance(value,str):return _mask_text(value,extras,{})
        if isinstance(value,list):return [safe_context(x) for x in value]
        if isinstance(value,dict):return {k:safe_context(v) for k,v in value.items() if not k.startswith('_')}
        if isinstance(value,(int,float)) and not isinstance(value,bool):return _mask_text(str(value),extras,{})
        return value
    wire={k:safe_context(v) for k,v in payload.items() if not k.startswith('_') and k not in ('dependency_fingerprint','correction_revision','glossary','style',TASK_ARRAY.get(task))}
    if TASK_ARRAY.get(task):wire[TASK_ARRAY[task]]=payload.get(TASK_ARRAY[task])
    maps={};array=TASK_ARRAY.get(task)
    if array:
        rows=wire.get(array)
        if not isinstance(rows,list) or not rows:raise ValidationFailure('A non-empty page/figure unit is required')
        ids=[x.get('id') for x in rows]
        if any(not isinstance(x,str) for x in ids) or len(ids)!=len(set(ids)):raise ValidationFailure('Input ids must be unique strings')
        clean=[]
        for row in rows:
            if _symbol_provenance(row):raise Blocked('Symbol-font spans cannot enter LLM requests')
            source=row.get('source',row.get('text',''));mapping={};source_masked=_mask_text(source,extras,mapping);maps[row['id']]=mapping
            entry={'id':row['id'],'source':source_masked}
            if row.get('_emphasis_protocol'):
                from .emphasis import contract
                contract_data=contract(row,source_masked)
                entry['source']=contract_data['source']
                entry['emphasis_source']=contract_data['phrases']
            elif row.get('_style_pairs'):
                entry['style_pair_instructions']='Each open/close marker pair encloses the equivalent translated phrase. Move complete pairs to natural target order; preserve every pair once, no nesting or text outside pairs.'
            if row.get('quality_neighbors'):
                entry['quality_neighbors']=[{
                    **{k:neighbor[k] for k in ('id','side','request_id') if k in neighbor},
                    'source':_mask_text(neighbor['source'],extras,{}),
                    'target':_mask_text(neighbor['target'],extras,{})}
                    for neighbor in row['quality_neighbors']]
            if row.get('term_context'):
                # Same page/spread body already translated: names must agree (auditor p47).
                entry['term_context']=[{'source':_mask_text(x['source'],extras,{}),'target':_mask_text(x['target'],extras,{})}
                                       for x in row['term_context']]
                entry['term_context_instructions']=('Reference only, not to translate: body text of the same page with its approved translation. '
                    'Name every object, star or proper noun exactly as that translation does.')
            if row.get('line_continuation'):
                entry['line_continuation']=[{'side':x['side'],'line_source':_mask_text(x['line_source'],extras,{}),'glossary':x['glossary']}
                                            for x in row['line_continuation']]
                entry['line_continuation_instructions']=('This block is one line of a phrase continued in the adjacent block (side). '
                    'Together, both translations must read as the approved glossary form, each keeping only its own line.')
            if row.get('_story_break'):
                # One sentence split by a column/page break (auditor batch-21, p14/p15).
                entry['story_break']={'marker':row['_story_break']['marker'],'boundary':row['_story_break']['boundary']}
                entry['story_break_instructions']=row['_story_break']['instructions']
            if 'max_chars' in row:entry['max_chars']=row['max_chars']
            glossary_source=re.sub(r'\{v\d+\}','',source) if row.get('_style_markers') or row.get('_style_pairs') else source
            terms=glossary_matches(glossary_source,glossary,key='target' if task=='shorten' else 'source')
            entry['glossary']=[{'source':x['source'],'target':x['target']} for x in terms]
            if task=='shorten' and max([len(x['target']) for x in terms]+[0])>entry['max_chars']:
                raise ValidationFailure('Impossible character limit: an approved glossary form alone exceeds max_chars',[row['id']])
            if any(protected(x[k],'',extras) for x in terms for k in ('source','target')):raise Blocked('Glossary term includes protected content')
            clean.append(entry)
        wire[array]=clean
        wire['_style_pairs']={r['id']:r['_style_pairs'] for r in rows if r.get('_style_pairs') and not r.get('_emphasis_protocol')}
        wire['_flat_emphasis']={r['id']:{**r,'source':_mask_text(r['source'],extras,{})} for r in rows if r.get('_emphasis_protocol')}
        wire['_style_orders']={r['id']:r['_style_markers'] for r in rows if r.get('_style_markers') and not r.get('_emphasis_protocol')}
        wire['_joined_glossary']={r['id']:r['_joined_glossary'] for r in rows if r.get('_joined_glossary')}
        wire['_story_breaks']={r['id']:r['_story_break']['marker'] for r in rows if r.get('_story_break')}
        wire['_initial_upper']=[r['id'] for r in rows if r.get('_initial_upper')]
    else:
        def safe(value):
            if isinstance(value,str):return _mask_text(value,extras,{})
            if isinstance(value,list):return [safe(x) for x in value]
            if isinstance(value,dict):return {k:safe(v) for k,v in value.items() if not k.startswith('_')}
            return value
        wire=safe(wire)
    if payload.get('style'):wire['style']=_mask_text(payload['style'],extras,{})
    return wire,maps


def _validate(task,data,wire,schema):
    from .text_quality import text_issues, marked_style_issues, neighbor_issues
    if isinstance(data,str):
        try:data=json.loads(data)
        except ValueError:raise ValidationFailure('JSON does not parse') from None
    try:jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as e:
        missing=(' missing '+','.join(k for k in e.validator_value if k not in e.instance)) if e.validator=='required' else ''
        raise ValidationFailure('Schema violation: '+str(e.validator)+missing+' at '+'.'.join(map(str,e.absolute_path))) from None
    array=TASK_ARRAY.get(task)
    if not array:return data
    sent=wire[array];returned=data[array];expected=[x['id'] for x in sent];actual=[x['id'] for x in returned]
    if Counter(expected)!=Counter(actual):raise ValidationFailure('Returned ids differ or contain duplicates',{i for i in set(expected)|set(actual) if Counter(expected)[i]!=Counter(actual)[i]})
    bad=[];issues=[]
    byid={x['id']:x for x in returned}
    for source in sent:
        row=byid[source['id']]
        if task=='classify_labels':continue
        translated=row['translation']
        flat=wire.get('_flat_emphasis',{}).get(source['id'])
        if flat:
            from .emphasis import reconstruct
            try:reconstruct(row,flat)
            except (ValueError,KeyError,TypeError) as exc:
                bad.append(source['id']);issues.append('emphasis: '+str(exc))
        if task in ('translate_page','translate_labels','shorten'):
            quality=text_issues(source['source'],translated,source['id'],
                                glossary=source.get('glossary',[]),style_guide=wire.get('style',''))
            if not wire.get('_style_pairs',{}).get(source['id']):quality+=marked_style_issues(source['source'],translated,source['id'])
            quality+=neighbor_issues(source,translated,byid)
            if quality:
                bad.append(source['id']);issues.extend(i['code']+' for id '+source['id'] for i in quality)
                bad.extend(identifier for i in quality for identifier in i.get('related_units',[]) if identifier in expected)
        if task in ('translate_page','translate_labels') and numeric_quantifier_issues(source['source'],translated):
            bad.append(source['id']);issues.append('numeric quantifier displaced after its noun for id '+source['id'])
        pairs=wire.get('_style_pairs',{}).get(source['id'])
        if pairs:
            from .style_spans import decode
            try:decode(translated,pairs)
            except ValueError as exc:bad.append(source['id']);issues.append(str(exc))
        style_order=wire.get('_style_orders',{}).get(source['id'])
        if style_order and (re.findall(r'\{v\d+\}',translated)!=style_order or not translated.strip().startswith(style_order[0]) or not translated.strip().endswith(style_order[-1])):
            bad.append(source['id']);issues.append('style markers changed order or boundary')
        if Counter(PLACEHOLDER.findall(source['source']))!=Counter(PLACEHOLDER.findall(translated)):
            bad.append(source['id']);issues.append('placeholders changed')
        # Source values were masked, so literal numeric/glyph values may not be introduced.
        if PROTECTED.search(PLACEHOLDER.sub('',translated)):
            bad.append(source['id']);issues.append('protected values introduced')
        from .glossary_forms import glossary_form_present
        glossary_translation=re.sub(r'\{v\d+\}','',translated)
        for term in source.get('glossary',[]):
            related=[item['target'] for item in source.get('glossary',[]) if item['target']!=term['target']]
            if not glossary_form_present(glossary_translation,term['target'],related_terms=related):bad.append(source['id']);issues.append('approved glossary form missing for id '+source['id']+': '+term['source']+' => '+term['target'])
        if 'max_chars' in source and len(translated)>source['max_chars']:bad.append(source['id']);issues.append('character limit exceeded')
        if source.get('term_context') and task=='translate_page':
            from .text_quality import term_consistency_issues
            context=[{'id':f'context{i}',**x} for i,x in enumerate(source['term_context'])]
            for issue in term_consistency_issues([{'id':source['id'],'source':source['source'],'target':translated}],context):
                bad.append(source['id']);issues.append('term inconsistent with same-page translation for id '+source['id']+': '+issue['source_term']+' => '+issue['expected'])
        from .text_quality import story_break_issue, lowercase_initial
        if source['id'] in wire.get('_initial_upper',()) and lowercase_initial(translated):
            bad.append(source['id']);issues.append('heading must open with a capital letter, as its source does, for id '+source['id'])
        marker=wire.get('_story_breaks',{}).get(source['id'])
        if marker and story_break_issue(translated,marker):
            bad.append(source['id']);issues.append('story break '+marker+' must sit once at a word boundary with a word on each side for id '+source['id'])
        from .page_context import joined_glossary_ok
        for joint in wire.get('_joined_glossary',{}).get(source['id'],[]):
            partner=byid.get(joint['partner'])
            if partner is None:continue
            pair=(translated,partner['translation']) if joint['side']=='after' else (partner['translation'],translated)
            if not joined_glossary_ok(*pair,joint):
                bad.extend([source['id'],joint['partner']]);issues.append('approved glossary form split across stacked lines for ids '+source['id']+', '+joint['partner']+': '+joint['source']+' => '+joint['target'])
    if bad:raise ValidationFailure('; '.join(dict.fromkeys(issues)),bad)
    return data


def _restore(task,data,maps,original):
    array=TASK_ARRAY.get(task)
    if not array or task=='classify_labels':return data
    data=json.loads(json.dumps(data));originals={x['id']:x for x in original[array]}
    for row in data[array]:
        text=row['translation'];masked_original=text
        text=PLACEHOLDER.sub(lambda m:maps.get(row['id'],{}).get(m.group(),m.group()),text)
        source=originals[row['id']].get('source',originals[row['id']].get('text',''))
        if Counter(PROTECTED.findall(PLACEHOLDER.sub('',source)))!=Counter(PROTECTED.findall(PLACEHOLDER.sub('',text))):raise ValidationFailure('Restored protected values changed',[row['id']])
        if 'max_chars' in originals[row['id']] and len(text)>originals[row['id']]['max_chars']:raise ValidationFailure('Restored text exceeds limit',[row['id']])
        row['translation']=text
        if 'emphasis' in row:
            from .emphasis import intervals,occurrence_at
            masked_text=masked_original
            transform=lambda value:PLACEHOLDER.sub(lambda m:maps.get(row['id'],{}).get(m.group(),m.group()),value)
            ranges=intervals(masked_text,row['emphasis'])
            for phrase,(start,end,_) in zip(row['emphasis'],ranges):
                phrase['text']=transform(masked_text[start:end])
                phrase['occurrence']=occurrence_at(text,phrase['text'],len(transform(masked_text[:start])))
        if originals[row['id']].get('_emphasis_protocol'):
            from .emphasis import reconstruct
            try:reconstruct(row,originals[row['id']])
            except (ValueError,KeyError,TypeError) as exc:raise ValidationFailure('Restored emphasis invalid: '+str(exc),[row['id']]) from None
    return data


def _log(book,entry):
    entry={'timestamp':datetime.now(timezone.utc).isoformat(),**entry}
    with book_lock(book):
        with (Path(book)/'work/costs.jsonl').open('a') as f:f.write(_json(entry)+'\n');f.flush();os.fsync(f.fileno())


def _affected_pages(payload,rows=None,ids=None):
    """Page identity is local context; identifiers on the wire are opaque keys."""
    declared=payload.get('_pages',[]) or ([payload['_page']] if payload.get('_page') is not None else [])
    if any(not isinstance(p,int) or isinstance(p,bool) or p<1 for p in declared):raise Blocked('Page context must contain positive page numbers')
    if rows is None:
        rows=payload.get('blocks',payload.get('labels',[]))
    if ids:rows=[r for r in rows if r.get('id') in ids]
    mapped=[];mapping=payload.get('_id_pages',{})
    for row in rows:
        identifier=row.get('id','');match=re.match(r'^p(\d+)_',identifier)
        page=mapping.get(identifier,int(match.group(1)) if match else None)
        if page is None:return sorted(set(declared))
        if declared and page not in declared:raise Blocked('Block identifier page differs from local page context')
        dependents=payload.get('_page_dependencies',{}).get(identifier,[page])
        if any(p not in declared for p in dependents) and declared:raise Blocked('Dependent page differs from local page context')
        mapped.extend(dependents)
    return sorted(set(mapped or declared))


def _pause(book,pages,reason,delay,resume_after=None):
    pages=[pages] if isinstance(pages,int) else list(pages or [])
    now=time.time()
    with book_lock(book):
        atomic_json(book/'work/quota_pause.json',{'timestamp':datetime.now(timezone.utc).isoformat(),'reason':reason,'page':pages[0] if len(pages)==1 else None,'pages':pages,
                                                   'resume_after_unix':resume_after if resume_after else now+delay,'resume_after_source':'provider_reset' if resume_after else 'backoff','exit_code':75})
        path=book/'work/manifest.json'
        if path.exists():
            manifest=json.loads(path.read_text())
            for row in manifest.get('pages',[]):
                if row['page'] in pages:row['status']='pending'
            atomic_json(path,manifest)
    raise QuotaPaused('Subscription quota paused; see work/quota_pause.json')


def _check_pause(book):
    path=book/'work/quota_pause.json'
    if path.exists() and json.loads(path.read_text()).get('resume_after_unix',0)>time.time():raise QuotaPaused('Existing quota pause has not expired')


def _review(book,pages,detail=None):
    pages=[pages] if isinstance(pages,int) else list(pages or [])
    with book_lock(book):
        path=book/'work/review_queue.json';rows=json.loads(path.read_text()) if path.exists() else []
        rows=[r for r in rows if not (r.get('page') in pages and r.get('reason')=='llm_validation')]
        for page in pages:
            artifacts=list((book/'work/backend_selection').glob(f'p{page:03d}.png'))
            row={'page':page,'reason':'llm_validation','artifacts':[str(p) for p in artifacts],'suggested_action':'Review model JSON, ids, protected markers and approved terminology'}
            if detail:row['detail']=detail
            rows.append(row)
        atomic_json(path,rows)
        path=book/'work/manifest.json'
        if path.exists():
            data=json.loads(path.read_text())
            for row in data.get('pages',[]):
                if row['page'] in pages:row['status']='review'
            atomic_json(path,data)


def _run_unit(task:str,payload:dict)->LLMResult:
    """One page/figure per task. Local underscore keys never enter a model prompt."""
    if '_book' not in payload:raise Blocked('run_task requires local _book context')
    book=Path(payload['_book']).resolve();work=book/'work';work.mkdir(parents=True,exist_ok=True)
    configpath=Path(payload.get('_config',ROOT/'config/pipeline.toml')).resolve()
    config=tomllib.loads(configpath.read_text());provider=config.get('provider',{});task_config=config.get('tasks',{}).get(task)
    if task_config is None:raise Blocked('Task is not configured: '+task)
    def path(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    paths=config.get('paths',{});g=path(paths.get('glossary','glossary/astro_en_es.csv'));s=path(paths.get('style_guide','glossary/style_guide.md'));d=path(paths.get('do_not_translate','glossary/do_not_translate.txt'))
    if task in GATED_TASKS:require_approval(book,g,s,d)
    _check_pause(book)
    extras=read_extras(d)
    with g.open() as f:glossary=list(csv.DictReader(f))
    try:wire,maps=_prepare(task,payload,extras,glossary)
    except ValidationFailure:
        _review(book,_affected_pages(payload));raise
    if task in GATED_TASKS:wire['style']=_mask_text(s.read_text(),extras,{})
    template=path(task_config['prompt']).read_text();schema=json.loads(path(task_config['schema']).read_text())
    wire['_template']=template
    if wire.get('_flat_emphasis'):
        from .emphasis import wire_schema
        schema=wire_schema(schema,TASK_ARRAY[task])
    model=task_config['model'];effort=task_config['effort'];backend_name=provider.get('backend','claude_cli');pages=_affected_pages(payload);page=pages[0] if len(pages)==1 else None
    images=[Path(p) for p in payload.get('_images',[])]
    if images and (task!='qa_judge' or payload.get('_images_verified') is not True):raise Blocked('Only locally masked and verified QA images may enter LLM boundary')
    from .translation_context import response_key
    cache_key=response_key(config,g,s,d,payload,task)
    with _parallel_slot(work,int(provider.get('max_parallel',2))):
        # Per-key lock closes race where two processes request the same uncached unit.
        locks=work/'.llm_locks';locks.mkdir(exist_ok=True)
        with (locks/cache_key).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            db=_cache_connect(work)
            try:
                found=db.execute('SELECT result FROM responses WHERE key=?',(cache_key,)).fetchone()
                if found and _numeric_cache_failures(task,json.loads(found[0])['data'],payload[TASK_ARRAY[task]] if task in TASK_ARRAY else []):
                    found=None
                if found:
                    result=LLMResult(**json.loads(found[0]));result.cached=True;result.latency_s=0;result.input_tokens=0;result.output_tokens=0;result.cached_input_tokens=0;result.estimated_cost_usd=0
                    _log(book,{'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':result.backend,'model':result.model,'effort':result.effort,'cached':True,'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'latency_s':0,'characters':sum(len(r['source']) for r in wire.get(TASK_ARRAY.get(task,''),[])),'cost_usd':0,'status':'cache_hit'})
                    return result
                active=backend_name;backend=_make_backend(active,work,provider);delays=config.get('quota',{}).get('backoff_seconds',[30,120,600]);quota_attempt=0
                last_error=None;attempt_wire=wire;accepted={};metrics={'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'latency_s':0,'estimated_cost_usd':0};final_result=None
                # Optional configured escalation for flat-emphasis units; empty means none.
                escalation_model=str(provider.get('escalation_model') or '').strip()
                escalation_effort=str(provider.get('escalation_effort') or '').strip() or effort
                attempts=6 if wire.get('_flat_emphasis') and escalation_model else 3
                escalation_unavailable=None
                for validation_attempt in range(attempts):
                    if validation_attempt==3:
                        model,effort=escalation_model,escalation_effort
                        attempt_wire=json.loads(json.dumps(wire));attempt_wire['validation_error']=str(last_error)
                        accepted={}
                    while True:
                        start=time.monotonic()
                        try:
                            result=backend.complete_json(task,attempt_wire,schema,model,effort,images=images)
                            break
                        except QuotaExceeded as exc:
                            escalated=validation_attempt>=3
                            _log(book,{'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':active,'model':model,'effort':effort,'cached':False,'input_tokens':None,'cached_input_tokens':None,'output_tokens':None,'latency_s':time.monotonic()-start,'status':'quota','cost_usd':None,**({'escalation':'unavailable'} if escalated and exc.model_scoped else {})})
                            if escalated and exc.model_scoped:
                                escalation_unavailable=f'{model}/{effort}: model-specific subscription limit';break
                            resume_after=getattr(exc,'resume_after',None)
                            if resume_after is None and quota_attempt<len(delays):
                                delay=delays[quota_attempt];quota_attempt+=1
                                # Small slices preserve interruptibility; tests inject zero delays.
                                remaining=float(delay)
                                while remaining>0:time.sleep(min(remaining,30));remaining-=min(remaining,30)
                                continue
                            fallback=provider.get('fallback','none')
                            if fallback!='none' and fallback!=active:
                                if fallback=='openai_api' and not os.getenv('OPENAI_API_KEY'):_pause(book,pages,'quota; paid fallback not enabled/available',delays[-1] if delays else 600)
                                active=fallback;backend=_make_backend(active,work,provider);quota_attempt=0;continue
                            _pause(book,pages,'subscription quota exhausted',delays[-1] if delays else 600,resume_after=resume_after)
                        except Exception as exc:
                            escalated=validation_attempt>=3 and not isinstance(exc,IsolationError)
                            _log(book,{'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':active,'model':model,'effort':effort,'cached':False,'input_tokens':None,'cached_input_tokens':None,'output_tokens':None,'latency_s':time.monotonic()-start,'status':'backend_error','cost_usd':None,**({'escalation':'unavailable'} if escalated else {})})
                            if escalated:
                                # An unavailable stronger model must not abort the batch: the unit goes to review.
                                escalation_unavailable=f'{model}/{effort}: {type(exc).__name__}: {str(exc)[:300]}';break
                            if isinstance(exc,BackendError):raise
                            raise BackendError('Provider request failed: '+type(exc).__name__) from None
                    if escalation_unavailable:break
                    chars=sum(len(r['source']) for r in attempt_wire.get(TASK_ARRAY.get(task,''),[])) or len(_json(attempt_wire))
                    entry={'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':result.backend,'requested_backend':backend_name,'model':result.model,'effort':result.effort,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'latency_s':result.latency_s,'characters':chars,'cached':False,'attempt':validation_attempt+1,'fallback':result.backend!=backend_name,'cost_usd':None,'cost_status':'unknown_subscription_cost' if result.backend!='openai_api' else 'unknown_tariff_or_usage'}
                    if result.backend=='claude_cli':
                        # Subscription call: the CLI's USD figure is an API-equivalent estimate, not a charge.
                        entry['cost_status']='estimated_subscription';entry['estimated_cost_usd']=getattr(result,'estimated_cost_usd',None)
                    if validation_attempt>=3:entry['escalation']='emphasis_validation'
                    if result.backend=='openai_api':
                        rates=config.get('pricing',{}).get(result.model,{})
                        if result.input_tokens is not None and result.output_tokens is not None and 'input_per_million' in rates and 'output_per_million' in rates:
                            entry['cost_usd']=(result.input_tokens*rates['input_per_million']+result.output_tokens*rates['output_per_million'])/1_000_000
                        else:entry['cost_status']='unknown_tariff_or_usage'
                    for key in metrics:
                        value=getattr(result,key)
                        metrics[key]=None if value is None or metrics[key] is None else metrics[key]+value
                    try:
                        data=_validate(task,result.data,attempt_wire,schema)
                        array=TASK_ARRAY.get(task)
                        if validation_attempt%3==2 and array and accepted:
                            merged={**accepted,**{x['id']:x for x in data[array]}}
                            data={array:[merged[x['id']] for x in wire[array]]}
                            _validate(task,data,wire,schema)
                        data=_restore(task,data,maps,payload)
                        entry['status']='ok';_log(book,entry)
                        final_result=LLMResult(data,result.model,result.effort,metrics['input_tokens'],metrics['output_tokens'],result.backend,False,metrics['latency_s'],cached_input_tokens=metrics['cached_input_tokens'],estimated_cost_usd=metrics.get('estimated_cost_usd'));break
                    except ValidationFailure as exc:
                        last_error=exc;entry['status']='validation_error';entry['validation_error']=str(exc);_log(book,entry)
                        atomic_json(work/'llm_validation'/f'{cache_key}-attempt-{validation_attempt+1}.json',{'task':task,'phase':payload.get('_phase'),'pages':pages,'error':str(exc),'ids':sorted(exc.ids),'wire':{k:v for k,v in attempt_wire.items() if k!='_template'},'response':result.data})
                        attempt_wire=json.loads(json.dumps(wire));attempt_wire['validation_error']=str(exc)
                        array=TASK_ARRAY.get(task)
                        if validation_attempt%3==1 and array and exc.ids:
                            if isinstance(result.data,dict):accepted={x['id']:x for x in result.data.get(array,[]) if x.get('id') not in exc.ids and x.get('id') in {r['id'] for r in wire[array]}}
                            attempt_wire[array]=[x for x in wire[array] if x['id'] in exc.ids]
                            if not attempt_wire[array]:attempt_wire=wire
                if final_result is None:
                    if escalation_unavailable:
                        detail='escalation unavailable ('+escalation_unavailable+'); last validation error: '+str(last_error)
                        _review(book,_affected_pages(payload,ids=last_error.ids),detail=detail);raise ValidationFailure(str(last_error)+'; '+detail,last_error.ids)
                    _review(book,_affected_pages(payload,ids=last_error.ids));raise ValidationFailure(str(last_error))
                # Do not cache fallback output under the requested backend key.
                if final_result.backend==backend_name:
                    db.execute('INSERT OR REPLACE INTO responses VALUES (?,?)',(cache_key,_json(asdict(final_result))));db.commit()
                pause=work/'quota_pause.json'
                if pause.exists():pause.unlink()
                return final_result
            finally:db.close()

# Per-block caching and batching wrap the validated provider unit below.

def _numeric_cache_failures(task,data,rows):
    """Recheck restored numeric and textual quality in both cache layers."""
    from .text_quality import text_issues, marked_style_issues, neighbor_issues
    if task not in ('translate_page','translate_labels'):return []
    sources={r['id']:r.get('source',r.get('text','')) for r in rows}
    requests={r['id']:r for r in rows}
    answers={r['id']:r for r in data[TASK_ARRAY[task]]}
    from .emphasis import reconstruct
    from .text_quality import story_break_issue, lowercase_initial
    invalid=[]
    for row in data[TASK_ARRAY[task]]:
        req=requests[row['id']]
        if req.get('_emphasis_protocol'):
            try:reconstruct(row,req)
            except (ValueError,KeyError,TypeError):invalid.append(row['id'])
        if (req.get('_initial_upper') and lowercase_initial(row['translation'])) or (
                req.get('_story_break') and story_break_issue(row['translation'],req['_story_break']['marker'])):
            invalid.append(row['id'])
    return invalid+[r['id'] for r in data[TASK_ARRAY[task]]
            if numeric_quantifier_issues(sources[r['id']],r['translation'])
            or text_issues(sources[r['id']],r['translation'],r['id'])
            or marked_style_issues(sources[r['id']],r['translation'],r['id'])
            or neighbor_issues(requests[r['id']],r['translation'],answers)]

def run_task(task:str,payload:dict)->LLMResult:
    array=TASK_ARRAY.get(task)
    if not array:return _run_unit(task,payload)
    from .emphasis import VERSION
    payload={**payload,array:[{**r,'_emphasis_protocol':VERSION} if r.get('_style_pairs') else r for r in payload.get(array,[])]}
    book=Path(payload['_book']).resolve();work=book/'work';work.mkdir(parents=True,exist_ok=True)
    config=tomllib.loads(Path(payload.get('_config',ROOT/'config/pipeline.toml')).read_text())
    task_config=config['tasks'][task];paths=config.get('paths',{});provider=config.get('provider',{})
    def path(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    g=path(paths.get('glossary','glossary/astro_en_es.csv'));s=path(paths.get('style_guide','glossary/style_guide.md'));d=path(paths.get('do_not_translate','glossary/do_not_translate.txt'))
    if task in GATED_TASKS:require_approval(book,g,s,d)
    rows=payload.get(array,[]);ids=[r.get('id') for r in rows]
    if any(_symbol_provenance(r) for r in rows):raise Blocked('Symbol-font spans cannot enter LLM requests')
    if not rows or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):raise ValidationFailure('Input ids must be unique strings in a nonempty unit')
    limit=min(6000,int(config.get('batching',{}).get('max_chars_per_call',6000)))
    if limit<=0:raise Blocked('max_chars_per_call must be positive')
    # Split oversized text at whitespace without cutting glossary terms or
    # placeholders, then restore the caller's span ids after validated chunks.
    if any(len(r.get('source',r.get('text','')))>limit for r in rows):
        if any(r.get('_style_pairs') and len(r.get('source',''))>limit for r in rows):raise ValidationFailure('Complete emphasis unit exceeds call limit; reviewed subdivision required')
        if task!='translate_page':
            _review(book,_affected_pages(payload));raise ValidationFailure('Oversized non-page unit requires reviewed subdivision')
        with g.open() as stream:terms=list(csv.DictReader(stream))
        fragments=[];mapping={};fragment_pages={}
        for row in rows:
            source=row.get('source',row.get('text',''));parts=[];offset=0
            forbidden=[m.span() for term in terms for m in re.finditer(re.escape(term['source']),source,re.I)]
            forbidden += [m.span() for m in PLACEHOLDER.finditer(source)]
            while len(source)-offset>limit:
                boundaries=[m.end() for m in re.finditer(r'\s+',source) if offset<m.end()<=offset+limit and not any(a<m.end()<b for a,b in forbidden)]
                if not boundaries:_review(book,_affected_pages(payload));raise ValidationFailure('No safe boundary within oversized span')
                end=max(boundaries);parts.append(source[offset:end]);offset=end
            parts.append(source[offset:]);mapping[row['id']]=[]
            for index,part in enumerate(parts):
                fid=row['id']+'__fragment_'+str(index);mapping[row['id']].append(fid)
                row_pages=_affected_pages(payload,rows=[row])
                if len(row_pages)==1:fragment_pages[fid]=row_pages[0]
                fragments.append({**row,'id':fid,'source':part})
        result=run_task(task,{**payload,array:fragments,'_id_pages':fragment_pages});byid={r['id']:r for r in result.data[array]};combined=[]
        for row in rows:
            parts=[byid[i] for i in mapping[row['id']]]
            combined.append({'id':row['id'],'translation':' '.join(p['translation'].strip() for p in parts),'glossary_terms_used':list(dict.fromkeys(t for p in parts for t in p['glossary_terms_used']))})
        result.data={array:combined};return result
    from .translation_context import context_data,block_keys
    common=context_data(config,g,s,d,task)
    keys=block_keys(config,g,s,d,payload,task)
    locks=work/'.llm_locks';locks.mkdir(exist_ok=True);handles=[]
    try:
        # Sorted block locks avoid duplicate quota consumption for overlapping
        # pages across processes while independent pages retain parallelism.
        for key in sorted(set(keys.values())):
            handle=(locks/('block-'+key)).open('a');fcntl.flock(handle,fcntl.LOCK_EX);handles.append(handle)
        db=_cache_connect(work)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS blocks (key TEXT PRIMARY KEY, result TEXT NOT NULL)')
            results={};pending=[];seen={};cache_hits=0;cache_provenance=[]
            for row in rows:
                found=db.execute('SELECT result FROM blocks WHERE key=?',(keys[row['id']],)).fetchone()
                if found:
                    item=json.loads(found[0]);item['id']=row['id']
                    provenance=item.pop('_cache_provenance',{'model':common['model'],'effort':common['effort'],'backend':common['backend']})
                    # Recheck current phrase matching before accepting old block caches.
                    terms=list(csv.DictReader(g.open()))
                    match_source=re.sub(r'\{v\d+\}','',row.get('source',row.get('text','')))
                    target=re.sub(r'\{v\d+\}','',item.get('translation',''))
                    expected=glossary_matches(match_source,terms,key='target' if task=='shorten' else 'source')
                    from .glossary_forms import glossary_form_present
                    valid=task=='classify_labels' or all(glossary_form_present(target,t['target'],
                        related_terms=[x['target'] for x in expected if x['target']!=t['target']]) for t in expected)
                    if _numeric_cache_failures(task,{array:[item]},[row]):valid=False
                    if valid:results[row['id']]=item;cache_hits+=1;cache_provenance.append(provenance)
                    elif keys[row['id']] not in seen:pending.append(row);seen[keys[row['id']]]=row['id']
                elif keys[row['id']] not in seen:pending.append(row);seen[keys[row['id']]]=row['id']
            groups=[];current=[];chars=0
            for row in pending:
                size=len(row.get('source',row.get('text','')))
                if current and chars+size>limit:groups.append(current);current=[];chars=0
                current.append(row);chars+=size
            if current:groups.append(current)
            combined=LLMResult({array:[]},common['model'],common['effort'],0,0,common['backend'],not groups,0,cached_input_tokens=0)
            for group in groups:
                group_keys={keys[r['id']] for r in group}
                covered=[r for r in rows if keys[r['id']] in group_keys]
                dependencies={r['id']:_affected_pages(payload,rows=[item for item in covered if keys[item['id']]==keys[r['id']]]) for r in group}
                result=_run_unit(task,{**payload,array:group,'_pages':_affected_pages(payload,rows=covered),'_page_dependencies':dependencies})
                if combined.backend!=result.backend and combined.latency_s:raise BackendError('Mixed backends inside one page require review')
                combined.backend=result.backend;combined.model=result.model;combined.effort=result.effort;combined.latency_s+=result.latency_s
                for metric in ('input_tokens','output_tokens','cached_input_tokens'):
                    old=getattr(combined,metric);new=getattr(result,metric);setattr(combined,metric,None if old is None or new is None else old+new)
                for item in result.data[array]:
                    results[item['id']]=item
                    if result.backend==common['backend']:db.execute('INSERT OR REPLACE INTO blocks VALUES (?,?)',(keys[item['id']],_json({**item,'_cache_provenance':{'model':result.model,'effort':result.effort,'backend':result.backend}})))
                db.commit()
            for row in rows:
                if row['id'] not in results:results[row['id']]={**results[seen[keys[row['id']]]],'id':row['id']}
            if not groups and cache_provenance:
                combined.model=cache_provenance[0]['model'];combined.effort=cache_provenance[0]['effort'];combined.backend=cache_provenance[0]['backend']
            combined.data={array:[results[row['id']] for row in rows]}
            if cache_hits:
                _log(book,{'task':task,'page':payload.get('_page'),'pages':_affected_pages(payload),'phase':payload.get('_phase','legacy'),'backend':combined.backend,'model':combined.model,'effort':combined.effort,'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'latency_s':0,'cached':True,'block_cache_hits':cache_hits,'effective_cache_provenance':cache_provenance,'status':'block_cache_hit','cost_usd':0})
            return combined
        finally:db.close()
    finally:
        for handle in handles:handle.close()


def validate_cached_translation(task, data, payload, glossary, style, protected_path, schema):
    """Revalidate restored cache data using the current masked-wire contract."""
    from collections import defaultdict, deque
    extras=read_extras(protected_path)
    terms=list(csv.DictReader(Path(glossary).open()))
    if any('emphasis' in r for r in data.get(TASK_ARRAY[task],[])):
        from .retranslation import validate_answer
        requests={r['id']:r for r in payload[TASK_ARRAY[task]]}
        for row in data[TASK_ARRAY[task]]:
            from .emphasis import answer_value
            validate_answer(requests[row['id']],answer_value(row),extras=extras,glossary=terms)
        return data
    wire,maps=_prepare(task,payload,extras,terms)
    wire['style']=_mask_text(Path(style).read_text(),extras,{})
    masked=json.loads(json.dumps(data))
    array=TASK_ARRAY[task]
    for row in masked[array]:
        available=defaultdict(deque)
        for marker,value in maps.get(row['id'],{}).items():available[value].append(marker)
        temporary={}
        text=_mask_text(row['translation'],extras,temporary)
        def replace(match):
            marker=match.group()
            if marker not in temporary:return marker
            value=temporary[marker]
            if not available[value]:raise ValidationFailure('Cached protected values changed')
            return available[value].popleft()
        row['translation']=PLACEHOLDER.sub(replace,text)
    _validate(task,masked,wire,schema)
    _restore(task,masked,maps,payload)
    return data


# --- Availability probe for `doctor --probe-models` ---------------------------
# Minimal call per configured model through the configured backend. It sends no
# book text, never writes quota_pause.json and journals usage in costs.jsonl.
PROBE_SCHEMA = {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
PROBE_TEMPLATE = 'Prueba de disponibilidad del modelo. Devuelve exactamente {"ok": true}.'
EFFORT_ORDER = ('minimal','low','medium','high','xhigh','max')


def probe_targets(config):
    """Distinct models from [tasks] plus provider.escalation_model, cheapest effort."""
    targets = {}
    def add(model, effort, role):
        item = targets.setdefault(model, {'model':model, 'efforts':[], 'roles':[]})
        item['efforts'].append(effort); item['roles'].append(role)
    for name, value in config.get('tasks', {}).items():
        add(value['model'], value.get('effort', 'low'), name)
    provider = config.get('provider', {})
    escalation = str(provider.get('escalation_model') or '').strip()
    if escalation:
        add(escalation, str(provider.get('escalation_effort') or '').strip() or 'low', 'escalation')
    rank = lambda effort: EFFORT_ORDER.index(effort) if effort in EFFORT_ORDER else len(EFFORT_ORDER)
    return [{'model':t['model'], 'effort':min(t['efforts'], key=rank), 'roles':t['roles']} for t in targets.values()]


def probe_models(book, config_path):
    book = Path(book).resolve(); (book/'work').mkdir(parents=True, exist_ok=True)
    config = tomllib.loads(Path(config_path).read_text()); provider = config.get('provider', {})
    name = provider.get('backend', 'claude_cli'); results = []
    for target in probe_targets(config):
        model, effort = target['model'], target['effort']
        start = time.monotonic(); result = None; detail = ''; resume_after = None
        try:
            backend = _make_backend(name, book/'work', provider)
            result = backend.complete_json('doctor_probe', {'_template':PROBE_TEMPLATE, 'probe':'ok'},
                                           PROBE_SCHEMA, model, effort)
            status = 'available'
        except QuotaExceeded as exc:
            status = 'limited'; detail = str(exc)[:300]; resume_after = exc.resume_after
        except ModelUnavailable as exc:
            status = 'unavailable'; detail = str(exc)[:300]
        except BackendError as exc:
            status = 'error'; detail = f'{type(exc).__name__}: {str(exc)[:300]}'
        except Exception as exc:
            status = 'error'; detail = type(exc).__name__
        entry = {'task':'doctor_probe', 'phase':'doctor', 'page':None, 'pages':[], 'backend':name,
                 'requested_backend':name, 'model':result.model if result else model, 'effort':effort,
                 'cached':False, 'input_tokens':getattr(result, 'input_tokens', None),
                 'cached_input_tokens':getattr(result, 'cached_input_tokens', None),
                 'output_tokens':getattr(result, 'output_tokens', None), 'latency_s':time.monotonic()-start,
                 'characters':0, 'cost_usd':None, 'status':'probe_'+status,
                 'cost_status':'estimated_subscription' if name == 'claude_cli' else
                               'unknown_subscription_cost' if name in SUBSCRIPTION_BACKENDS else 'unknown_tariff_or_usage'}
        if result is not None and name == 'claude_cli':
            entry['estimated_cost_usd'] = result.estimated_cost_usd
        _log(book, entry)
        results.append({**target, 'status':status, 'detail':detail, 'resume_after_unix':resume_after,
                        'effective_model':result.model if result else None})
    return results
