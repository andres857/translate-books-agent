"""Deterministic PDF operations. Incomplete evidence always fails closed."""
from __future__ import annotations
import csv
import fcntl
import hashlib
import html
import json
import os
import re
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageChops

# Font ascender/descender bboxes can overlap adjacent printed lines. This
# deterministic extraction setting bounds text to its nominal glyph height.
# It changes extraction geometry only, never source bytes or graphics.
fitz.TOOLS.set_small_glyph_heights(True)

class Blocked(RuntimeError):
    """A gate or preservation invariant has not been satisfied."""

SYMBOL_FONT = re.compile(r'astro|zodiac|symbol|dingbat|wingding', re.I)
PROTECTED = re.compile(r'\d+(?:[.,:/−+\-]\d+)*|[°º′″\u2600-\u26ff\u2700-\u27bf\ue000-\uf8ff]|\b(?:R|Rx|ASC|Asc|[Mm][Cc]|[Ii][Cc]|[Aa][Cc]|[Dd][Cc]|DSC)\b')

def sha256(path):
    with open(path,'rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def atomic_json(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

@contextmanager
def book_lock(book):
    folder=Path(book)/'work'; folder.mkdir(parents=True,exist_ok=True)
    with open(folder/'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(lock,fcntl.LOCK_UN)

def protected(text,font='',extras=()):
    return bool(SYMBOL_FONT.search(font) or PROTECTED.search(text) or any(re.search(r'(?<!\w)'+re.escape(x)+r'(?!\w)',text) for x in extras if x))

def read_extras(path=None):
    return [x.strip() for x in Path(path).read_text().splitlines() if x.strip() and not x.startswith('#')] if path and Path(path).exists() else []

def fingerprint(book,glossary,style,do_not_translate=None):
    return {'source_sha256':sha256(Path(book)/'source.pdf'),'glossary_sha256':sha256(glossary),'style_sha256':sha256(style),'do_not_translate_sha256':sha256(do_not_translate) if do_not_translate else None}

def approve_glossary(book,glossary,style,approved_by,do_not_translate=None):
    """Record a human decision; callers must obtain that decision externally."""
    if not approved_by.strip(): raise Blocked('Human approver identity required')
    data={**fingerprint(book,glossary,style,do_not_translate),'approved':True,'approved_by':approved_by}
    with book_lock(book): atomic_json(Path(book)/'work/glossary_approval.json',data)
    return data

def require_approval(book,glossary,style,do_not_translate=None):
    p=Path(book)/'work/glossary_approval.json'
    if not p.exists(): raise Blocked('G3: glossary and style require human approval')
    data=json.loads(p.read_text())
    if not data.get('approved') or not data.get('approved_by') or any(data.get(k)!=v for k,v in fingerprint(book,glossary,style,do_not_translate).items()):
        raise Blocked('G3: approval missing or hashes changed')
    return data

TRIAGE_VERSION=2
ROUTES={'native':['A'],'mixed':['A','B'],'vector_figure':['A'],'scanned':['C']}

def visible_native_letters(page):
    """Letters a reader sees as native text: inside the page box, not render
    mode 3 (invisible OCR layers), not transparent and not in symbol fonts.

    Printer slug lines outside the trim box (e.g. Helvetica job names below
    the page) are excluded, so a bare illustration counts zero.
    """
    count=0
    for span in page.get_texttrace():
        if span.get('type')==3 or not span.get('opacity',1) or SYMBOL_FONT.search(span.get('font','')):continue
        for char in span['chars']:
            box=fitz.Rect(char[3])
            if chr(char[0]).isalpha() and page.rect.contains(fitz.Point((box.x0+box.x1)/2,(box.y0+box.y1)/2)):count+=1
    return count

def classify_page(page,config=None):
    """Triage one page. A full-page raster is a scan (route C) only when the
    page has little extractable text *and* no visible native text overlay.
    Chapter openings with native titles over full-bleed artwork are mixed:
    route A composes their native text, the raster is preserved.
    """
    config=config or {}
    text=page.get_text(); images=[]
    for item in page.get_images(full=True):
        for rect in page.get_image_rects(item[0]):
            images.append({'xref':item[0],'rect':list(rect),'has_text':None})
    cover=max([fitz.Rect(x['rect']).get_area()/page.rect.get_area() for x in images] or [0])
    letters=visible_native_letters(page)
    overlay=letters>=config.get('native_overlay_min_letters',8)
    scanned=len(text.strip())<config.get('scanned_char_threshold',80) and cover>=config.get('scanned_image_area_ratio',.8) and not overlay
    kind='scanned' if scanned else 'mixed' if images else 'vector_figure' if page.get_drawings() else 'native'
    return {'page':page.number+1,'type':kind,'chars':len(text),'images':images,
            'symbol_fonts':[f[3] for f in page.get_fonts() if SYMBOL_FONT.search(f[3])],'route':ROUTES[kind][0],
            'routes':ROUTES[kind],'visible_native_letters':letters,'image_cover':round(cover,4)}

def _triage_rows(source,config):
    rows=[]
    with fitz.open(source) as doc:
        for page in doc:rows.append(classify_page(page,config))
    return rows

def _merge_triage(old_pages,rows,stamp):
    """Update only classification keys; every state key of a page survives."""
    old={r['page']:r for r in old_pages};pages=[];changes=[]
    for row in rows:
        previous=old.get(row['page'])
        fresh={'page':row['page'],'type':row['type'],'chars':row['chars'],'images':row['images'],'symbol_fonts':row['symbol_fonts'],
               'route':row['route'],'status':'pending','attempts':0,'stages':{r:'pending' for r in row['routes']}}
        if previous is None:pages.append(fresh);continue
        merged=json.loads(json.dumps(previous))
        if (previous.get('type'),previous.get('route'))!=(row['type'],row['route']):
            stages=dict(previous.get('stages',{}))
            for route in row['routes']:stages.setdefault(route,'pending')
            change={'page':row['page'],'from':{'type':previous.get('type'),'route':previous.get('route'),'status':previous.get('status')},
                    'to':{'type':row['type'],'route':row['route']},'visible_native_letters':row['visible_native_letters'],
                    'triage_version':TRIAGE_VERSION,**stamp}
            merged.update(type=row['type'],route=row['route'],stages=stages)
            merged.setdefault('retriage_history',[]).append(change);changes.append(change)
        pages.append(merged)
    return pages,changes

def _manifest_summary(digest,config,pages):
    ratio=sum(p['type']=='scanned' for p in pages)/max(len(pages),1)
    return {'scanned_ratio':ratio,'warnings':['More than 30% scanned pages'] if ratio>config.get('scanned_warning_ratio',.3) else []}

def triage(book,config=None):
    book=Path(book); config=config or {}; source=book/'source.pdf'; digest=sha256(source)
    with book_lock(book):
        manifest=book/'work/manifest.json'
        if manifest.exists():
            old=json.loads(manifest.read_text())
            if old['source_sha256']!=digest: raise Blocked('Immutable source changed')
            if old.get('triage_config')==config: return old
            # A configuration change reclassifies, never resets page state.
            from datetime import datetime,timezone
            pages,_=_merge_triage(old['pages'],_triage_rows(source,config),{'at':datetime.now(timezone.utc).isoformat(),'reason':'triage_config_changed'})
            result={**old,'triage_config':config,'triage_version':TRIAGE_VERSION,'pages':pages,**_manifest_summary(digest,config,pages)}
            atomic_json(manifest,result); return result
        pages=[{k:v for k,v in r.items() if k not in ('routes','visible_native_letters','image_cover')}|{'status':'pending','attempts':0,'stages':{x:'pending' for x in r['routes']}}
               for r in _triage_rows(source,config)]
        result={'version':1,'source_sha256':digest,'triage_config':config,'triage_version':TRIAGE_VERSION,'pages':pages,**_manifest_summary(digest,config,pages)}
        atomic_json(manifest,result); return result

def retriage(book,config=None,apply=False):
    """Reclassify an existing manifest with the current classifier.

    Dry run by default (no write). With ``apply`` only ``type``, ``route`` and
    ``stages`` of changed pages are updated (new routes added as pending,
    existing stage states kept) and a ``retriage_history`` entry records the
    previous values; status, attempts, QA links and all other keys survive.
    Downstream production checkpoints decide whether a page is recomposed.
    """
    from datetime import datetime,timezone
    book=Path(book); config=config if config is not None else None; source=book/'source.pdf'
    path=book/'work/manifest.json'
    if not path.exists():raise Blocked('No manifest to re-triage; run triage first')
    with book_lock(book):
        old=json.loads(path.read_text()); digest=sha256(source)
        if old['source_sha256']!=digest:raise Blocked('Immutable source changed')
        config=old.get('triage_config',{}) if config is None else config
        pages,changes=_merge_triage(old['pages'],_triage_rows(source,config),{'at':datetime.now(timezone.utc).isoformat(),'reason':'retriage'})
        summary=_manifest_summary(digest,config,pages)
        if apply and changes:
            atomic_json(path,{**old,'triage_config':config,'triage_version':TRIAGE_VERSION,'pages':pages,**summary})
    return {'applied':bool(apply and changes),'manifest':str(path),'triage_version':TRIAGE_VERSION,'changes':changes,
            'scanned_ratio_before':old.get('scanned_ratio'),'scanned_ratio_after':summary['scanned_ratio']}

def extract_units(page,extras=()):
    """Conservative whole-span selection: mixed protected spans remain untouched."""
    units=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                if span['text'].strip() and not protected(span['text'],span['font'],extras):
                    units.append({'bbox':list(span['bbox']),'source':span['text'],'font':span['font'],'size':span['size'],'rotate':0 if tuple(line['dir'])==(1.,0.) else None})
    return units

def protected_boxes(page,extras=()):
    result=[]
    for b in page.get_text('rawdict')['blocks']:
        for line in b.get('lines',[]):
            for span in line['spans']:
                txt=''.join(c['c'] for c in span['chars'])
                if protected(txt,span['font'],extras): result.extend(fitz.Rect(c['bbox']) for c in span['chars'])
    return result

EZ_SAVE_OPTIONS=dict(garbage=3,deflate=True,deflate_images=True,deflate_fonts=True,use_objstms=1)

def deterministic_pdf_bytes(doc,**options):
    """Same content, same bytes: no random trailer /ID and no save dates.

    MuPDF writes a random /ID unless told not to, and ``no_new_id`` alone keeps
    whatever ID the document already carried (a random one from an earlier
    save, or none). Candidate hashes bind visual reviews and approvals, so the
    trailer gets an ID derived from the bytes written without it, and the Info
    dates are dropped. Object order is MuPDF's deterministic garbage collection.
    """
    info=doc.xref_get_key(-1,'Info')
    if info[0]=='xref':
        xref=int(info[1].split()[0])
        for key in ('CreationDate','ModDate'):
            if doc.xref_get_key(xref,key)[0]!='null':doc.xref_set_key(xref,key,'null')
    doc.xref_set_key(-1,'ID','null')
    plain=doc.tobytes(**options,no_new_id=True)
    ident=hashlib.sha256(plain).hexdigest()[:32].upper()
    with fitz.open(stream=plain,filetype='pdf') as out:
        out.xref_set_key(-1,'ID',f'[<{ident}><{ident}>]')
        # Already compacted/compressed: write the objects as they are.
        return out.tobytes(no_new_id=True,use_objstms=options.get('use_objstms',0))

def save_deterministic(doc,path,**options):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    data=deterministic_pdf_bytes(doc,**options)
    fd,tmp=tempfile.mkstemp(suffix='.pdf',prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _save_pdf(doc,path):
    save_deterministic(doc,path,**EZ_SAVE_OPTIONS)

def reinsert(book,payload,glossary,style,do_not_translate=None,shortener=None,font_path=None,layout=None,shortforms=None):
    """Transactional paragraph fitting, conservative label edits, then one shortening batch.

    ``shortforms`` must be a separately human-approved file plus hash record;
    the approved main glossary is never mutated.
    """
    from .layout import extract_layout_units,expansion_candidates,shortening_budget,format_cached_translation,safe_redaction_rects
    book=Path(book);require_approval(book,glossary,style,do_not_translate);layout=layout or {}
    if not (book/'work/manifest.json').exists():triage(book)
    elif json.loads((book/'work/manifest.json').read_text())['source_sha256']!=sha256(book/'source.pdf'):raise Blocked('Immutable source changed')
    forms={}
    if shortforms:
        path=Path(shortforms['path']);approval=json.loads(Path(shortforms['approval']).read_text())
        if approval.get('approved') is not True or approval.get('sha256')!=sha256(path) or approval.get('scope')!='shortforms':raise Blocked('Short forms require separate hash-bound human approval')
        if approval.get('base_glossary_sha256') not in (None,sha256(glossary)):raise Blocked('Short forms approval refers to a different base glossary')
        with open(path) as stream:forms={r['source'].casefold():r['short_target'] for r in csv.DictReader(stream) if r.get('short_target')}
    dependencies=fingerprint(book,glossary,style,do_not_translate)
    cache_key=hashlib.sha256(json.dumps({'implementation':'paragraph-fit-v6','payload':payload,'layout':layout,'dependencies':dependencies,'font_sha256':sha256(font_path) if font_path else None,'shortforms':forms},sort_keys=True).encode()).hexdigest()
    cached=book/'work'/f'fit-p{payload["page"]:04d}.json'
    if cached.exists():
        previous=json.loads(cached.read_text());output=Path(previous.get('output',''))
        if previous.get('cache_key')==cache_key and output.is_file() and sha256(output)==previous.get('output_sha256'):return previous
    extras=read_extras(do_not_translate);reports=[];failed=[]
    with fitz.open(book/'source.pdf') as original:
        original_page=original[payload['page']-1]
        if original_page.rotation:raise Blocked('Rotated PDF pages require coordinate normalization before editing')
        guards=protected_boxes(original_page,extras);units=extract_layout_units(original_page,extras);legacy=extract_units(original_page,extras)
        normalized=[];editable=[]
        for block in payload['blocks']:
            rect=fitz.Rect(block['bbox']);rotate=block.get('rotate',0)
            if rotate not in (0,90,180,270):raise Blocked('Arbitrary/radial rotation requires manual review')
            if protected(block['source'],block.get('font',''),extras):raise Blocked('Protected content must not be redacted or supplied as translation')
            if any(rect.intersects(r) for r in guards):raise Blocked('Redaction intersects protected characters')
            matched=[u for u in units+legacy if u['source']==block['source'] and fitz.Rect(u['bbox'])==rect]
            if not matched:raise Blocked('Block must match an extracted safe span or paragraph exactly')
            canonical=matched[0];item={**canonical,**block}
            if canonical.get('spans') and block.get('spans',canonical['spans'])!=canonical['spans']:raise Blocked('Source span provenance differs from extraction')
            item['spans']=canonical.get('spans',[canonical]);item['vector']=canonical.get('vector',block.get('vector',False));item['kind']=canonical.get('kind','label');item['context']=canonical.get('context','running_text')
            approved_label_target=forms.get(block['source'].casefold()) if item['kind']=='label' and item['context'] in ('figure_label','table_label','legend') else None
            if protected(block['target'],'',extras) and block['target']!=approved_label_target:raise Blocked('Protected target is not an explicitly approved label short form')
            if any(rect.intersects(r) for r in editable):raise Blocked('Overlapping edits require review')
            editable.append(rect);normalized.append(item)
        working=fitz.open();working.insert_pdf(original,from_page=payload['page']-1,to_page=payload['page']-1)
        used_boxes=[]
        try:
            for index,block in enumerate(normalized):
                rect=fitz.Rect(block['bbox']);target=format_cached_translation(block,block['target']);size=block.get('size',11)
                if target.strip()==block['source'].strip():
                    reports.append({'id':block.get('id',str(index)),'bbox':list(rect),'edit_bbox':list(rect),'source':block['source'],'target':target,'spans':block['spans'],'kind':block['kind'],'vector':block['vector'],'fit':True,'scale':1.,'review':False,'unchanged':True});used_boxes.append(rect);continue
                grown=expansion_candidates(original_page,block,units,guards)
                grown=[r for r in grown if not any(r.intersects(previous) for previous in used_boxes)]
                if block.get('expand_bbox'):
                    requested=fitz.Rect(block['expand_bbox'])
                    if requested in grown:grown.insert(0,requested)
                candidates=([*grown,rect] if block['kind']=='label' else [rect,*grown])
                # Try the natural font size before permitting conservative shrink.
                minscale=layout.get('vector_label_min_scale',1) if block['vector'] else layout.get('min_scale',.75)
                attempts=[];success=False;lastscale=minscale;chosen=None
                short_target=forms.get(block['source'].casefold()) if block['kind']=='label' and block.get('context') in ('figure_label','table_label','legend') else None
                targets=[(target,bool(short_target and target==short_target))]
                if short_target and short_target!=target:targets.append((short_target,True))
                for proposed,used_shortform in targets:
                    if protected(proposed,'',extras) and not used_shortform:raise Blocked('Target contains protected content without label approval')
                    for low in dict.fromkeys([1.,minscale]):
                        for candidate in candidates:
                            trial=fitz.open(stream=working.tobytes(),filetype='pdf');tp=trial[0]
                            redactions=safe_redaction_rects(original_page,block)
                            for r in redactions:tp.add_redact_annot(r,fill=False)
                            tp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,graphics=fitz.PDF_REDACT_LINE_ART_NONE)
                            css=f'body {{font-family:sans-serif;font-size:{size}pt;line-height:1.05;margin:0;padding:0}} p {{margin:0;padding:0}}'
                            archive=None
                            if font_path:
                                fp=Path(font_path).resolve();archive=fitz.Archive(str(fp.parent));css+=f'@font-face {{font-family:Book;src:url({fp.name});}} body {{font-family:Book;}}'
                            spare,scale=tp.insert_htmlbox(candidate,html.escape(proposed).replace('\n','<br>'),css=css,archive=archive,scale_low=low,rotate=block.get('rotate',0));lastscale=scale
                            import unicodedata
                            normalized_text=lambda value:''.join(c for c in unicodedata.normalize('NFKC',value) if not c.isspace() and c!='\u00ad')
                            if spare>=0 and normalized_text(proposed) not in normalized_text(tp.get_text()):spare=-1
                            attempts.append({'bbox':list(candidate),'scale_low':low,'fit':spare>=0,'scale':scale,'shortform':used_shortform})
                            if spare>=0:
                                working.close();working=trial;success=True;chosen=candidate;target=proposed;break
                            trial.close()
                        if success:break
                    if success:break
                if success:
                    used_boxes.append(chosen)
                    reports.append({'id':block.get('id',str(index)),'bbox':list(rect),'edit_bbox':list(chosen),'source':block['source'],'target':target,'spans':block['spans'],'kind':block['kind'],'vector':block['vector'],'fit':True,'scale':scale,'review':scale<layout.get('warn_scale',.8),'attempts':attempts,'redaction_rects':[list(r) for r in redactions],'context':block['context'],'target_before_format':block['target'],'shortform_used':used_shortform,'shortform_approval_sha256':sha256(shortforms['approval']) if used_shortform else None})
                else:
                    budget=shortening_budget(block,target,max(candidates,key=lambda r:r.get_area()))
                    if len(block['source'])>=layout.get('short_label_threshold',15):budget=min(budget,max(1,int(len(block['source'])*layout.get('shorten_ratio',1.1))))
                    reports.append({'id':block.get('id',str(index)),'bbox':list(rect),'source':block['source'],'target':target,'spans':block['spans'],'kind':block['kind'],'vector':block['vector'],'fit':False,'scale':lastscale,'review':True,'attempts':attempts})
                    failed.append({'id':str(index),'source':target,'original_source':block['source'],'max_chars':budget})
            if failed and shortener:
                shortened=shortener(failed);byid={x['id']:x['translation'] for x in shortened}
                if set(byid)!={x['id'] for x in failed} or len(shortened)!=len(failed):raise Blocked('Shortener ids differ from failed blocks')
                retry=json.loads(json.dumps(payload))
                for item in failed:
                    target=byid[item['id']]
                    if len(target)>item['max_chars'] or protected(target,'',extras):raise Blocked('Shortening violated limit or protection')
                    retry['blocks'][int(item['id'])]['target']=target
                return reinsert(book,retry,glossary,style,do_not_translate,shortener=None,font_path=font_path,layout=layout,shortforms=shortforms)
            result={'cache_key':cache_key,'page':payload['page'],'source_sha256':sha256(book/'source.pdf'),'blocks':reports,'status':'review' if any(x['review'] for x in reports) else 'pending_qa'}
            with book_lock(book):
                if all(x['fit'] for x in reports) and len(reports)==len(payload['blocks']):
                    original.delete_page(payload['page']-1);original.insert_pdf(working,start_at=payload['page']-1)
                    output=book/'work'/f'translated-p{payload["page"]:04d}.pdf';_save_pdf(original,output);result['output']=str(output);result['output_sha256']=sha256(output)
                atomic_json(cached,result)
                if result['status']=='review':
                    qp=book/'work/review_queue.json';queue=json.loads(qp.read_text()) if qp.exists() else [];queue=[x for x in queue if x['page']!=payload['page']]
                    queue.append({'page':payload['page'],'reason':'Fit failure or scale below warning threshold','artifacts':[str(cached)],'suggested_action':'Review layout, approved short forms, then grouped shortening'});atomic_json(qp,queue)
                manifest=json.loads((book/'work/manifest.json').read_text());state=manifest['pages'][payload['page']-1];state['attempts']+=1;state['status']='review' if result['status']=='review' else 'translated';state['dependencies']=dependencies;atomic_json(book/'work/manifest.json',manifest)
            return result
        finally:working.close()

def _symbol_spans(page):
    return Counter(span['text'] for block in page.get_text('dict')['blocks'] for line in block.get('lines',[]) for span in line['spans'] if SYMBOL_FONT.search(span['font']))

def _protected_characters_preserved(before,after,extras):
    def signatures(page,only_protected):
        counts=Counter()
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    text=''.join(c['c'] for c in span['chars'])
                    if only_protected and not protected(text,span['font'],extras):continue
                    for char in span['chars']:
                        counts[(char['c'],span['font'],tuple(round(x,3) for x in char['bbox']))]+=1
        return counts
    return not (signatures(before,True)-signatures(after,False))

def visual_qa(book,candidate,edits,glossary=None,vlm=None,human_pages=(),do_not_translate=None,selected_pages=None):
    book=Path(book); pages=[]; extras=read_extras(do_not_translate)
    if edits.get('source_sha256')!=sha256(book/'source.pdf'): raise Blocked('QA edits do not bind to source')
    if edits.get('output_sha256')!=sha256(candidate): raise Blocked('QA edits do not bind to candidate')
    with fitz.open(book/'source.pdf') as a,fitz.open(candidate) as b:
        if len(a)!=len(b): raise Blocked('Pagination changed')
        for i,(pa,pb) in enumerate(zip(a,b),1):
            if selected_pages is not None and i not in selected_pages:continue
            if pa.rotation or pb.rotation: raise Blocked('Rotated PDF pages require coordinate normalization before QA masking')
            blocks=edits.get('blocks',[]) if edits.get('page')==i else []
            before=pa.get_text(); after=pb.get_text()
            expected_new=Counter(token for block in blocks if block.get('shortform_used') and block.get('shortform_approval_sha256') for token in PROTECTED.findall(block['target']))
            equal=Counter(PROTECTED.findall(before))+expected_new==Counter(PROTECTED.findall(after)) and _symbol_spans(pa)==_symbol_spans(pb)
            protected_spans_equal=_protected_characters_preserved(pa,pb,extras)
            px=pa.get_pixmap(dpi=110); aa=Image.frombytes('RGB',(px.width,px.height),px.samples)
            px=pb.get_pixmap(dpi=110); bb=Image.frombytes('RGB',(px.width,px.height),px.samples)
            renderdir=book/'work/renders'; renderdir.mkdir(parents=True,exist_ok=True); aa.save(renderdir/f'p{i:04d}-before.png'); bb.save(renderdir/f'p{i:04d}-after.png')
            mask=Image.new('L',aa.size,255); draw=ImageDraw.Draw(mask)
            for block in blocks:
                r=fitz.Rect(block.get('edit_bbox',block['bbox'])); draw.rectangle([r.x0*110/72-2,r.y0*110/72-2,r.x1*110/72+2,r.y1*110/72+2],fill=0)
            import numpy as np
            delta=np.any(np.asarray(aa)!=np.asarray(bb),axis=2); valid=np.asarray(mask)>0; diff=float(delta[valid].mean()) if valid.any() else 0.
            missing=[]
            if glossary:
                from .language import glossary_matches
                with open(glossary) as stream: rows=list(csv.DictReader(stream))
                for term in glossary_matches(before,rows):
                    if not re.search(r'(?<!\w)'+re.escape(term['target'])+r'(?!\w)',after,re.I):
                        approved_labels=[x for x in blocks if x.get('shortform_used') and x.get('shortform_approval_sha256') and x.get('kind')=='label' and x['source'].casefold()==term['source'].casefold() and x['target'] in after]
                        source_count=len(re.findall(r'(?<!\w)'+re.escape(term['source'])+r'(?!\w)',before,re.I))
                        if len(approved_labels)<source_count:missing.append(term['source'])
            residual=[x['source'] for x in blocks if x['source'].casefold()!=x['target'].casefold() and re.search(r'(?<!\w)'+re.escape(x['source'])+r'(?!\w)',after,re.I)]
            from .language import residual_english,qa_texts
            allowed=list(extras)
            if glossary:
                with open(glossary) as stream: allowed.extend(row['target'] for row in csv.DictReader(stream))
            residual.extend(residual_english(qa_texts(pb,extras),allowed))
            residual=list(dict.fromkeys(residual))
            notes='Not run: full-page raster protection masks are unverified; local QA and human review only' if pa.get_images() else 'Not run: VLM evidence required'; complete=False
            if vlm and not pa.get_images():
                # Mask ALL source and candidate protected spans before a visual model sees either render.
                for p,im in ((pa,aa),(pb,bb)):
                    dd=ImageDraw.Draw(im)
                    for r in protected_boxes(p,extras): dd.rectangle([r.x0*110/72-3,r.y0*110/72-3,r.x1*110/72+3,r.y1*110/72+3],fill='black')
                response=vlm(aa,bb,i); complete=response.get('ok') is True and isinstance(response.get('residual_english'),list); notes=response.get('notes',''); residual.extend(response.get('residual_english',[]))
            elif vlm: notes='Blocked: raster images need locally verified protection masks'
            scale=min([x['scale'] for x in blocks] or [1]); graphics=bool(pa.get_images() or pa.get_drawings())
            deterministic_ok=equal and protected_spans_equal and scale>=.8 and diff<=.01 and not missing and not residual and complete and (not graphics or i in human_pages)
            ok=deterministic_ok
            pages.append({'page':i,'verdict':'ok' if ok else 'revisar','residual_english':residual,'residual_english_method':'Lingua EN/ES local confidence >=0.70 plus whole-word source matches; short labels require human review','glossary_missing':missing,'glyph_set_equal':equal,'protected_spans_equal':protected_spans_equal,'min_scale':scale,'non_text_pixel_diff':diff,'vlm_notes':notes,'vlm_complete':complete,'language_check_complete':True,'deterministic_ok':deterministic_ok,'human_graphics_review':i in human_pages})
    result={'source_sha256':sha256(book/'source.pdf'),'candidate_sha256':sha256(candidate),'pages':pages}
    with book_lock(book):
        atomic_json(book/'work/qa.json',result)
        for p in pages: atomic_json(book/'work/qa'/f'p{p["page"]:04d}.json',p)
        queue_path=book/'work/review_queue.json';existing_queue=json.loads(queue_path.read_text()) if queue_path.exists() else []; existing_queue=[x for x in existing_queue if x['page'] not in {p['page'] for p in pages}]
        atomic_json(book/'work/review_queue.json',existing_queue+[{'page':p['page'],'reason':'QA incomplete or preservation check failed','artifacts':[str(book/'work/qa'/f'p{p["page"]:04d}.json'),str(book/'work/renders'/f'p{p["page"]:04d}-before.png'),str(book/'work/renders'/f'p{p["page"]:04d}-after.png')],'suggested_action':'Review geometry, language, and masked VLM evidence; resolve all checks'} for p in pages if p['verdict']!='ok'])
        mp=book/'work/manifest.json'
        if mp.exists():
            manifest=json.loads(mp.read_text())
            for p in pages: manifest['pages'][p['page']-1]['status']='qa_ok' if p['verdict']=='ok' else 'review'
            atomic_json(mp,manifest)
    return result

def assemble(book,parts,toc,qa,manual_pages=()):
    book=Path(book)
    with fitz.open(book/'source.pdf') as source: count=len(source)
    if sorted(p['page'] for p in qa.get('pages',[]))!=list(range(1,count+1)) or any(p['verdict']!='ok' and p['page'] not in manual_pages for p in qa['pages']): raise Blocked('G6: incomplete QA or unresolved review pages')
    if qa.get('source_sha256')!=sha256(book/'source.pdf'): raise Blocked('QA source hash mismatch')
    if len(parts)!=1 or qa.get('candidate_sha256')!=sha256(parts[0]): raise Blocked('Assembly currently requires one complete hash-bound candidate; merge part QA first')
    if not isinstance(toc,dict) or toc.get('source_sha256')!=sha256(book/'source.pdf') or not toc.get('translation_provenance'): raise Blocked('Translated TOC must have source hash and script translation provenance (including empty TOC)')
    doc=fitz.open()
    for part in parts:
        with fitz.open(part) as p: doc.insert_pdf(p)
    if len(doc)!=count: raise Blocked('Assembly changed pagination')
    doc.set_toc(toc['entries']); doc.subset_fonts(); path=book/'out/translated.pdf'
    with book_lock(book):
        _save_pdf(doc,path); atomic_json(book/'out/report.json',{'output_sha256':sha256(path),'manual_pages':list(manual_pages),'qa':qa})
        (book/'out/informe_calidad.md').write_text('# Informe de calidad\n\nG7: revisión humana final pendiente.\n\nCasos manuales explícitos: '+', '.join(map(str,manual_pages))+'\n\nCostos: consultar work/costs.jsonl; ausencia de tarifa = desconocido.\n',encoding='utf-8')
    doc.close(); return {'output':str(path)}
