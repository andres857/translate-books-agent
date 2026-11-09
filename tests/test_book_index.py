"""Gap 11 (G-INDEX): index (p188-191) and glossary (p186-187) re-sorted for Spanish.

Real cases: p188 'Aquarius 27, 76-79' + subheadings ('Chiron in 135'...) at
x+8.5, continuation lines at x+17, letter headings SohoPro-Bold 14 pt grey;
p189 '176, 181, 183' continues 'Descendant ...' at an irregular x+11.3;
p190 inline 'luminaries see Moon; Sun'. p192 (about the author) is NOT index.
p186 run-in 'Air signs Gemini, Libra, and Aquarius.' (SohoPro-Bold grey term +
SohoPro-Light definition). Translation is pseudo/offline here: no LLM.
"""
from collections import Counter
from pathlib import Path

import pymupdf as fitz
import pytest

from astro_translate import book_index as bi
from astro_translate.core import Blocked, read_extras

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT/'books/the-secrets-of-astrology'
secrets = pytest.mark.skipif(not (BOOK/'source.pdf').is_file(), reason='Secrets source PDF not available')


def test_spanish_collation():
    words = ['ñandú', 'nube', 'zodíaco', 'El ascendente', 'casa natal', 'casamiento', 'Cáncer', 'cancer',
             'la luna', 'Luna nueva', 'ábaco', '“plana”', '10.ª casa', '2.ª casa', '1.ª casa', 'cúspides']
    ordered = sorted(words, key=bi.sort_key)
    assert ordered.index('ábaco') < ordered.index('El ascendente') < ordered.index('cancer')
    assert ordered.index('cancer') < ordered.index('Cáncer') < ordered.index('casa natal') < ordered.index('casamiento')
    assert ordered.index('nube') < ordered.index('ñandú') < ordered.index('“plana”') < ordered.index('zodíaco')
    assert ordered.index('la luna') < ordered.index('Luna nueva')  # article ignored
    assert ordered[:3] == ['1.ª casa', '2.ª casa', '10.ª casa']  # numeric, before letters
    assert bi.heading_letter('El ascendente') == 'A' and bi.heading_letter('ñandú') == 'Ñ'
    assert bi.heading_letter('Órbita') == 'O'


@secrets
def test_roles_are_structural():
    with fitz.open(BOOK/'source.pdf') as doc:
        roles = {p.number+1: bi.page_role(p) for p in doc}
    assert {n: r for n, r in roles.items() if r} == {186: 'glossary', 187: 'glossary', 188: 'index', 189: 'index',
                                                    190: 'index', 191: 'index'}


@secrets
def test_index_extraction_real_pages():
    with fitz.open(BOOK/'source.pdf') as doc:
        index = next(s for s in bi.analyze(doc) if s['kind'] == 'index')
    assert index['pages'] == [188, 189, 190, 191]
    entries = {e['term']: e for e in index['entries']}
    aquarius = entries['Aquarius']
    assert aquarius['locators'] == '27, 76-79'
    assert {'term': 'Chiron in', 'locators': '135'} in aquarius['subs']
    houses = next(s for s in aquarius['subs'] if s['term'] == 'in the houses')
    assert houses['locators'] == '140, 142, 144, 146, 148, 150, 154, 156, 158, 160, 162, 164'
    assert entries['Descendant']['locators'].endswith('173, 176, 181, 183')
    assert entries['luminaries']['xref'] == {'label': 'see', 'targets': ['Moon', 'Sun'], 'inline': True}
    assert entries['cardinal signs']['xref']['targets'] == ['Aries', 'Cancer', 'Capricorn', 'Libra']
    assert entries['mutable signs']['xref']['targets'][-1] == 'Virgo'  # continuation line
    assert entries['IC (Imum Coeli)']['locators'] == '23, 138-39, 169, 171, 172-73, 176, 181, 183'
    assert index['metrics']['pitch'] == 12.0 and index['metrics']['size'] == 8.0
    assert len({(f['page'], f['column']) for f in index['frames']}) == 12
    requests = bi.requests(index)
    assert not requests['blocks']
    assert all(not any(ch.isdigit() for ch in r['source']) or r['source'][0].isdigit() for r in requests['labels'])
    sources = [r['source'] for r in requests['labels']]
    assert len(sources) == len(set(sources)) and 'Chiron in' in sources and 'see also' in sources
    assert 'INDEX' in sources and 'Index' in sources and 'Astrology' in sources
    assert not any(bi.LOCATORS.match(s) for s in sources)


@secrets
def test_glossary_extraction_real_pages():
    with fitz.open(BOOK/'source.pdf') as doc:
        glossary = next(s for s in bi.analyze(doc) if s['kind'] == 'glossary')
    assert glossary['pages'] == [186, 187] and len(glossary['entries']) == 39
    first = glossary['entries'][0]
    assert first['term'] == 'Air signs' and first['definition'] == 'Gemini, Libra, and Aquarius.'
    geo = next(e for e in glossary['entries'] if e['term'] == 'Geocentric')
    assert geo['definition'].endswith('centre looking out at the zodiac and planets around them.')  # crosses columns
    assert glossary['styles']['term']['bold'] and glossary['styles']['term']['color'] == 0x6f6f6f
    assert 20 < glossary['metrics']['entry_gap'] < 30


@secrets
def test_pseudo_compose_index_and_glossary(tmp_path):
    extras = read_extras(ROOT/'glossary/do_not_translate.txt')
    source = BOOK/'source.pdf'
    with fitz.open(source) as doc:
        structures = bi.analyze(doc)
    for structure in structures:
        answers = bi.translate(structure, bi.pseudo_translator(1.25, extras))
        assert all(not bi.LOCATORS.match(t) for t in answers)  # nothing with locators was requested
        outputs = {n: tmp_path/f'p{n:04d}.pdf' for n in structure['pages']}
        report = bi.compose(source, structure, answers, tmp_path, outputs)
        findings = bi.local_qa(source, structure, report, extras)
        assert report['scale'] >= .75
        for n, f in findings.items():
            assert f['locators_equal'] and f['folios_equal'] and not f['overflow'], n
            assert f['non_text_pixel_diff'] == 0, n
        # Spanish order of the composed headwords.
        assert report['order'] == sorted(report['order'], key=bi.sort_key)
        if structure['kind'] == 'index':
            assert report['scale'] == 1.0
            with fitz.open(outputs[188]) as out:
                spans = [s for b in out[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
                text = out[0].get_text()
            headings = [s['text'] for s in spans if s['size'] == 14 and len(s['text'].strip()) == 1]
            assert headings and headings == sorted(headings, key=bi.sort_key)
            assert all(s['color'] == structure['styles']['heading']['color'] for s in spans
                       if s['size'] == 14 and len(s['text'].strip()) == 1)
            assert '188' in text


def test_overflow_blocks_explicitly():
    structure = {'kind': 'glossary', 'pages': [1], 'frames': [{'page': 1, 'column': 0, 'x0': 10, 'top': 10, 'bottom': 40}],
                 'entries': [{'term': 'Term', 'definition': 'word '*400, 'size': 10}],
                 'metrics': {'pitch': 12, 'size': 10, 'entry_gap': 5, 'measure': 100, 'baseline': 8},
                 'styles': {}, 'labels': {}}
    font = {'font': fitz.Font('helv')}
    with pytest.raises(Blocked, match='does not fit'):
        bi.layout(structure, {}, {'term': font, 'body': font})


try:  # reuse the offline production fixture (gate/run_task doubles only)
    from test_production import offline_book  # noqa: F401
except ImportError:  # pragma: no cover
    offline_book = None


@secrets
def test_production_routes_index_through_book_index(offline_book):
    import json
    from astro_translate.production import produce
    book, calls, config, _ = offline_book
    result = produce(book, pages='188', config_path=config)
    assert result['batches']
    label_calls = [p for t, p in calls if t == 'translate_labels']
    assert label_calls and all(p['context'].startswith('Back-of-book index') for p in label_calls)
    assert not [p for t, p in calls if t == 'translate_page']
    sent = [r['source'] for p in label_calls for r in p['labels']]
    assert 'Chiron in' in sent and not any(bi.LOCATORS.match(s) for s in sent)
    qa = json.loads((book/'work/production/p0188-qa.json').read_text())
    assert qa['back_matter'] == 'index' and qa['locators_equal'] and qa['folios_equal']
    assert qa['order'] == sorted(qa['order'], key=bi.sort_key)
    saved = json.loads((book/'work/production/back_matter-index-p0188.json').read_text())
    assert set(saved['answers']) == set(sent)
    # The mock echoes English: never accepted as a translation.
    manifest = json.loads((book/'work/manifest.json').read_text())
    assert manifest['pages'][187]['status'] != 'done_qa_ok'
    fit = json.loads((book/'work/production/p0188-fit.json').read_text())
    with fitz.open(fit['output']) as out:
        assert len(out) == 1 and '188' in out[0].get_text()
