"""Deterministic English-month date labels, without any model calls."""
from __future__ import annotations
import re

MONTHS={
    'january':('enero',31),'february':('febrero',29),'march':('marzo',31),
    'april':('abril',30),'may':('mayo',31),'june':('junio',30),
    'july':('julio',31),'august':('agosto',31),'september':('septiembre',30),
    'october':('octubre',31),'november':('noviembre',30),'december':('diciembre',31),
}
ALIASES={name[:3]:name for name in MONTHS};ALIASES['sept']='september'
MONTH_PATTERN='(?:'+'|'.join(sorted([*MONTHS,*ALIASES],key=len,reverse=True))+r')\.?'
MONTH_RE=re.compile(r'\b('+MONTH_PATTERN+r')\b',re.I)
DATE_RE=re.compile(r'^(?:(?P<month_first>'+MONTH_PATTERN+r')\s*(?P<day_after>\d{1,2})|(?P<day_first>\d{1,2})\s*(?P<month_after>'+MONTH_PATTERN+r'))(?:\s*,?\s*(?P<year>\d{4}))?$',re.I)


def looks_like_date(source:str)->bool:
    return bool(MONTH_RE.search(source) and re.search(r'\d',source) and re.fullmatch(r'(?:'+MONTH_PATTERN+r'|\d|[\s,–—−/-])+',source.strip(),re.I))


def _single(source):
    match=DATE_RE.fullmatch(source.strip())
    if not match:return None
    name=(match['month_first'] or match['month_after']).lower().rstrip('.')
    name=ALIASES.get(name,name);target,maximum=MONTHS[name]
    day=match['day_after'] or match['day_first'];year=match['year']
    if not 1<=int(day)<=maximum:raise ValueError('Invalid day in date label')
    return day+' de '+target+(' de '+year if year else '')


def translate_date(source:str)->str|None:
    """Return a complete deterministic label, or None for ordinary prose.

    A date-like string with unsupported syntax is rejected by callers rather
    than being silently submitted to the model. Source digit strings and their
    order must survive byte-for-byte, including any leading zero.
    """
    parts=re.split(r'\s*[–—−-]\s*',source.strip())
    if len(parts)>2:return None
    converted=[_single(part) for part in parts]
    if any(part is None for part in converted):return None
    result='–'.join(converted)
    if re.findall(r'\d+',source)!=re.findall(r'\d+',result):raise ValueError('Date conversion changed source numbers')
    return result
