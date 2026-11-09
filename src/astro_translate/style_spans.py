"""Local legacy pair identities; new inference uses emphasis.py flat annotations."""
import re
from copy import deepcopy
TOKEN=re.compile(r'\{v\d+\}')
PAIR_INSTRUCTION=(
    'For rows with style_pair_instructions, this supersedes the template requirement to keep style markers in source order: '
    'each consecutive opening/closing pair encloses the equivalent translated phrase, and complete pairs may move to natural target order. '
    'Preserve every marker once, its pairing and equivalent emphasis; never nest or cross pairs. '
    'Other rows retain the template style-marker rules. All numeric/protected-marker and glossary rules still apply.'
)


def encode(runs):
    pairs=[];parts=[]
    for i,r in enumerate(runs):
        a,b='{v'+str(2*i+1)+'}','{v'+str(2*i+2)+'}'
        pairs.append({'open':a,'close':b,'style':deepcopy(r['style'])})
        parts.append(a+r['text']+b)
    return ''.join(parts),pairs


def decode(text,pairs):
    opens={p['open']:p for p in pairs};seen=set();runs=[];active=None;position=0
    for m in TOKEN.finditer(text):
        chunk=text[position:m.start()];token=m.group()
        if token in seen:raise ValueError('Repeated emphasis marker')
        seen.add(token)
        if active is None:
            if chunk.strip() or token not in opens:raise ValueError('Text outside paired emphasis or closing marker without opening')
            if chunk and runs:runs[-1]['text']+=chunk
            active=opens[token]
        else:
            if token!=active['close'] or not chunk.strip():raise ValueError('Crossed or empty emphasis pair')
            runs.append({'text':chunk,'style':deepcopy(active['style'])});active=None
        position=m.end()
    if active or text[position:].strip() or seen!={m for p in pairs for m in (p['open'],p['close'])}:raise ValueError('Incomplete emphasis pairs')
    return runs


def decode_answer(answer,request):
    if isinstance(answer,dict):
        from .emphasis import reconstruct
        return reconstruct(answer,request)
    return decode(answer,request['_style_pairs'])
