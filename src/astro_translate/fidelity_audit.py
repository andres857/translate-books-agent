"""Verify typography on actual PDF characters, independently of layout claims."""
from __future__ import annotations
import re
import unicodedata
import pymupdf as fitz


def _font_key(name):
    name = re.sub(r'^[A-Z]{6}\+', '', name)
    # MuPDF exposes raw CFF names with/without their redundant Regular suffix.
    return re.sub(r'[^a-z0-9]', '', name.casefold()).removesuffix('regular')


def _characters(text):
    return [c for c in unicodedata.normalize('NFKC', text).replace('\u2010','-').replace('\u2011','-')
            if not c.isspace() and c != '\u00ad']


def audit_rendered_styles(page, result):
    """Match every translated run against PDF text, color, size and font name.

    Generated font instances need distinct internal names: verifying a font file
    exists in resources alone does not prove the correct run actually uses it.
    Original symbolic spans are outside the editable containers audited here.
    """
    issues = []
    checked = 0
    import hashlib
    embedded={}
    for row in page.get_fonts(full=True):
        data=page.parent.extract_font(row[0])[3]
        if data:embedded.setdefault(_font_key(row[3]),set()).add(hashlib.sha256(data).hexdigest())
    for block in result['blocks']:
        rect = fitz.Rect(block['edit_bbox']) + (-.1, -.1, .1, .1)
        observed = []
        for bb in page.get_text('rawdict', clip=rect)['blocks']:
            for line in bb.get('lines', []):
                for span in line['spans']:
                    for char in span['chars']:
                        observed.extend((c, span) for c in _characters(char['c']))
        text = ''.join(c for c, _ in observed)
        target = ''.join(_characters(block['target']))
        offset = text.find(target)
        if offset < 0:
            issues.append({'id': block['id'], 'reason': 'translated_text_missing_in_container'})
            continue
        expected = []
        if len(block['target_runs']) != len(block['font_runs']):
            issues.append({'id': block['id'], 'reason': 'run_count_mismatch'})
            continue
        for run, font in zip(block['target_runs'], block['font_runs']):
            expected.extend((c, run['style'], font) for c in _characters(run['text']))
        if ''.join(c for c, _, _ in expected) != target:
            issues.append({'id': block['id'], 'reason': 'target_runs_mismatch'})
            continue
        reasons = set()
        for (_, span), (_, style, font) in zip(observed[offset:offset+len(target)], expected):
            checked += 1
            if span['color'] != style['color']:
                reasons.add('run_color_changed')
            if abs(span['size'] - font['size']) > .02:
                reasons.add('run_size_changed')
            if (_font_key(span['font']) != _font_key(font['engine_name']) and not (font.get('source')=='original' and embedded.get(_font_key(span['font']))=={font['sha256']})):
                reasons.add('run_font_changed')
        issues.extend({'id': block['id'], 'reason': reason} for reason in sorted(reasons))
    return {'ok': not issues, 'characters_checked': checked, 'issues': issues,
            'checks': ['per_character_color', 'per_character_size', 'per_character_font_instance']}
