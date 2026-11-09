"""Prepare, propose (explicit inference), and import a signed review supplement."""
import argparse
import json
from pathlib import Path
from astro_translate import llm
from astro_translate.core import Blocked,atomic_json,sha256,book_lock
from astro_translate.production import Production,read,selected_pages
from astro_translate.short_forms import dependencies,digest,approve,load_approved


def prepare(r,diagnostics,pages):
    requests=[];artifacts={}
    for n in selected_pages(pages,100000):
        path=Path(diagnostics)/f'p{n:04d}-short-form-requests.json'
        if not path.exists():continue
        data=read(path)
        if data['dependencies']!=dependencies(r.book,r.glossary,r.style,r.protected):raise Blocked('Stale short-form diagnostics')
        requests+=data['requests'];artifacts[str(path.resolve())]=sha256(path)
    return {'dependencies':dependencies(r.book,r.glossary,r.style,r.protected),
            'config_sha256':sha256(r.config_path),'requests':requests,'artifacts':artifacts,'llm_calls':0}


def propose(r,plan,destination):
    if plan['dependencies']!=dependencies(r.book,r.glossary,r.style,r.protected) or plan['config_sha256']!=sha256(r.config_path):raise Blocked('Stale proposal dependencies')
    if any(not Path(p).is_file() or sha256(p)!=h for p,h in plan['artifacts'].items()):raise Blocked('Changed fit diagnostics')
    r.gates();llm._check_pause(r.book)
    old=read(destination,{'dependencies':plan['dependencies'],'proposals':[]})
    if old['dependencies']!=plan['dependencies']:raise Blocked('Existing proposal dependencies changed')
    done={x['request_sha256'] for x in old['proposals']}
    done.update(load_approved(r.work/'short_forms',plan['dependencies']))
    pending=[x for x in plan['requests'] if x['request_sha256'] not in done]
    # Page/figure bounded calls, never an automatic call from composition.
    for n in sorted({x['page'] for x in pending}):
        group=[x for x in pending if x['page']==n]
        if sum(len(x['full_target']) for x in group)>6000:raise Blocked('Proposal batch exceeds 6000 characters')
        blocks=[{'id':f'p{n:04d}_'+x['unit_id'],'source':x['full_target'],'max_chars':x['max_chars']} for x in group]
        result=llm.run_task('shorten',{'_book':str(r.book),'_config':str(r.config_path),'_phase':'production',
            '_pages':[n],'blocks':blocks,
            'coordination_instruction':'Propose concise equivalent labels, preserving meaning, emphasis/case and every glossary term. No new facts. Keep protected placeholders unchanged. These are proposals for review, never approvals.',
            'meaning_context':[{'id':b['id'],'source':x['source']} for b,x in zip(blocks,group)],
            'geometry_constraints':[{'id':b['id'],'max_width_pt':x['max_width_pt'],'font_size_pt':x['font_size']*.75,'min_scale':.75} for b,x in zip(blocks,group)]})
        answers={x['id']:x['translation'] for x in result.data['blocks']}
        if set(answers)!={b['id'] for b in blocks} or len(answers)!=len(result.data['blocks']):raise Blocked('Short form ids differ')
        provenance={'entry_point':'astro_translate.llm.run_task','backend':result.backend,
            'model':result.model,'response_sha256':digest(result.data)}
        additions=[{**x,'short_target':answers[b['id']],'provenance':provenance} for b,x in zip(blocks,group)]
        if any(not x['short_target'].strip() or len(x['short_target'])>x['max_chars'] for x in additions):raise Blocked('Short form character budget failed')
        with book_lock(r.book):
            latest=read(destination,{'dependencies':plan['dependencies'],'proposals':[]})
            if latest['dependencies']!=plan['dependencies']:raise Blocked('Concurrent proposal dependency change')
            existing={x['request_sha256'] for x in latest['proposals']}
            latest['proposals'] += [x for x in additions if x['request_sha256'] not in existing]
            atomic_json(destination,latest)
    return read(destination,old)


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','propose','approve']);p.add_argument('--book',required=True)
    p.add_argument('--pages',default='41-60');p.add_argument('--diagnostics',type=Path);p.add_argument('--plan',type=Path)
    p.add_argument('--review',type=Path);p.add_argument('--execute',action='store_true');a=p.parse_args()
    r=Production(a.book);folder=r.work/'short_forms';planpath=a.plan or folder/'plan.json'
    if not planpath.resolve().is_relative_to(r.work):raise Blocked('Plan must reside below book/work')
    if a.command=='prepare':
        if a.diagnostics is None:p.error('--diagnostics required')
        data=prepare(r,a.diagnostics,a.pages);atomic_json(planpath,data)
        print(json.dumps({'requests':len(data['requests']),'plan':str(planpath),'llm_calls':0}))
    elif a.command=='propose':
        if not a.execute:p.error('propose requires --execute (real LLM call)')
        data=propose(r,read(planpath),folder/'proposals.json')
        print(json.dumps({'proposals':len(data['proposals']),'proposals_sha256':digest(data),'review_request_sha256s':[x['request_sha256'] for x in data['proposals']]}))
    else:
        if a.review is None:p.error('--review required: externally signed, content-bound JSON')
        data=read(folder/'proposals.json')
        if data['dependencies']!=dependencies(r.book,r.glossary,r.style,r.protected):raise Blocked('Stale proposals')
        print(approve(folder,data,read(a.review)))

if __name__=='__main__':
    try:main()
    except llm.QuotaPaused as exc:
        print(str(exc));raise SystemExit(75)
