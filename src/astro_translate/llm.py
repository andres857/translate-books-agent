"""The only model boundary: subscription SDK/CLI and explicitly enabled API.

All provider imports belong here. run_task validates, masks protected values,
uses fresh isolated threads, journals usage, and resumes through a local cache.
"""
from __future__ import annotations

import base64
import csv
import fcntl
import hashlib
import importlib.metadata
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
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

ROOT = Path(__file__).resolve().parents[2]
FIXED_INSTRUCTION = 'No ejecutes comandos, no leas ni escribas archivos, no uses herramientas. Responde únicamente con el JSON solicitado.'
PLACEHOLDER = re.compile(r'\{[nv]\d+\}')
MASK_VERSION = 'n-markers-style-order-v2'
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

class LLMBackend(Protocol):
    def complete_json(self, task: str, payload: dict, schema: dict, model: str, effort: str, images: list[Path] | None = None) -> LLMResult: ...

class QuotaExceeded(RuntimeError): pass
class BackendError(Blocked): pass
class IsolationError(BackendError): pass
class ValidationFailure(Blocked):
    def __init__(self, message, ids=None): super().__init__(message); self.ids = set(ids or [])
class QuotaPaused(Blocked):
    exit_code = 75


def _json(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',',':'))
def _digest(value): return hashlib.sha256(_json(value).encode()).hexdigest()
def _enum(value): return getattr(value,'value',value)
def _prompt(task, payload):
    clean = {k:v for k,v in payload.items() if not k.startswith('_')}
    return FIXED_INSTRUCTION+'\n\n'+payload.get('_template','')+'\n\n'+_json(clean)

def _quota(value):
    text = str(value).lower()
    return any(x in text for x in ('usagelimitexceeded','ratelimitexceeded','usage limit','usage_limit','rate limit','rate_limit','quota exceeded','quota_exceeded','insufficient_quota'))

def _account_chatgpt(account):
    data = account.model_dump(mode='json') if hasattr(account,'model_dump') else account
    return isinstance(data,dict) and isinstance(data.get('account'),dict) and data['account'].get('type')=='chatgpt'


def _safe_overrides():
    # Enforced in addition to the profile so accidental future edits fail closed.
    values = {'web_search':'disabled','project_doc_max_bytes':0,'skills.include_instructions':False,
              'features.skip_host_skill_discovery':True,'features.shell_tool':False,'features.unified_exec':False,
              'features.apps':False,'features.plugins':False,'features.multi_agent':False,'agents.enabled':False,
              'features.view_image':False,'features.browser_use':False,'features.computer_use':False,
              'features.sleep_tool':False,'features.goals':False,'features.memories':False,'features.skill_search':False,
              'mcp_servers':{}}
    return tuple(f'{k}={json.dumps(v).lower() if isinstance(v,bool) else json.dumps(v)}' for k,v in values.items())


def _sandbox_folder(work,provider):
    work=Path(work).resolve();configured=Path(provider.get('sandbox_dir','work/.codex_llm_sandbox'))
    folder=(configured if configured.is_absolute() else work.parent/configured).resolve()
    if folder==work or not folder.is_relative_to(work):raise IsolationError('Sandbox directory must be a dedicated child of this book work directory')
    folder.mkdir(parents=True,exist_ok=True);return folder


def _profile_overrides(profile,cwd=None):
    # app-server 0.163 rejects --profile; equivalent profile values are supplied
    # through its public config overrides instead, without replacing user config.
    home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
    file=home/(profile+'.config.toml')
    if not file.is_file():raise IsolationError('Install the translator profile before using Codex')
    values=tomllib.loads(file.read_text()); overrides=[]
    def flatten(data,prefix=()):
        for key,value in data.items():
            keys=(*prefix,key)
            if isinstance(value,dict):flatten(value,keys)
            else:overrides.append('.'.join(json.dumps(k) for k in keys)+'='+json.dumps(value))
    flatten(values)
    servers=set(values.get('mcp_servers',{}))
    global_config=home/'config.toml'
    if global_config.exists():servers.update(tomllib.loads(global_config.read_text()).get('mcp_servers',{}))
    if cwd is not None:
        for parent in (Path(cwd).resolve(),*Path(cwd).resolve().parents):
            project=parent/'.codex/config.toml'
            if project.is_file():servers.update(tomllib.loads(project.read_text()).get('mcp_servers',{}))
    for server in servers:overrides.append('mcp_servers.'+json.dumps(server)+'.enabled=false')
    return tuple(overrides)


class CodexSDKBackend:
    def __init__(self, work:Path, provider:dict): self.work=Path(work); self.provider=provider
    def complete_json(self, task,payload,schema,model,effort,images=None):
        from openai_codex import Codex,CodexConfig,Sandbox,ApprovalMode,TextInput,LocalImageInput
        from openai_codex.generated.v2_all import ItemStartedNotification,ItemCompletedNotification,TurnCompletedNotification,ThreadTokenUsageUpdatedNotification
        started=time.monotonic(); profile=self.provider.get('codex_profile','translator')
        folder=_sandbox_folder(self.work,self.provider)
        with tempfile.TemporaryDirectory(prefix='call-',dir=folder) as cwd:
            cfg=CodexConfig(cwd=cwd,codex_bin=self.provider.get('codex_bin'),
                config_overrides=(*_profile_overrides(profile,cwd),*_safe_overrides()),
                env={'OPENAI_API_KEY':'','CODEX_API_KEY':''})
            with Codex(cfg) as client:
                if not _account_chatgpt(client.account()): raise BackendError('Codex authentication must be ChatGPT, not an API key')
                thread=client.thread_start(model=model,cwd=cwd,sandbox=Sandbox.read_only,approval_mode=ApprovalMode.deny_all,
                    ephemeral=True,developer_instructions=FIXED_INSTRUCTION)
                user_input=_prompt(task,payload)
                if images:
                    user_input=[TextInput(text=user_input),*[LocalImageInput(path=str(Path(p).resolve())) for p in images]]
                handle=thread.turn(user_input,effort=effort,output_schema=schema,approval_mode=ApprovalMode.deny_all,sandbox=Sandbox.read_only)
                final=None; usage=None; status=None; timed_out=threading.Event()
                def timeout():
                    timed_out.set()
                    try: handle.interrupt()
                    except Exception: pass
                    client.close()
                timer=threading.Timer(float(self.provider.get('timeout_seconds',180)),timeout);timer.daemon=True;timer.start()
                try:
                    for event in handle.stream():
                        data=event.payload
                        if isinstance(data,(ItemStartedNotification,ItemCompletedNotification)):
                            item=getattr(data.item,'root',data.item);kind=_enum(getattr(item,'type',None))
                            if kind not in ('agentMessage','reasoning','userMessage'):
                                handle.interrupt();raise IsolationError(f'Codex attempted forbidden item type: {kind}')
                            if isinstance(data,ItemCompletedNotification) and kind=='agentMessage':final=item.text
                        if isinstance(data,ThreadTokenUsageUpdatedNotification):usage=data.token_usage.last
                        if isinstance(data,TurnCompletedNotification):
                            status=_enum(data.turn.status)
                            if data.turn.error:
                                code=_enum(getattr(data.turn.error.codex_error_info,'root',data.turn.error.codex_error_info))
                                if _quota(code) or _quota(data.turn.error.message):raise QuotaExceeded('Codex subscription usage/rate limit')
                                raise BackendError('Codex turn failed: '+str(code)+'; '+str(data.turn.error.message)[:1500])
                    if timed_out.is_set():raise BackendError('Codex SDK timed out')
                    if status!='completed' or not final:raise BackendError('Codex returned no completed final response')
                finally:timer.cancel()
                try: data=json.loads(final)
                except ValueError: data=final # defensive validation and retry occurs centrally, with usage retained
                return LLMResult(data,model,effort,getattr(usage,'input_tokens',None),getattr(usage,'output_tokens',None),'codex_sdk',False,time.monotonic()-started,cached_input_tokens=getattr(usage,'cached_input_tokens',None))


class CodexExecBackend:
    def __init__(self, work:Path, provider:dict):self.work=Path(work);self.provider=provider
    def complete_json(self,task,payload,schema,model,effort,images=None):
        started=time.monotonic();bin=self.provider.get('codex_exec_bin','codex');profile=self.provider.get('codex_profile','translator')
        env=os.environ.copy();env.pop('OPENAI_API_KEY',None);env.pop('CODEX_API_KEY',None)
        auth=subprocess.run([bin,'login','status'],capture_output=True,text=True,env=env,timeout=20)
        if auth.returncode or 'ChatGPT' not in auth.stdout+auth.stderr:raise BackendError('Codex authentication must be ChatGPT')
        folder=_sandbox_folder(self.work,self.provider)
        with tempfile.TemporaryDirectory(prefix='call-',dir=folder) as tmp:
            base=Path(tmp);cwd=base/'empty';cwd.mkdir();schema_path=base/'schema.json';schema_path.write_text(_json(schema))
            command=[bin,'-p',profile]
            for override in (*_profile_overrides(profile,cwd),*_safe_overrides()):command.extend(['-c',override])
            command+=['exec','--ephemeral','--sandbox','read-only','--skip-git-repo-check','-m',model,'-c',f'model_reasoning_effort={json.dumps(effort)}','--json','--output-schema',str(schema_path)]
            for path in images or []:command.extend(['--image',str(Path(path).resolve())])
            command.append('-')
            try: result=subprocess.run(command,input=_prompt(task,payload),capture_output=True,text=True,cwd=cwd,env=env,timeout=self.provider.get('timeout_seconds',180))
            except subprocess.TimeoutExpired:raise BackendError('Codex exec timed out') from None
            final=None;usage={};error=None
            for line in result.stdout.splitlines():
                try:event=json.loads(line)
                except ValueError:continue
                if event.get('type') in ('item.started','item.completed'):
                    item=event.get('item',{});kind=item.get('type')
                    if kind not in ('agent_message','reasoning'):raise IsolationError(f'Codex exec attempted forbidden item: {kind}')
                    if event['type']=='item.completed' and kind=='agent_message':final=item.get('text')
                if event.get('type')=='turn.completed':usage=event.get('usage',{})
                if event.get('type') in ('turn.failed','error'):error=event
            if error or result.returncode:
                if _quota(error) or _quota(result.stderr):raise QuotaExceeded('Codex subscription usage/rate limit')
                raise BackendError('Codex exec failed; no provider stderr or secrets written to logs')
            if not final:raise BackendError('Codex exec returned no final JSON response')
            try:data=json.loads(final)
            except ValueError:data=final
            return LLMResult(data,model,effort,usage.get('input_tokens'),usage.get('output_tokens'),'codex_exec',False,time.monotonic()-started,cached_input_tokens=usage.get('cached_input_tokens'))


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
    classes={'codex_sdk':CodexSDKBackend,'codex_exec':CodexExecBackend,'openai_api':OpenAIAPIBackend}
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
    wire={k:safe_context(v) for k,v in payload.items() if not k.startswith('_') and k not in ('glossary','style',TASK_ARRAY.get(task))}
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
            if 'max_chars' in row:entry['max_chars']=row['max_chars']
            glossary_source=re.sub(r'\{v\d+\}','',source) if row.get('_style_markers') else source
            terms=glossary_matches(glossary_source,glossary,key='target' if task=='shorten' else 'source')
            entry['glossary']=[{'source':x['source'],'target':x['target']} for x in terms]
            if task=='shorten' and max([len(x['target']) for x in terms]+[0])>entry['max_chars']:
                raise ValidationFailure('Impossible character limit: an approved glossary form alone exceeds max_chars',[row['id']])
            if any(protected(x[k],'',extras) for x in terms for k in ('source','target')):raise Blocked('Glossary term includes protected content')
            clean.append(entry)
        wire[array]=clean
        wire['_style_orders']={r['id']:r['_style_markers'] for r in rows if r.get('_style_markers')}
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
    if isinstance(data,str):
        try:data=json.loads(data)
        except ValueError:raise ValidationFailure('JSON does not parse') from None
    try:jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as e:raise ValidationFailure('Schema violation: '+str(e.validator)+' at '+'.'.join(map(str,e.absolute_path))) from None
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
        style_order=wire.get('_style_orders',{}).get(source['id'])
        if style_order and (re.findall(r'\{v\d+\}',translated)!=style_order or not translated.strip().startswith(style_order[0]) or not translated.strip().endswith(style_order[-1])):
            bad.append(source['id']);issues.append('style markers changed order or boundary')
        if Counter(PLACEHOLDER.findall(source['source']))!=Counter(PLACEHOLDER.findall(translated)):
            bad.append(source['id']);issues.append('placeholders changed')
        # Source values were masked, so literal numeric/glyph values may not be introduced.
        if PROTECTED.search(PLACEHOLDER.sub('',translated)):
            bad.append(source['id']);issues.append('protected values introduced')
        glossary_translation=re.sub(r'\{v\d+\}','',translated) if style_order else translated
        for term in source.get('glossary',[]):
            if not re.search(r'(?<!\w)'+re.escape(term['target'])+r'(?!\w)',glossary_translation,re.I):bad.append(source['id']);issues.append('approved glossary form missing for id '+source['id']+': '+term['source']+' => '+term['target'])
        if 'max_chars' in source and len(translated)>source['max_chars']:bad.append(source['id']);issues.append('character limit exceeded')
    if bad:raise ValidationFailure('; '.join(dict.fromkeys(issues)),bad)
    return data


def _restore(task,data,maps,original):
    array=TASK_ARRAY.get(task)
    if not array or task=='classify_labels':return data
    data=json.loads(json.dumps(data));originals={x['id']:x for x in original[array]}
    for row in data[array]:
        text=row['translation']
        text=PLACEHOLDER.sub(lambda m:maps.get(row['id'],{}).get(m.group(),m.group()),text)
        source=originals[row['id']].get('source',originals[row['id']].get('text',''))
        if Counter(PROTECTED.findall(PLACEHOLDER.sub('',source)))!=Counter(PROTECTED.findall(PLACEHOLDER.sub('',text))):raise ValidationFailure('Restored protected values changed',[row['id']])
        if 'max_chars' in originals[row['id']] and len(text)>originals[row['id']]['max_chars']:raise ValidationFailure('Restored text exceeds limit',[row['id']])
        row['translation']=text
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


def _pause(book,pages,reason,delay):
    pages=[pages] if isinstance(pages,int) else list(pages or [])
    now=time.time()
    with book_lock(book):
        atomic_json(book/'work/quota_pause.json',{'timestamp':datetime.now(timezone.utc).isoformat(),'reason':reason,'page':pages[0] if len(pages)==1 else None,'pages':pages,'resume_after_unix':now+delay,'exit_code':75})
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


def _review(book,pages):
    pages=[pages] if isinstance(pages,int) else list(pages or [])
    with book_lock(book):
        path=book/'work/review_queue.json';rows=json.loads(path.read_text()) if path.exists() else []
        rows=[r for r in rows if not (r.get('page') in pages and r.get('reason')=='llm_validation')]
        for page in pages:
            artifacts=list((book/'work/backend_selection').glob(f'p{page:03d}.png'))
            rows.append({'page':page,'reason':'llm_validation','artifacts':[str(p) for p in artifacts],'suggested_action':'Review model JSON, ids, protected markers and approved terminology'})
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
    model=task_config['model'];effort=task_config['effort'];backend_name=provider.get('backend','codex_sdk');pages=_affected_pages(payload);page=pages[0] if len(pages)==1 else None
    images=[Path(p) for p in payload.get('_images',[])]
    if images and (task!='qa_judge' or payload.get('_images_verified') is not True):raise Blocked('Only locally masked and verified QA images may enter LLM boundary')
    normalized=json.loads(json.dumps({k:v for k,v in payload.items() if not k.startswith('_')}))
    def normalize(value):
        if isinstance(value,str):return unicodedata.normalize('NFC',value.replace('\r\n','\n'))
        if isinstance(value,list):return [normalize(x) for x in value]
        if isinstance(value,dict):return {k:normalize(v) for k,v in value.items()}
        return value
    cache_key=_digest({'mask_version':MASK_VERSION,'task':task,'payload':normalize(normalized),'model':model,'effort':effort,'backend':backend_name,
        'glossary':sha256(g),'style':sha256(s),'protected':sha256(d),'template':template,'schema':schema,'images':[sha256(p) for p in images]})
    with _parallel_slot(work,int(provider.get('max_parallel',2))):
        # Per-key lock closes race where two processes request the same uncached unit.
        locks=work/'.llm_locks';locks.mkdir(exist_ok=True)
        with (locks/cache_key).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            db=_cache_connect(work)
            try:
                found=db.execute('SELECT result FROM responses WHERE key=?',(cache_key,)).fetchone()
                if found:
                    result=LLMResult(**json.loads(found[0]));result.cached=True;result.latency_s=0;result.input_tokens=0;result.output_tokens=0;result.cached_input_tokens=0
                    _log(book,{'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':result.backend,'model':result.model,'effort':result.effort,'cached':True,'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'latency_s':0,'characters':sum(len(r['source']) for r in wire.get(TASK_ARRAY.get(task,''),[])),'cost_usd':0,'status':'cache_hit'})
                    return result
                active=backend_name;backend=_make_backend(active,work,provider);delays=config.get('quota',{}).get('backoff_seconds',[30,120,600]);quota_attempt=0
                last_error=None;attempt_wire=wire;accepted={};metrics={'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'latency_s':0};final_result=None
                for validation_attempt in range(3):
                    while True:
                        start=time.monotonic()
                        try:
                            result=backend.complete_json(task,attempt_wire,schema,model,effort,images=images)
                            break
                        except QuotaExceeded:
                            _log(book,{'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':active,'model':model,'effort':effort,'cached':False,'input_tokens':None,'cached_input_tokens':None,'output_tokens':None,'latency_s':time.monotonic()-start,'status':'quota','cost_usd':None if active=='openai_api' else 0})
                            if quota_attempt<len(delays):
                                delay=delays[quota_attempt];quota_attempt+=1
                                # Small slices preserve interruptibility; tests inject zero delays.
                                remaining=float(delay)
                                while remaining>0:time.sleep(min(remaining,30));remaining-=min(remaining,30)
                                continue
                            fallback=provider.get('fallback','none')
                            if fallback!='none' and fallback!=active:
                                if fallback=='openai_api' and not os.getenv('OPENAI_API_KEY'):_pause(book,pages,'quota; paid fallback not enabled/available',delays[-1] if delays else 600)
                                active=fallback;backend=_make_backend(active,work,provider);quota_attempt=0;continue
                            _pause(book,pages,'subscription quota exhausted',delays[-1] if delays else 600)
                        except Exception as exc:
                            _log(book,{'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':active,'model':model,'effort':effort,'cached':False,'input_tokens':None,'cached_input_tokens':None,'output_tokens':None,'latency_s':time.monotonic()-start,'status':'backend_error','cost_usd':None if active=='openai_api' else 0})
                            if isinstance(exc,BackendError):raise
                            raise BackendError('Provider request failed: '+type(exc).__name__) from None
                    chars=sum(len(r['source']) for r in attempt_wire.get(TASK_ARRAY.get(task,''),[])) or len(_json(attempt_wire))
                    entry={'task':task,'page':page,'pages':pages,'phase':payload.get('_phase','legacy'),'backend':result.backend,'requested_backend':backend_name,'model':result.model,'effort':result.effort,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'latency_s':result.latency_s,'characters':chars,'cached':False,'attempt':validation_attempt+1,'fallback':result.backend!=backend_name,'cost_usd':None if result.backend=='openai_api' else 0}
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
                        if validation_attempt==2 and array and accepted:
                            merged={**accepted,**{x['id']:x for x in data[array]}}
                            data={array:[merged[x['id']] for x in wire[array]]}
                            _validate(task,data,wire,schema)
                        data=_restore(task,data,maps,payload)
                        entry['status']='ok';_log(book,entry)
                        final_result=LLMResult(data,result.model,result.effort,metrics['input_tokens'],metrics['output_tokens'],result.backend,False,metrics['latency_s'],cached_input_tokens=metrics['cached_input_tokens']);break
                    except ValidationFailure as exc:
                        last_error=exc;entry['status']='validation_error';entry['validation_error']=str(exc);_log(book,entry)
                        atomic_json(work/'llm_validation'/f'{cache_key}-attempt-{validation_attempt+1}.json',{'task':task,'phase':payload.get('_phase'),'pages':pages,'error':str(exc),'ids':sorted(exc.ids),'wire':{k:v for k,v in attempt_wire.items() if k!='_template'},'response':result.data})
                        attempt_wire=json.loads(json.dumps(wire));attempt_wire['validation_error']=str(exc)
                        array=TASK_ARRAY.get(task)
                        if validation_attempt==1 and array and exc.ids:
                            if isinstance(result.data,dict):accepted={x['id']:x for x in result.data.get(array,[]) if x.get('id') not in exc.ids and x.get('id') in {r['id'] for r in wire[array]}}
                            attempt_wire[array]=[x for x in wire[array] if x['id'] in exc.ids]
                            if not attempt_wire[array]:attempt_wire=wire
                if final_result is None:_review(book,_affected_pages(payload,ids=last_error.ids));raise ValidationFailure(str(last_error))
                # Do not cache fallback output under the requested backend key.
                if final_result.backend==backend_name:
                    db.execute('INSERT OR REPLACE INTO responses VALUES (?,?)',(cache_key,_json(asdict(final_result))));db.commit()
                pause=work/'quota_pause.json'
                if pause.exists():pause.unlink()
                return final_result
            finally:db.close()

# Per-block caching and batching wrap the validated provider unit below.

def run_task(task:str,payload:dict)->LLMResult:
    array=TASK_ARRAY.get(task)
    if not array:return _run_unit(task,payload)
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
    limit=int(config.get('batching',{}).get('max_chars_per_call',6000))
    if limit<=0:raise Blocked('max_chars_per_call must be positive')
    # Split oversized text at whitespace without cutting glossary terms or
    # placeholders, then restore the caller's span ids after validated chunks.
    if any(len(r.get('source',r.get('text','')))>limit for r in rows):
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
    common={'mask_version':MASK_VERSION,'task':task,'model':task_config['model'],'effort':task_config['effort'],'backend':provider.get('backend','codex_sdk'),
        'glossary':sha256(g),'style':sha256(s),'protected':sha256(d),'prompt':sha256(path(task_config['prompt'])),'schema':sha256(path(task_config['schema']))}
    context={k:v for k,v in payload.items() if not k.startswith('_') and k!=array}
    keys={r['id']:_digest({**common,'context':context,'row':{k:unicodedata.normalize('NFC',v.replace('\r\n','\n')) if isinstance(v,str) else v for k,v in r.items() if k not in ('id','bbox','size','rotate','font')}}) for r in rows}
    locks=work/'.llm_locks';locks.mkdir(exist_ok=True);handles=[]
    try:
        # Sorted block locks avoid duplicate quota consumption for overlapping
        # pages across processes while independent pages retain parallelism.
        for key in sorted(set(keys.values())):
            handle=(locks/('block-'+key)).open('a');fcntl.flock(handle,fcntl.LOCK_EX);handles.append(handle)
        db=_cache_connect(work)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS blocks (key TEXT PRIMARY KEY, result TEXT NOT NULL)')
            results={};pending=[];seen={};cache_hits=0
            for row in rows:
                found=db.execute('SELECT result FROM blocks WHERE key=?',(keys[row['id']],)).fetchone()
                if found:
                    item=json.loads(found[0]);item['id']=row['id']
                    # Recheck current phrase matching before accepting old block caches.
                    terms=list(csv.DictReader(g.open()))
                    match_source=re.sub(r'\{v\d+\}','',row.get('source',row.get('text','')))
                    target=re.sub(r'\{v\d+\}','',item.get('translation',''))
                    expected=glossary_matches(match_source,terms,key='target' if task=='shorten' else 'source')
                    valid=task=='classify_labels' or all(re.search(r'(?<!\w)'+re.escape(t['target'])+r'(?!\w)',target,re.I) for t in expected)
                    if valid:results[row['id']]=item;cache_hits+=1
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
                combined.backend=result.backend;combined.latency_s+=result.latency_s
                for metric in ('input_tokens','output_tokens','cached_input_tokens'):
                    old=getattr(combined,metric);new=getattr(result,metric);setattr(combined,metric,None if old is None or new is None else old+new)
                for item in result.data[array]:
                    results[item['id']]=item
                    if result.backend==common['backend']:db.execute('INSERT OR REPLACE INTO blocks VALUES (?,?)',(keys[item['id']],_json(item)))
                db.commit()
            for row in rows:
                if row['id'] not in results:results[row['id']]={**results[seen[keys[row['id']]]],'id':row['id']}
            combined.data={array:[results[row['id']] for row in rows]}
            if cache_hits:
                _log(book,{'task':task,'page':payload.get('_page'),'pages':_affected_pages(payload),'phase':payload.get('_phase','legacy'),'backend':common['backend'],'model':common['model'],'effort':common['effort'],'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'latency_s':0,'cached':True,'block_cache_hits':cache_hits,'status':'block_cache_hit','cost_usd':0})
            return combined
        finally:db.close()
    finally:
        for handle in handles:handle.close()
