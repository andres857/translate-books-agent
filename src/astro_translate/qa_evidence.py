"""Retain VLM evidence only for exactly the same source and candidate bytes."""
from copy import deepcopy


def preserve_vlm(fresh, previous):
    if (not fresh.get('candidate_sha256') or
        fresh.get('candidate_sha256') != previous.get('candidate_sha256') or
        fresh.get('source_sha256') != previous.get('source_sha256')):
        return fresh
    result=dict(fresh)
    result.update({k:deepcopy(v) for k,v in previous.items() if k=='vlm' or k.startswith('vlm_')})
    return result
