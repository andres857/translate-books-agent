"""Exact source memory and deterministic reuse across repeated clauses.

This module never performs inference. Sentence/overlap planning yields blocks
for one bounded multi-page run_task call, then restores location punctuation and
source typography without translating anything locally.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from .numeric_order import numeric_quantifier_issues
from .text_quality import text_issues, marked_style_issues

STYLE_MARKER=re.compile(r'\{v\d+\}')
MEMORY_VERSION='fidelity-clauses-v1'


def normalize_source(text):
    text=unicodedata.normalize('NFC',text)
    text=re.sub(r'\s+',' ',text).strip()
    return re.sub(r'(?<=[^\d\s]) +([.,!?;:])',r'\1',text)


def _ranges(text):
    start=0
    for match in re.finditer(r'[.!?](?=\s|$)',text):
        token=re.search(r'([A-Za-z.]+)$',text[:match.start()])
        if token and token.group(1).lower() in ('mr','mrs','ms','dr','prof','e.g','i.e','st'):continue
        yield start,match.end();start=match.end()
    if start<len(text):yield start,len(text)


def _core_range(text,start,end):
    while start<end and (text[start].isspace() or text[start] in ',;:'):start+=1
    while end>start and (text[end-1].isspace() or text[end-1] in '.!?'):end-=1
    return start,end


def _style_at(unit,index):
    for run in unit.get('style_runs',[]):
        if run['start']<=index<run['end']:return run['style']
    return unit.get('style',{})


def _annotate(unit,start,end,cells=(),starts=None):
    """Style-marked source; ``cells`` are offsets that open a run even with an equal style."""
    text=unit['source'];runs=[]
    for index in range(start,end):
        style=_style_at(unit,index)
        if runs and runs[-1]['style']==style and index not in cells:runs[-1]['text']+=text[index]
        else:runs.append({'text':text[index],'style':style,'cell_start':bool(runs) and index in cells})
    if len(runs)<=1:return normalize_source(text[start:end]),[],[]
    if starts is not None:starts.extend(i for i,run in enumerate(runs) if run['cell_start'])
    marked='';markers=[];styles=[]
    for index,run in enumerate(runs,1):
        marker='{v'+str(index)+'}';markers.append(marker);styles.append(run['style']);marked+=marker+run['text']
    markers.append('{v'+str(len(runs)+1)+'}');marked+=markers[-1]
    return marked,markers,styles


def _cells(unit):
    from .structured_layout import field_cell_starts
    return set(field_cell_starts(unit))


JOINT_TAG=' \u2016'  # memory key of a joined sentence; never equal to an unbroken sentence


def _runs_between(unit,start,end):
    runs=[]
    for index in range(start,end):
        style=_style_at(unit,index)
        if runs and runs[-1]['style']==style:runs[-1]['text']+=unit['source'][index]
        else:runs.append({'text':unit['source'][index],'style':style})
    return runs


def _joints(units,joins):
    """Sentences split between two units (page_context.story_continuations).

    The tail's last sentence and the head's first sentence form one entry; a
    style marker at the break partitions the answer (auditor batch-21, p14/p15).
    Returns {(unit id, sentence index): (side, joint)}.
    """
    from .page_context import STORY_BREAK_INSTRUCTIONS
    byid={u['id']:u for u in units};found={}
    for tail_id,head_id in joins:
        tail,head=byid.get(tail_id),byid.get(head_id)
        if tail is None or head is None or tail_id==head_id:continue
        tail_ranges,head_ranges=list(_ranges(tail['source'])),list(_ranges(head['source']))
        if not tail_ranges or not head_ranges:continue
        tail_key,head_key=(tail_id,len(tail_ranges)-1),(head_id,0)
        if tail_key in found or head_key in found or _cells(tail) or _cells(head):continue
        ta,tb=_core_range(tail['source'],*tail_ranges[-1]);ha,hb=_core_range(head['source'],*head_ranges[0])
        if ta>=tb or ha>=hb or tail['source'][tb:tail_ranges[-1][1]].strip():continue
        before,after=_runs_between(tail,ta,tb),_runs_between(head,ha,hb)
        after[0]['text']=' '+after[0]['text']
        runs=before+after
        markers=['{v'+str(i+1)+'}' for i in range(len(runs)+1)]
        canonical=normalize_source(tail['source'][ta:tb]+' '+head['source'][ha:hb])
        joint={'key':hashlib.sha256(('story-break\n'+canonical).encode()).hexdigest(),
               'source':canonical+JOINT_TAG,'marked':''.join(m+r['text'] for m,r in zip(markers,runs))+markers[-1],
               'markers':markers,'styles':[r['style'] for r in runs],'boundary':len(before),
               'tail':tail_id,'head':head_id,'tail_page':tail['page'],
               'break':{'marker':markers[len(before)],'boundary':'page' if tail['page']!=head['page'] else 'column',
                        'instructions':STORY_BREAK_INSTRUCTIONS}}
        found[tail_key]=('tail',joint);found[head_key]=('head',joint)
    return found


def build_plan(units,joins=()):
    """Deduplicate complete sentences and shared quoted prefixes/clauses.

    ``joins`` are (tail unit id, head unit id) pairs whose boundary sentence is
    sent once, joined (page_context.story_continuations).
    """
    joints=_joints(units,joins)
    sources=[]
    for unit in units:
        for sentence,(start,end) in enumerate(_ranges(unit['source'])):
            if (unit['id'],sentence) in joints:continue
            a,b=_core_range(unit['source'],start,end)
            if a<b:sources.append(unit['source'][a:b])
    # A shorter complete sentence reused as a prefix of a longer body sentence
    # remains one memory entry; location-specific full stop/comma stays local.
    anchors=set()
    for candidate in sources:
        if len(candidate)<20 or len(candidate.split())<4:continue
        pattern=r'(?<!\w)'+re.escape(candidate)+r'(?!\w)'
        if any(len(other)>len(candidate) and re.search(pattern,other) for other in sources):anchors.add(candidate)
    occurrences=[];groups={};unit_plans=[];joint_keys={}
    for unit in units:
        pieces=[];text=unit['source'];cells=_cells(unit)
        for sentence,(start,end) in enumerate(_ranges(text)):
            if (unit['id'],sentence) in joints:
                side,joint=joints[(unit['id'],sentence)];core_a,core_b=_core_range(text,start,end)
                part=[0,joint['boundary']] if side=='tail' else [joint['boundary'],len(joint['styles'])]
                occurrence={'key':joint['key'],'source':normalize_source(text[core_a:core_b])+JOINT_TAG+side,
                            'unit_id':unit['id'],'page':unit['page'],'prefix':text[start:core_a],'suffix':text[core_b:end],
                            'style':_style_at(unit,core_a),'marked':joint['marked'],'markers':joint['markers'],
                            'styles':joint['styles'],'slice':part,'side':side,
                            'partner':joint['head' if side=='tail' else 'tail']}
                index=len(occurrences);occurrences.append(occurrence);pieces.append({'occurrence':index,'sentence':sentence})
                groups.setdefault(joint['key'],[]).append(index);joint_keys[joint['key']]=joint
                continue
            cuts={start,end}
            for anchor in sorted(anchors,key=len,reverse=True):
                for match in re.finditer(r'(?<!\w)'+re.escape(anchor)+r'(?!\w)',text[start:end]):
                    cuts.update((start+match.start(),start+match.end()))
            ordered=sorted(cuts)
            for a,b in zip(ordered,ordered[1:]):
                core_a,core_b=_core_range(text,a,b)
                if core_a==core_b:
                    pieces.append({'literal':text[a:b],'style':_style_at(unit,a),'sentence':sentence});continue
                canonical=normalize_source(text[core_a:core_b]);starts=[]
                marked,markers,styles=_annotate(unit,core_a,core_b,cells,starts)
                key=hashlib.sha256(canonical.encode()).hexdigest()
                occurrence={'key':key,'source':canonical,'unit_id':unit['id'],'page':unit['page'],'prefix':text[a:core_a],'suffix':text[core_b:b],'style':_style_at(unit,core_a),'marked':marked,'markers':markers,'styles':styles}
                if starts:occurrence['cell_starts']=starts
                index=len(occurrences);occurrences.append(occurrence);pieces.append({'occurrence':index,'sentence':sentence})
                groups.setdefault(key,[]).append(index)
        unit_plans.append({'unit':unit,'pieces':pieces})
    from .page_context import initial_upper_units
    headings=initial_upper_units(units)
    sole={item['unit']['id'] for item in unit_plans if sum('occurrence' in p for p in item['pieces'])==1}
    def heading(occurrence):
        # The whole unit is this clause: it opens the heading line.
        return (occurrence['unit_id'] in headings and occurrence['unit_id'] in sole and not occurrence['prefix'].strip()
                and not re.search(r'\w',occurrence['suffix']))
    requests=[];entries={}
    for key,indexes in groups.items():
        selected=max((occurrences[i] for i in indexes),key=lambda x:len(x['markers']))
        joint=joint_keys.get(key)
        identifier=f'p{(joint["tail_page"] if joint else selected["page"]):04d}_s{key[:20]}'
        request={'id':identifier,'source':selected['marked']}
        if selected['markers']:request['_style_markers']=selected['markers']
        source=joint['source'] if joint else selected['source']
        entry={'id':identifier,'source':source,'marked_source':selected['marked'],'markers':selected['markers'],'styles':selected['styles'],'pages':sorted({occurrences[i]['page'] for i in indexes})}
        if joint:
            request['_story_break']=joint['break'];entry['story_break']=joint['break']['marker']
        elif all(heading(occurrences[i]) for i in indexes):
            request['_initial_upper']=True;entry['initial_upper']=True
        requests.append(request);entries[key]=entry
    return {'version':MEMORY_VERSION,'requests':requests,'entries':entries,'occurrences':occurrences,'units':unit_plans}


def _decode(entry,translation):
    if numeric_quantifier_issues(entry['marked_source'],translation):
        raise ValueError('Displaced numeric quantifier; retranslate via llm.run_task')
    if text_issues(entry['marked_source'],translation,entry['id']) or marked_style_issues(entry['marked_source'],translation,entry['id']):
        raise ValueError('Text quality failed; retranslate via llm.run_task')
    markers=entry['markers']
    if not markers:return re.sub(r'[.!?]+$','',translation.strip()),None
    if STYLE_MARKER.findall(translation)!=markers or not translation.strip().startswith(markers[0]) or not translation.strip().endswith(markers[-1]):raise ValueError('Style markers changed order or boundary')
    runs=[]
    for index,marker in enumerate(markers[:-1]):
        text=translation.split(marker,1)[1].split(markers[index+1],1)[0]
        runs.append({'text':text,'style':entry['styles'][index]})
    if runs:
        runs[0]['text']=runs[0]['text'].lstrip();runs[-1]['text']=re.sub(r'[.!?]+$','',runs[-1]['text'].rstrip())
    return ''.join(r['text'] for r in runs),runs


def _append(runs,text,style):
    if not text:return
    if runs and runs[-1]['style']==style:runs[-1]['text']+=text
    else:runs.append({'text':text,'style':style})


def _insert(runs,offset,text):
    """Insert at a character offset, in the style of the text that follows it."""
    for run in runs:
        if offset<len(run['text']):run['text']=run['text'][:offset]+text+run['text'][offset:];return
        offset-=len(run['text'])
    if runs:runs[-1]['text']+=text


def _restore_openings(runs,start):
    """Spanish «¡»/«¿» for a sentence whose final «!»/«?» was kept local.

    The memory core is sent without its terminal mark, so the opening mark is
    composition punctuation of this occurrence, placed at the start of the
    exclamative/interrogative clause (inside an opening quote that encloses it).
    """
    from .text_quality import opening_punctuation_issues
    text=''.join(r['text'] for r in runs)[start:]
    for issue in reversed(opening_punctuation_issues(text,'')):
        region=text[issue['start']:issue['end']]
        quote=max(region.rfind(q) for q in '“‘«')
        index=issue['start']+quote+1 if quote>=0 and re.search(r'[!?][”’»]',region) else issue['start']
        while index<issue['end'] and not (text[index].isalnum() or text[index]=='{'):index+=1
        if index>=issue['end']:continue
        _insert(runs,start+index,issue['opening']);text=text[:index]+issue['opening']+text[index:]


def resolve_plan(plan,answers):
    decoded={key:_decode(entry,answers[entry['id']]) for key,entry in plan['entries'].items()}
    units=[];occurrences=[]
    for item in plan['units']:
        unit=item['unit'];runs=[];sentence=None;sentence_start=0;links=[]
        for piece in item['pieces']:
            if piece.get('sentence')!=sentence:
                if sentence is not None:_restore_openings(runs,sentence_start)
                sentence=piece.get('sentence');sentence_start=sum(len(r['text']) for r in runs)
            if 'literal' in piece:_append(runs,piece['literal'],piece['style']);continue
            occurrence=plan['occurrences'][piece['occurrence']];entry=plan['entries'][occurrence['key']];plain,styled=decoded[occurrence['key']]
            _append(runs,occurrence['prefix'],occurrence['style'])
            if 'slice' in occurrence:
                # One fragment of a sentence joined across a column/page break.
                if occurrence['marked']!=entry['marked_source']:raise ValueError('Joined sentence has a different style partition; alignment review required')
                start,end=occurrence['slice']
                part=[{'text':styled[i]['text'],'style':occurrence['styles'][i]} for i in range(start,end)]
                if occurrence['side']=='tail':part[-1]['text']=part[-1]['text'].rstrip()
                else:part[0]['text']=part[0]['text'].lstrip()
                plain=''.join(r['text'] for r in part)
                if not re.search(r'\w',plain):raise ValueError('Story break left an empty fragment; retranslate via llm.run_task')
                for run in part:_append(runs,run['text'],run['style'])
                links.append({'side':occurrence['side'],'partner':occurrence['partner'],'sentence_key':occurrence['key'],
                              'boundary':'page' if len(entry['pages'])>1 else 'column'})
            elif occurrence['markers']:
                if occurrence['marked']!=entry['marked_source']:raise ValueError('Repeated styled clause has a different style partition; alignment review required')
                for index,run in enumerate(styled or []):
                    if index in occurrence.get('cell_starts',()):runs.append({'text':run['text'],'style':occurrence['styles'][index],'cell_start':True})
                    else:_append(runs,run['text'],occurrence['styles'][index])
            else:_append(runs,plain,occurrence['style'])
            _append(runs,occurrence['suffix'],runs[-1]['style'] if runs else occurrence['style'])
            occurrences.append({'source':occurrence['source'],'target':plain,'page':unit['page'],'id':unit['id'],'sentence_key':occurrence['key']})
        if sentence is not None:_restore_openings(runs,sentence_start)
        resolved={**unit,'target':''.join(r['text'] for r in runs),'target_runs':runs}
        if links:resolved['story_continuation']=links
        units.append(normalize_complete_question(resolved))
    return {'units':units,'occurrences':occurrences,'issues':check_consistency(occurrences)}


def check_consistency(occurrences):
    groups={}
    for row in occurrences:groups.setdefault(normalize_source(row['source']),[]).append(row)
    return [{'source':source,'reason':'different_translations_for_same_source','occurrences':rows} for source,rows in groups.items() if len({normalize_source(row['target']) for row in rows})>1]


class SentenceMemory:
    """Exact canonical-source memory bound to all translation dependencies."""
    def __init__(self,work:Path,context:str):
        Path(work).mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(Path(work)/'llm_cache.sqlite',timeout=30);self.context=context
        self.db.execute('CREATE TABLE IF NOT EXISTS sentence_memory (context TEXT, source TEXT, entry TEXT NOT NULL, PRIMARY KEY(context,source))')
    def get(self,entry):
        found=self.db.execute('SELECT entry FROM sentence_memory WHERE context=? AND source=?',(self.context,entry['source'])).fetchone()
        if not found:return None
        saved=json.loads(found[0])
        if numeric_quantifier_issues(entry['source'],saved['plain']):return None
        from .text_quality import lowercase_initial
        if entry.get('initial_upper') and lowercase_initial(saved['plain']):return None
        if text_issues(entry['source'],saved['plain'],entry['id']) or marked_style_issues(saved['marked_source'],saved['translation'],entry['id']):return None
        if not entry['markers']:return saved['plain']
        if saved['marked_source']==entry['marked_source']:return saved['translation']
        raise ValueError('Exact translation exists but new inline-style alignment needs review')
    def put(self,entry,translation,*,replace=False):
        """``replace`` only for a saved entry already rejected by a cross-unit check
        (page term consistency or a glossary phrase split over stacked lines)."""
        plain,_=_decode(entry,translation)
        found=self.db.execute('SELECT entry FROM sentence_memory WHERE context=? AND source=?',(self.context,entry['source'])).fetchone()
        if found and not replace:
            previous=json.loads(found[0])['plain']
            if previous!=plain and not numeric_quantifier_issues(entry['source'],previous) and not text_issues(entry['source'],previous,entry['id']):raise ValueError('Exact sentence memory conflict')
        saved={'plain':plain,'translation':translation,'marked_source':entry['marked_source']}
        self.db.execute('INSERT OR REPLACE INTO sentence_memory VALUES (?,?,?)',(self.context,entry['source'],json.dumps(saved,ensure_ascii=False)));self.db.commit()
    def close(self):self.db.close()


def check_rendered_consistency(occurrences,page_texts):
    """Verify reused targets actually appear at each required page occurrence.

    This supplements memory validation: comparing only planned targets cannot
    detect missing or altered text in the rendered PDF. Typography whitespace,
    ligatures and case are normalized, while words and their order stay fixed.
    """
    issues=check_consistency(occurrences);groups={}
    for row in occurrences:groups.setdefault((row['page'],normalize_source(row['source'])),[]).append(row)
    for (page,source),rows in groups.items():
        # Distinct sources translated identically do not consume each other's
        # expected occurrence counts; repeated copies of one source do.
        targets={normalize_source(row['target']) for row in rows}
        if len(targets)!=1:continue
        target=next(iter(targets));text=normalize_source(unicodedata.normalize('NFKC',page_texts.get(page,''))).casefold()
        needle=normalize_source(unicodedata.normalize('NFKC',target)).casefold()
        actual=len(re.findall(r'(?<!\w)'+re.escape(needle)+r'(?!\w)',text)) if needle else 0
        if actual<len(rows):issues.append({'source':source,'target':target,'page':page,'reason':'expected_translation_missing_in_render','expected_occurrences':len(rows),'observed_occurrences':actual})
    return issues


def normalize_complete_question(unit):
    """Restore Spanish opening punctuation on a complete question unit only.

    This is punctuation composition, not translation. Sentence memory keeps its
    punctuation-free core; source quotation/fragment units are left untouched.
    """
    source=unit.get('source','').strip();target=unit.get('target','')
    if (not source.endswith('?') or len(list(_ranges(source)))!=1
            or source.startswith(('"','“','‘',"'"))
            or unit.get('kind') in ('quote','fragment') or unit.get('context') in ('quote','fragment')
            or not target.rstrip().endswith('?') or '¿' in target):return unit
    copied=json.loads(json.dumps(unit));runs=copied.get('target_runs',[])
    for run in runs:
        if run['text'].strip():
            first=len(run['text'])-len(run['text'].lstrip());run['text']=run['text'][:first]+'¿'+run['text'][first:];break
    if runs:copied['target']=''.join(run['text'] for run in runs)
    else:
        first=len(target)-len(target.lstrip());copied['target']=target[:first]+'¿'+target[first:]
    copied['editorial_punctuation']='opening_inverted_question_mark'
    return copied


# ---------------------------------------------------------------- cross-page phrase consistency

EN_STOP = frozenset('a an and or of the in on at to for with by from is are its it as this that these those your our their'.split())
ES_STOP = frozenset('a al de del el la las lo los un una unos unas y o u e en que por para con se su sus es son'.split())
WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
# Pronouns, auxiliaries, modals, conjunctions and adverbs: a phrase holding one is
# not a nominal phrase (its wording may legitimately change with the sentence).
EN_FUNCTION = frozenset('''i you he she we they me him her us them my mine yours his hers ours theirs itself yourself
themselves myself who whom whose which what when where why how if but so than then there here not no nor
be been being am was were has have had do does did can could may might must shall should will would
very more most much many some any each every all also just only even still yet about into through over
under up down out off again further once both either neither own same such too can't don't won't'''.split())


def _fold_word(word):
    word = ''.join(c for c in unicodedata.normalize('NFD', word.casefold()) if unicodedata.category(c) != 'Mn')
    if len(word) > 5 and word.endswith('es'):
        return word[:-2]
    if len(word) > 3 and word.endswith('s'):
        return word[:-1]
    return word


def _source_phrases(text, sizes=range(3, 7)):
    """Source n-grams (3–6 words) that start and end with a content word and hold ≥2 of them."""
    words = [w.casefold() for w in WORD_RE.findall(text)]
    found = set()
    for n in sizes:
        for i in range(len(words)-n+1):
            gram = words[i:i+n]
            if (gram[0] in EN_STOP or gram[-1] in EN_STOP or sum(w not in EN_STOP for w in gram) < 2
                    or any(w in EN_FUNCTION for w in gram)):
                continue
            found.add(' '.join(gram))
    return found


def _content_target(unit, glossary):
    """Target words with their stems, glossary target forms and source-copied words masked."""
    from .language import glossary_matches
    target = unit.get('target', '')
    for row in glossary_matches(unit.get('source', ''), glossary) if glossary else []:
        if row.get('target'):
            target = re.sub(r'(?<!\w)'+re.escape(row['target'])+r'(?!\w)', lambda m: ' '*len(m.group()), target, flags=re.I)
    copied = {w.casefold() for w in WORD_RE.findall(unit.get('source', '')) if len(w) >= 2}
    tokens = []
    for match in WORD_RE.finditer(target):
        word = match.group()
        if word.casefold() in copied or word.casefold() in ES_STOP:
            tokens.append((match.start(), match.end(), None))
        else:
            tokens.append((match.start(), match.end(), _fold_word(word)))
    return target, tokens


def _window(target, tokens, stems):
    """Shortest token window holding every stem and no other content word:
    (text, token count, stems in order) or None."""
    best = None
    for i, (_s, _e, stem) in enumerate(tokens):
        if stem not in stems:
            continue
        seen = set()
        for j in range(i, len(tokens)):
            if tokens[j][2] in stems:
                seen.add(tokens[j][2])
            if seen == stems:
                if best is None or j-i < best[1]-best[0]:
                    best = (i, j)
                break
    if best is None:
        return None
    i, j = best
    inner = [t[2] for t in tokens[i:j+1] if t[2] is not None]
    if any(stem not in stems for stem in inner) or re.search(r'\d', target[tokens[i][0]:tokens[j][1]]):
        return None  # other content words or numbers (index folios) inside: not the same phrase
    return target[tokens[i][0]:tokens[j][1]], j-i+1, inner


def phrase_consistency_issues(units, book_units, glossary=()):
    """Same source phrase on different pages rendered differently (audit batch-17, p23/p139).

    ``units`` are the page under QA; ``book_units`` saved units of other pages (with
    ``page``). A repeated source phrase (3–6 words, maximal) needs ≥2 content words
    that are translated (not copied, like MC/IC). Its Spanish rendering is the
    shortest window holding the content stems shared by both targets, after masking
    glossary target forms (the validator owns them) and source-copied words. The
    window may hold only those stems and function words, in the same order on both
    pages; more shared stems than translated words + 1, or a window longer than
    2k+2 tokens, is ambiguous and skipped. What remains differs in articles,
    prepositions or number («mitad de los cielos» / «mitad del cielo») and is
    reported (minor, retranslate). Evidence only: no target is changed.
    """
    index = {}
    for other in book_units:
        for phrase in _source_phrases(other.get('source', '')):
            index.setdefault(phrase, []).append(other)
    prepared = {}

    def prepare(unit):
        key = id(unit)
        if key not in prepared:
            target, tokens = _content_target(unit, glossary)
            words = {w.casefold() for w in WORD_RE.findall(unit.get('target', ''))}
            prepared[key] = (target, tokens, {t[2] for t in tokens if t[2]}, words)
        return prepared[key]
    issues = []
    for unit in units:
        phrases = sorted((p for p in _source_phrases(unit.get('source', '')) if p in index), key=len, reverse=True)
        kept = []
        for phrase in phrases:  # maximal phrases only
            if not any(f' {phrase} ' in f' {longer} ' for longer in kept):
                kept.append(phrase)
        target, tokens, own, words = prepare(unit)
        for phrase in kept:
            content = [w for w in phrase.split() if w not in EN_STOP]
            others, mine_text = [], None
            for other in index[phrase]:
                if other.get('page') is not None and other.get('page') == unit.get('page'):
                    continue
                other_target, other_tokens, theirs_stems, other_words = prepare(other)
                translated = [w for w in content if w not in words and w not in other_words]
                stems = own & theirs_stems
                if len(translated) < 2 or not stems or len(stems) > len(translated)+1:
                    continue
                mine, theirs = _window(target, tokens, stems), _window(other_target, other_tokens, stems)
                if (not mine or not theirs or max(mine[1], theirs[1]) > 2*len(translated)+2
                        or mine[2] != theirs[2]):
                    continue  # ambiguous, or another construction (other word order)
                if [w.casefold() for w in WORD_RE.findall(mine[0])] != [w.casefold() for w in WORD_RE.findall(theirs[0])]:
                    mine_text = mine_text or mine[0]
                    others.append({'page': other.get('page'), 'unit_id': other.get('id'), 'target': theirs[0]})
            if others and not any(i['unit_id'] == unit.get('id') and i['target'] == mine_text for i in issues):
                issues.append({'code': 'phrase_inconsistency', 'unit_id': unit.get('id'), 'action': 'retranslate',
                               'severity': 'minor', 'source_phrase': phrase, 'target': mine_text, 'others': others})
    return issues
