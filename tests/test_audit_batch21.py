"""Auditor batch-21 translation defects (book-auditor, 2026-11-09) on real pages; offline, no LLM.

p14/p15: «…heavenly bodies, and Egyptian | symbolism.» was translated as two
independent fragments («y las egipcias | simbolismo.»). p17: «lo que la
astrología carece» (falta «de»). p21: subhead «Earth-centred» → «geocéntrico».
p12/p14: «siglo 4», «25,000» are protected values kept literally by G3: a
warning, never a change. Sources come from the immutable source.pdf; the
audited targets are frozen here because the production artifacts will be
retranslated.
"""
import copy
import json
import re
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import fidelity

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / 'books/the-secrets-of-astrology'
pytestmark = pytest.mark.skipif(not (BOOK / 'source.pdf').exists(), reason='book source not available')

# Audited model output (work/production, batch-21), frozen.
P14_U0006_AUDITED = ('Después, la astrología se extendió a Grecia y Egipto, donde nació el horóscopo. Combinaba el '
                     'zodíaco, las ideas griegas de los cuatro elementos y los cuerpos celestes, y las egipcias')
P15_U0003_AUDITED = ('simbolismo. En la antigua Roma, todos —desde los emperadores hasta la gente común— recurrían a '
                     'la astrología en busca de respuestas. Un punto culminante')
P17_U0006_AUDITED = ('La psicología aporta lo que la astrología carece por naturaleza: una teoría de por qué las personas '
                     'se comportan como lo hacen y de cómo se forma nuestra personalidad.')
P17_AUDITOR_FORM = 'aquello de lo que la astrología carece'  # auditor's correction (DPD), batch-21.json
P12_U0003_AUDITED_TAIL = 'Los griegos retomaron estas ideas tras las conquistas de Alejandro Magno en el siglo 4 a. C. Moldeados por sus'
P14_U0003_AUDITED_PART = 'Los ciclos lunares, por ejemplo, se registraban hace ya 25,000 años.'


def real_units(*numbers):
    from astro_translate.core import read_extras
    extras = read_extras(BOOK / 'work/glossary/do_not_translate.txt')
    with fitz.open(BOOK / 'source.pdf') as doc:
        return {n: fidelity.extract_fidelity_units(doc[n-1], extras) for n in numbers}


def prefixed(units_by_page):
    return [{**u, 'id': f'p{n:04d}_'+u['id'], 'page': n} for n, units in units_by_page.items() for u in units]


# ---------------------------------------------------------------- detection

def test_sentences_split_by_page_and_column_breaks_are_detected_p14_p15():
    from astro_translate.page_context import story_continuations
    pairs = story_continuations(prefixed(real_units(14, 15)))
    assert ('p0014_u0006', 'p0015_u0003', 'page') in pairs        # «and Egyptian» | «symbolism.»
    assert ('p0014_u0003', 'p0014_u0005', 'column') in pairs      # «the Sun’s movement» | «through the constellations.»
    # A paragraph closed by a subhead («…To Greece and Rome») or followed by a
    # capitalized paragraph never joins.
    assert not any(t == 'p0014_u0005' for t, _, _ in pairs)
    assert not any(t == 'p0015_u0003' for t, _, _ in pairs)


def test_display_quote_lines_and_index_lists_never_join_across_pages_p175_p189():
    from astro_translate.page_context import story_continuations
    pairs = story_continuations(prefixed(real_units(175, 176, 188, 189)))
    assert not [p for p in pairs if p[2] == 'page']


# ------------------------------------------------- joint translation (mocked LLM)

class FakeBackend:
    def __init__(self, answer):
        self.answer = answer; self.calls = []

    def complete_json(self, task, payload, schema, model, effort, images=None):
        from astro_translate import llm
        self.calls.append(json.loads(json.dumps({k: v for k, v in payload.items() if k != '_template'})))
        return llm.LLMResult(self.answer(payload, len(self.calls)), model, effort, 12, 7, 'claude_cli', False, .01)


SYLLABLES = 'ka lo mi nu pe ri so tu ba de fi go ja le mo na'.split()


def filler(source, counter):
    """Neutral placeholder words around every protected/style token: no translation."""
    out = []
    for part in re.split(r'(\{[nv]\d+\})', source):
        if re.fullmatch(r'\{[nv]\d+\}', part) or not re.search(r'[^\W\d_]', part):
            out.append(part); continue
        counter[0] += 1
        n = counter[0]; word = SYLLABLES[n % 16]+SYLLABLES[(n//16) % 16]+SYLLABLES[(n//256) % 16]+'r'
        first = re.search(r'[^\W\d_]', part).group()
        lead, trail = re.match(r'\s*', part).group(), re.search(r'\s*$', part).group()
        out.append(lead+(word.capitalize() if first.isupper() else word)+trail)
    return ''.join(out)


def joint_answer(row, counter):
    """Recognisable fragment ends, protected values kept on their side of the break."""
    markers = re.findall(r'\{v\d+\}', row['source'])
    marker = row['story_break']['marker']
    assert len(markers) == 3 and marker == markers[1]
    before, after = row['source'][len(markers[0]):-len(markers[2])].split(marker)
    values = lambda text: ''.join(' '+m for m in re.findall(r'\{n\d+\}', text))
    return markers[0]+filler(before, counter)+' colafinal'+marker+' cabezainicial'+values(after)+markers[2]


def answers(rows, counter, joint=joint_answer):
    return {'blocks': [{'id': r['id'], 'translation': joint(r, counter) if r.get('story_break') else filler(r['source'], counter),
                        'glossary_terms_used': []} for r in rows]}


@pytest.fixture
def offline_book(tmp_path):
    """Temporary approved book with the real page geometry injected; no LLM."""
    from astro_translate import adapters
    from astro_translate.core import approve_glossary, atomic_json
    import tomllib
    book = tmp_path / 'book'; book.mkdir()
    doc = fitz.open()
    for _ in range(22):
        doc.new_page(width=552.77, height=660.49)
    doc.save(book / 'source.pdf'); doc.close()
    g = tmp_path / 'g.csv'; g.write_text('source,target,tgt_lng\nSun sign,signo solar,es\n')
    s = tmp_path / 'style.md'; s.write_text('Español neutro.')
    p = tmp_path / 'protected.txt'; p.write_text('MC\n')
    schema = {'type': 'object', 'properties': {'blocks': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'translation': {'type': 'string'}, 'glossary_terms_used': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['id', 'translation', 'glossary_terms_used'], 'additionalProperties': False}}},
        'required': ['blocks'], 'additionalProperties': False}
    sp = tmp_path / 'schema.json'; sp.write_text(json.dumps(schema))
    pr = tmp_path / 'prompt.md'; pr.write_text('Translate each block.')
    config = tmp_path / 'pipeline.toml'
    config.write_text(f'[provider]\nbackend="claude_cli"\nfallback="none"\nmax_parallel=2\n[quota]\nbackoff_seconds=[0,0,0]\n'
                      f'pause_exit_code=75\n[batching]\nmax_chars_per_call=6000\n[paths]\nglossary="{g}"\nstyle_guide="{s}"\n'
                      f'do_not_translate="{p}"\n[tasks.translate_page]\nmodel="sonnet"\neffort="low"\nprompt="{pr}"\nschema="{sp}"\n')
    approve_glossary(book, g, s, 'TEST ONLY', p)
    atomic_json(book / 'work/manifest.json', {'pages': [{'page': n, 'status': 'pending'} for n in range(1, 23)]})
    cfg = tomllib.loads(config.read_text())
    atomic_json(book / 'work/backend_approval.json', {'approved': True, 'approved_by': 'TEST ONLY', 'backend': 'claude_cli',
        'route': 'pymupdf', 'backend_config_sha256': adapters.backend_config_digest(cfg), 'authorized_phases': ['pilot']})
    return {'book': book, 'config': config, 'glossary': g, 'style': s, 'protected': p}


def translate(env, monkeypatch, units_by_page, answer, pages):
    from astro_translate import adapters, llm
    monkeypatch.setattr(fidelity, 'extract_fidelity_units',
                        lambda page, *a, **k: copy.deepcopy(units_by_page.get(page.number + 1, [])))
    fake = FakeBackend(answer)
    monkeypatch.setattr(llm, '_make_backend', lambda *a, **k: fake)
    adapters.fidelity_translate(env['book'], env['glossary'], env['style'], env['protected'], pages,
                                config_path=env['config'], reinsert_pages=False, phase='pilot')
    return fake


def saved(env, number):
    data = json.loads((env['book'] / f'work/pilot/p{number:04d}-translation.json').read_text())
    return {u['id']: u for u in data['blocks']}


def rows(fake):
    return [row for call in fake.calls for row in call['blocks']]


def body_units(*numbers):
    """Real units without p15's pull quote (its emphasis protocol is covered elsewhere)."""
    units = real_units(*numbers)
    if 15 in units:
        units[15] = [u for u in units[15] if u['id'] not in ('u0001', 'u0002')]
    return units


def test_cross_page_sentence_is_one_request_split_at_the_break_p14_p15(offline_book, monkeypatch):
    """Selecting p14 alone sends «…and Egyptian» with p15's «symbolism» in one row."""
    units = body_units(14, 15); counter = [0]
    fake = translate(offline_book, monkeypatch, units, lambda wire, call: answers(wire['blocks'], counter), '14')
    joint = [r for r in rows(fake) if r.get('story_break', {}).get('boundary') == 'page']
    assert len(joint) == 1
    source = joint[0]['source']
    assert 'and Egyptian' in source and 'symbolism' in source and 'In ancient Rome' not in source
    assert joint[0]['story_break_instructions'].startswith('One sentence continues across a column or page break')
    assert any(r.get('story_break', {}).get('boundary') == 'column' and 'through the constellations' in r['source']
               for r in rows(fake))
    p14 = saved(offline_book, 14)
    assert p14['p0014_u0006']['target'].endswith('colafinal')
    assert p14['p0014_u0006']['story_continuation'][0]['partner'] == 'p0015_u0003'
    assert p14['p0014_u0003']['target'].endswith('colafinal') and p14['p0014_u0005']['target'].startswith('cabezainicial.')
    assert not (offline_book['book'] / 'work/pilot/p0015-translation.json').exists()  # context only, never written

    # Later batch: p15 reuses the joint answer from sentence memory, no new request for it.
    fake = translate(offline_book, monkeypatch, units, lambda wire, call: answers(wire['blocks'], counter), '15')
    assert not [r for r in rows(fake) if 'symbolism' in r['source']]
    p15 = saved(offline_book, 15)
    assert p15['p0015_u0003']['target'].startswith('cabezainicial. ')
    assert p15['p0015_u0003']['story_continuation'][0]['partner'] == 'p0014_u0006'

    from astro_translate.page_context import cross_page_continuity
    assert cross_page_continuity(offline_book['book'] / 'work/pilot', 14) == ([], [])
    assert cross_page_continuity(offline_book['book'] / 'work/pilot', 15) == ([], [])


def test_broken_story_break_answer_is_asked_again_with_validation_error(offline_book, monkeypatch):
    units = body_units(14, 15); counter = [0]

    def answer(wire, call):
        def glued(r, counter):  # everything before the break: the head fragment is left empty
            return joint_answer(r, counter).replace(' cabezainicial', '')
        return answers(wire['blocks'], counter, joint=glued if call == 1 else joint_answer)
    fake = translate(offline_book, monkeypatch, units, answer, '14,15')
    assert 'story break' in fake.calls[1]['validation_error']
    p14, p15 = saved(offline_book, 14), saved(offline_book, 15)
    assert p14['p0014_u0006']['target'].endswith('colafinal') and p15['p0015_u0003']['target'].startswith('cabezainicial.')


def test_cross_page_continuity_qa_flags_independent_fragments_p14_p15(tmp_path):
    """Audited artifacts: both fragments translated apart → blocking on each page;
    a joint page next to a stale neighbour → warning on it, blocking on the neighbour."""
    from astro_translate.page_context import cross_page_continuity
    units = real_units(14, 15)
    audited = {'p0014_u0006': P14_U0006_AUDITED, 'p0015_u0003': P15_U0003_AUDITED}
    def write(number, blocks):
        (tmp_path / f'p{number:04d}-translation.json').write_text(json.dumps({'page': number, 'blocks': blocks}))
    pages = {n: [{**u, 'target': audited.get(u['id'], u['source'])} for u in prefixed({n: units[n]})] for n in (14, 15)}
    for n, blocks in pages.items():
        write(n, blocks)
    blocking, warnings = cross_page_continuity(tmp_path, 14)
    assert [(i['unit_id'], i['code'], i['partner']) for i in blocking] == [('p0014_u0006', 'cross_page_sentence_not_joint', 'p0015_u0003')]
    assert cross_page_continuity(tmp_path, 15)[0][0]['unit_id'] == 'p0015_u0003'
    joint = [{**u, 'story_continuation': [{'side': 'tail', 'partner': 'p0015_u0003', 'sentence_key': 'k', 'boundary': 'page'}]}
             if u['id'] == 'p0014_u0006' else u for u in pages[14]]
    write(14, joint)
    blocking, warnings = cross_page_continuity(tmp_path, 14)
    assert not blocking and [w['code'] for w in warnings] == ['cross_page_partner_stale']
    assert cross_page_continuity(tmp_path, 15)[0][0]['code'] == 'cross_page_sentence_not_joint'


# ------------------------------------------------------------- grammar QA

def test_missing_governed_preposition_is_a_retranslation_issue_p17():
    from astro_translate.text_quality import text_issues
    source = real_units(17)[17]
    unit = next(u for u in source if u['source'].startswith('Psychology provides what astrology naturally lacks'))
    issues = [i for i in text_issues(unit['source'], P17_U0006_AUDITED, 'p0017_u0006') if i['code'] == 'preposition_regime']
    assert issues and issues[0]['action'] == 'retranslate' and issues[0]['expected_preposition'] == 'de'
    fixed = P17_U0006_AUDITED.replace('lo que la astrología carece', P17_AUDITOR_FORM)
    assert not [i for i in text_issues(unit['source'], fixed, 'p0017_u0006') if i['code'] == 'preposition_regime']


@pytest.mark.parametrize('text', [
    'La astrología carece de rigor.', 'lo que carece de sentido', 'Eso depende.',
    'Dice que la respuesta depende del contexto.', 'un planeta que carece por completo de luz propia',
    'Es en lo que la autora insiste.', 'el tema en el que consiste la técnica', 'a lo que se refiere',
])
def test_governed_prepositions_present_are_not_flagged(text):
    from astro_translate.text_quality import preposition_regime_issues
    assert preposition_regime_issues(text, 'x') == []


def test_lowercase_subhead_is_rejected_by_validator_memory_and_qa_p21(tmp_path):
    from astro_translate import llm
    from astro_translate.consistency import SentenceMemory, build_plan
    from astro_translate.text_quality import page_issues
    units = prefixed(real_units(21))
    plan = build_plan([u for u in units if u['kind'] in ('label', 'paragraph') and u['id'] in
                       ('p0021_u0003', 'p0021_u0004', 'p0021_u0005', 'p0021_u0006')])
    flagged = [r for r in plan['requests'] if r.get('_initial_upper')]
    assert {r['source'] for r in flagged} == {'Earth-centred', 'The nature of symbols'}
    request = next(r for r in flagged if r['source'] == 'Earth-centred')
    wire, _ = llm._prepare('translate_page', {'blocks': [request]}, (), [])
    schema = {'type': 'object'}
    with pytest.raises(llm.ValidationFailure, match='capital letter'):
        llm._validate('translate_page', {'blocks': [{'id': request['id'], 'translation': 'geocéntrico', 'glossary_terms_used': []}]}, wire, schema)
    llm._validate('translate_page', {'blocks': [{'id': request['id'], 'translation': 'Geocéntrico', 'glossary_terms_used': []}]}, wire, schema)
    entry = next(e for e in plan['entries'].values() if e['id'] == request['id'])
    memory = SentenceMemory(tmp_path, 'test')
    try:
        memory.put(entry, 'geocéntrico')
        assert memory.get(entry) is None  # the audited memory is retranslated, not reused
    finally:
        memory.close()
    blocks = [{**u, 'target': 'geocéntrico' if u['id'] == 'p0021_u0003' else u['source']} for u in units]
    assert [i['unit_id'] for i in page_issues(blocks) if i['code'] == 'heading_initial_case'] == ['p0021_u0003']


def test_validation_failures_are_asked_again_through_run_task(offline_book, monkeypatch):
    """Item 3: a failing unit is re-requested with ``validation_error``, not only marked."""
    from astro_translate import llm
    answers_by_call = {1: [P17_U0006_AUDITED, 'geocéntrico'],
                       2: [P17_U0006_AUDITED.replace('lo que la astrología carece', P17_AUDITOR_FORM), 'Geocéntrico']}
    fake = FakeBackend(lambda wire, call: {'blocks': [{'id': r['id'], 'translation': text, 'glossary_terms_used': []}
                                                      for r, text in zip(wire['blocks'], answers_by_call[call])]})
    monkeypatch.setattr(llm, '_make_backend', lambda *a, **k: fake)
    result = llm.run_task('translate_page', {'_book': str(offline_book['book']), '_config': str(offline_book['config']),
        '_phase': 'pilot', '_pages': [17, 21], 'blocks': [
            {'id': 'p0017_s1', 'source': 'Psychology provides what astrology naturally lacks— a theory of why people behave the way they do and how our personalities are formed'},
            {'id': 'p0021_s1', 'source': 'Earth-centred', '_initial_upper': True}]})
    assert len(fake.calls) == 2
    assert 'preposition_regime' in fake.calls[1]['validation_error'] and 'capital letter' in fake.calls[1]['validation_error']
    assert {r['id']: r['translation'] for r in result.data['blocks']}['p0021_s1'] == 'Geocéntrico'


# --------------------------------------------- protected numbers: warn, never change

def test_literal_protected_numbers_are_warnings_not_issues_p12_p14():
    from astro_translate.text_quality import format_warnings, page_issues
    blocks = [{'id': 'p0012_u0003', 'source': 'in the 4th century BCE. Shaped by their', 'target': P12_U0003_AUDITED_TAIL},
              {'id': 'p0014_u0003', 'source': 'as long as 25,000 years ago.', 'target': P14_U0003_AUDITED_PART}]
    before = copy.deepcopy(blocks)
    warnings = format_warnings(blocks)
    assert {(w['unit_id'], w['code'], w['value']) for w in warnings} == {
        ('p0012_u0003', 'arabic_century_numeral', '4'), ('p0014_u0003', 'english_thousands_separator', '25,000')}
    assert all(w['action'] == 'none' for w in warnings)
    assert blocks == before
    assert not {i['code'] for i in page_issues(blocks)} & {'arabic_century_numeral', 'english_thousands_separator'}
