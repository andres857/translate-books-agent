"""Reviewable short forms, separate from G3, never implicit model approval.

Composition first exhausts full-target free-space fitting down to 0.75. Only
then may it consult a reviewed supplement bound to source, styles, geometry,
full target and dependencies. Every proposal still has to pass PDF ink guards.
"""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import pymupdf as fitz
from .core import Blocked,atomic_json,sha256


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def dependencies(book,glossary,style,protected):
    return {name:sha256(path) if path else None for name,path in
        [('source',Path(book)/'source.pdf'),('glossary',glossary),('style',style),('protected',protected)]}


def make_request(page,unit,boxes,font_map):
    if (unit.get('story_frames') or unit.get('native_ordinal') or unit.get('row_parent_id')
        or unit.get('short_form')
        or not (unit['kind'] in ('label','caption') or unit['source'].isupper())):return None
    # One style per short form avoids fabricating mappings of new words onto
    # mixed emphasis. Other units stay explicit layout/editorial review.
    styles={tuple(sorted(r['style'].items())) for r in unit['target_runs'] if r['text'].strip()}
    if len(styles)!=1:return None
    style=next(r['style'] for r in unit['target_runs'] if r['text'].strip())
    choice=font_map[(unit['scale_group'],tuple(sorted(style.items())))];font=choice['font']
    width=max((b.width for b in boxes),default=fitz.Rect(unit['bbox']).width)
    full_width=font.text_length(unit['target'],fontsize=style['size']*.75)
    budget=max(1,min(len(unit['target'])-1,int(len(unit['target'])*width/max(full_width,1))))
    scope={'page':page,'unit_id':unit['id'],'source':unit['source'],'full_target':unit['target'],
           'spans':unit['spans'],'target_runs':unit['target_runs'],'bbox':unit['bbox'],
           'scale_group':unit['scale_group'],'min_scale':.75,'max_width_pt':width,
           'max_chars':budget,'candidate_boxes':[list(b) for b in boxes],
           # Cached instances include timestamps in their byte hash. Bind the
           # review to outlines/axes/calibration, not an artifact directory.
           'font_identity':{'source':choice['source'],
               'base_sha256':choice.get('base_sha256',choice['sha256']),
               'axes':choice.get('axes'),'metric_calibration':choice.get('metric_calibration')},
           'font_size':style['size'],
           'geometry_blocked':not bool(boxes)}
    return {**scope,'request_sha256':digest(scope)}


def approve(folder,proposals,review):
    """Explicit CLI action with an externally supplied, content-bound review."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if (not isinstance(review.get('approved_by'),str) or not review['approved_by'].strip()
        or review.get('proposals_sha256')!=digest(proposals)):
        raise Blocked('Review must identify reviewer and exact proposals hash')
    ids=review.get('request_sha256s',[]);byid={p['request_sha256']:p for p in proposals['proposals']}
    if not ids or len(ids)!=len(set(ids)) or not set(ids)<=byid.keys():raise Blocked('Review selection invalid')
    forms=[byid[i] for i in ids]
    if any(not p.get('short_target') or not p.get('provenance',{}).get('backend') for p in forms):raise Blocked('Proposal missing model provenance or target')
    import fcntl
    with (folder/'.review.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        versions=[int(p.stem.split('-v')[-1]) for p in folder.glob('approved-v*.json') if re.fullmatch(r'approved-v\d+',p.stem)]
        version=max(versions,default=0)+1
        content={'version':version,'dependencies':proposals['dependencies'],'forms':forms,
                 'review':review,'approved_at':datetime.now(timezone.utc).isoformat()}
        result={**content,'sha256':digest(content)};file=folder/f'approved-v{version}.json'
        # Refuse replacement even when concurrent approvals choose the same version.
        atomic_json(file,result)
        return file


def load_approved(folder,expected_dependencies):
    approved={}
    paths=sorted(Path(folder).glob('approved-v*.json'),key=lambda p:int(p.stem.split('-v')[-1]))
    for file in paths:
        data=json.loads(file.read_text());content={k:v for k,v in data.items() if k!='sha256'}
        if data.get('sha256')!=digest(content):raise Blocked('Short form supplement hash changed')
        if data.get('dependencies')!=expected_dependencies:raise Blocked('Short form supplement dependencies changed')
        if not data.get('review',{}).get('approved_by'):raise Blocked('Unsigned short form supplement')
        for row in data['forms']:
            if row['request_sha256'] not in data['review'].get('request_sha256s',[]):raise Blocked('Unreviewed short form selection')
            approved[row['request_sha256']]={**row,'supplement_sha256':data['sha256'],'supplement':str(file)}
    return approved


def apply_approved(unit,request,approved,glossary,extras):
    row=approved.get(request['request_sha256'])
    if row is None:return None
    from .retranslation import validate_answer
    if row['source']!=unit['source'] or row['full_target']!=unit['target']:raise Blocked('Short form source/target changed')
    target=row['short_target']
    if unit['target'].isupper() and not target.isupper():raise Blocked('Short form changes display case')
    if not target.strip() or len(target)>request['max_chars']:raise Blocked('Short form exceeds approved geometry budget')
    # Validate conservation and terminology against the existing full Spanish
    # target (shorten semantics), including all protected values.
    from .language import glossary_matches
    required=glossary_matches(unit['target'],glossary,key='target')
    validate_answer({'id':unit['id'],'source':unit['target']},target,extras=extras)
    for term in required:
        if not re.search(r'(?<!\w)'+re.escape(term['target'])+r'(?!\w)',target,re.I):raise Blocked('Short form drops required glossary term')
    revised=deepcopy(unit);revised['target']=target
    revised['target_runs']=[{'text':target,'style':deepcopy(unit['target_runs'][0]['style'])}]
    revised['short_form']={'full_target':unit['target'],'request_sha256':request['request_sha256'],
        'supplement_sha256':row['supplement_sha256'],'supplement':row['supplement']}
    return revised
